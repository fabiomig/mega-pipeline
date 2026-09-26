#!/usr/bin/env python3
"""
mega_multipart_transfer.py - Túnel MEGA -> 1fichier para ARQUIVOS MULTIPARTE com PASSWORD.

Cenário: um conjunto de links MEGA que são partes de um arquivo RAR dividido
(ex: Nome.part01.rar ... Nome.part10.rar) protegido por password. O túnel:
  1. Agrupa as partes pelo nome base (detecta .partNN.rar e .rar/.r00/.r01).
  2. Descarrega + desencripta (AES-128-CTR) cada parte da MEGA.
  3. Extrai o arquivo com a password (7z / unar / unrar / bsdtar).
  4. Envia APENAS o ficheiro de imagem (.iso / .img) para o 1fichier.
  5. Ativa inline: 1 e limpa o disco temporário.

Auto-contido: não depende de outros ficheiros. Instala crypto/RAR automaticamente
quando corre como root (Google Colab) ou usa GitHub Actions (ubuntu).

Uso:
  # Google Colab (instala ferramentas automaticamente se for root)
  !python3 mega_multipart_transfer.py

  # Links via argumento
  python3 mega_multipart_transfer.py "https://mega...#!id!key" "https://mega...#!id!key"

  # Variáveis de ambiente
  MEGA_LINKS="link1\nlink2"  FOLDER_ID="22726720" \
  ARCHIVE_PASSWORDS="goldtuganime.biz,goldtuganime.com" \
  FICHIER_API_KEY="..." python3 mega_multipart_transfer.py

  # Só inspecionar (agrupa e mostra nomes/tamanhos, não descarrega)
  python3 mega_multipart_transfer.py --dry-run
"""

import os
import re
import sys
import json
import time
import base64
import socket
import shutil
import subprocess
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path


# --------------------------------------------------------------------------
# Bootstrap de criptografia (instala pycryptodomex se não houver nada)
# --------------------------------------------------------------------------
def _load_crypto():
    global HAVE_CRYPTOGRAPHY, HAVE_CRYPTODOME, Cipher, algorithms, modes, AES, Counter
    HAVE_CRYPTOGRAPHY = HAVE_CRYPTODOME = False
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher as _C, algorithms as _A, modes as _M
        Cipher, algorithms, modes = _C, _A, _M
        HAVE_CRYPTOGRAPHY = True
    except ImportError:
        pass
    try:
        from Cryptodome.Cipher import AES as _AES
        from Cryptodome.Util import Counter as _Ctr
        AES, Counter = _AES, _Ctr
        HAVE_CRYPTODOME = True
    except ImportError:
        pass
    if not (HAVE_CRYPTOGRAPHY or HAVE_CRYPTODOME):
        print("⚙️  A instalar biblioteca de criptografia (pycryptodomex)...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pycryptodomex"], check=False)
        from Cryptodome.Cipher import AES as _AES
        from Cryptodome.Util import Counter as _Ctr
        AES, Counter = _AES, _Ctr
        HAVE_CRYPTODOME = True


_load_crypto()


# --------------------------------------------------------------------------
# Helpers MEGA (AES-128-CTR) + 1fichier  [auto-contidos]
# --------------------------------------------------------------------------
def get_active_proxy():
    for name, port in (("warp", 40000), ("tor", 9050)):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            if s.connect_ex(('127.0.0.1', port)) == 0:
                s.close()
                return (name, port)
            s.close()
        except Exception:
            pass
    return None


def rotate_proxy(proxy_type: str, port: int):
    print(f"🔄 A renovar IP do {proxy_type.upper()}...")
    if proxy_type == "warp":
        for c in ("disconnect", "registration delete", "registration new", "mode proxy",
                  "proxy port 40000", "connect"):
            subprocess.run(f"warp-cli --accept-tos {c}", shell=True, capture_output=True)
    elif proxy_type == "tor":
        subprocess.run("service tor restart || killall -HUP tor", shell=True, capture_output=True)
    time.sleep(3)


def b64url(s: str) -> bytes:
    s = s.replace('-', '+').replace('_', '/')
    while len(s) % 4 != 0:
        s += '='
    return base64.b64decode(s)


def decrypt_cbc(aes_key: bytes, data: bytes) -> bytes:
    if HAVE_CRYPTOGRAPHY:
        dec = Cipher(algorithms.AES(aes_key), modes.CBC(b'\x00' * 16)).decryptor()
        return dec.update(data) + dec.finalize()
    if HAVE_CRYPTODOME:
        return AES.new(aes_key, AES.MODE_CBC, iv=b'\x00' * 16).decrypt(data)
    raise RuntimeError("Requer 'cryptography' ou 'pycryptodome'")


def normalize_filename(filename: str) -> str:
    """Limpa tags redundantes, grupos e checksums de ficheiros de anime/séries."""
    stem, ext = os.path.splitext(filename)
    clean = re.sub(r'\[[a-fA-F0-9]{8}\]', '', stem)
    clean = re.sub(r'\[[^\]]+\]', '', clean)
    clean = re.sub(r'\((?:1080p|720p|480p|2160p|4k)\)', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\s{2,}', ' ', clean).strip()
    clean = re.sub(r'\s*-\s*$', '', clean)
    return f"{clean}{ext}" if clean else filename


def parse_mega_url(url_or_str: str):
    clean = url_or_str.strip()
    m = re.search(r'([a-zA-Z0-9_-]{8,12})[#!,/]([a-zA-Z0-9_-]{20,50})', clean)
    if m:
        return m.group(1), m.group(2)
    return "", ""


def get_mega_info(file_id: str, key_str: str, proxy_info=None) -> dict:
    raw_key = b64url(key_str)
    aes_key = bytes(raw_key[i] ^ raw_key[i + 16] for i in range(16))
    iv_upper = int.from_bytes(raw_key[16:24], byteorder='big')

    data_json = json.dumps([{'a': 'g', 'g': 1, 'p': file_id, 'ssl': 1}])
    if proxy_info:
        cmd = ["curl", "-s", "--socks5-hostname", f"127.0.0.1:{proxy_info[1]}",
               "-H", "Content-Type: application/json", "-H", "User-Agent: Mozilla/5.0",
               "-d", data_json, "https://g.api.mega.co.nz/cs?id=0"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        res_list = json.loads(proc.stdout)
    else:
        req = urllib.request.Request('https://g.api.mega.co.nz/cs?id=0',
                                     data=data_json.encode('utf-8'),
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            res_list = json.loads(resp.read().decode('utf-8'))

    if not res_list or not isinstance(res_list, list):
        raise ValueError(f"Resposta inválida da MEGA API: {res_list}")
    res = res_list[0]
    if isinstance(res, int) or 'e' in res:
        err = res if isinstance(res, int) else res.get('e')
        raise ValueError(f"Ficheiro MEGA indisponível ou removido (código {err})")
    if 'g' not in res:
        raise ValueError(f"Link de download não retornado pela MEGA: {res}")

    dl_url = res['g']
    size = res.get('s', 0)
    at_bytes = b64url(res.get('at', ''))
    try:
        attr_dec = decrypt_cbc(aes_key, at_bytes)
        m = re.search(r'"n":"(.*?)"', attr_dec.decode('utf-8', errors='ignore'))
        orig_filename = m.group(1) if m else f"{file_id}.bin"
    except Exception:
        orig_filename = f"{file_id}.bin"

    return {
        'file_id': file_id, 'key_str': key_str, 'aes_key': aes_key, 'iv_upper': iv_upper,
        'dl_url': dl_url, 'size': size,
        'filename': normalize_filename(orig_filename), 'orig_filename': orig_filename,
    }


def download_mega_raw(info: dict, enc_path: str, proxy_info=None, max_retries: int = 5) -> bool:
    curr_proxy = proxy_info
    for attempt in range(1, max_retries + 1):
        cmd = ["curl", "-L", "-s", "-S", "--fail", "--connect-timeout", "20", "--retry", "2", "-o", enc_path]
        if curr_proxy:
            cmd.extend(["--socks5-hostname", f"127.0.0.1:{curr_proxy[1]}"])
        cmd.append(info['dl_url'])
        label = f" [via {curr_proxy[0].upper()}]" if curr_proxy else ""
        print(f"📥 A descarregar da MEGA{label} ({info['size']/(1024*1024):.1f} MB)...")
        start_t = time.time()
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode == 0 and os.path.exists(enc_path) and os.path.getsize(enc_path) > 0:
            elapsed = time.time() - start_t
            spd = (os.path.getsize(enc_path) / (1024 * 1024)) / (elapsed if elapsed > 0 else 0.01)
            print(f"✅ Download MEGA concluído em {elapsed:.1f}s ({spd:.1f} MB/s)")
            return True
        err_out = p.stderr.lower()
        if "509" in err_out or "bandwidth" in err_out or "limit" in err_out:
            print(f"⚠️  MEGA Bandwidth Limit (509) na tentativa {attempt}/{max_retries}!")
            if curr_proxy:
                rotate_proxy(curr_proxy[0], curr_proxy[1]); time.sleep(3); continue
            curr_proxy = get_active_proxy()
            if curr_proxy:
                rotate_proxy(curr_proxy[0], curr_proxy[1]); continue
            time.sleep(10)
        else:
            print(f"⚠️  Aviso curl ({p.returncode}): {p.stderr.strip()[:150]}")
            if curr_proxy:
                rotate_proxy(curr_proxy[0], curr_proxy[1])
            time.sleep(4)
    return False


def decrypt_file_ctr(enc_path: str, dec_path: str, aes_key: bytes, iv_upper: int):
    print("🔓 A desencriptar AES-128-CTR...")
    start_t = time.time()
    iv_int = iv_upper << 64
    if HAVE_CRYPTOGRAPHY:
        cipher = Cipher(algorithms.AES(aes_key), modes.CTR(iv_int.to_bytes(16, byteorder='big')))
        decryptor = cipher.decryptor()
        def dec(c): return decryptor.update(c)
    elif HAVE_CRYPTODOME:
        cipher = AES.new(aes_key, AES.MODE_CTR, counter=Counter.new(128, initial_value=iv_int))
        def dec(c): return cipher.decrypt(c)
    else:
        raise RuntimeError("Sem biblioteca de criptografia")
    with open(enc_path, "rb") as fin, open(dec_path, "wb") as fout:
        while True:
            chunk = fin.read(512 * 1024)
            if not chunk:
                break
            fout.write(dec(chunk))
    print(f"✅ Desencriptado com sucesso em {time.time() - start_t:.2f}s!")


def get_1fichier_upload_node(api_key: str, retries: int = 5):
    url = "https://api.1fichier.com/v1/upload/get_upload_server.cgi"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}",
               "User-Agent": "CloudMegaTransfer/1.0"}
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=b"{}", headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if "url" in data and "id" in data:
                    return data["url"], data["id"]
                if "message" in data and "flood" in str(data["message"]).lower():
                    print(f"⚠️  Flood na 1fichier, a aguardar 60s ({attempt}/{retries})...")
                    time.sleep(60); continue
        except urllib.error.HTTPError as e:
            if e.code in (429, 403) or "flood" in str(e).lower():
                print(f"⚠️  HTTP {e.code} / Flood na 1fichier, a aguardar 60s...")
                time.sleep(60); continue
            time.sleep(5)
    raise RuntimeError("Falha ao obter servidor de upload no 1fichier")


def upload_to_1fichier(file_path: str, filename: str, api_key: str, did: int) -> dict:
    file_size = os.path.getsize(file_path)
    print("📡 A alocar nó dedicado 1fichier...")
    node_host, upload_id = get_1fichier_upload_node(api_key)
    print(f"🚀 A enviar '{filename}' ({file_size/(1024*1024):.1f} MB) para {node_host}...")
    start_time = time.time()
    upload_url = f"https://{node_host}/upload.cgi?id={upload_id}"
    cmd = ["curl", "-s", "-S", "--tcp-nodelay",
           "-H", f"Authorization: Bearer {api_key}",
           "-F", f"file[]=@{file_path};filename={filename}"]
    if did > 0:
        cmd.extend(["-F", f"did={did}"])
    cmd.append(upload_url)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Erro no curl upload: {proc.stderr}")
    elapsed = time.time() - start_time
    print(f"✅ Upload 1fichier concluído em {elapsed:.1f}s ({(file_size/(1024*1024))/(elapsed or 0.01):.1f} MB/s)")
    time.sleep(2)
    req_end = urllib.request.Request(f"https://{node_host}/end.pl?xid={upload_id}",
                                     headers={"Authorization": f"Bearer {api_key}", "JSON": "1",
                                              "User-Agent": "CloudMegaTransfer/1.0"})
    with urllib.request.urlopen(req_end, timeout=60) as resp:
        raw_report = resp.read().decode('utf-8', errors='ignore')
    report_json = json.loads(raw_report)
    if report_json.get("links"):
        return report_json["links"][0]
    raise RuntimeError(f"1fichier não devolveu links válidos: {raw_report}")


def set_1fichier_inline(api_key: str, download_url: str):
    m = re.search(r'\?([a-zA-Z0-9]+)', download_url)
    if not m:
        return
    file_id = m.group(1)
    payload = json.dumps({"urls": [download_url], "inline": 1}).encode('utf-8')
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}",
               "User-Agent": "CloudMegaTransfer/1.0"}
    try:
        req = urllib.request.Request("https://api.1fichier.com/v1/file/chattr.cgi",
                                     data=payload, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            print(f"🎬 Inline ativado para {file_id}: {data.get('status', 'OK')}")
    except Exception:
        pass


def get_existing_1fichier_files(api_key: str, folder_id: int) -> set:
    try:
        payload = json.dumps({"folder_id": folder_id}).encode('utf-8')
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}",
                   "User-Agent": "CloudMegaTransfer/1.0"}
        req = urllib.request.Request("https://api.1fichier.com/v1/file/ls.cgi",
                                     data=payload, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return {item.get("filename", "") for item in data.get("items", [])}
    except Exception as e:
        print(f"⚠️  Aviso ao listar ficheiros existentes: {e}")
        return set()


# Formato novo: Nome.part01.rar / Nome.part1.rar
PART_NEW_RE = re.compile(r'^(?P<base>.+?)\.part(?P<num>\d+)\.rar$', re.IGNORECASE)
# Formato antigo: Nome.rar (primeira) / Nome.r00 / Nome.r01 ...
PART_OLD_RE = re.compile(r'^(?P<base>.+?)\.(?P<ext>rar|r\d{2})$', re.IGNORECASE)

IMAGE_EXTS = ('.iso', '.img')
DEFAULT_PASSWORDS = ['goldtuganime.biz', 'goldtuganime.com']


# --------------------------------------------------------------------------
# Ferramentas de extração
# --------------------------------------------------------------------------
RAR_NATIVE = ('unar', 'unrar', 'unrar-free')  # suportam RAR nativamente
SEVENZIP = ('7z', '7za', '7zz')              # precisam do codec p7zip-rar


def tool_supports_rar(tool: str) -> bool:
    """True se a ferramenta consegue mesmo abrir RAR (não apenas existir)."""
    if tool in RAR_NATIVE:
        return True
    if tool in SEVENZIP:
        # O 7z só lê RAR se o codec (p7zip-rar) estiver instalado.
        try:
            p = subprocess.run([tool, 'i'], capture_output=True, text=True, timeout=15)
            return 'rar' in (p.stdout + p.stderr).lower()
        except Exception:
            return False
    return False  # bsdtar: suporte RAR varia; só entra como último recurso


def detect_tools() -> list:
    """Ferramentas de extração disponíveis, por prioridade (RAR-capazes primeiro)."""
    present = [t for t in ('7z', '7za', '7zz', 'unar', 'unrar', 'unrar-free', 'bsdtar')
               if shutil.which(t)]
    rar_ok = [t for t in present if tool_supports_rar(t)]
    others = [t for t in present if t not in rar_ok]
    return rar_ok + others  # ex.: bsdtar fica como fallback final


def has_rar_tool(tools: list) -> bool:
    return any(tool_supports_rar(t) for t in tools)


def _install_rar_tools():
    print("⚙️  A instalar suporte RAR (p7zip-rar/unar/unrar-free)...")
    cmds = [
        "apt-get update -qq",
        "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq p7zip-full p7zip-rar unar unrar-free",
    ]
    for c in cmds:
        try:
            subprocess.run(c, shell=True, capture_output=True, timeout=600)
        except Exception as e:
            print(f"   aviso: {e}")


def ensure_extractor() -> list:
    """Garante um extrator que suporte RAR; em root (Colab) instala o que faltar."""
    tools = detect_tools()
    if has_rar_tool(tools):
        return tools

    # 7z existe mas sem codec RAR (típico no Colab), ou não há nada -> instalar.
    if os.geteuid() == 0:
        _install_rar_tools()
        tools = detect_tools()

    if not has_rar_tool(tools):
        print("❌ Nenhuma ferramenta com suporte RAR (7z+p7zip-rar / unar / unrar).")
        print("   Colab/Debian: !apt-get update && apt-get install -y p7zip-full p7zip-rar unar")
        print("   macOS:        brew install sevenzip unar")
        sys.exit(1)

    return tools


def _extract_cmd(tool: str, archive: str, out_dir: str, password: str) -> list:
    if tool in ('7z', '7za', '7zz'):
        return [tool, 'x', f'-p{password}', f'-o{out_dir}', '-y', archive]
    if tool == 'unar':
        return [tool, '-p', password, '-o', out_dir, '-f', archive]
    if tool in ('unrar', 'unrar-free'):
        return [tool, 'x', f'-p{password}', '-o+', '-y', archive, out_dir + os.sep]
    if tool == 'bsdtar':
        return [tool, '-x', '-f', archive, '-C', out_dir, '--passphrase', password]
    raise ValueError(f"Ferramenta desconhecida: {tool}")


def find_images(directory: Path) -> list:
    """Encontra ficheiros de imagem (.iso/.img) válidos (>1 MB) dentro de directory."""
    if not directory.exists():
        return []
    out = []
    for p in directory.rglob('*'):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS and p.stat().st_size > 1024 * 1024:
            out.append(p)
    return sorted(out)


def extract_iso(first_part: Path, out_dir: Path, passwords: list, tools: list):
    """Tenta cada (ferramenta, password) até obter uma imagem válida.

    A ferramenta é o ciclo externo: assim que uma consegue abrir o arquivo,
    testamos as passwords nessa mesma ferramenta (evita repetir extrações
    grandes com a password errada em várias ferramentas).
    """
    for tool in tools:
        for pw in passwords:
            shutil.rmtree(out_dir, ignore_errors=True)
            out_dir.mkdir(parents=True, exist_ok=True)
            cmd = _extract_cmd(tool, str(first_part), str(out_dir), pw)
            print(f"🔓 A extrair com {tool} (password: {pw[:4]}***)...")
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True)
            except Exception as e:
                print(f"   falha ao executar {tool}: {e}")
                continue
            images = find_images(out_dir)
            if images:
                print(f"✅ Extraído com {tool}: {', '.join(i.name for i in images)}")
                return images, pw, tool
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            reason = tail[-1][:160] if tail else f"rc={proc.returncode}"
            print(f"   sem imagem ({reason})")
    return [], None, None


# --------------------------------------------------------------------------
# Agrupamento de partes
# --------------------------------------------------------------------------
def classify_part(filename: str):
    """Devolve (base, part_index, kind) ou (None, None, None) se não for parte."""
    m = PART_NEW_RE.match(filename)
    if m:
        return m.group('base'), int(m.group('num')), 'new'
    m = PART_OLD_RE.match(filename)
    if m:
        base = m.group('base')
        ext = m.group('ext').lower()
        if ext == 'rar':
            return base, 0, 'old'
        return base, int(ext[1:]) + 1, 'old'
    return None, None, None


def build_groups(probed: list) -> dict:
    """Agrupa partes por nome base -> {'base','kind','parts':[(idx, item)]}."""
    groups = {}
    for item in probed:
        base, idx, kind = classify_part(item['orig_filename'])
        if base is None:
            # Não é parte de arquivo: trata como ficheiro único (base = próprio nome).
            base, idx, kind = os.path.splitext(item['orig_filename'])[0], 0, 'single'
        g = groups.setdefault(base, {'base': base, 'kind': kind, 'parts': []})
        g['parts'].append((idx, item))
    for g in groups.values():
        g['parts'].sort(key=lambda t: t[0])
    return groups


# --------------------------------------------------------------------------
# Configuração / API key
# --------------------------------------------------------------------------
def load_api_key() -> str:
    api_key = os.environ.get("FICHIER_API_KEY", "").strip()
    if api_key:
        return api_key
    cfg = os.path.expanduser("~/.1fichier_token")
    if os.path.exists(cfg):
        with open(cfg) as f:
            c = f.read().strip()
            try:
                return json.loads(c).get("api_key", "")
            except Exception:
                return c
    return ""


def load_passwords() -> list:
    raw = os.environ.get("ARCHIVE_PASSWORDS", "")
    if raw.strip():
        return [p.strip() for p in re.split(r'[,\n]', raw) if p.strip()]
    return list(DEFAULT_PASSWORDS)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    dry_run = '--dry-run' in sys.argv or '--resolve-only' in sys.argv

    links_raw = os.environ.get("MEGA_LINKS", "")
    if args:
        links_raw = "\n".join(args)
    links = [l.strip() for l in links_raw.splitlines()
             if l.strip() and not l.strip().startswith('#')]

    if not links:
        print("ℹ️  Nenhum link MEGA fornecido.")
        return

    api_key = load_api_key()
    if not api_key and not dry_run:
        print("❌ FICHIER_API_KEY não configurada!")
        sys.exit(1)

    folder_id = int(os.environ.get("FOLDER_ID", "22726720"))
    passwords = load_passwords()

    print("🧩 Túnel MEGA multipart -> 1fichier (só ISO)")
    print(f"🔑 Passwords: {', '.join(p[:4] + '***' for p in passwords)}")
    print(f"📁 Pasta 1fichier: {folder_id}")

    proxy_info = get_active_proxy()
    if proxy_info:
        print(f"🛡️  Proxy {proxy_info[0].upper()} ativo em 127.0.0.1:{proxy_info[1]}")

    # 1. Inspecionar metadados de todos os links (barato, sem descarregar conteúdo).
    print(f"\n🔍 A inspecionar {len(links)} link(s)...")
    probed = []
    for link in links:
        file_id, key_str = parse_mega_url(link)
        if not file_id or not key_str:
            print(f"⚠️  Link inválido ignorado: {link}")
            continue
        try:
            info = get_mega_info(file_id, key_str, proxy_info=proxy_info)
            info['link'] = link
            probed.append(info)
            print(f"   {info['size']/(1024*1024):8.1f} MB  {info['orig_filename']}")
        except Exception as e:
            print(f"❌ Erro ao ler {link}: {e}")

    if not probed:
        print("❌ Nenhuma parte válida encontrada.")
        return

    groups = build_groups(probed)
    print(f"\n📦 {len(groups)} arquivo(s) detetado(s):")
    for base, g in groups.items():
        total = sum(i['size'] for _, i in g['parts'])
        nums = [idx for idx, _ in g['parts']]
        print(f"   • {base}  [{len(g['parts'])} partes {nums}]  {total/(1024*1024*1024):.2f} GB")

    if dry_run:
        print("\n🏁 --dry-run: nada será descarregado/enviado.")
        return

    tools = ensure_extractor()
    print(f"🛠️  Extratores disponíveis: {', '.join(tools)}")

    # 2. Ficheiros já presentes no 1fichier (para não gastar quota à toa).
    existing = get_existing_1fichier_files(api_key, folder_id)
    existing_stems = {os.path.splitext(f)[0] for f in existing}

    work_root = Path(os.environ.get("WORK_DIR", "/tmp/mega_multipart"))
    work_root.mkdir(parents=True, exist_ok=True)

    uploaded = 0
    for base, g in groups.items():
        print(f"\n{'='*60}\n📦 Grupo: {base}")
        group_dir = work_root / re.sub(r'[^\w.\-]+', '_', base)
        out_dir = group_dir / 'extracted'

        # Pre-check: se a imagem (ou o nome base) já existe, salta.
        if base in existing_stems or (base + '.iso') in existing:
            print(f"⏩ Já existe no 1fichier ('{base}'). A saltar...")
            continue

        try:
            # 2a. Descarregar + desencriptar cada parte.
            shutil.rmtree(group_dir, ignore_errors=True)
            group_dir.mkdir(parents=True, exist_ok=True)
            ok_all = True
            for idx, item in g['parts']:
                enc = group_dir / f"{item['file_id']}.raw"
                dec = group_dir / item['orig_filename']
                if dec.exists() and dec.stat().st_size == item['size']:
                    print(f"   ⏩ parte {idx} já descarregada: {dec.name}")
                    continue
                print(f"\n[{idx}] {item['orig_filename']}")
                if not download_mega_raw(item, str(enc), proxy_info=proxy_info):
                    print(f"❌ Falha no download da parte {idx}")
                    ok_all = False
                    break
                decrypt_file_ctr(str(enc), str(dec), item['aes_key'], item['iv_upper'])
                enc.unlink(missing_ok=True)
            if not ok_all:
                shutil.rmtree(group_dir, ignore_errors=True)
                continue

            # 2b. Extrair (aponta para a primeira parte; o extrator encadeia as restantes).
            first_part = group_dir / g['parts'][0][1]['orig_filename']
            images, pw, tool = extract_iso(first_part, out_dir, passwords, tools)
            if not images:
                print("❌ Não foi possível extrair uma imagem (.iso/.img). "
                      "Verifica a password ou instala unar/p7zip-rar.")
                shutil.rmtree(group_dir, ignore_errors=True)
                continue

            # 2c. Libertar as partes RAR antes do upload (poupa disco).
            for _, item in g['parts']:
                (group_dir / item['orig_filename']).unlink(missing_ok=True)

            # 2d. Enviar só a(s) imagem(ns).
            for img in images:
                if img.name in existing:
                    print(f"⏩ {img.name} já existe no 1fichier. A saltar upload.")
                    continue
                res = upload_to_1fichier(str(img), img.name, api_key, folder_id)
                dl_link = res.get("download", "")
                print(f"🎉 Link 1fichier: {dl_link}")
                if dl_link:
                    set_1fichier_inline(api_key, dl_link)
                existing.add(img.name)
                uploaded += 1

        except Exception as e:
            print(f"❌ Erro no grupo '{base}': {e}")
        finally:
            shutil.rmtree(group_dir, ignore_errors=True)

    print(f"\n✨ Terminado: {uploaded} imagem(ns) enviadas para o 1fichier.")


if __name__ == "__main__":
    main()
