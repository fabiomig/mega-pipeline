#!/usr/bin/env python3
# ==============================================================================
# mega_1fichier_upload.py - Orquestrador de Uploads Remotos da MEGA para o 1fichier
# Utiliza o túnel com desencriptação em tempo real (https://tunel.hospidy.com/mega)
# ==============================================================================

import os
import sys
import re
import json
import time
import ssl
import base64
import argparse
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# Forçar flush de stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

# Cores ANSI
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
RED = "\033[0;31m"
BOLD = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"

TUNNEL_BASE = os.environ.get("TUNNEL_BASE", "https://tunel.hospidy.com")
PLAY_BASE = os.environ.get("PLAY_BASE", "https://play.hospidy.com")
SCRIPT_DIR = Path(__file__).parent.resolve()
STATE_FILE = SCRIPT_DIR / "mega_upload_state.json"

DEFAULT_LINKS = [
    "https://mega.nz/#!QTcyFC5B!kwVIGmkvCdBH-SVeP5N-fKTYcTKcxU_KmFVsEi6-pKM",
    "https://mega.nz/#!tO9k0AQQ!EKDMMXTPdsFLt_JZytn1idq2sTmiWFTSBZOV22VKzVM",
    "https://mega.nz/#!dfFXnSaQ!OKhe7et7kanFHnINNUXM5SrUlmsYMWsHtrsQogrzCmA",
    "https://mega.nz/#!xSEmnYSR!R9F62YnZJJ5_d6sWUR4zTzo6N1KN-5xJGnN8B4WDaUI",
    "https://mega.nz/#!QOsnCYwA!2AzaNh0syOAw7uqbdZJtNJuDHu_JVP5Fm-WvEYG5OzY",
    "https://mega.nz/#!kSlzyAhI!USWVipZpVZjmnNjFegJS9mrQsvwcxjHC4FPzGCdu4R4",
    "https://mega.nz/#!5TdDFRSJ!RtMczhAayNdUnKoOFC5vz3VqlVvzvJbyXUL33_7L6SI",
    "https://mega.nz/#!9L8BVKZK!ESH59E280uHq0hxF7ImrIyDbb8q3h4C3IxAT3-qoQjI",
    "https://mega.nz/#!hK1kwaTA!O2Na7wa4Hnt55ZlBO4A8cOv0n36m1yoBwP0d958PfmE",
    "https://mega.nz/#!dHtVBLCa!WbWQTpnopWX1xdMoEK01ugJb607xiVH7M-EXMYVFlrE",
    "https://mega.nz/#!xD1HkZ5Y!v_tMu68gd-Vl0HrXK-tU4gQ85EfUCKPX7q49FODdGoc",
    "https://mega.nz/#!sWtzRKDC!s3hQcZ51C5vP2DbsNKjYekMZHn08Fk4fgFycPCFH4hM",
    "https://mega.nz/#!AKkhHTZS!qS6hiT1RQsL_64BVMuPGUvf6OtZ8w4LzlqMgO6mdPYU",
    "https://mega.nz/#!dGERTCzR!0W9AjKe9mZNgixknLtMQPmJs1eyweljT_ZOIgkiK6gQ",
    "https://mega.nz/#!cLNgUKKJ!SZv1bBbwr61UeuhNanNMGtiY2dF6k4EfuflMloOZ7Ks",
    "https://mega.nz/#!1LVnSBgS!7eHINtS8w8jhVlXwT66I7F48H5AINEr2gDcyyQTjBLs",
    "https://mega.nz/#!QbN3lZYT!cwWXEvaIRY6s4OGuS_QByh-1sh76xtdSRaB0-oU2KSE",
    "https://mega.nz/#!BLcxBYaQ!_7OK7pi1-cMvKgtnZ7CdeJatT4b_BRFZ_DmXOu3vMLA",
    "https://mega.nz/#!IXdGQByJ!BM6Ft4Ujsg42PKxcbypTRXA26n8uSvn2cXn1spOaqdg",
    "https://mega.nz/#!sblWzaIL!_fOxkhUAvEuZIRQliZ5M8BREBYNqbqHRu1wf6zesLPk",
    "https://mega.nz/#!IWk3Vb7C!KCHXB4KvRpcONwCVH5UcJSIZmn8qZ6_XhpWuid62fR0",
    "https://mega.nz/#!MXMyETpB!jgfMtiB2YtVB69_TCDazMp1h_IfxFP-qiXAE1VkdPhU",
    "https://mega.nz/#!BeUG0BjK!zugtSDhgQCSItW12ElTbb9Hm7CBRwjm0E-K_WunCtUM",
    "https://mega.nz/#!FftkhaYT!0rPzFQzlOxo4tNCok6m5cW0fF_ezb9KGLSkJfkPALEc",
    "https://mega.nz/#!JGM0wLBI!0Q4gJWpU-rVH3_25dPfVr0QVKR1L5505SakdmV7ZT4c",
    "https://mega.nz/#!kCk2EYoK!0gi4Haz6vSN0kOKEPSq4PxNE9ROeESSt3_byzc5wN_0",
    "https://mega.nz/#!6INAkJyB!pjZtxYJzL2HNcklei_VmT1Je7kQauf83mAzawT4EtSs",
    "https://mega.nz/#!bRViDTjD!WqHWD23iozbt1yZ6TK7-DRjhyViHEiBpc1BmzabBlXw",
    "https://mega.nz/#!3Z9SCT4D!V412GBnTHeo6YYPQD7hIqcriXwC4thYh-7LU_rTQAZw",
    "https://mega.nz/#!vI9XzQCD!hwQXs5blT5tkLZj4KViw-64Zjdk87nXQw0mTVZ0Vlfw",
    "https://mega.nz/#!qIUgmTSL!42HTiHniFwNbeHmEF5jGBasHwwL_QfOXYyqSuWKxWaU",
    "https://mega.nz/#!iVVlmByZ!UkdPbd3xFFVNMdNzy5iuCp-FVjBvdxtnbT-U4fl5-dI",
    "https://mega.nz/#!Td0ywbyK!iS24cegToM7MRB9xQIqZO-vBZ392FuBa7WJI9AC3TFw",
    "https://mega.nz/#!zVlGkQSC!DuytxJmakq5F9feHmwvgJv37UeEcY6DEX5GMg_-dC_s",
    "https://mega.nz/#!7JdhSSiC!tOOleL78-Db-zBm6h0Tg_PEpKqGc5SnY568etmv6tV8",
    "https://mega.nz/#!DIFXiALS!ZmKItSFBcoVMsmD9NqCGJxR_9o0TBH_lC24TEnQHNIQ",
    "https://mega.nz/#!GM9TRbrI!d_fbvdC1keyjRHyx4j_76R_IwqYwVhY7QyyhtVF-5ms",
    "https://mega.nz/#!WQNg1QCa!4g6FVrP3kJeClXW3rFtcsQZCWjdKkKnkiexo7ZkYas4",
    "https://mega.nz/#!aEtGFarI!B58KUnBGLDLnCmLQ6NRYpf3DR7gN7hm44b53ktY6Br8"
]


# ==============================================================================
# FUNÇÕES DE CRIPTOGRAFIA E PARSING DA MEGA
# ==============================================================================

def b64url(s: str) -> bytes:
    s = s.replace('-', '+').replace('_', '/')
    while len(s) % 4 != 0:
        s += '='
    return base64.b64decode(s)


def decrypt_cbc(aes_key: bytes, data: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        cipher = Cipher(algorithms.AES(aes_key), modes.CBC(b'\x00' * 16))
        return cipher.decryptor().update(data) + cipher.decryptor().finalize()
    except ImportError:
        try:
            from Cryptodome.Cipher import AES
            return AES.new(aes_key, AES.MODE_CBC, iv=b'\x00' * 16).decrypt(data)
        except ImportError:
            raise RuntimeError("Requer 'cryptography' ou 'pycryptodome' para desencriptar os atributos da MEGA")


def parse_mega_url(url_or_str: str) -> tuple[str | None, str | None]:
    clean = url_or_str.strip()
    m = re.search(r'([a-zA-Z0-9_-]{8,12})[#!,/]([a-zA-Z0-9_-]{20,50})', clean)
    if m:
        return m.group(1), m.group(2)
    return None, None


def normalize_filename(filename: str) -> str:
    """Normaliza o título removendo tags entre colchetes [ex: grupos, CRC], resoluções redundantes e espaços extras."""
    if not filename:
        return "download.bin"
    m = re.match(r'^(.*?)(\.[a-zA-Z0-9]+)$', filename)
    if not m:
        base, ext = filename, ''
    else:
        base, ext = m.group(1), m.group(2)
    # Remover colchetes [qualquer coisa]
    base = re.sub(r'\[.*?\]', '', base)
    # Remover resoluções / tags técnicas entre parênteses
    base = re.sub(r'\((?:1080p|720p|480p|x264|x265|hevc|web-dl|bluray|dvdrip|aac|opus).*?\)', '', base, flags=re.IGNORECASE)
    # Remover parênteses finais residuais se restarem vazios ou redundantes
    base = re.sub(r'\s*\([^)]*\)\s*$', '', base)
    # Normalizar múltiplos espaços e traços soltos
    base = re.sub(r'\s+', ' ', base).strip()
    base = re.sub(r'\s*-\s*$', '', base).strip()
    return f"{base}{ext}"


def resolve_mega_links_batch(links: list[str]) -> list[dict]:
    """Resolve uma lista de URLs da MEGA numa única chamada em lote à API da MEGA."""
    parsed = []
    for l in links:
        fid, key = parse_mega_url(l)
        if fid and key:
            parsed.append((fid, key, l))

    if not parsed:
        return []

    payload = [{'a': 'g', 'p': fid, 'ssl': 1} for fid, _, _ in parsed]
    req = urllib.request.Request(
        'https://g.api.mega.co.nz/cs?id=0',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
    )

    with urllib.request.urlopen(req, timeout=25) as resp:
        results = json.loads(resp.read().decode('utf-8'))

    resolved = []
    for (fid, key_str, orig_url), res in zip(parsed, results):
        if isinstance(res, int) or 'e' in res:
            print(f"{RED}❌ Erro ao consultar {fid}: {res}{NC}")
            continue

        raw_key = b64url(key_str)
        aes_key = bytes(raw_key[i] ^ raw_key[i+16] for i in range(16))
        at_bytes = b64url(res.get('at', ''))

        try:
            attr_dec = decrypt_cbc(aes_key, at_bytes)
            m = re.search(r'"n":"(.*?)"', attr_dec.decode('utf-8', errors='ignore'))
            filename = m.group(1) if m else f"{fid}.mkv"
        except Exception:
            filename = f"{fid}.mkv"

        size = res.get('s', 0)
        clean_filename = normalize_filename(filename)

        # Montar URL do túnel (com codificação segura)
        tunnel_url = f"{TUNNEL_BASE}/mega/{fid}/{key_str}/{urllib.parse.quote(clean_filename)}"

        resolved.append({
            'fid': fid,
            'key': key_str,
            'orig_url': orig_url,
            'filename': clean_filename,
            'raw_filename': filename,
            'size': size,
            'tunnel_url': tunnel_url
        })

    return resolved


# ==============================================================================
# CLIENTE DA API DO 1FICHIER
# ==============================================================================

class FichierAPI:
    def __init__(self, api_key: str, min_delay: float = 1.5):
        self.api_key = api_key
        self.ctx = ssl.create_default_context()
        self.min_delay = min_delay
        self.last_call_time = 0.0

    def _call(self, endpoint: str, payload: dict | None = None, max_retries: int = 3) -> dict:
        url = f"https://api.1fichier.com/v1/{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "mega-1fichier-tunnel/1.0"
        }
        data = json.dumps(payload if payload is not None else {}).encode("utf-8")

        for attempt in range(max_retries):
            # Respeitar estritamente o limite de taxa de requisições ao 1fichier
            elapsed = time.time() - self.last_call_time
            if elapsed < self.min_delay:
                time.sleep(self.min_delay - elapsed)

            self.last_call_time = time.time()
            req = urllib.request.Request(url, data=data, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=25, context=self.ctx) as resp:
                    raw = resp.read().decode("utf-8")
                    parsed = json.loads(raw) if raw else {}
                    # Deteção proativa de Flood/IP Lock
                    if isinstance(parsed, dict) and parsed.get("status") == "KO":
                        msg = str(parsed.get("message", ""))
                        if "flood" in msg.lower() or "locked" in msg.lower():
                            wait_s = 120 * (attempt + 1)
                            print(f"\n{RED}🛑 [1FICHIER RATE LIMIT] {msg}. A pausar {wait_s}s para proteger o IP contra bloqueios...{NC}")
                            time.sleep(wait_s)
                            continue
                    return parsed
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="ignore")
                if e.code in (403, 429) or "flood" in err_body.lower() or "locked" in err_body.lower():
                    wait_s = 120 * (attempt + 1)
                    print(f"\n{RED}🛑 [1FICHIER HTTP {e.code}] Flood / IP Lock detetado: {err_body[:100]}. A pausar {wait_s}s...{NC}")
                    time.sleep(wait_s)
                    continue
                try:
                    return json.loads(err_body)
                except Exception:
                    raise e
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < max_retries - 1:
                    print(f"{YELLOW}⚠️ Conexão falhou ({e}), a aguardar 5s antes de tentar novamente...{NC}")
                    time.sleep(5)
                    continue
                raise
        return {}

    def get_folders(self, folder_id: int = 0) -> list[dict]:
        res = self._call("folder/ls.cgi", {"folder_id": folder_id})
        return res.get("sub_folders", [])

    def create_folder(self, name: str, parent_id: int = 0) -> int:
        res = self._call("folder/mkdir.cgi", {"name": name, "folder_id": parent_id})
        if res.get("status") == "OK":
            return res.get("folder_id")
        raise RuntimeError(f"Erro ao criar pasta {name}: {res}")

    def list_files(self, folder_id: int = 0) -> dict[str, str]:
        """Retorna {filename: download_url} dos ficheiros existentes na pasta."""
        try:
            res = self._call("file/ls.cgi", {"folder_id": folder_id})
            files = {}
            for item in res.get("items", []):
                files[item.get("filename")] = item.get("url")
            return files
        except Exception:
            return {}

    def remote_request(self, urls: list[str], folder_id: int | None = None) -> dict:
        payload = {"urls": urls}
        if folder_id:
            payload["folder_id"] = folder_id
        return self._call("remote/request.cgi", payload)

    def get_remote_tasks_map(self) -> dict[int, dict]:
        """Obtém o estado de todas as tarefas numa única chamada leve (/v1/remote/ls.cgi), poupando chamadas repetitivas."""
        try:
            res = self._call("remote/ls.cgi", {})
            tasks = {}
            for t in res.get("data", []):
                tid = t.get("id")
                if tid:
                    tasks[int(tid)] = t
            return tasks
        except Exception:
            return {}

    def get_remote_info(self, task_id: int | str) -> dict:
        return self._call("remote/info.cgi", {"id": int(task_id)})

    def set_inline(self, urls: list[str]) -> bool:
        if not urls:
            return True
        res = self._call("file/chattr.cgi", {"urls": urls, "inline": 1})
        return res.get("status") == "OK"

    def normalize_folder(self, folder_id: int) -> int:
        """Verifica ficheiros na pasta do 1fichier, garante 'inline: 1' e normaliza os títulos removendo tags residuais."""
        try:
            res = self._call("file/ls.cgi", {"folder_id": folder_id})
            items = res.get("items", [])
            modified = 0
            for item in items:
                fname = item.get("filename", "")
                url = item.get("url")
                norm = normalize_filename(fname)
                is_inline = item.get("inline", 0) == 1

                payload = {"urls": [url], "inline": 1}
                needs_update = False
                if fname != norm:
                    payload["filename"] = norm
                    needs_update = True
                if not is_inline:
                    needs_update = True

                if needs_update:
                    ch_res = self._call("file/chattr.cgi", payload)
                    if ch_res.get("status") == "OK":
                        modified += 1
                        print(f"  {GREEN}✔ Normalizado & Inline:{NC} '{fname}' -> {BOLD}'{norm}'{NC}")
            return modified
        except Exception as e:
            print(f"  {RED}Erro ao normalizar pasta {folder_id}:{NC} {e}")
            return 0

    def trigger_sync(self):
        try:
            req = urllib.request.Request(
                f"{PLAY_BASE}/sync",
                data=b"{}",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=10, context=self.ctx) as resp:
                data = json.loads(resp.read().decode())
                return data.get("total_files")
        except Exception:
            return None


def get_token() -> str:
    for p in [os.path.expanduser("~/.1fichier_token"), "/home/fabio/.1fichier_token", ".1fichier_token"]:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    c = f.read().strip()
                if c.startswith("{"):
                    return json.loads(c).get("api_key")
                return c.splitlines()[0].strip()
            except Exception:
                pass
    raise RuntimeError("Chave de API do 1fichier não encontrada em ~/.1fichier_token")


# ==============================================================================
# GESTÃO DE ESTADO
# ==============================================================================

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"tasks": {}, "completed": {}, "folder_id": None}


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ==============================================================================
# COMANDOS PRINCIPAIS
# ==============================================================================

def cmd_resolve(links: list[str]):
    print(f"\n{CYAN}{BOLD}🔍 A analisar {len(links)} links da MEGA...{NC}")
    items = resolve_mega_links_batch(links)
    total_sz = sum(i['size'] for i in items)
    print(f"{GREEN}✅ {len(items)} episódios identificados ({total_sz / (1024**3):.2f} GB total):{NC}\n")

    for idx, it in enumerate(items, 1):
        sz_mb = it['size'] / (1024**2)
        print(f" {idx:2d}. {BOLD}{it['filename']}{NC} ({sz_mb:.1f} MB)")
        print(f"     {DIM}Túnel: {it['tunnel_url']}{NC}")

    return items


def cmd_submit(api: FichierAPI, links: list[str], series_name: str = "Sailor Moon Crystal", batch_size: int = 5):
    state = load_state()

    # 1. Localizar ou criar a subpasta da Série
    series_master_id = 22692434 # ID oficial da pasta 'Series'
    target_folder_id = state.get("folder_id")

    if not target_folder_id:
        subfolders = api.get_folders(series_master_id)
        for sf in subfolders:
            if sf.get("name").lower() == series_name.lower():
                target_folder_id = sf.get("id")
                break

        if not target_folder_id:
            print(f"{CYAN}📁 A criar pasta '{series_name}' dentro de 'Series'...{NC}")
            target_folder_id = api.create_folder(series_name, parent_id=series_master_id)

        state["folder_id"] = target_folder_id
        save_state(state)

    print(f"{GREEN}📁 Pasta de Destino no 1fichier:{NC} {BOLD}{series_name}{NC} (ID: {target_folder_id})")

    # 2. Verificar ficheiros já existentes na pasta
    existing_files = api.list_files(target_folder_id)
    print(f"ℹ️  Ficheiros já presentes na pasta: {len(existing_files)}")

    # 3. Analisar links da MEGA
    items = resolve_mega_links_batch(links)
    pending = []

    for it in items:
        fn = it['filename']
        if fn in existing_files:
            print(f"  {GREEN}✓{NC} {fn} {DIM}(Já existe no 1fichier){NC}")
            state["completed"][fn] = existing_files[fn]
        else:
            pending.append(it)

    save_state(state)

    if not pending:
        print(f"\n{GREEN}🎉 Todos os {len(items)} episódios já se encontram no 1fichier!{NC}")
        return

    print(f"\n{CYAN}🚀 A submeter {len(pending)} episódios pendentes em lotes de {batch_size}...{NC}")

    # Submeter em lotes para garantir fila limpa no 1fichier
    for i in range(0, len(pending), batch_size):
        chunk = pending[i:i+batch_size]
        urls = [it['tunnel_url'] for it in chunk]
        names = [it['filename'] for it in chunk]

        print(f"\n📦 A enviar lote {i//batch_size + 1} ({len(chunk)} ficheiros):")
        for n in names:
            print(f"   - {n}")

        try:
            res = api.remote_request(urls, folder_id=target_folder_id)
            if res.get("status") == "OK":
                task_id = str(res.get("id"))
                print(f"   {GREEN}✓ Tarefa 1fichier criada:{NC} ID {task_id}")
                state["tasks"][task_id] = {
                    "names": names,
                    "submitted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "QUEUED"
                }
                save_state(state)
            else:
                print(f"   {RED}❌ Erro 1fichier:{NC} {res.get('message', res)}")
        except Exception as e:
            print(f"   {RED}❌ Exceção ao submeter:{NC} {e}")

        if i + batch_size < len(pending):
            time.sleep(2)

    print(f"\n{GREEN}✅ Submissão concluída! Execute com '--watch' para acompanhar os downloads.{NC}")


def cmd_watch(api: FichierAPI, poll_interval: int = 45):
    print(f"\n{CYAN}{BOLD}📡 A monitorizar transferências remotas no 1fichier (Intervalo {poll_interval}s, anti-flood ativo)... (Ctrl+C para sair){NC}\n")
    state = load_state()

    while True:
        state = load_state()
        active_tasks = [tid for tid, tdata in state["tasks"].items() if tdata.get("status") != "COMPLETED"]

        if not active_tasks:
            print(f"{GREEN}🎉 Todas as tarefas registadas foram concluídas!{NC}")
            break

        print(f"[{time.strftime('%H:%M:%S')}] A verificar estado ({len(active_tasks)} tarefa(s) pendente(s))...")

        # Uma única chamada leve para obter estado de todas as tarefas de uma só vez
        remote_map = api.get_remote_tasks_map()
        completed_any = False

        for tid in active_tasks:
            rem_t = remote_map.get(int(tid)) if str(tid).isdigit() else None
            # Se a execução ainda estiver em 1970-01-01, significa que está em fila / a transferir; poupa chamadas
            if rem_t and rem_t.get("execution_date", "").startswith("1970-01-01"):
                continue

            try:
                info = api.get_remote_info(tid)
                results = info.get("result", [])

                if results:
                    print(f"  {GREEN}Tarefa {tid} concluída!{NC}")
                    state["tasks"][tid]["status"] = "COMPLETED"
                    state["tasks"][tid]["results"] = results
                    completed_any = True

                    for r in results:
                        status = r.get("status")
                        dl_link = r.get("download_link")
                        msg = r.get("message", "")
                        print(f"    - {status}: {msg}")
                        if dl_link:
                            name_match = re.search(r'\]\s*([^/]+)$', r.get('url', ''))
                            if name_match:
                                state["completed"][urllib.parse.unquote(name_match.group(1))] = dl_link
            except Exception as e:
                print(f"  {RED}Erro ao consultar tarefa {tid}:{NC} {e}")

        save_state(state)

        # Se houver tarefas concluídas, normalizar títulos e aplicar inline: 1
        if completed_any:
            folder_id = state.get("folder_id", 22726720)
            print(f"{CYAN}🔧 A verificar/normalizar títulos e fixar 'inline: 1' na pasta...{NC}")
            api.normalize_folder(folder_id)
            cnt = api.trigger_sync()
            if cnt is not None:
                print(f"{GREEN}🔄 Catálogo do play.hospidy.com sincronizado ({cnt} ficheiros totais)!{NC}")

        time.sleep(poll_interval)


# ==============================================================================
# ENTRYPOINT
# ==============================================================================

def cmd_status(api: FichierAPI):
    state = load_state()
    print(f"\n{CYAN}{BOLD}📡 Estado das Transferências Remotas no 1fichier:{NC}\n")
    folder_id = state.get("folder_id", 22726720)
    existing = api.list_files(folder_id)
    print(f"📁 Pasta 'Sailor Moon Crystal' (ID {folder_id}): {len(existing)} ficheiros no 1fichier")

    print(f"\n{'ID Tarefa':<12} {'Data Envio':<20} {'Estado':<12} {'Ficheiros':<30}")
    print("-" * 75)

    # 1 única chamada leve para obter o mapa de todas as tarefas remotas
    remote_map = api.get_remote_tasks_map()

    for tid, tdata in sorted(state.get("tasks", {}).items(), key=lambda x: x[0]):
        status_color = YELLOW
        st = tdata.get("status", "QUEUED")
        names = tdata.get("names", [])
        names_str = f"{len(names)} episódios"
        if len(names) == 1:
            names_str = names[0][:30]

        rem_t = remote_map.get(int(tid)) if str(tid).isdigit() else None
        if rem_t:
            exec_date = rem_t.get("execution_date", "")
            if exec_date and not exec_date.startswith("1970-01-01"):
                st = "COMPLETED"
                status_color = GREEN
            else:
                st = "IN_PROGRESS"
                status_color = YELLOW

        print(f"{tid:<12} {tdata.get('submitted_at', ''):<20} {status_color}{st:<12}{NC} {names_str}")


def main():
    parser = argparse.ArgumentParser(description="Orquestrador MEGA -> 1fichier via Túnel Hospidy")
    parser.add_argument("--links-file", "-f", help="Ficheiro com links da MEGA (um por linha)")
    parser.add_argument("--resolve-only", "-r", action="store_true", help="Apenas inspeciona os links e metadados")
    parser.add_argument("--status", "-s", action="store_true", help="Mostra o estado atual das tarefas no 1fichier")
    parser.add_argument("--watch", "-w", action="store_true", help="Monitoriza o progresso até à conclusão total")
    parser.add_argument("--normalize", "-n", action="store_true", help="Normaliza os títulos de todos os ficheiros na pasta e ativa inline: 1")
    parser.add_argument("--sync", action="store_true", help="Força a atualização do catálogo no play.hospidy.com")
    parser.add_argument("--series", default="Sailor Moon Crystal", help="Nome da subpasta em 'Series' (predefinição: Sailor Moon Crystal)")
    parser.add_argument("--batch-size", "-b", type=int, default=5, help="Tamanho dos lotes de submissão (predefinição: 5)")
    parser.add_argument("urls", nargs="*", help="Links da MEGA avulsos para transferir")

    args = parser.parse_args()

    api_key = get_token()
    api = FichierAPI(api_key)

    if args.normalize:
        state = load_state()
        fid = state.get("folder_id", 22726720)
        print(f"\n{CYAN}{BOLD}🔧 A verificar e normalizar títulos na pasta {fid} (inline: 1)...{NC}")
        mod = api.normalize_folder(fid)
        print(f"{GREEN}✅ {mod} ficheiro(s) atualizados com sucesso.{NC}")
        cnt = api.trigger_sync()
        if cnt is not None:
            print(f"{GREEN}🔄 Catálogo do play.hospidy.com sincronizado ({cnt} ficheiros totais)!{NC}")
        return

    if args.sync:
        print(f"{CYAN}🔄 A sincronizar catálogo play.hospidy.com...{NC}")
        cnt = api.trigger_sync()
        print(f"{GREEN}✅ Catálogo sincronizado com sucesso ({cnt} ficheiros)!{NC}")
        return

    # Determinar lista de links
    links = []
    if args.links_file:
        with open(args.links_file, "r", encoding="utf-8") as f:
            links = [l.strip() for l in f if l.strip()]
    elif args.urls:
        links = args.urls
    else:
        links = DEFAULT_LINKS

    if args.resolve_only:
        cmd_resolve(links)
        return

    if args.status:
        cmd_status(api)
        return

    if args.watch:
        cmd_watch(api)
        return

    # Comportamento padrão: Submeter links e monitorizar
    print(f"\n{BOLD}{CYAN}======================================================================{NC}")
    print(f"{BOLD}{CYAN}  Mega -> 1fichier Remote Upload Tunnel Pipeline{NC}")
    print(f"{BOLD}{CYAN}======================================================================{NC}")
    print(f"Links a processar: {len(links)}")
    print(f"Série: {BOLD}{args.series}{NC}")
    print(f"Túnel: {BOLD}{TUNNEL_BASE}{NC}\n")

    cmd_submit(api, links, series_name=args.series, batch_size=args.batch_size)


if __name__ == "__main__":
    main()
