#!/usr/bin/env python3
# ==============================================================================
# dvd_1fichier.py - Utilitário de Upload de ISOs para 1fichier (Conta Pessoal)
# Integração Forense com dvd-rip para Android / Termux
# ==============================================================================

import os
import sys
import glob
import time
import json
import signal
import ssl
import socket
import shutil
import subprocess
import threading
import concurrent.futures
import http.client
import urllib.request
import urllib.error
import urllib.parse
import re
import argparse
from datetime import datetime
from pathlib import Path

# Forçar flush imediato de stdout para logs e background
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

# Cores ANSI para terminal
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
RED = "\033[0;31m"
BOLD = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"

# Diretórios e caminhos padrão
HOME_DIR = os.path.expanduser("~")
ADVD_DEFAULT = "/sdcard/ADVD" if os.path.exists("/sdcard/ADVD") else os.path.join(HOME_DIR, "ADVD")
BASE_DIRS = [d for d in [ADVD_DEFAULT, "/sdcard/ADVD", os.path.join(HOME_DIR, "ADVD"), os.path.join(HOME_DIR, "Downloads"), "/sdcard/Download", "."] if os.path.exists(d)]
if ADVD_DEFAULT not in BASE_DIRS:
    BASE_DIRS.insert(0, ADVD_DEFAULT)

METADATA_DIR = "/sdcard/ADVD/.metadata" if os.path.exists("/sdcard/ADVD") else os.path.join(ADVD_DEFAULT, ".metadata")
LINKS_FILE = "/sdcard/ADVD/links_1fichier.txt" if os.path.exists("/sdcard/ADVD") else os.path.join(ADVD_DEFAULT, "links_1fichier.txt")
CONFIG_FILE_HOME = os.path.expanduser("~/.1fichier_token")
CONFIG_FILE_ADVD = "/sdcard/ADVD/.1fichier_token" if os.path.exists("/sdcard/ADVD") else os.path.join(ADVD_DEFAULT, ".1fichier_token")

# Endpoint do Túnel Cloudflare para Transfer.it -> 1fichier
TUNNEL_BASE = "https://tunel.hospidy.com"
PLAY_BASE = "https://play.hospidy.com"
TRANSFER_LINKS_CANDIDATES = [
    os.path.join(ADVD_DEFAULT, "links.txt"),
    "/sdcard/ADVD/links.txt",
    "/sdcard/ADVD/links_transferit.txt",
    "/sdcard/ADVD/links_transfer.txt",
    os.path.expanduser("~/links.txt"),
    os.path.expanduser("~/links_transferit.txt"),
    "links.txt",
    "links_transferit.txt",
    "/sdcard/Download/links.txt",
    "/sdcard/Download/links_transferit.txt"
]

CHUNK_SIZE = 1024 * 1024  # 1 MB por chunk para telemetria fluida de velocidade e saturação TCP
print_lock = threading.Lock()


def format_bytes(size_bytes: int) -> str:
    """Converte bytes para formato legível (KB, MB, GB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_speed(bps: float) -> str:
    """Converte bytes por segundo para formato legível."""
    if bps < 1024 * 1024:
        return f"{bps / 1024:.1f} KB/s"
    return f"{bps / (1024 * 1024):.1f} MB/s"


def format_time(seconds: float) -> str:
    """Formata segundos em MM:SS ou HH:MM:SS."""
    if seconds < 0 or seconds > 86400 * 7:
        return "--:--"
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


g_caffeinate_proc = None

def acquire_wake_lock():
    """Ativa o termux-wake-lock no Android ou caffeinate no macOS."""
    global g_caffeinate_proc
    if sys.platform == "darwin":
        try:
            import subprocess
            g_caffeinate_proc = subprocess.Popen(["caffeinate", "-d", "-i", "-m", "-u"])
        except Exception:
            pass
    else:
        try:
            import subprocess
            subprocess.run(["termux-wake-lock"], check=False, capture_output=True)
        except Exception:
            pass


def release_wake_lock():
    """Liberta o termux-wake-lock ou termina o caffeinate no macOS."""
    global g_caffeinate_proc
    if sys.platform == "darwin" and g_caffeinate_proc:
        try:
            g_caffeinate_proc.terminate()
            g_caffeinate_proc = None
        except Exception:
            pass
    else:
        try:
            import subprocess
            subprocess.run(["termux-wake-unlock"], check=False, capture_output=True)
        except Exception:
            pass


# Configuração e Chave de API
def load_config() -> dict:
    """Carrega as definições salvas da conta 1fichier."""
    for cfg_path in [CONFIG_FILE_HOME, CONFIG_FILE_ADVD]:
        if os.path.isfile(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if not content:
                    continue
                if content.startswith("{"):
                    return json.loads(content)
                else:
                    return {"api_key": content.splitlines()[0].strip(), "did": 0}
            except Exception:
                pass
    return {}


def save_config(api_key: str, did: int = 0):
    """Guarda a chave de API e a pasta de destino nos ficheiros de configuração."""
    cfg = {"api_key": api_key.strip(), "did": int(did)}
    saved = False
    for cfg_path in [CONFIG_FILE_HOME, CONFIG_FILE_ADVD]:
        try:
            parent = os.path.dirname(cfg_path)
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            os.chmod(cfg_path, 0o600)
            saved = True
        except Exception:
            pass
    return saved


def get_api_key(cli_arg: str | None = None) -> tuple[str | None, int]:
    """Obtém a chave de API a partir de CLI, variáveis de ambiente ou ficheiro."""
    if cli_arg:
        return cli_arg.strip(), 0

    env_key = os.environ.get("ONEFICHIER_API_KEY") or os.environ.get("FICHIER_API_KEY")
    if env_key:
        return env_key.strip(), 0

    cfg = load_config()
    if cfg and cfg.get("api_key"):
        return cfg.get("api_key"), cfg.get("did", 0)

    return None, 0


def verify_account(api_key: str) -> dict | None:
    """Valida a chave de API com o endpoint /v1/user/info.cgi do 1fichier."""
    url = "https://api.1fichier.com/v1/user/info.cgi"
    req = urllib.request.Request(
        url,
        data=b"{}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "dvd-rip-1fichier/1.0"
        }
    )
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") == "OK":
                return data
    except urllib.error.HTTPError as e:
        try:
            err_data = json.loads(e.read().decode("utf-8"))
            return {"status": "KO", "message": err_data.get("message", str(e))}
        except Exception:
            return {"status": "KO", "message": str(e)}
    except Exception as e:
        return {"status": "KO", "message": str(e)}
    return None


def interactive_setup_api_key() -> str | None:
    """Guia interativo para configurar a chave de API do 1fichier."""
    print(f"\n{CYAN}{BOLD}🔑 Configuração da Conta 1fichier{NC}")
    print("=" * 60)
    print("Para enviar os ISOs diretamente para a tua conta do 1fichier,")
    print("precisas da tua Chave de API (API Key).")
    print(f"\n{YELLOW}Passos para obter a tua chave:{NC}")
    print(" 1. Entra na tua conta em: https://1fichier.com/login.pl")
    print(" 2. Abre a página de Parâmetros: https://1fichier.com/console/params.pl")
    print(" 3. Procura a secção 'API Key' / 'Clé d'API'")
    print(" 4. Clica em 'Get my API Key' e copia a chave exibida.")
    print("=" * 60)

    try:
        raw_key = input(f"\n{BOLD}Cola a tua API Key do 1fichier: {NC}")
        key = raw_key.strip().strip("'\"")
    except (EOFError, KeyboardInterrupt):
        print("\nCancelado.")
        return None

    if not key:
        print(f"{RED}❌ Nenhuma chave inserida.{NC}")
        return None

    print(f"\n{CYAN}A validar chave de API junto da 1fichier...{NC}")
    info = verify_account(key)

    if info and info.get("status") == "OK":
        email = info.get("email", "Desconhecido")
        offer_id = info.get("offer", 0)
        offer_names = {0: "Gratuita / Free", 1: "Premium", 2: "Access", 3: "Premium GOLD"}
        offer_str = offer_names.get(offer_id, f"Tipo {offer_id}")

        print(f"{GREEN}✅ Chave validada com sucesso!{NC}")
        print(f"   👤 Conta : {BOLD}{email}{NC}")
        print(f"   ⭐ Plano : {offer_str}")

        did_input = input(f"\nID da Pasta de Destino no 1fichier [0 = Raiz da Conta]: ").strip()
        did = int(did_input) if did_input.isdigit() else 0

        save_config(key, did)
        print(f"{GREEN}💾 Definições gravadas com sucesso em {CONFIG_FILE_HOME}!{NC}\n")
        return key
    else:
        msg = info.get("message", "Falha na autenticação") if info else "Erro desconhecido"
        print(f"{YELLOW}⚠️  Aviso: Não foi possível validar online ({msg}).{NC}")
        try:
            force_save = input("Desejas guardar esta chave mesmo assim? (S/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None
        if force_save in ["", "s", "sim", "y", "yes"]:
            did_input = input(f"ID da Pasta de Destino no 1fichier [0 = Raiz da Conta]: ").strip()
            did = int(did_input) if did_input.isdigit() else 0
            save_config(key, did)
            print(f"{GREEN}💾 Chave gravada com sucesso em {CONFIG_FILE_HOME}!{NC}\n")
            return key
        return None


# Barra de Progresso
class UploadProgressTracker:
    """Gere a exibição da telemetria de upload em tempo real (ecrã e log)."""

    def __init__(self, filename: str, total_bytes: int):
        self.filename = filename
        self.total_bytes = total_bytes
        self.start_time = time.time()
        self.last_display_time = 0.0
        self.last_speed_time = self.start_time
        self.last_speed_bytes = 0
        self.current_speed = 0.0
        self.last_log_time = 0.0

    def update(self, sent: int):
        now = time.time()
        effective_total = self.total_bytes if self.total_bytes > 0 else sent

        # Throttle para exibição no terminal (máx ~4-5 updates por segundo)
        if self.last_display_time > 0 and (now - self.last_display_time < 0.20) and sent < effective_total:
            return
        self.last_display_time = now

        # Cálculo de velocidade instantânea e média móvel ponderada
        dt = now - self.last_speed_time
        if dt >= 0.4:
            db = sent - self.last_speed_bytes
            instant_speed = db / dt
            if self.current_speed <= 0.0:
                self.current_speed = instant_speed
            else:
                self.current_speed = (self.current_speed * 0.7) + (instant_speed * 0.3)
            self.last_speed_bytes = sent
            self.last_speed_time = now

        pct = (sent / effective_total * 100.0) if effective_total > 0 else 0.0
        pct = min(100.0, max(0.0, pct))

        elapsed = now - self.start_time
        overall_speed = sent / elapsed if elapsed > 0 else 0.0
        speed = self.current_speed if self.current_speed > 1024 else overall_speed
        eta_sec = ((effective_total - sent) / speed) if speed > 1024 else -1

        # Modo Background / Registo em ficheiro
        if not sys.stdout.isatty():
            if now - self.last_log_time >= 2.0 or sent >= effective_total or self.last_log_time == 0:
                self.last_log_time = now
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                log_line = (
                    f"[{ts}] [1FICHIER] {pct:5.1f}% "
                    f"({format_bytes(sent)} / {format_bytes(effective_total)}) "
                    f"⚡ {format_speed(speed):<10} "
                    f"⏳ ETA: {format_time(eta_sec)}\n"
                )
                with print_lock:
                    sys.stdout.write(log_line)
                    sys.stdout.flush()
            return

        # Modo Interativo TTY (\r)
        bar_len = 22
        filled = int(bar_len * (pct / 100.0))
        bar = "=" * filled + (">" if filled < bar_len else "")
        bar = bar.ljust(bar_len)

        line = (
            f"\r\033[K  {CYAN}[{bar}]{NC} {BOLD}{pct:5.1f}%{NC} "
            f"({format_bytes(sent)} / {format_bytes(effective_total)}) "
            f"⚡ {GREEN}{BOLD}{format_speed(speed):<10}{NC} "
            f"⏳ ETA: {BOLD}{format_time(eta_sec):<8}{NC}"
        )
        with print_lock:
            sys.stdout.write(line)
            sys.stdout.flush()


# Localização e Catálogo de ISOs
def get_map_integrity(map_path: str) -> tuple[float, bool]:
    """Lê um ficheiro .map do ddrescue e calcula a percentagem resgatada."""
    if not os.path.exists(map_path):
        return -1.0, False
    total_sec = 0
    rescued_sec = 0
    try:
        with open(map_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line.startswith("# total_sectors"):
                    parts = line.split()
                    if len(parts) >= 3:
                        total_sec = int(parts[2])
                    continue
                if line.startswith("#") or not line:
                    continue
                parts = line.split()
                if len(parts) >= 3:
                    size = int(parts[1])
                    status = parts[2]
                    if status == "+":
                        rescued_sec += size
        if total_sec > 0:
            pct = (rescued_sec / total_sec) * 100.0
            return pct, (pct >= 99.99)
    except Exception:
        pass
    return -1.0, False


def find_reports_for_iso(iso_path: str) -> list[str]:
    """Procura relatórios associados ao ISO."""
    reports = []
    base_name = Path(iso_path).stem
    iso_dir = Path(iso_path).parent

    candidates = [
        os.path.join(METADATA_DIR, f"{base_name}.iso.relatorio.txt"),
        os.path.join(METADATA_DIR, f"{base_name}.relatorio.txt"),
        os.path.join(iso_dir, f"{base_name}.iso.relatorio.txt"),
        os.path.join(iso_dir, f"{base_name}.relatorio.txt"),
        os.path.join(iso_dir, "relatorio.txt"),
    ]
    for c in candidates:
        if os.path.exists(c) and c not in reports:
            reports.append(c)
    return reports


def get_existing_1fichier_link(iso_path: str) -> str | None:
    """Verifica se já existe link do 1fichier nos relatórios ou links_1fichier.txt."""
    reports = find_reports_for_iso(iso_path)
    for rep in reports:
        try:
            with open(rep, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "1fichier.com/?" in line:
                        parts = line.split("1fichier.com/?")
                        if len(parts) > 1:
                            token = parts[1].strip().split()[0].split(")")[0].strip()
                            return f"https://1fichier.com/?{token}"
        except Exception:
            pass

    if os.path.exists(LINKS_FILE):
        base_name = Path(iso_path).name
        try:
            with open(LINKS_FILE, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if base_name in line and "1fichier.com/?" in line:
                        parts = line.split("1fichier.com/?")
                        token = parts[1].strip().split()[0].split(")")[0].strip()
                        return f"https://1fichier.com/?{token}"
        except Exception:
            pass
    return None


def get_existing_transfer_link(iso_path: str) -> str | None:
    """Verifica se já existe link do transfer.it nos relatórios ou links.txt."""
    reports = find_reports_for_iso(iso_path)
    for rep in reports:
        try:
            with open(rep, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "transfer.it/t/" in line:
                        parts = line.split("transfer.it/t/")
                        if len(parts) > 1:
                            token = parts[1].strip().split()[0].split(")")[0].split("/")[0].strip()
                            return f"https://transfer.it/t/{token}"
        except Exception:
            pass

    for l_path in TRANSFER_LINKS_CANDIDATES:
        if os.path.exists(l_path):
            base_name = Path(iso_path).name
            try:
                with open(l_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if base_name in line and "transfer.it/t/" in line:
                            parts = line.split("transfer.it/t/")
                            token = parts[1].strip().split()[0].split(")")[0].split("/")[0].strip()
                            return f"https://transfer.it/t/{token}"
            except Exception:
                pass
def parse_selection_indices(raw_input: str, max_count: int) -> list[int]:
    """Interpreta seleções como '1', '1,3,5', '1-4', '1-3,5', 'all', 't', etc."""
    raw = raw_input.strip().lower()
    if raw in ["all", "a", "t", "todos", "*"]:
        return list(range(max_count))

    selected = set()
    parts = re.split(r'[,;\s]+', raw)
    for part in parts:
        if not part:
            continue
        if '-' in part:
            sub = part.split('-')
            if len(sub) == 2 and sub[0].isdigit() and sub[1].isdigit():
                start, end = int(sub[0]), int(sub[1])
                for idx in range(min(start, end), max(start, end) + 1):
                    if 1 <= idx <= max_count:
                        selected.add(idx - 1)
        elif part.isdigit():
            idx = int(part)
            if 1 <= idx <= max_count:
                selected.add(idx - 1)
    return sorted(list(selected))


def scan_transferit_catalog(api_key: str | None = None) -> list[dict]:
    """Descobre todos os ficheiros (ISOs ou quaisquer outros) com link Transfer.it e estado no 1fichier."""
    seen_handles = set()
    items = []

    # 1. Obter ficheiros existentes no 1fichier (online para precisão absoluta)
    online_files_by_name = {}
    if api_key:
        try:
            req = urllib.request.Request(
                "https://api.1fichier.com/v1/file/ls.cgi",
                data=b"{}",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "dvd-rip-1fichier/1.0"}
            )
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for of in data.get("items", []):
                    fn = of.get("filename", "").lower()
                    if fn:
                        online_files_by_name[fn] = of.get("url")
        except Exception:
            pass

    # 2. Ler ficheiros de links do 1fichier conhecidos (links_1fichier.txt)
    fichier_links_by_name = {}
    for l1f in [LINKS_FILE, os.path.expanduser("~/links_1fichier.txt"), "links_1fichier.txt"]:
        if os.path.isfile(l1f):
            try:
                with open(l1f, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if "1fichier.com/?" in line:
                            parts = line.split("1fichier.com/?")
                            token = parts[1].strip().split()[0].split(")")[0].strip()
                            m_fn = re.search(r'(?:\]\s*|^)([^\s\(->]+(?:\.[a-zA-Z0-9]+)?)', line)
                            if m_fn:
                                fname = m_fn.group(1).strip().lower()
                                fichier_links_by_name[fname] = f"https://1fichier.com/?{token}"
            except Exception:
                pass

    # 3. Varrer candidatos de links.txt do Transfer.it
    for cand in TRANSFER_LINKS_CANDIDATES:
        if not os.path.isfile(cand):
            continue
        try:
            with open(cand, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    m = re.search(r'https?://transfer\.it/t/([a-zA-Z0-9_-]{10,20})', line)
                    if not m:
                        continue
                    handle = m.group(1)
                    if handle in seen_handles:
                        continue
                    seen_handles.add(handle)

                    transfer_url = f"https://transfer.it/t/{handle}"

                    fn = None
                    fn = None
                    sz_str = None
                    dt_str = None

                    if "|" in line:
                        parts = [p.strip() for p in line.split("|")]
                        if len(parts) >= 4:
                            dt_str = parts[0]
                            fn = parts[1]
                            sz_str = parts[2]
                    else:
                        m_fn = re.search(r'(?:\]\s*|^)([a-zA-Z0-9_.\-\+ ]+?\.[a-zA-Z0-9]{2,5})\b', line)
                        if m_fn and not m_fn.group(1).startswith("http"):
                            fn = m_fn.group(1).strip()

                        m_sz = re.search(r'\(([\d\.]+\s*(?:B|KB|MB|GB|TB))\)', line, re.I)
                        if m_sz:
                            sz_str = m_sz.group(1).strip()

                        m_dt = re.search(r'\[(.*?)\]', line)
                        if m_dt:
                            dt_str = m_dt.group(1).strip()

                    f_link = None
                    if fn and fn.lower() in online_files_by_name:
                        f_link = online_files_by_name[fn.lower()]
                    elif fn and fn.lower() in fichier_links_by_name:
                        f_link = fichier_links_by_name[fn.lower()]

                    items.append({
                        "handle": handle,
                        "filename": fn or f"{handle}.bin",
                        "size_str": sz_str or "Desconhecido",
                        "date_str": dt_str or "-",
                        "transfer_url": transfer_url,
                        "fichier_link": f_link,
                        "source": cand
                    })
        except Exception:
            pass

    # 4. Varrer relatórios .relatorio.txt em .metadata
    if os.path.isdir(METADATA_DIR):
        for rep in glob.glob(os.path.join(METADATA_DIR, "*.relatorio.txt")):
            try:
                with open(rep, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    m = re.search(r'transfer\.it/t/([a-zA-Z0-9_-]{10,20})', content)
                    if m:
                        handle = m.group(1)
                        if handle not in seen_handles:
                            seen_handles.add(handle)
                            fn = Path(rep).stem.replace(".iso.relatorio", ".iso").replace(".relatorio", "")
                            f_link = None
                            m_1f = re.search(r'1fichier\.com/\?([a-zA-Z0-9]+)', content)
                            if m_1f:
                                f_link = f"https://1fichier.com/?{m_1f.group(1)}"
                            elif fn.lower() in online_files_by_name:
                                f_link = online_files_by_name[fn.lower()]

                            items.append({
                                "handle": handle,
                                "filename": fn,
                                "size_str": "Ver relatório",
                                "date_str": "-",
                                "transfer_url": f"https://transfer.it/t/{handle}",
                                "fichier_link": f_link,
                                "source": rep
                            })
            except Exception:
                pass

    # 5. Varrer ISOs no disco que já tenham link Transfer.it
    try:
        for iso in scan_available_isos():
            t_link = iso.get("transfer_link")
            if t_link:
                m = re.search(r'transfer\.it/t/([a-zA-Z0-9_-]{10,20})', t_link)
                if m:
                    handle = m.group(1)
                    if handle not in seen_handles:
                        seen_handles.add(handle)
                        items.append({
                            "handle": handle,
                            "filename": iso["filename"],
                            "size_str": format_bytes(iso["size"]),
                            "date_str": "-",
                            "transfer_url": t_link,
                            "fichier_link": iso.get("link"),
                            "source": iso["path"]
                        })
    except Exception:
        pass

    return items


def scan_available_isos() -> list[dict]:
    """Pesquisa todos os ficheiros ISO existentes e respetivos estados."""
    raw_files = []
    for d in BASE_DIRS:
        if os.path.isdir(d):
            raw_files.extend(glob.glob(os.path.join(d, "*.iso")))
            raw_files.extend(glob.glob(os.path.join(d, "*/*.iso")))

    seen = set()
    isos = []
    for f in sorted(raw_files, key=lambda x: os.path.getmtime(x), reverse=True):
        real_p = os.path.realpath(f)
        if real_p in seen:
            continue
        seen.add(real_p)

        try:
            sz = os.path.getsize(f)
        except OSError:
            continue
        if sz < 1024 * 1024:  # ignorar < 1 MB
            continue

        base_name = Path(f).stem
        parent_dir = Path(f).parent

        map_candidates = [
            os.path.join(METADATA_DIR, f"{base_name}.iso.map"),
            f"{f}.map",
            os.path.join(parent_dir, f"{base_name}.iso.map"),
        ]
        map_path = None
        for mc in map_candidates:
            if os.path.exists(mc):
                map_path = mc
                break

        pct, is_complete = get_map_integrity(map_path) if map_path else (-1.0, True)
        link = get_existing_1fichier_link(f)
        transfer_link = get_existing_transfer_link(f)

        isos.append({
            "path": f,
            "filename": Path(f).name,
            "size": sz,
            "mtime": os.path.getmtime(f),
            "pct": pct,
            "is_complete": is_complete,
            "link": link,
            "transfer_link": transfer_link,
            "map_path": map_path,
        })
    return isos


def record_1fichier_in_report(iso_path: str, download_url: str, remove_url: str | None = None):
    """Regista o link nos relatórios locais e no catálogo central links_1fichier.txt."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sz = os.path.getsize(iso_path) if os.path.exists(iso_path) else 0
    sz_str = format_bytes(sz)
    base_name = Path(iso_path).name

    section_text = (
        "\n====================================================\n"
        "  PARTILHA / UPLOAD (1FICHIER)                      \n"
        f"  Data de Envio   : {now_str}\n"
        f"  Link de Download: {download_url}\n"
    )
    if remove_url:
        section_text += f"  Link de Remoção : {remove_url}\n"
    section_text += (
        f"  Tamanho Enviado : {sz_str} ({sz:,} bytes)\n"
        "====================================================\n"
    )

    reports = find_reports_for_iso(iso_path)
    if not reports:
        try:
            os.makedirs(METADATA_DIR, exist_ok=True)
            default_rep = os.path.join(METADATA_DIR, f"{Path(iso_path).stem}.iso.relatorio.txt")
            with open(default_rep, "w", encoding="utf-8") as f:
                f.write("====================================================\n")
                f.write("  RELATÓRIO DE INTEGRIDADE DOS FICHEIROS DO DVD    \n")
                f.write(f"  Ficheiro ISO: {iso_path}\n")
                f.write(f"  Criado em   : {now_str}\n")
            reports = [default_rep]
        except Exception:
            pass

    for rep in reports:
        try:
            with open(rep, "a", encoding="utf-8") as f:
                f.write(section_text)
        except Exception:
            pass

    # Catálogo central (/sdcard/ADVD/links_1fichier.txt ou ~/links_1fichier.txt)
    for target_links in [LINKS_FILE, os.path.expanduser("~/links_1fichier.txt")]:
        try:
            parent = os.path.dirname(target_links)
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)
            entry = f"[{now_str}] {base_name} ({sz_str}) -> {download_url}\n"
            with open(target_links, "a", encoding="utf-8") as f:
                f.write(entry)
            break
        except Exception:
            pass


def record_1fichier_remote_in_report(iso_path: str, task_id: str, tunnel_url: str, transfer_link: str):
    """Regista o pedido de remote upload nos relatórios locais e no catálogo central links_1fichier.txt."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sz = os.path.getsize(iso_path) if os.path.exists(iso_path) else 0
    sz_str = format_bytes(sz)
    base_name = Path(iso_path).name

    section_text = (
        "\n====================================================\n"
        "  PARTILHA / UPLOAD (1FICHIER - REMOTE UPLOAD)      \n"
        f"  Data do Pedido  : {now_str}\n"
        f"  ID da Tarefa    : #{task_id}\n"
        f"  Origem Transfer : {transfer_link}\n"
        f"  URL Túnel       : {tunnel_url}\n"
        f"  Tamanho         : {sz_str} ({sz:,} bytes)\n"
        "====================================================\n"
    )

    reports = find_reports_for_iso(iso_path)
    for rep in reports:
        try:
            with open(rep, "a", encoding="utf-8") as f:
                f.write(section_text)
        except Exception:
            pass

    for target_links in [LINKS_FILE, os.path.expanduser("~/links_1fichier.txt")]:
        try:
            parent = os.path.dirname(target_links)
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)
            entry = f"[{now_str}] [REMOTE #{task_id}] {base_name} ({sz_str}) -> {tunnel_url}\n"
            with open(target_links, "a", encoding="utf-8") as f:
                f.write(entry)
            break
        except Exception:
            pass


def request_remote_upload_1fichier(api_key: str, transfer_url_or_handle: str, iso_filename: str | None = None, did: int = 0) -> dict:
    """Solicita o Remote Upload na API do 1fichier usando o túnel com o nome exato."""
    m = re.search(r'([a-zA-Z0-9_-]{10,20})', transfer_url_or_handle)
    if not m:
        return {"status": "KO", "message": "ID do Transfer.it inválido"}
    handle = m.group(1)

    resolved_name = iso_filename
    if not resolved_name or resolved_name == "download.bin":
        try:
            req = urllib.request.Request(f"{TUNNEL_BASE}/api/resolve?xh={handle}", headers={"User-Agent": "dvd-1fichier/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("filename"):
                    resolved_name = data["filename"]
        except Exception:
            pass

    if not resolved_name:
        resolved_name = f"{handle}.iso"

    tunnel_url = f"{TUNNEL_BASE}/tunel/{handle}/{urllib.parse.quote(resolved_name)}"

    url = "https://api.1fichier.com/v1/remote/request.cgi"
    payload = {"urls": [tunnel_url]}
    if did and did > 0:
        payload["folder_id"] = did

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "dvd-rip-1fichier/1.0"
        }
    )
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            data["tunnel_url"] = tunnel_url
            data["filename"] = resolved_name
            data["handle"] = handle
            return data
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8"))
            return {"status": "KO", "message": err.get("message", str(e))}
        except Exception:
            return {"status": "KO", "message": str(e)}
    except Exception as e:
        return {"status": "KO", "message": str(e)}


def get_remote_upload_info(api_key: str, task_id: int | str) -> dict | None:
    """Consulta os detalhes de uma tarefa de transferência remota específica no 1fichier."""
    url = "https://api.1fichier.com/v1/remote/info.cgi"
    req = urllib.request.Request(
        url,
        data=json.dumps({"id": int(task_id)}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "dvd-rip-1fichier/1.0"
        }
    )
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def wait_remote_upload_completion(api_key: str, task_id: int | str, timeout: int = 180, poll_interval: float = 2.0) -> dict | None:
    """Monitoriza a tarefa remota até o 1fichier concluir e devolver o link definitivo."""
    start_t = time.time()
    spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    spin_idx = 0

    while time.time() - start_t < timeout:
        elapsed = int(time.time() - start_t)
        spin_char = spinner[spin_idx % len(spinner)]
        spin_idx += 1

        if sys.stdout.isatty():
            sys.stdout.write(f"\r\033[K   {CYAN}{spin_char}{NC} {BOLD}A aguardar conclusão no 1fichier...{NC} ({elapsed}s)")
            sys.stdout.flush()

        info = get_remote_upload_info(api_key, task_id)
        if info:
            results = info.get("result", [])
            if isinstance(results, list) and len(results) > 0:
                res_item = results[0]
                dl_link = res_item.get("download_link")
                st = res_item.get("status")
                if dl_link and st == "OK":
                    if sys.stdout.isatty():
                        sys.stdout.write("\r\033[K")
                        sys.stdout.flush()
                    return res_item
                elif st == "KO":
                    if sys.stdout.isatty():
                        sys.stdout.write("\r\033[K")
                        sys.stdout.flush()
                    return res_item

        time.sleep(poll_interval)

    if sys.stdout.isatty():
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
    return None


def set_file_inline(api_key: str, download_url: str) -> bool:
    """Marca o ficheiro como inline=1 no 1fichier para streaming e visualização direta."""
    url = "https://api.1fichier.com/v1/file/chattr.cgi"
    req = urllib.request.Request(
        url,
        data=json.dumps({"urls": [download_url], "inline": 1}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "dvd-rip-1fichier/1.0"
        }
    )
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("status") == "OK" or "No changes requested" in data.get("message", "")
    except Exception:
        return False


def trigger_catalog_sync(api_key: str | None = None) -> bool:
    """Notifica play.hospidy.com/sync para atualizar imediatamente o catálogo de streams."""
    if not api_key:
        api_key, _ = load_config()
    if not api_key:
        return False

    url = f"{PLAY_BASE}/sync"
    req = urllib.request.Request(
        url,
        data=b"{}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "dvd-rip-1fichier/1.0"
        }
    )
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") == "OK":
                total = data.get("total_files", 0)
                print(f"   🔄 Catálogo atualizado em {PLAY_BASE}/api/catalog ({total} ficheiros) ✅")
                return True
    except Exception as e:
        print(f"   ⚠️  Aviso: Não foi possível atualizar catálogo remoto em {PLAY_BASE}/sync: {e}")
    return False


def upload_iso_via_remote_transferit(item: dict, api_key: str, did: int = 0) -> bool:
    """Envia um ficheiro ISO com link Transfer.it para o 1fichier via Remote Upload."""
    transfer_link = item.get("transfer_link")
    if not transfer_link:
        print(f"{RED}❌ O ficheiro {item['filename']} não possui link do Transfer.it.{NC}")
        return False

    print(f"\n{CYAN}{BOLD}⚡ A preparar Remote Upload para 1fichier...{NC}")
    print(f"   📁 Ficheiro     : {BOLD}{item['filename']}{NC} ({format_bytes(item['size'])})")
    print(f"   🔗 Transfer.it  : {transfer_link}")
    print(f"   🚀 A consultar túnel em {TUNNEL_BASE}...")

    res = request_remote_upload_1fichier(api_key, transfer_link, iso_filename=item["filename"], did=did)
    if res.get("status") == "OK":
        task_id = res.get("id", "Desconhecido")
        tunnel_url = res.get("tunnel_url", "")
        resolved_name = res.get("filename", item["filename"])
        print(f"\n{GREEN}✅ Pedido de Remote Upload registado no 1fichier com sucesso!{NC}")
        print(f"   🆔 ID da Tarefa : {BOLD}#{task_id}{NC}")
        print(f"   📁 Nome Gravado : {BOLD}{resolved_name}{NC}")
        print(f"   🌐 URL do Túnel : {tunnel_url}")
        print(f"   ⚡ O 1fichier está a descarregar diretamente na cloud (~180 MB/s)!")
        print(f"   🔋 {DIM}Zero tráfego e zero bateria consumidos no telemóvel.{NC}")

        record_1fichier_remote_in_report(item["path"], str(task_id), tunnel_url, transfer_link)

        # Monitorização em tempo real da conclusão
        print(f"\n   ⏳ A monitorizar progresso no 1fichier...")
        remote_res = wait_remote_upload_completion(api_key, task_id)
        if remote_res and remote_res.get("download_link"):
            dl_link = remote_res["download_link"]
            file_msg = remote_res.get("message", "")
            m_id = re.search(r'\?([a-zA-Z0-9]+)', dl_link)
            file_id = m_id.group(1) if m_id else dl_link.split("?")[-1]
            play_link = f"{PLAY_BASE}/{file_id}"

            # Ativar modo inline=1 no 1fichier
            set_file_inline(api_key, dl_link)

            # Sincronizar catálogo com play.hospidy.com
            trigger_catalog_sync(api_key)

            record_1fichier_in_report(item["path"], dl_link)

            print(f"{GREEN}{BOLD}🎉 Concluído com Sucesso no 1fichier!{NC}")
            if file_msg:
                print(f"   📊 Telemetria   : {DIM}{file_msg}{NC}")
            print(f"   📥 Link 1fichier: {BOLD}{dl_link}{NC} {CYAN}(Inline ativado ✅){NC}")
            print(f"   🎬 Stream VLC   : {GREEN}{BOLD}{play_link}{NC}")
            print(f"   💡 {YELLOW}Podes colar o link do Stream VLC diretamente no VLC para começar a ver!{NC}\n")
            return True
        elif remote_res and remote_res.get("status") == "KO":
            msg = remote_res.get("message", "Falha no download remoto")
            print(f"{RED}❌ 1fichier reportou erro: {msg}{NC}\n")
            return False
        else:
            print(f"{YELLOW}ℹ️  O download continua em segundo plano no 1fichier.{NC}")
            print(f"   Podes consultar o estado mais tarde com: {BOLD}dvd-1fichier -rs{NC}\n")
            return True
    else:
        msg = res.get("message", "Erro desconhecido")
        print(f"\n{RED}❌ Erro no pedido de Remote Upload: {msg}{NC}\n")
        return False


def manage_transferit_remote_menu(api_key: str, did: int = 0, initial_selection: str | None = None):
    """Mostra catálogo de ficheiros (ISOs e outros) com link Transfer.it e permite enviar 1, alguns ou todos para o 1fichier."""
    print(f"\n{CYAN}A pesquisar catálogo Transfer.it e a consultar estado no 1fichier...{NC}")
    items = scan_transferit_catalog(api_key=api_key)

    if not items:
        print(f"\n{YELLOW}ℹ️  Nenhum ficheiro com link Transfer.it encontrado nos registos (links.txt) ou no disco.{NC}")
        print(f"   Pastas verificadas: /sdcard/ADVD, ~/links.txt, ./links.txt, etc.\n")
        try:
            input(f"{BOLD}Pressiona Enter para continuar...{NC}")
        except (EOFError, KeyboardInterrupt):
            pass
        return

    print(f"\n{CYAN}{BOLD}📡 Catálogo de Ficheiros Transfer.it -> 1fichier{NC}")
    print("=" * 86)
    print(f" {'#':<3} {'Ficheiro':<34} {'Tamanho':<10} {'Handle':<14} {'Estado 1fichier'}")
    print("-" * 86)

    pending_indices = []
    for idx, it in enumerate(items, start=1):
        fn = it["filename"]
        if len(fn) > 33:
            fn = fn[:30] + "..."
        sz = it["size_str"]
        h = it["handle"]
        if it["fichier_link"]:
            m_fid = re.search(r'\?([a-zA-Z0-9]+)', it["fichier_link"])
            fid = m_fid.group(1) if m_fid else it["fichier_link"]
            st = f"{GREEN}✅ No 1fichier{NC} ({fid})"
        else:
            pending_indices.append(idx - 1)
            st = f"{YELLOW}⏳ Pendente{NC}"

        print(f" {BOLD}{idx:2d}{NC}) {fn:<34} {sz:<10} {h:<14} {st}")

    print("=" * 86)
    print(f" Total: {len(items)} ficheiro(s) | {len(pending_indices)} pendente(s) de envio para 1fichier")
    print("-" * 86)
    print(f" Podes escolher:")
    print(f"  • {BOLD}1{NC}         -> Enviar apenas esse ficheiro")
    print(f"  • {BOLD}1,3,5{NC}     -> Enviar alguns ficheiros específicos")
    print(f"  • {BOLD}1-4{NC}       -> Enviar um intervalo de ficheiros")
    print(f"  • {BOLD}A{NC} ou {BOLD}T{NC}    -> Enviar TODOS os {len(pending_indices)} pendentes")
    print(f"  • {BOLD}Q{NC}         -> Voltar ao menu principal / Sair")
    print("=" * 86)

    sel_str = initial_selection
    if not sel_str:
        try:
            sel_str = input(f"{BOLD}Escolhe os ficheiros a enviar para o 1fichier: {NC}").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n")
            return

    if not sel_str or sel_str.upper() == "Q":
        return

    # Se escolher todos
    if sel_str.upper() in ["A", "T", "ALL", "TODOS"]:
        targets = [items[i] for i in pending_indices]
    else:
        chosen_idxs = parse_selection_indices(sel_str, len(items))
        if not chosen_idxs:
            print(f"{RED}❌ Nenhuma seleção válida.{NC}")
            return
        targets = [items[i] for i in chosen_idxs]

    if not targets:
        print(f"{YELLOW}ℹ️  Nenhum ficheiro selecionado para envio.{NC}")
        return

    print(f"\n{CYAN}{BOLD}🚀 A iniciar envio de {len(targets)} ficheiro(s) para o 1fichier via Remote Upload...{NC}")
    success = 0
    for i, it in enumerate(targets, start=1):
        print(f"\n{BOLD}[{i}/{len(targets)}] {it['filename']} ({it['size_str']}){NC}")
        item_dict = {
            "filename": it["filename"],
            "size": 0,
            "path": it.get("source") if it.get("source") and os.path.exists(it.get("source")) else it["filename"],
            "transfer_link": it["transfer_url"]
        }
        if upload_iso_via_remote_transferit(item_dict, api_key, did=did):
            success += 1
        time.sleep(0.5)

    print(f"\n{GREEN}{BOLD}🎉 Concluído: {success}/{len(targets)} ficheiros enviados e configurados com sucesso!{NC}\n")
    try:
        input(f"{BOLD}Pressiona Enter para continuar...{NC}")
    except (EOFError, KeyboardInterrupt):
        pass


def upload_all_remote_transferit_isos(api_key: str, did: int = 0):
    """Envia todos os ficheiros pendentes que tenham link Transfer.it para o 1fichier via Remote Upload."""
    manage_transferit_remote_menu(api_key, did=did, initial_selection="all")


def show_remote_uploads_status(api_key: str):
    """Consulta e apresenta a lista das últimas transferências remotas no 1fichier."""
    print(f"\n{CYAN}{BOLD}📡 Estado das Transferências Remotas no 1fichier{NC}")
    print("=" * 72)
    url = "https://api.1fichier.com/v1/remote/ls.cgi"
    req = urllib.request.Request(
        url,
        data=b"{}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "dvd-rip-1fichier/1.0"
        }
    )
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"{RED}❌ Erro ao consultar a API do 1fichier: {e}{NC}")
        return

    items = data.get("data", [])
    if not items:
        print(f"{YELLOW}Nenhuma transferência remota registada na conta.{NC}")
        print("=" * 72)
        return

    print(f"Últimas {len(items[:8])} transferências:")
    for task in items[:8]:
        t_id = task.get("id")
        r_date = task.get("request_date", "-")
        e_date = task.get("execution_date", "")
        is_done = e_date and not e_date.startswith("1970")

        if is_done:
            info = get_remote_upload_info(api_key, t_id)
            dl_link = None
            if info and info.get("result"):
                dl_link = info["result"][0].get("download_link")

            if dl_link:
                set_file_inline(api_key, dl_link)
                m_id = re.search(r'\?([a-zA-Z0-9]+)', dl_link)
                f_id = m_id.group(1) if m_id else dl_link.split("?")[-1]
                play_link = f"{PLAY_BASE}/{f_id}"
                print(f" • Tarefa #{t_id} [{e_date}]: {GREEN}✅ Concluído{NC}")
                print(f"   📥 {BOLD}{dl_link}{NC} {CYAN}(Inline ✅){NC}")
                print(f"   🎬 {CYAN}{play_link}{NC}")
            else:
                print(f" • Tarefa #{t_id} [{e_date}]: {GREEN}✅ Concluído{NC}")
        else:
            print(f" • Tarefa #{t_id} [{r_date}]: {YELLOW}⏳ Em fila / A descarregar{NC}")

    print("=" * 72)
    try:
        input(f"\n{BOLD}Pressiona Enter para voltar ao menu...{NC}")
    except (EOFError, KeyboardInterrupt):
        pass


# Motor de Upload Streaming para 1fichier
def get_upload_node(api_key: str | None = None, retries: int = 3) -> tuple[str, str]:
    """Obtém o nó de upload ativo da API da 1fichier com tolerância a rate-limit."""
    url = "https://api.1fichier.com/v1/upload/get_upload_server.cgi"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "dvd-rip-1fichier/1.0"
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=b"{}", headers=headers)
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if "url" in data and "id" in data:
                    return data["url"], data["id"]
                if "message" in data and "flood" in str(data.get("message")).lower():
                    if attempt < retries:
                        time.sleep(10)
                        continue
                raise RuntimeError(f"Resposta da API: {data}")
        except urllib.error.HTTPError as e:
            if (e.code == 429 or "flood" in str(e).lower()) and attempt < retries:
                time.sleep(10)
                continue
            raise
    raise RuntimeError("Não foi possível obter o nó de upload após várias tentativas.")


def upload_iso_to_1fichier(
    iso_path: str,
    api_key: str,
    did: int = 0,
    force: bool = False,
    use_curl: bool = False
) -> dict | None:
    """Envia um ficheiro ISO com máxima velocidade (Turbo nativo C e buffers ampliados para 8 MB)."""
    if not os.path.isfile(iso_path):
        with print_lock:
            print(f"{RED}❌ Ficheiro não encontrado: {iso_path}{NC}")
        return None

    filename = Path(iso_path).name
    file_size = os.path.getsize(iso_path)

    # Verificar se já existe link
    existing_link = get_existing_1fichier_link(iso_path)
    if existing_link and not force:
        with print_lock:
            print(f"{YELLOW}ℹ️  O ISO '{filename}' já tem link no 1fichier:{NC} {CYAN}{existing_link}{NC}")
            print(f"   (Usa --force para enviar novamente)")
        return {"download": existing_link, "filename": filename, "already_uploaded": True}

    with print_lock:
        print(f"\n{BOLD}===================================================={NC}")
        print(f"{CYAN}{BOLD}☁️  Upload 1fichier [VELOCIDADE MÁXIMA]{NC}")
        print(f"   Ficheiro : {BOLD}{filename}{NC}")
        print(f"   Tamanho  : {format_bytes(file_size)} ({file_size:,} bytes)")
        if did > 0:
            print(f"   Pasta ID : {did}")
        print(f"{BOLD}===================================================={NC}")

    acquire_wake_lock()

    try:
        # 1. Obter nó de upload dedicado
        with print_lock:
            print(f"📡 A alocar servidor de upload dedicado no 1fichier para '{filename}'...")
        node_host, upload_id = get_upload_node(api_key)
        with print_lock:
            print(f"✅ Conectado ao servidor: {BOLD}{node_host}{NC} (ID: {upload_id})")

        # 2. Tentar motor nativo C (curl) para throughput máximo de rede
        curl_ok = False
        has_curl = bool(shutil.which("curl"))
        
        if use_curl and has_curl:
            with print_lock:
                print(f"🚀 {GREEN}{BOLD}[TURBO C-ENGINE]{NC} A transmitir com libcurl nativo ({format_bytes(file_size)})...")
            cmd = [
                "curl", "-#", "-o", "/dev/null", "-w", "%{http_code}",
                "--tcp-nodelay",
                "-H", f"Authorization: Bearer {api_key}",
                "-H", "User-Agent: dvd-rip-1fichier/1.0",
                "-F", f"file[]=@{iso_path};filename={filename}",
            ]
            if did > 0:
                cmd.extend(["-F", f"did={did}"])
            cmd.append(f"https://{node_host}/upload.cgi?id={upload_id}")
            proc = subprocess.run(cmd)
            if proc.returncode == 0:
                curl_ok = True
            else:
                with print_lock:
                    print(f"{YELLOW}⚠️  Curl interrompido. A alternar para motor Python otimizado (8 MB)...{NC}")

        # Fallback para Python com TCP Window Tuning e blocos de 8 MB
        if not curl_ok:
            boundary = f"----Boundary1fichier{int(time.time())}X{upload_id}"
            
            prefix_parts = []
            if did > 0:
                prefix_parts.append(
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="did"\r\n\r\n'
                    f"{did}\r\n"
                )
            prefix_parts.append(
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file[]"; filename="{filename}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n"
            )
            prefix_bytes = "".join(prefix_parts).encode("utf-8")
            suffix_bytes = f"\r\n--{boundary}--\r\n".encode("utf-8")

            total_content_length = len(prefix_bytes) + file_size + len(suffix_bytes)

            with print_lock:
                print(f"🚀 A enviar fluxo de dados ({format_bytes(file_size)}) com TCP Tuning (8 MB)...")
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(node_host, timeout=300, context=ctx)
            conn.connect()
            if conn.sock:
                try:
                    conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError:
                    pass
                try:
                    conn.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8 * 1024 * 1024)
                except OSError:
                    pass

            conn.putrequest("POST", f"/upload.cgi?id={upload_id}")
            conn.putheader("Authorization", f"Bearer {api_key}")
            conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
            conn.putheader("Content-Length", str(total_content_length))
            conn.putheader("User-Agent", "dvd-rip-1fichier/1.0")
            conn.endheaders()

            conn.send(prefix_bytes)

            tracker = UploadProgressTracker(filename, file_size)
            sent_bytes = 0

            with open(iso_path, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    conn.send(chunk)
                    sent_bytes += len(chunk)
                    tracker.update(sent_bytes)

            conn.send(suffix_bytes)

            if sys.stdout.isatty():
                sys.stdout.write("\n")
            with print_lock:
                print(f"⏳ A processar e a gerar link final no cluster da 1fichier...")

            resp = conn.getresponse()
            resp.read()
            conn.close()

        # Pausa preventiva de rate-limit
        time.sleep(1.5)

        # 6. Consultar relatório final em /end.pl
        ctx_end = ssl.create_default_context()
        conn_end = http.client.HTTPSConnection(node_host, timeout=60, context=ctx_end)
        conn_end.request(
            "GET",
            f"/end.pl?xid={upload_id}",
            headers={"Authorization": f"Bearer {api_key}", "JSON": "1", "User-Agent": "dvd-rip-1fichier/1.0"}
        )
        resp_end = conn_end.getresponse()
        report_data_raw = resp_end.read().decode("utf-8", errors="ignore")
        conn_end.close()

        try:
            report_json = json.loads(report_data_raw)
        except Exception:
            report_json = None

        if report_json and "links" in report_json and report_json["links"]:
            link_info = report_json["links"][0]
            download_url = link_info.get("download", "")
            remove_url = link_info.get("remove", "")

            # Ativar modo inline no 1fichier
            set_file_inline(api_key, download_url)

            # Sincronizar catálogo com play.hospidy.com
            trigger_catalog_sync(api_key)

            m_id = re.search(r'\?([a-zA-Z0-9]+)', download_url)
            file_id = m_id.group(1) if m_id else download_url.split("?")[-1]
            play_link = f"{PLAY_BASE}/{file_id}"

            print(f"\n{GREEN}{BOLD}🎉 Upload Concluído com Sucesso!{NC}")
            print(f"   🔗 Link 1fichier: {BOLD}{download_url}{NC} {CYAN}(Inline ativado ✅){NC}")
            print(f"   🎬 Stream VLC   : {GREEN}{BOLD}{play_link}{NC}")
            if remove_url:
                print(f"   🗑️  Link Remoção : {DIM}{remove_url}{NC}")

            record_1fichier_in_report(iso_path, download_url, remove_url)
            print(f"   📄 Registado em: {LINKS_FILE}")
            reports = find_reports_for_iso(iso_path)
            if reports:
                print(f"   📋 Relatório atualizado: {reports[0]}")
            return link_info
        else:
            print(f"{YELLOW}⚠️  Ficheiro enviado, mas não foi possível ler JSON de retorno.{NC}")
            print(f"Resposta bruta: {report_data_raw[:300]}")
            return None

    except Exception as e:
        print(f"\n{RED}❌ Erro durante o upload: {e}{NC}")
        return None
    finally:
        release_wake_lock()


def upload_all_pending_isos(
    api_key: str,
    did: int = 0,
    force: bool = False,
    concurrency: int = 2,
    use_curl: bool = False
):
    """Envia todos os ISOs pendentes com suporte a conexões paralelas."""
    isos = scan_available_isos()
    if not isos:
        print(f"{YELLOW}ℹ️  Nenhum ficheiro ISO encontrado em /sdcard/ADVD ou pastas padrão.{NC}")
        return

    to_upload = [x for x in isos if force or not x["link"]]

    print(f"\n{CYAN}{BOLD}📦 Envio em Lote para 1fichier ({concurrency} conexões paralelas){NC}")
    print(f"Encontrados: {len(isos)} ISO(s) no total | Pendentes de envio: {len(to_upload)}")
    if concurrency > 1:
        print(f"⚡ Modo Paralelo: A enviar {concurrency} ISOs em simultâneo para acelerar a transferência")
    print("=" * 60)

    if not to_upload:
        print(f"{GREEN}✅ Todos os ISOs já foram enviados para o 1fichier!{NC}")
        print("Para reenviar algum ISO, usa a opção --force.")
        return

    results = []
    success_count = 0
    fail_count = 0

    if concurrency <= 1 or len(to_upload) == 1:
        # Envio sequencial
        for i, item in enumerate(to_upload, start=1):
            print(f"\n{BOLD}[{i}/{len(to_upload)}] A processar: {item['filename']} ({format_bytes(item['size'])}){NC}")
            res = upload_iso_to_1fichier(item["path"], api_key, did=did, force=force, use_curl=use_curl)
            if res and res.get("download"):
                success_count += 1
                results.append((item["filename"], res.get("download"), "OK"))
            else:
                fail_count += 1
                results.append((item["filename"], "-", "FALHA"))

            if i < len(to_upload):
                time.sleep(2.0)
    else:
        # Envio paralelo com ThreadPoolExecutor
        def worker(item):
            time.sleep(1.0)
            res = upload_iso_to_1fichier(item["path"], api_key, did=did, force=force, use_curl=use_curl)
            return item["filename"], res

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_item = {executor.submit(worker, item): item for item in to_upload}
            for future in concurrent.futures.as_completed(future_to_item):
                item = future_to_item[future]
                try:
                    fname, res = future.result()
                    if res and res.get("download"):
                        success_count += 1
                        results.append((fname, res.get("download"), "OK"))
                    else:
                        fail_count += 1
                        results.append((fname, "-", "FALHA"))
                except Exception as e:
                    fail_count += 1
                    results.append((item["filename"], "-", f"ERRO: {e}"))

    # Resumo Final
    print(f"\n{BOLD}===================================================={NC}")
    print(f"{GREEN}{BOLD}📊 RESUMO DO ENVIO EM LOTE (1FICHIER){NC}")
    print(f"   Sucesso: {success_count} | Falhas: {fail_count} | Total: {len(to_upload)}")
    print("=" * 60)
    for fname, dlink, st in results:
        status_color = GREEN if st == "OK" else RED
        print(f" {status_color}[{st}]{NC} {BOLD}{fname}{NC} -> {dlink}")
    print(f"====================================================\n")


def organize_root_files_to_folder(api_key: str, folder_id: int = 22685921) -> int:
    """Move quaisquer ficheiros que estejam na raiz (folder_id=0) para a pasta dvds de destino."""
    try:
        req_ls = urllib.request.Request(
            'https://api.1fichier.com/v1/file/ls.cgi',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json', 'User-Agent': 'dvd-rip-1fichier/1.0'},
            data=json.dumps({'folder_id': 0}).encode()
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req_ls, timeout=10, context=ctx) as resp:
            root_files = json.loads(resp.read().decode()).get('items', [])

        urls = [f['url'] for f in root_files if f.get('url')]
        if not urls:
            return 0

        payload = {'urls': urls, 'destination_folder_id': folder_id}
        req_mv = urllib.request.Request(
            'https://api.1fichier.com/v1/file/mv.cgi',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json', 'User-Agent': 'dvd-rip-1fichier/1.0'},
            data=json.dumps(payload).encode()
        )
        with urllib.request.urlopen(req_mv, timeout=15, context=ctx) as resp:
            res = json.loads(resp.read().decode())
            return res.get('moved', len(urls))
    except Exception:
        return 0


# Menu Interativo
def show_interactive_menu(api_key: str, did: int = 0):
    """Menu CLI interativo com listagem de ISOs e opções."""
    while True:
        isos = scan_available_isos()

        print(f"\n{CYAN}{BOLD}📁 Catálogo de ISOs - Envio para 1fichier{NC}")
        print("=" * 72)

        if not isos:
            print(f"{YELLOW}Nenhum ficheiro ISO encontrado em /sdcard/ADVD ou no diretório atual.{NC}")
            print("=" * 72)
            return

        remote_ready_count = 0
        for idx, item in enumerate(isos, start=1):
            sz_str = format_bytes(item["size"])
            name = item["filename"]
            if len(name) > 28:
                name = name[:25] + "..."

            integ_str = f"{item['pct']:.1f}%" if item["pct"] >= 0 else "N/A"
            if item["link"]:
                status_str = f"{GREEN}☁️  1fichier{NC}"
            elif item.get("transfer_link"):
                remote_ready_count += 1
                status_str = f"{CYAN}{BOLD}⚡ Transfer.it (Remote){NC}"
            else:
                status_str = f"{YELLOW}⏳ Pendente (Local){NC}"

            print(f" {BOLD}{idx:2d}{NC}) {name:<28} {sz_str:>9}  [{integ_str:>6}]  {status_str}")

        print("=" * 72)
        print(f"  {CYAN}{BOLD}T{NC}) Catálogo Transfer.it -> 1fichier (Ver e enviar 1, alguns ou todos) ⚡")
        if remote_ready_count > 0:
            print(f"  {CYAN}{BOLD}R{NC}) Remote Upload de TODOS com link Transfer.it ({remote_ready_count} prontos)")
        print(f"  {BOLD}M{NC}) Mover ficheiros soltos na raiz para a pasta 'dvds'")
        print(f"  {BOLD}S{NC}) Ver estado das transferências remotas no 1fichier")
        print(f"  {BOLD}A{NC}) Enviar TODOS os pendentes (Upload local)")
        print(f"  {BOLD}C{NC}) Configurar Chave de API / Pasta")
        print(f"  {BOLD}Q{NC}) Sair")
        print("=" * 72)

        try:
            choice = input(f"{BOLD}Escolhe uma opção (número(s) ou T/R/M/S/A/C/Q): {NC}").strip().upper()
        except (EOFError, KeyboardInterrupt):
            print("\n")
            return

        if choice == "Q" or choice == "":
            return
        elif choice in ["T", "TRANSFER"]:
            manage_transferit_remote_menu(api_key, did=did)
        elif choice == "R":
            upload_all_remote_transferit_isos(api_key, did=did)
        elif choice in ["M", "MOVE", "ORGANIZE"]:
            target_folder = did if did > 0 else 22685921
            print(f"\n{CYAN}A verificar ficheiros soltos na raiz do 1fichier...{NC}")
            moved_count = organize_root_files_to_folder(api_key, target_folder)
            if moved_count > 0:
                print(f"{GREEN}✅ {moved_count} ficheiro(s) movido(s) com sucesso para a pasta dvds (ID: {target_folder})!{NC}\n")
            else:
                print(f"{YELLOW}ℹ️  Nenhum ficheiro solto na raiz do 1fichier (já está tudo organizado na pasta dvds).{NC}\n")
            try:
                input(f"{BOLD}Pressiona Enter para continuar...{NC}")
            except (EOFError, KeyboardInterrupt):
                pass
        elif choice == "S":
            show_remote_uploads_status(api_key)
        elif choice == "C":
            new_key = interactive_setup_api_key()
            if new_key:
                api_key = new_key
        elif choice == "A":
            upload_all_pending_isos(api_key, did=did)
            return
        else:
            chosen = parse_selection_indices(choice, len(isos))
            if chosen:
                selected_items = [isos[i] for i in chosen]
                for item in selected_items:
                    if item.get("transfer_link"):
                        upload_iso_via_remote_transferit(item, api_key, did=did)
                    else:
                        upload_iso_to_1fichier(item["path"], api_key, did=did)
                return

            matched = [x for x in isos if choice.lower() in x["filename"].lower()]
            if len(matched) == 1:
                item = matched[0]
                if item.get("transfer_link"):
                    upload_iso_via_remote_transferit(item, api_key, did=did)
                else:
                    upload_iso_to_1fichier(item["path"], api_key, did=did)
                return
            elif len(matched) > 1:
                print(f"{YELLOW}⚠️  Múltiplos ficheiros coincidem com '{choice}'.{NC}")
            else:
                print(f"{RED}❌ Opção ou ficheiro não encontrado.{NC}")


def main():
    parser = argparse.ArgumentParser(
        description="Utilitário de Upload de ISOs para 1fichier (dvd-rip companion)",
        add_help=True
    )
    parser.add_argument("target", nargs="?", help="Caminho do ficheiro ISO, número da sessão, link transfer.it, 'all', 'remote', 'config', 'verify', ou omitir para menu interativo")
    parser.add_argument("extra_arg", nargs="?", help="Argumento adicional opcional (ex: chave no comando config: dvd-1fichier config SUA_CHAVE)")
    parser.add_argument("--remote", "-r", action="store_true", help="Enviar ISOs com link Transfer.it via Remote Upload direto")
    parser.add_argument("--remote-status", "-rs", action="store_true", help="Ver estado dos uploads remotos no 1fichier")
    parser.add_argument("--all", "-a", action="store_true", help="Enviar todos os ISOs pendentes para a conta")
    parser.add_argument("-c", "--concurrency", type=int, default=2, help="Número de ficheiros a enviar em paralelo em lote local (padrão: 2)")
    parser.add_argument("--turbo", "--curl", action="store_true", help="Utilizar motor nativo em C (curl) para velocidade máxima de rede")
    parser.add_argument("--api-key", "-k", help="Chave de API do 1fichier (substitui a salva)")
    parser.add_argument("--folder-id", "--did", "-f", type=int, default=0, help="ID da pasta de destino no 1fichier (padrão: 0 = raiz)")
    parser.add_argument("--force", action="store_true", help="Reenviar mesmo que o ficheiro já tenha sido enviado")
    parser.add_argument("--config", action="store_true", help="Executar assistente de configuração da chave de API")
    parser.add_argument("--list", "-l", action="store_true", help="Listar ISOs e respetivos estados no 1fichier")
    parser.add_argument("--verify", action="store_true", help="Verificar validade da chave de API configurada")

    args = parser.parse_args()

    # Tratamento de sinais para libertar wake-lock
    def handle_signal(sig, frame):
        print(f"\n{RED}🛑 Upload interrompido pelo utilizador.{NC}")
        release_wake_lock()
        sys.exit(130)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Assistente de configuração
    if args.config or args.target == "config":
        if args.extra_arg:
            key_to_save = args.extra_arg.strip().strip("'\"")
            print(f"{CYAN}A validar chave fornecida...{NC}")
            info = verify_account(key_to_save)
            did = args.folder_id
            save_config(key_to_save, did)
            if info and info.get("status") == "OK":
                email = info.get("email", "Desconhecido")
                print(f"{GREEN}✅ Chave válida para a conta {BOLD}{email}{NC}! Guardada em {CONFIG_FILE_HOME}")
            else:
                print(f"{YELLOW}⚠️  Chave guardada em {CONFIG_FILE_HOME} (não foi possível confirmar online).{NC}")
            return
        interactive_setup_api_key()
        return

    # Obter chave de API
    api_key, saved_did = get_api_key(args.api_key)
    did = args.folder_id if args.folder_id > 0 else saved_did

    # Se apenas quer verificar conta
    if args.verify or args.target == "verify":
        if not api_key:
            print(f"{YELLOW}⚠️  Nenhuma chave de API configurada. Corre: dvd-1fichier config{NC}")
            sys.exit(1)
        info = verify_account(api_key)
        if info and info.get("status") == "OK":
            email = info.get("email", "Desconhecido")
            offer_id = info.get("offer", 0)
            offer_names = {0: "Gratuita / Free", 1: "Premium", 2: "Access", 3: "Premium GOLD"}
            print(f"{GREEN}✅ Chave de API 1fichier Válida!{NC}")
            print(f"   👤 Email: {BOLD}{email}{NC}")
            print(f"   ⭐ Plano: {offer_names.get(offer_id, f'Tipo {offer_id}')}")
            print(f"   📁 Pasta Destino (DID): {did}")
        else:
            msg = info.get("message", "Falha de validação") if info else "Erro"
            print(f"{RED}❌ Chave de API inválida: {msg}{NC}")
        return

    # Listagem apenas
    if args.list or args.target == "list":
        isos = scan_available_isos()
        print(f"\n{CYAN}{BOLD}📁 ISOs Disponíveis:{NC}")
        if not isos:
            print(f"{YELLOW}Nenhum ficheiro ISO encontrado em /sdcard/ADVD ou no diretório atual.{NC}")
        for idx, item in enumerate(isos, start=1):
            if item["link"]:
                st = f"{GREEN}1fichier: {item['link']}{NC}"
            elif item.get("transfer_link"):
                st = f"{CYAN}Transfer.it: {item['transfer_link']} (Pronto p/ Remote){NC}"
            else:
                st = f"{YELLOW}Pendente{NC}"
            print(f" {idx:2d}) {item['filename']} ({format_bytes(item['size'])}) -> {st}")
        return

    # Se não há chave configurada, pedir agora de forma interativa
    if not api_key:
        api_key = interactive_setup_api_key()
        if not api_key:
            print(f"{RED}❌ Chave de API necessária para associar os envios à tua conta 1fichier.{NC}")
            sys.exit(1)

    # Ver estado das transferências remotas
    if args.remote_status or args.target in ["remote-status", "status-remote", "rs", "-rs"]:
        show_remote_uploads_status(api_key)
        return

    # Catálogo Transfer.it -> 1fichier (Ver catálogo e enviar 1, alguns ou todos)
    if args.remote or args.target in ["remote", "transfer", "t", "r", "catalog"]:
        manage_transferit_remote_menu(api_key, did=did, initial_selection=args.extra_arg)
        return

    # Mover ficheiros soltos da raiz para a pasta dvds
    if args.target in ["move", "mv", "organize"]:
        target_folder = did if did > 0 else 22685921
        print(f"\n{CYAN}A verificar ficheiros soltos na raiz do 1fichier...{NC}")
        moved_count = organize_root_files_to_folder(api_key, target_folder)
        if moved_count > 0:
            print(f"{GREEN}✅ {moved_count} ficheiro(s) movido(s) com sucesso para a pasta dvds (ID: {target_folder})!{NC}\n")
        else:
            print(f"{YELLOW}ℹ️  Nenhum ficheiro solto na raiz do 1fichier (já está tudo organizado na pasta dvds).{NC}\n")
        return

    # Forçar sincronização manual do catálogo
    if args.target in ["sync", "catalog-sync"]:
        print(f"\n{CYAN}{BOLD}🔄 A forçar sincronização do catálogo em {PLAY_BASE}...{NC}")
        if trigger_catalog_sync(api_key):
            print(f"{GREEN}✅ Catálogo sincronizado com sucesso!{NC}\n")
        else:
            print(f"{RED}❌ Falha ao sincronizar catálogo.{NC}\n")
        return

    # Enviar todos via Remote Upload diretamente
    if args.target == "remote-all":
        upload_all_remote_transferit_isos(api_key, did=did)
        return

    # Se target for link direto do Transfer.it
    if args.target and ("transfer.it/t/" in args.target or len(args.target) == 12 and args.target.isalnum()):
        print(f"\n{CYAN}{BOLD}⚡ A enviar link do Transfer.it diretamente para o 1fichier...{NC}")
        res = request_remote_upload_1fichier(api_key, args.target, did=did)
        if res.get("status") == "OK":
            task_id = res.get("id")
            tunnel_url = res.get("tunnel_url")
            filename = res.get("filename")
            print(f"{GREEN}✅ Remote Upload submetido com sucesso!{NC}")
            print(f"   🆔 Tarefa: #{task_id}")
            print(f"   📁 Nome  : {filename}")
            print(f"   🌐 URL   : {tunnel_url}")

            print(f"\n   ⏳ A monitorizar progresso no 1fichier...")
            remote_res = wait_remote_upload_completion(api_key, task_id)
            if remote_res and remote_res.get("download_link"):
                dl_link = remote_res["download_link"]
                file_msg = remote_res.get("message", "")
                m_id = re.search(r'\?([a-zA-Z0-9]+)', dl_link)
                file_id = m_id.group(1) if m_id else dl_link.split("?")[-1]
                play_link = f"{PLAY_BASE}/{file_id}"

                set_file_inline(api_key, dl_link)
                trigger_catalog_sync(api_key)

                print(f"{GREEN}{BOLD}🎉 Concluído com Sucesso no 1fichier!{NC}")
                if file_msg:
                    print(f"   📊 Telemetria   : {DIM}{file_msg}{NC}")
                print(f"   📥 Link 1fichier: {BOLD}{dl_link}{NC} {CYAN}(Inline ativado ✅){NC}")
                print(f"   🎬 Stream VLC   : {GREEN}{BOLD}{play_link}{NC}")
                print(f"   💡 {YELLOW}Podes colar o link do Stream VLC diretamente no VLC para começar a ver!{NC}\n")
            elif remote_res and remote_res.get("status") == "KO":
                print(f"{RED}❌ 1fichier reportou erro: {remote_res.get('message')}{NC}\n")
            else:
                print(f"{YELLOW}ℹ️  Em execução em segundo plano no 1fichier. Consulta com: dvd-1fichier -rs{NC}\n")
        else:
            print(f"{RED}❌ Falha: {res.get('message', 'Erro')}{NC}")
        return

    # Enviar todos os ISOs pendentes localmente
    if args.all or args.target == "all":
        upload_all_pending_isos(
            api_key,
            did=did,
            force=args.force,
            concurrency=args.concurrency,
            use_curl=args.turbo
        )
        return

    # Sem argumentos -> Menu Interativo
    if not args.target:
        show_interactive_menu(api_key, did=did)
        return

    # Se argumento for número ou lista/intervalo de números (ex: 1, 1,3,5, 1-4)
    isos = scan_available_isos()
    chosen = parse_selection_indices(args.target, len(isos))
    if chosen:
        for idx in chosen:
            item = isos[idx]
            if item.get("transfer_link"):
                upload_iso_via_remote_transferit(item, api_key, did=did)
            else:
                upload_iso_to_1fichier(item["path"], api_key, did=did, force=args.force, use_curl=args.turbo)
        return

    # Se for caminho de ficheiro direto
    if os.path.isfile(args.target):
        # Verificar se tem link transfer.it
        t_link = get_existing_transfer_link(args.target)
        if t_link:
            upload_iso_via_remote_transferit({"path": args.target, "filename": Path(args.target).name, "size": os.path.getsize(args.target), "transfer_link": t_link}, api_key, did=did)
        else:
            upload_iso_to_1fichier(args.target, api_key, did=did, force=args.force, use_curl=args.turbo)
        return

    # Procurar em /sdcard/ADVD/
    cand = os.path.join("/sdcard/ADVD", args.target)
    if os.path.isfile(cand):
        t_link = get_existing_transfer_link(cand)
        if t_link:
            upload_iso_via_remote_transferit({"path": cand, "filename": Path(cand).name, "size": os.path.getsize(cand), "transfer_link": t_link}, api_key, did=did)
        else:
            upload_iso_to_1fichier(cand, api_key, did=did, force=args.force, use_curl=args.turbo)
        return

    cand_iso = cand if cand.endswith(".iso") else f"{cand}.iso"
    if os.path.isfile(cand_iso):
        t_link = get_existing_transfer_link(cand_iso)
        if t_link:
            upload_iso_via_remote_transferit({"path": cand_iso, "filename": Path(cand_iso).name, "size": os.path.getsize(cand_iso), "transfer_link": t_link}, api_key, did=did)
        else:
            upload_iso_to_1fichier(cand_iso, api_key, did=did, force=args.force, use_curl=args.turbo)
        return

    # Pesquisa parcial por nome
    isos = scan_available_isos()
    matched = [x for x in isos if args.target.lower() in x["filename"].lower()]
    if len(matched) == 1:
        item = matched[0]
        if item.get("transfer_link"):
            upload_iso_via_remote_transferit(item, api_key, did=did)
        else:
            upload_iso_to_1fichier(item["path"], api_key, did=did, force=args.force, use_curl=args.turbo)
    elif len(matched) > 1:
        print(f"{YELLOW}⚠️  Múltiplos ficheiros coincidem com '{args.target}'. Especifica o nome completo.{NC}")
        show_interactive_menu(api_key, did=did)
    else:
        print(f"{RED}❌ Ficheiro ISO '{args.target}' não encontrado.{NC}")
        show_interactive_menu(api_key, did=did)


if __name__ == "__main__":
    main()
