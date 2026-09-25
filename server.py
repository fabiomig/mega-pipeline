import os
import http.server
import socketserver
import urllib.request
import urllib.parse
import urllib.error
import json
import base64
import re
import sys
import time
import ssl
import threading
from datetime import datetime, timezone
try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    HAVE_CRYPTOGRAPHY = True
except ImportError:
    HAVE_CRYPTOGRAPHY = False

try:
    from Cryptodome.Cipher import AES
    from Cryptodome.Util import Counter
    HAVE_CRYPTODOME = True
except ImportError:
    HAVE_CRYPTODOME = False

PORT = 8787

# Cache Persistente em Disco do Catálogo
CATALOG_CACHE_PATH = os.environ.get(
    "CATALOG_CACHE_FILE",
    "/home/fabio/transfer-tunnel/catalog_cache.json" if os.path.exists("/home/fabio/transfer-tunnel") else os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog_cache.json")
)
CATALOG_MEMORY_CACHE = None
CATALOG_LOCK = threading.Lock()
LAST_SYNC_TIME = 0
MIN_SYNC_INTERVAL = 1800  # 30 minutos mínimos entre sincronizações completas no 1fichier
CATALOG_DISK_MTIME = 0
SYNC_IN_PROGRESS = False

# Cache de Streams Diretos em Memória (TTL 8h para estabilidade total de streaming)
STREAM_CACHE = {}  # file_id: (direct_url, expire_timestamp)
STREAM_CACHE_LOCK = threading.Lock()
STREAM_CACHE_TTL = 28800  # 8 horas

# Cache de Ficheiros MEGA em Memória (TTL 1h)
MEGA_CACHE = {}  # file_id: (info_dict, expire_timestamp)
MEGA_CACHE_LOCK = threading.Lock()



def b64url(s):
    s = s.replace('-', '+').replace('_', '/')
    while len(s) % 4 != 0:
        s += '='
    return base64.b64decode(s)


def decrypt_cbc(aes_key, data):
    if HAVE_CRYPTOGRAPHY:
        cipher = Cipher(algorithms.AES(aes_key), modes.CBC(b'\x00' * 16))
        return cipher.decryptor().update(data) + cipher.decryptor().finalize()
    elif HAVE_CRYPTODOME:
        return AES.new(aes_key, AES.MODE_CBC, iv=b'\x00' * 16).decrypt(data)
    else:
        raise RuntimeError("Nenhuma biblioteca de criptografia encontrada (requer cryptography ou pycryptodome)")


def decrypt_ctr(aes_key, iv_upper, block_offset, data):
    iv_int = (iv_upper << 64) + block_offset
    if HAVE_CRYPTOGRAPHY:
        iv_bytes = iv_int.to_bytes(16, byteorder='big')
        return Cipher(algorithms.AES(aes_key), modes.CTR(iv_bytes)).decryptor().update(data)
    elif HAVE_CRYPTODOME:
        ctr = Counter.new(128, initial_value=iv_int)
        return AES.new(aes_key, AES.MODE_CTR, counter=ctr).decrypt(data)
    else:
        raise RuntimeError("Nenhuma biblioteca de criptografia encontrada (requer cryptography ou pycryptodome)")


def mega_api(xh, payload):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        f"https://g.api.mega.co.nz/cs?id=0&x={xh}",
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode('utf-8'))


def decrypt_attributes(attr_b64, key_b64):
    raw_key = b64url(key_b64)
    key = bytes(raw_key[i] ^ raw_key[i+16] for i in range(16)) if len(raw_key) == 32 else raw_key
    dec = decrypt_cbc(key, b64url(attr_b64))
    s = dec.decode('utf-8', errors='ignore')
    m = re.search(r'"n":"(.*?)"', s)
    return m.group(1) if m else "download.bin"


def parse_mega_url(url_or_str):
    """Extrai file_id e key_str de links da MEGA:
       - https://mega.nz/file/<id>#<key>
       - https://mega.nz/#!<id>!<key>
       - <id>#<key> ou <id>/<key>
    """
    clean = url_or_str.strip()
    m = re.search(r'([a-zA-Z0-9_-]{8,12})[#!,/]([a-zA-Z0-9_-]{20,50})', clean)
    if m:
        return m.group(1), m.group(2)
    return None, None


def normalize_filename(filename: str) -> str:
    """Normaliza o nome do ficheiro removendo tags entre colchetes [ex: CRC, grupo] e resoluções redundantes."""
    if not filename:
        return "download.bin"
    m = re.match(r'^(.*?)(\.[a-zA-Z0-9]+)$', filename)
    if not m:
        base, ext = filename, ''
    else:
        base, ext = m.group(1), m.group(2)
    base = re.sub(r'\[.*?\]', '', base)
    base = re.sub(r'\((?:1080p|720p|480p|x264|x265|hevc|web-dl|bluray|dvdrip|aac|opus).*?\)', '', base, flags=re.IGNORECASE)
    base = re.sub(r'\s*\([^)]*\)\s*$', '', base)
    base = re.sub(r'\s+', ' ', base).strip()
    base = re.sub(r'\s*-\s*$', '', base).strip()
    return f"{base}{ext}"


def get_mega_file_info(file_id, key_str, force_refresh=False):
    """Obtém metadados, CDN URL e chaves criptográficas para streaming on-the-fly da MEGA."""
    now = time.time()
    if not force_refresh:
        with MEGA_CACHE_LOCK:
            if file_id in MEGA_CACHE:
                info, exp = MEGA_CACHE[file_id]
                if now < exp:
                    return info

    raw_key = b64url(key_str)
    aes_key = bytes(raw_key[i] ^ raw_key[i+16] for i in range(16))
    iv_upper = int.from_bytes(raw_key[16:24], byteorder='big')

    payload = [{'a': 'g', 'g': 1, 'p': file_id, 'ssl': 1}]
    req = urllib.request.Request(
        'https://g.api.mega.co.nz/cs?id=0',
        data=json.dumps(payload).encode('utf-8'),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        res_list = json.loads(resp.read().decode('utf-8'))
        if not res_list or not isinstance(res_list, list):
            raise ValueError(f"Resposta inválida da MEGA API ({res_list})")
        res = res_list[0]

    if isinstance(res, int) or 'e' in res:
        err_code = res if isinstance(res, int) else res.get('e')
        raise ValueError(f"Ficheiro MEGA indisponível ou removido (código {err_code})")
    if 'g' not in res:
        raise ValueError(f"Link de download direto não devolvido pela MEGA ({res})")

    dl_url = res['g']
    size = res.get('s', 0)
    at_bytes = b64url(res.get('at', ''))

    try:
        attr_dec = decrypt_cbc(aes_key, at_bytes)
        m = re.search(r'"n":"(.*?)"', attr_dec.decode('utf-8', errors='ignore'))
        filename = m.group(1) if m else f"{file_id}.bin"
    except Exception:
        filename = f"{file_id}.bin"

    info = {
        'file_id': file_id,
        'key_str': key_str,
        'raw_key': raw_key,
        'aes_key': aes_key,
        'iv_upper': iv_upper,
        'dl_url': dl_url,
        'size': size,
        'filename': filename
    }
    with MEGA_CACHE_LOCK:
        MEGA_CACHE[file_id] = (info, now + 900)  # Cache 15 min (tokens CDN expiram)
    return info


def resolve_transfer(xh):
    res = mega_api(xh, [{"a": "f", "c": 1, "r": 1}])
    if not res or not isinstance(res, list) or "f" not in res[0]:
        raise ValueError(f"Ficheiro ou transferência inválida ({res})")

    nodes = res[0]["f"]
    file_node = next((n for n in nodes if n.get("t") == 0), None)
    if not file_node:
        raise ValueError("Nenhum ficheiro encontrado neste link")

    filename = decrypt_attributes(file_node.get("a", ""), file_node.get("k", ""))
    size = file_node.get("s", 0)
    node_handle = file_node.get("h")

    g_res = mega_api(xh, [{"a": "g", "n": node_handle, "pt": 1, "g": 1, "ssl": 1}])
    if not g_res or not isinstance(g_res, list) or "g" not in g_res[0]:
        raise ValueError("Não foi possível gerar o link direto da MEGA")

    direct_url = f"{g_res[0]['g']}/{urllib.parse.quote(filename)}"
    return {
        "filename": filename,
        "size": size,
        "direct_url": direct_url,
        "handle": xh
    }


def get_1fichier_token():
    for p in [os.path.expanduser("~/.1fichier_token"), "/home/fabio/.1fichier_token", ".1fichier_token"]:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content.startswith("{"):
                    return json.loads(content).get("api_key")
                else:
                    return content.splitlines()[0].strip()
            except Exception:
                pass
    return None


def resolve_1fichier_download(file_id):
    """Resolve o token de streaming com cache em memória (TTL 8 horas) para evitar sobrecarga."""
    now = time.time()
    last_known_url = None
    with STREAM_CACHE_LOCK:
        if file_id in STREAM_CACHE:
            cached_url, exp = STREAM_CACHE[file_id]
            last_known_url = cached_url
            if now < exp:
                return cached_url

    api_key = get_1fichier_token()
    if not api_key:
        if last_known_url:
            return last_known_url
        raise ValueError("Chave de API do 1fichier não configurada (~/.1fichier_token)")

    url = "https://api.1fichier.com/v1/download/get_token.cgi"
    payload = {
        "url": f"https://1fichier.com/?{file_id}",
        "inline": 1
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "play-hospidy/1.0"
        }
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") == "OK" and "url" in data:
                direct_url = data["url"]
                with STREAM_CACHE_LOCK:
                    STREAM_CACHE[file_id] = (direct_url, now + STREAM_CACHE_TTL)
                return direct_url
            if last_known_url:
                sys.stderr.write(f"Aviso 1fichier ({data.get('message')}). Usando token de streaming anterior em cache.\n")
                return last_known_url
            raise ValueError(data.get("message", "Falha ao obter streaming do 1fichier"))
    except Exception as e:
        if last_known_url:
            sys.stderr.write(f"Aviso exceção 1fichier ({e}). Usando token de streaming anterior em cache.\n")
            return last_known_url
        raise


def sync_catalog(api_key=None, force=False):
    """Consulta a API do 1fichier (pastas e ficheiros) e atualiza o catalog_cache.json de forma atómica."""
    global CATALOG_MEMORY_CACHE, LAST_SYNC_TIME
    now = time.time()
    if not force and CATALOG_MEMORY_CACHE is not None and (now - LAST_SYNC_TIME < MIN_SYNC_INTERVAL):
        return CATALOG_MEMORY_CACHE

    if not api_key:
        api_key = get_1fichier_token()
    if not api_key:
        raise ValueError("Chave de API do 1fichier não configurada (~/.1fichier_token)")

    ctx = ssl.create_default_context()

    # 1. Obter mapeamento de pastas do 1fichier
    req_folder = urllib.request.Request(
        "https://api.1fichier.com/v1/folder/ls.cgi",
        data=b"{}",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "play-hospidy/1.0"}
    )
    folder_map = {}          # folder_id -> folder_name
    folder_name_to_id = {}   # folder_name -> folder_id
    try:
        with urllib.request.urlopen(req_folder, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for sf in data.get("sub_folders", []):
                fid = sf.get("id")
                fname = sf.get("name")
                if fid is not None and fname:
                    folder_map[fid] = fname
                    folder_name_to_id[fname] = fid
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        sys.stderr.write(f"Aviso HTTP {e.code} ao consultar pastas: {err_msg[:120]}\n")
    except Exception as e:
        sys.stderr.write(f"Aviso ao consultar pastas no 1fichier: {e}\n")

    # Pastas mestras padrão requeridas
    master_folder_names = ["Filmes", "Series", "Musica", "Kids"]
    for mfn in master_folder_names:
        if mfn not in folder_name_to_id:
            try:
                req_mkdir = urllib.request.Request(
                    "https://api.1fichier.com/v1/folder/mkdir.cgi",
                    data=json.dumps({"name": mfn, "folder_id": 0}).encode("utf-8"),
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "play-hospidy/1.0"}
                )
                with urllib.request.urlopen(req_mkdir, timeout=10, context=ctx) as resp_mk:
                    res_mk = json.loads(resp_mk.read().decode("utf-8"))
                    if res_mk.get("status") == "OK" and "folder_id" in res_mk:
                        new_fid = res_mk["folder_id"]
                        folder_map[new_fid] = mfn
                        folder_name_to_id[mfn] = new_fid
            except Exception as e_mk:
                sys.stderr.write(f"Aviso: Não foi possível assegurar criação da pasta {mfn}: {e_mk}\n")

    # 1b. Mapear recursivamente subpastas existentes sob cada pasta mestra
    for mfn in master_folder_names:
        mfid = folder_name_to_id.get(mfn)
        if not mfid:
            continue
        queue = [mfid]
        while queue:
            parent_id = queue.pop(0)
            try:
                req_sub = urllib.request.Request(
                    "https://api.1fichier.com/v1/folder/ls.cgi",
                    data=json.dumps({"folder_id": parent_id}).encode("utf-8"),
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "play-hospidy/1.0"}
                )
                with urllib.request.urlopen(req_sub, timeout=10, context=ctx) as resp_sub:
                    sub_data = json.loads(resp_sub.read().decode("utf-8"))
                    for child in sub_data.get("sub_folders", []):
                        child_id = child.get("id")
                        if child_id and child_id not in folder_map:
                            folder_map[child_id] = mfn
                            queue.append(child_id)
            except Exception as e_sub:
                sys.stderr.write(f"Aviso ao consultar subpastas de {parent_id}: {e_sub}\n")

    # 2. Obter todos os ficheiros da conta via folder_id: -1 em chamada única
    req_files = urllib.request.Request(
        "https://api.1fichier.com/v1/file/ls.cgi",
        data=json.dumps({"folder_id": -1}).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "play-hospidy/1.0"}
    )
    try:
        with urllib.request.urlopen(req_files, timeout=20, context=ctx) as resp_f:
            f_data = json.loads(resp_f.read().decode("utf-8"))
            items = f_data.get("items", [])
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        if "Flood" in err_body or e.code == 403:
            sys.stderr.write(f"Aviso: 1fichier Flood/403 detectado ({err_body[:100]}). A manter cache existente.\n")
            if CATALOG_MEMORY_CACHE is not None:
                return CATALOG_MEMORY_CACHE
            if os.path.exists(CATALOG_CACHE_PATH):
                try:
                    with open(CATALOG_CACHE_PATH, "r", encoding="utf-8") as f:
                        CATALOG_MEMORY_CACHE = json.load(f)
                        return CATALOG_MEMORY_CACHE
                except Exception:
                    pass
        raise ValueError(f"Falha ao consultar ficheiros no 1fichier: {err_body or e}")
    except Exception as e:
        raise ValueError(f"Falha ao consultar ficheiros no 1fichier: {e}")

    LAST_SYNC_TIME = time.time()

    # 3. Estruturar ficheiros por pastas com codificação URL perfeita
    result_folders = {name: [] for name in master_folder_names}

    total_files = 0
    for item in items:
        raw_url = item.get("url", "")
        if not raw_url:
            continue
        file_id = raw_url.split("?")[-1].strip()
        filename = item.get("filename", "")
        size = item.get("size", 0)
        date_raw = item.get("date", "")
        date_str = date_raw.split()[0] if date_raw else ""

        # Codificação de segurança com urllib.parse.quote para acentos, parênteses e espaços
        safe_filename = urllib.parse.quote(filename)
        stream_url = f"https://play.hospidy.com/{file_id}/{safe_filename}"

        file_entry = {
            "filename": filename,
            "size": size,
            "date": date_str,
            "file_id": file_id,
            "stream_url": stream_url
        }

        fid = item.get("folder_id")
        target_name = folder_map.get(fid)

        # Classificação inteligente: ficheiros de áudio vão SEMPRE para Musica
        ext = os.path.splitext(filename)[1].lower()
        is_music = ext in {'.flac', '.mp3', '.m4a', '.aac', '.wav', '.ogg', '.opus', '.m3u', '.m3u8', '.alac'} or "_flac.zip" in filename.lower()

        if is_music:
            target_name = "Musica"
        elif target_name in master_folder_names:
            pass
        elif target_name == "dvds" or fid == 22685921 or target_name is None:
            target_name = "Filmes"

        if target_name not in result_folders:
            result_folders[target_name] = []
        result_folders[target_name].append(file_entry)
        total_files += 1

    # Enriquecimento com metadados para Kodi (.strm, .nfo, TMDb)
    try:
        from metadata_resolver import get_all_enriched_metadata
        metadata_cache = get_all_enriched_metadata(result_folders)
        for folder_name, f_list in result_folders.items():
            for f_entry in f_list:
                fname = f_entry.get("filename")
                if fname and fname in metadata_cache:
                    f_entry.update(metadata_cache[fname])
    except Exception as e_meta:
        sys.stderr.write(f"Aviso enriquecimento de metadados: {e_meta}\n")

    catalog_data = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_files": total_files,
        "folders": result_folders
    }

    # Gravação atómica em ficheiro local
    try:
        os.makedirs(os.path.dirname(os.path.abspath(CATALOG_CACHE_PATH)), exist_ok=True)
        temp_path = f"{CATALOG_CACHE_PATH}.tmp.{os.getpid()}"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(catalog_data, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, CATALOG_CACHE_PATH)
        try:
            CATALOG_DISK_MTIME = os.path.getmtime(CATALOG_CACHE_PATH)
        except OSError:
            pass
    except Exception as e:
        sys.stderr.write(f"Erro ao gravar {CATALOG_CACHE_PATH}: {e}\n")

    with CATALOG_LOCK:
        CATALOG_MEMORY_CACHE = catalog_data

    return catalog_data


def _trigger_background_sync_if_stale():
    """Verifica e atualiza em segundo plano se a cache tiver mais de 5 minutos, sem bloquear o utilizador."""
    global SYNC_IN_PROGRESS, LAST_SYNC_TIME
    now = time.time()
    if (now - LAST_SYNC_TIME > 1800) and not SYNC_IN_PROGRESS:
        SYNC_IN_PROGRESS = True
        def _bg():
            global SYNC_IN_PROGRESS
            try:
                sync_catalog(force=True)
            except Exception as e:
                sys.stderr.write(f"Aviso sync automático em background: {e}\n")
            finally:
                SYNC_IN_PROGRESS = False
        threading.Thread(target=_bg, daemon=True).start()


def get_catalog():
    """Devolve a cache do catálogo em memória ou lê do ficheiro (< 1ms). Recarrega se o ficheiro em disco mudou."""
    global CATALOG_MEMORY_CACHE, CATALOG_DISK_MTIME
    with CATALOG_LOCK:
        current_mtime = 0
        if os.path.exists(CATALOG_CACHE_PATH):
            try:
                current_mtime = os.path.getmtime(CATALOG_CACHE_PATH)
            except OSError:
                pass

        # Se a cache em memória é válida e o ficheiro em disco não mudou, devolve imediatamente
        if CATALOG_MEMORY_CACHE is not None and current_mtime <= CATALOG_DISK_MTIME:
            _trigger_background_sync_if_stale()
            return CATALOG_MEMORY_CACHE

        if os.path.exists(CATALOG_CACHE_PATH):
            try:
                with open(CATALOG_CACHE_PATH, "r", encoding="utf-8") as f:
                    CATALOG_MEMORY_CACHE = json.load(f)
                    CATALOG_DISK_MTIME = current_mtime
                    _trigger_background_sync_if_stale()
                    return CATALOG_MEMORY_CACHE
            except Exception as e:
                sys.stderr.write(f"Erro ao ler cache local de {CATALOG_CACHE_PATH}: {e}\n")

    # Se a cache ainda não existir, corre sync inicial
    try:
        return sync_catalog()
    except Exception as e:
        sys.stderr.write(f"Falha na sincronização inicial do catálogo: {e}\n")
        return {
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total_files": 0,
            "folders": {"Filmes": [], "Series": [], "Musica": [], "Kids": []}
        }


class TunnelHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        sys.stderr.write(f"[{self.log_date_time_string()}] {format % args}\n")

    def send_cors_headers(self):
        """Cabeçalhos CORS universais para permitir consumo no Kodi, telemóvel e navegadores."""
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS, HEAD')
        self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type, X-API-Key')
        self.send_header('Access-Control-Max-Age', '86400')

    def do_OPTIONS(self):
        """Responde a pedidos preflight CORS em todas as rotas."""
        self.send_response(204)
        self.send_cors_headers()
        self.end_headers()

    def do_HEAD(self):
        self.do_GET(head_only=True)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.strip('/')
        query = urllib.parse.parse_qs(parsed.query)

        # Endpoint: POST /sync ou /api/sync
        if path in ("sync", "api/sync"):
            auth_header = self.headers.get("Authorization", "")
            token = ""
            if auth_header.startswith("Bearer "):
                token = auth_header[7:].strip()
            elif "X-API-Key" in self.headers:
                token = self.headers["X-API-Key"].strip()
            elif "token" in query:
                token = query["token"][0].strip()
            elif "key" in query:
                token = query["key"][0].strip()

            configured_key = get_1fichier_token()

            # Validação estrita do token de segurança (requerido mesmo via túnel)
            if not (token and configured_key and token == configured_key):
                self.send_response(401)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Unauthorized - Chave de API inválida ou ausente"}).encode('utf-8'))
                return

            try:
                catalog = sync_catalog(configured_key, force=False)
                resp = {
                    "status": "OK",
                    "message": "Catálogo sincronizado com sucesso",
                    "updated_at": catalog.get("updated_at"),
                    "total_files": catalog.get("total_files"),
                    "folders": {k: len(v) for k, v in catalog.get("folders", {}).items()}
                }
                raw = json.dumps(resp, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(raw)))
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(raw)
            except Exception as e:
                err_resp = json.dumps({"status": "KO", "error": str(e)}).encode('utf-8')
                self.send_response(500)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(err_resp)))
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(err_resp)
            return

        self.send_response(404)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(b'{"error": "Endpoint not found"}')

    def do_GET(self, head_only=False):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.strip('/')
        query = urllib.parse.parse_qs(parsed.query)
        host = self.headers.get('Host', '').lower().split(':')[0]
        is_play_host = host.startswith("play.")

        # 0. Endpoint de Catálogo JSON: /api/catalog ou /catalog (Instantâneo a partir da cache)
        if path in ("api/catalog", "catalog", "api/catalog.json"):
            catalog = get_catalog()
            raw = json.dumps(catalog, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_cors_headers()
            self.end_headers()
            if not head_only:
                self.wfile.write(raw)
            return

        # 1. 1fichier Streaming para VLC/Kodi: play.hospidy.com/<id> ou /play/<id>
        if is_play_host or path.startswith("play/"):
            if is_play_host and not path:
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_cors_headers()
                self.end_headers()
                if head_only:
                    return
                html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Play Hospidy | VLC & Kodi 1fichier Streaming</title>
    <style>
        body { font-family: system-ui, -apple-system, sans-serif; max-width: 680px; margin: 40px auto; padding: 0 20px; background: #0b1120; color: #f8fafc; }
        .card { background: #1e293b; padding: 30px; border-radius: 16px; border: 1px solid #334155; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.5); }
        h1 { margin-top: 0; color: #f97316; display: flex; align-items: center; gap: 10px; font-size: 24px; }
        input[type=text] { width: 100%; box-sizing: border-box; padding: 14px; border-radius: 10px; border: 1px solid #475569; background: #0f172a; color: #fff; font-size: 15px; margin: 12px 0; outline: none; }
        button { background: #f97316; color: #fff; font-weight: 700; padding: 13px 22px; border: none; border-radius: 10px; cursor: pointer; font-size: 15px; }
        .res { margin-top: 6px; padding: 12px 14px; background: #0f172a; border-radius: 8px; border: 1px solid #334155; word-break: break-all; font-family: monospace; font-size: 14px; color: #fdba74; }
        .label { color: #94a3b8; font-size: 12px; text-transform: uppercase; margin-bottom: 4px; font-weight: 600; margin-top: 18px; }
        .btn-green { background: #22c55e; color: #0b1120; }
        .btn-blue { background: #38bdf8; color: #0b1120; text-decoration: none; display: inline-block; padding: 13px 22px; border-radius: 10px; font-weight: 700; }
        .catalog-badge { display: inline-block; margin-top: 20px; padding: 10px 16px; background: #0f172a; border-radius: 8px; border: 1px solid #334155; text-decoration: none; color: #38bdf8; font-weight: 600; font-size: 14px; }
    </style>
</head>
<body>
    <div class="card">
        <h1>🎬 Play Hospidy &rarr; VLC & Kodi Streaming</h1>
        <p style="color:#94a3b8">Cole o link do <b>1fichier</b> para gerar o link direto para o VLC, Kodi ou qualquer leitor multimédia.</p>
        <input type="text" id="inp" placeholder="https://1fichier.com/?vpuhjo8yurk0cvfwh0tf" value="https://1fichier.com/?vpuhjo8yurk0cvfwh0tf">
        <button onclick="genLink()">Gerar Link de Streaming</button>
        <div id="output" style="display:none; margin-top:20px;">
            <div class="label">Endereço de Rede para o Leitor</div>
            <div class="res" id="purl"></div>
            <div style="margin-top:12px; display:flex; gap:10px;">
                <button class="btn-green" onclick="copyVLC()">Copiar Link</button>
                <a id="vlcbtn" class="btn-blue" href="#">Abrir no VLC</a>
            </div>
            <p style="color:#94a3b8; font-size:13px; margin-top:15px;">No VLC: <b>Mídia &rarr; Abrir Fluxo de Rede (Ctrl+N)</b> e colar o link acima.</p>
        </div>
        <div style="text-align: center; margin-top: 25px; border-top: 1px solid #334155; padding-top: 20px;">
            <a href="/api/catalog" class="catalog-badge">📁 Ver Catálogo Geral JSON (/api/catalog)</a>
        </div>
    </div>
    <script>
        function genLink() {
            let val = document.getElementById('inp').value.trim();
            let m = val.match(/([a-zA-Z0-9_-]{15,25})/);
            if (!m) return alert('Link ou ID do 1fichier inválido');
            let playUrl = window.location.origin + '/' + m[1];
            document.getElementById('purl').innerText = playUrl;
            document.getElementById('vlcbtn').href = 'vlc://' + playUrl;
            document.getElementById('output').style.display = 'block';
        }
        function copyVLC() {
            navigator.clipboard.writeText(document.getElementById('purl').innerText);
            alert('Copiado! Basta colar no reprodutor.');
        }
    </script>
</body>
</html>"""
                self.wfile.write(html.encode())
                return

            clean_p = path[5:].strip('/') if path.startswith("play/") else path
            m_play = re.match(r'^([a-zA-Z0-9_-]{15,25})(?:/.*)?$', clean_p)
            if m_play:
                file_id = m_play.group(1)
                try:
                    stream_url = resolve_1fichier_download(file_id)
                    self.send_response(302)
                    self.send_header('Location', stream_url)
                    self.send_cors_headers()
                    self.end_headers()
                    return
                except Exception as e:
                    self.send_response(500)
                    self.send_header('Content-Type', 'text/plain; charset=utf-8')
                    self.send_cors_headers()
                    self.end_headers()
                    if not head_only:
                        self.wfile.write(f"Erro no streaming 1fichier: {str(e)}".encode())
                    return

        # 2. API: /api/resolve?url=...
        if path == "api/resolve":
            input_url = query.get("url", [""])[0] or query.get("xh", [""])[0]

            # Verificar se é link MEGA
            mega_fid, mega_key = parse_mega_url(input_url)
            if mega_fid and mega_key:
                try:
                    info = get_mega_file_info(mega_fid, mega_key)
                    host = self.headers.get('Host', 'hospidy.com')
                    proto = self.headers.get('X-Forwarded-Proto', 'https' if 'hospidy.com' in host else 'http')
                    clean_name = normalize_filename(info['filename'])
                    tunnel_url = f"{proto}://{host}/mega/{mega_fid}/{mega_key}/{urllib.parse.quote(clean_name)}"
                    data = {
                        "type": "mega",
                        "filename": clean_name,
                        "original_filename": info["filename"],
                        "size": info["size"],
                        "file_id": mega_fid,
                        "direct_url": tunnel_url,
                        "tunnel_url": tunnel_url
                    }
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_cors_headers()
                    self.end_headers()
                    self.wfile.write(json.dumps(data, ensure_ascii=False).encode())
                except Exception as e:
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.send_cors_headers()
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": str(e)}).encode())
                return

            m = re.search(r'([a-zA-Z0-9_-]{10,20})', input_url)
            if not m:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"error": "ID ou URL inválido"}).encode())
                return
            try:
                data = resolve_transfer(m.group(1))
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
            return

        # 3. Redirect: /t/<xh> or /tunel/<xh>/<filename> (Instant 302 Redirect)
        m_redirect = re.match(r'^(t|r|tunel|tunnel)/([a-zA-Z0-9_-]+)(?:/(.*))?$', path)
        if m_redirect:
            prefix = m_redirect.group(1)
            xh = m_redirect.group(2)
            has_filename = bool(m_redirect.group(3))
            try:
                data = resolve_transfer(xh)
                if not has_filename:
                    host = self.headers.get('Host', 'hospidy.com')
                    proto = self.headers.get('X-Forwarded-Proto', 'https' if 'hospidy.com' in host else 'http')
                    target = f"{proto}://{host}/{prefix}/{xh}/{urllib.parse.quote(data['filename'])}"
                    self.send_response(302)
                    self.send_header('Location', target)
                    self.send_header('Content-Disposition', f'attachment; filename="{data["filename"]}"')
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_cors_headers()
                    self.end_headers()
                    return

                self.send_response(302)
                self.send_header('Location', data["direct_url"])
                self.send_header('Content-Disposition', f'attachment; filename="{data["filename"]}"')
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_cors_headers()
                self.end_headers()
                return
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(f"Erro: {str(e)}".encode())
                return

        # 4. Stream Tunnel: /s/<xh> (Streams bytes through Pi if client doesn't follow 302)
        m_stream = re.match(r'^(s|stream)/([a-zA-Z0-9_-]+)(?:/(.*))?$', path)
        if m_stream:
            xh = m_stream.group(2)
            try:
                data = resolve_transfer(xh)
                req = urllib.request.Request(data["direct_url"], headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as remote:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_header('Content-Length', str(data['size']))
                    self.send_header('Content-Disposition', f'attachment; filename="{data["filename"]}"')
                    self.send_cors_headers()
                    self.end_headers()
                    if head_only:
                        return
                    while True:
                        chunk = remote.read(128 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                return
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_cors_headers()
                self.end_headers()
                if not head_only:
                    self.wfile.write(f"Erro no stream: {str(e)}".encode())
                return

        # 5. MEGA Direct Decryption Stream Tunnel: /mega/<file_id>/<key_str>[/<filename>]
        m_mega = re.match(r'^mega/([a-zA-Z0-9_-]{8,12})/([a-zA-Z0-9_-]{20,50})(?:/(.*))?$', path)
        if m_mega:
            fid = m_mega.group(1)
            key_str = m_mega.group(2)
            fname_req = m_mega.group(3)
            try:
                info = get_mega_file_info(fid, key_str)
                if not fname_req:
                    host = self.headers.get('Host', 'hospidy.com')
                    proto = self.headers.get('X-Forwarded-Proto', 'https' if 'hospidy.com' in host else 'http')
                    clean_name = normalize_filename(info['filename'])
                    target = f"{proto}://{host}/mega/{fid}/{key_str}/{urllib.parse.quote(clean_name)}"
                    self.send_response(302)
                    self.send_header('Location', target)
                    self.send_header('Content-Disposition', f'attachment; filename="{clean_name}"')
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_cors_headers()
                    self.end_headers()
                    return

                file_size = info['size']
                range_header = self.headers.get('Range')

                start = 0
                end = file_size - 1

                if range_header:
                    rm = re.match(r'bytes=(\d+)-(\d*)', range_header)
                    if rm:
                        start = int(rm.group(1))
                        if rm.group(2):
                            end = int(rm.group(2))

                if start >= file_size:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{file_size}')
                    self.send_cors_headers()
                    self.end_headers()
                    return

                end = min(end, file_size - 1)
                bytes_to_send = end - start + 1

                # Alinhamento obrigatório de 16 bytes para AES-CTR
                block_start = (start // 16) * 16
                discard_start = start - block_start
                block_end = ((end + 15) // 16) * 16

                lower_name = info['filename'].lower()
                if lower_name.endswith('.mkv'):
                    content_type = 'video/x-matroska'
                elif lower_name.endswith('.mp4'):
                    content_type = 'video/mp4'
                elif lower_name.endswith('.iso'):
                    content_type = 'application/x-iso9660-image'
                elif lower_name.endswith('.flac'):
                    content_type = 'audio/flac'
                else:
                    content_type = 'application/octet-stream'

                if range_header:
                    self.send_response(206)
                    self.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')
                else:
                    self.send_response(200)

                disp_filename = normalize_filename(urllib.parse.unquote(fname_req)) if fname_req else normalize_filename(info["filename"])
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(bytes_to_send))
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Content-Disposition', f'attachment; filename="{disp_filename}"')
                self.send_cors_headers()
                self.end_headers()

                if head_only:
                    return

                if range_header:
                    mega_url = f"{info['dl_url']}/{block_start}-{block_end - 1}"
                else:
                    mega_url = info['dl_url']

                try:
                    req = urllib.request.Request(mega_url, headers={"User-Agent": "Mozilla/5.0"})
                    remote_stream = urllib.request.urlopen(req, timeout=30)
                except urllib.error.HTTPError as e:
                    if e.code in (401, 403):
                        # Token expirou no CDN da MEGA; forçar renovação imediata
                        info = get_mega_file_info(fid, key_str, force_refresh=True)
                        mega_url = f"{info['dl_url']}/{block_start}-{block_end - 1}" if range_header else info['dl_url']
                        req = urllib.request.Request(mega_url, headers={"User-Agent": "Mozilla/5.0"})
                        remote_stream = urllib.request.urlopen(req, timeout=30)
                    else:
                        raise

                with remote_stream:
                    iv_int = (info['iv_upper'] << 64) + (block_start // 16)
                    if HAVE_CRYPTOGRAPHY:
                        iv_bytes = iv_int.to_bytes(16, byteorder='big')
                        cipher = Cipher(algorithms.AES(info['aes_key']), modes.CTR(iv_bytes))
                        decryptor = cipher.decryptor()
                        def decrypt_chunk(c):
                            return decryptor.update(c)
                    elif HAVE_CRYPTODOME:
                        ctr = Counter.new(128, initial_value=iv_int)
                        cipher = AES.new(info['aes_key'], AES.MODE_CTR, counter=ctr)
                        def decrypt_chunk(c):
                            return cipher.decrypt(c)
                    else:
                        raise RuntimeError("Nenhuma biblioteca de criptografia disponível")

                    sent = 0
                    is_first = True
                    buf_size = 128 * 1024

                    while sent < bytes_to_send:
                        to_read = min(buf_size, bytes_to_send - sent + (discard_start if is_first else 0))
                        chunk = remote_stream.read(to_read)
                        if not chunk:
                            break

                        plain = decrypt_chunk(chunk)
                        if is_first:
                            plain = plain[discard_start:]
                            is_first = False

                        to_send = plain[:bytes_to_send - sent]
                        try:
                            self.wfile.write(to_send)
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            break

                        sent += len(to_send)
                return
            except Exception as e:
                import traceback
                print(f"[MEGA ERROR] Exception in mega stream: {e}", file=sys.stderr, flush=True)
                traceback.print_exc(file=sys.stderr)
                sys.stderr.flush()
                try:
                    self.send_response(500)
                    self.send_header('Content-Type', 'text/plain; charset=utf-8')
                    self.send_cors_headers()
                    self.end_headers()
                    if not head_only:
                        self.wfile.write(f"Erro no túnel MEGA: {str(e)}".encode())
                except Exception:
                    pass
                return

        # 6. Web UI na raiz (/) ou /tunel
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_cors_headers()
        self.end_headers()
        if head_only:
            return
        html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Hospidy | Transfer.it & MEGA -> 1fichier Tunnel</title>
    <style>
        body { font-family: system-ui, -apple-system, sans-serif; max-width: 680px; margin: 40px auto; padding: 0 20px; background: #0b1120; color: #f8fafc; }
        .card { background: #1e293b; padding: 30px; border-radius: 16px; border: 1px solid #334155; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.5); }
        h1 { margin-top: 0; font-size: 24px; color: #38bdf8; display: flex; align-items: center; gap: 10px; }
        input[type=text] { width: 100%; box-sizing: border-box; padding: 14px; border-radius: 10px; border: 1px solid #475569; background: #0f172a; color: #fff; font-size: 15px; margin: 12px 0; outline: none; }
        input[type=text]:focus { border-color: #38bdf8; }
        button { background: #38bdf8; color: #0b1120; font-weight: 700; padding: 13px 22px; border: none; border-radius: 10px; cursor: pointer; font-size: 15px; transition: 0.2s; }
        button:hover { background: #7dd3fc; }
        .res { margin-top: 6px; padding: 12px 14px; background: #0f172a; border-radius: 8px; border: 1px solid #334155; word-break: break-all; font-family: monospace; font-size: 13px; color: #e2e8f0; }
        .label { color: #94a3b8; font-size: 12px; text-transform: uppercase; margin-bottom: 4px; font-weight: 600; margin-top: 18px; }
        .success-box { padding: 12px; background: #14532d; border-radius: 8px; color: #86efac; margin-top: 15px; display: none; font-weight: 600; text-align: center; }
        .tip { font-size: 13px; color: #94a3b8; margin-top: 8px; }
        .cat-link { display: inline-block; margin-top: 20px; color: #38bdf8; font-size: 14px; text-decoration: none; font-weight: 600; }
    </style>
</head>
<body>
    <div class="card">
        <h1>⚡ Transfer.it & MEGA &rarr; 1fichier Tunnel</h1>
        <p style="color:#94a3b8; line-height: 1.5;">Cole o link do <b>Transfer.it</b> ou da <b>MEGA</b> para gerar o link de streaming direto ou use o URL de túnel automático para o <b>1fichier Remote Upload</b>.</p>
        <input type="text" id="inp" placeholder="https://mega.nz/file/QTcyFC5B#... ou https://transfer.it/t/..." value="https://mega.nz/file/QTcyFC5B#kwVIGmkvCdBH-SVeP5N-fKTYcTKcxU_KmFVsEi6-pKM">
        <button id="btn" onclick="resolveLink()">Resolver Link</button>

        <div id="output" style="display:none;">
            <div class="label">Ficheiro Detetado</div>
            <div id="fname" style="font-size:18px; font-weight:bold; color:#38bdf8;"></div>

            <div class="label">1. URL Túnel Direto (Desencriptação on-the-fly / Streaming)</div>
            <div class="res" id="turl"></div>
            <button style="margin-top:8px; background:#a855f7; color:#fff;" onclick="copyTunnel()">Copiar URL Túnel</button>
            <div class="tip">Cole este link no Remote Upload do 1fichier ou diretamente no VLC/Kodi!</div>

            <div class="label">2. Link Direto / Download</div>
            <div class="res" id="durl"></div>
            <button style="margin-top:8px; background:#22c55e; color:#0b1120;" onclick="copyDirect()">Copiar Link Direto</button>
            <div class="tip">Link direto compatível com reprodução sem intermediários.</div>

            <div class="success-box" id="msg">Link copiado com sucesso!</div>
        </div>
        <div style="text-align: center; margin-top: 25px; border-top: 1px solid #334155; padding-top: 20px;">
            <a href="/api/catalog" class="cat-link">📁 Aceder ao Catálogo JSON Completo (/api/catalog)</a>
        </div>
    </div>
    <script>
        let currentDirect = '';
        let currentTunnel = '';
        async function resolveLink() {
            const val = document.getElementById('inp').value.trim();
            const btn = document.getElementById('btn');
            btn.innerText = 'A resolver na nuvem...';
            try {
                const res = await fetch('/api/resolve?url=' + encodeURIComponent(val));
                const data = await res.json();
                btn.innerText = 'Resolver Link';
                if (data.error) return alert(data.error);
                let szMb = (data.size / 1024 / 1024);
                let szStr = szMb > 1024 ? (szMb / 1024).toFixed(2) + ' GB' : szMb.toFixed(2) + ' MB';
                document.getElementById('fname').innerText = data.filename + ' (' + szStr + ')';
                currentDirect = data.direct_url;
                currentTunnel = data.tunnel_url || (window.location.origin + '/tunel/' + data.handle + '/' + encodeURIComponent(data.filename));
                document.getElementById('durl').innerText = currentDirect;
                document.getElementById('turl').innerText = currentTunnel;
                document.getElementById('output').style.display = 'block';
            } catch(e) {
                btn.innerText = 'Resolver Link';
                alert('Erro: ' + e);
            }
        }
        function copyDirect() {
            navigator.clipboard.writeText(currentDirect);
            showNotice();
        }
        function copyTunnel() {
            navigator.clipboard.writeText(currentTunnel);
            showNotice();
        }
        function showNotice() {
            const msg = document.getElementById('msg');
            msg.style.display = 'block';
            setTimeout(() => { msg.style.display = 'none'; }, 2500);
        }
    </script>
</body>
</html>"""
        self.wfile.write(html.encode())


class ReusableServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == '__main__':
    # Pré-carregar ou sincronizar catálogo no arranque
    print(f"A verificar catálogo persistente em {CATALOG_CACHE_PATH}...")
    try:
        cat = get_catalog()
        print(f"Catálogo carregado com sucesso: {cat.get('total_files', 0)} ficheiros.")
    except Exception as e:
        print(f"Aviso no arranque ao ler catálogo: {e}")

    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else PORT
    with ReusableServer(("", port), TunnelHandler) as httpd:
        print(f"Servidor iniciado na porta {port}")
        httpd.serve_forever()
