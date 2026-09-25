#!/data/data/com.termux/files/usr/bin/python3
import os
import sys
import subprocess
import json
import urllib.request
import urllib.parse
import hashlib
import base64
import re
import time
import ssl

PREFIX = os.environ.get("PREFIX", "/data/data/com.termux/files/usr")
HOME = os.environ.get("HOME", "/data/data/com.termux/files/home")

CD_ENGINE = os.path.join(PREFIX, "bin", "cd_engine")
if not os.path.isfile(CD_ENGINE):
    CD_ENGINE = os.path.join(HOME, "cd_engine")

PLAY_BASE = "https://play.hospidy.com"

BOLD = "\033[1m"
GREEN = "\033[0;32m"
CYAN = "\033[0;36m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
NC = "\033[0m"

def sanitize_filename(name):
    clean = re.sub(r'[\\/*?:"<>|]', "", name)
    clean = clean.strip(" .")
    return clean if clean else "Track"

def get_usb_device():
    try:
        out = subprocess.check_output(["termux-usb", "-l"], text=True, timeout=3)
        devs = json.loads(out)
        if devs and isinstance(devs, list):
            return devs[0]
    except Exception:
        pass
    return None

def calculate_discid(first_track, last_track, lead_out_lba, tracks):
    lead_out_mb = lead_out_lba + 150
    offsets_mb = [t["start"] + 150 for t in tracks]
    s = f"{first_track:02X}{last_track:02X}{lead_out_mb:08X}"
    for o in offsets_mb:
        s += f"{o:08X}"
    for _ in range(100 - len(offsets_mb) - 1):
        s += f"{0:08X}"
    h = hashlib.sha1(s.encode("ascii")).digest()
    discid = base64.b64encode(h).decode("ascii").replace("+", ".").replace("/", "_").replace("=", "-")
    toc_str = f"{first_track} {last_track} {lead_out_mb} " + " ".join(str(o) for o in offsets_mb)
    return discid, toc_str

def fetch_musicbrainz(discid, toc_str):
    url = f"https://musicbrainz.org/ws/2/discid/{discid}?toc={urllib.parse.quote(toc_str)}&inc=recordings+artists&fmt=json"
    req = urllib.request.Request(url, headers={"User-Agent": "AntigravityAudioCD/1.0 (fabio@android)"})
    try:
        with urllib.request.urlopen(req, timeout=7) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            releases = data.get("releases", [])
            if releases:
                rel = releases[0]
                title = rel.get("title", "Unknown Album")
                artist = rel.get("artist-credit", [{}])[0].get("name", "Unknown Artist")
                date = rel.get("date", "")
                track_titles = {}
                for m in rel.get("media", []):
                    for trk in m.get("tracks", []):
                        pos = trk.get("position")
                        t_title = trk.get("title")
                        if pos and t_title:
                            track_titles[pos] = t_title
                return artist, title, date, track_titles
    except Exception:
        pass
    return None, None, None, {}

def get_musica_folder_id(api_key):
    """Encontra ou cria a pasta 'Musica' na raiz da conta 1fichier."""
    ctx = ssl.create_default_context()
    try:
        req = urllib.request.Request(
            "https://api.1fichier.com/v1/folder/ls.cgi",
            data=json.dumps({"folder_id": 0}).encode(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode())
            for sf in data.get("sub_folders", []):
                name = sf.get("name", "").lower()
                if name in ["musica", "música", "music"]:
                    return sf.get("id")
    except Exception:
        pass

    # Tenta criar a pasta se não existir
    try:
        req_mk = urllib.request.Request(
            "https://api.1fichier.com/v1/folder/mkdir.cgi",
            data=json.dumps({"name": "Musica", "folder_id": 0}).encode(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_mk, timeout=10, context=ctx) as resp:
            res = json.loads(resp.read().decode())
            return res.get("folder_id", 22692435)
    except Exception:
        return 22692435

def get_or_create_album_folder(api_key, parent_id, album_name):
    """Obtém ou cria uma subpasta para o álbum dentro da pasta de Música."""
    ctx = ssl.create_default_context()
    try:
        req = urllib.request.Request(
            "https://api.1fichier.com/v1/folder/ls.cgi",
            data=json.dumps({"folder_id": parent_id}).encode(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode())
            for sf in data.get("sub_folders", []):
                if sf.get("name") == album_name:
                    return sf.get("id")
    except Exception:
        pass

    try:
        req_mk = urllib.request.Request(
            "https://api.1fichier.com/v1/folder/mkdir.cgi",
            data=json.dumps({"name": album_name, "folder_id": parent_id}).encode(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_mk, timeout=10, context=ctx) as resp:
            res = json.loads(resp.read().decode())
            return res.get("folder_id")
    except Exception:
        return None

def get_folder_files(api_key, folder_id):
    """Lista os ficheiros existentes numa pasta do 1fichier."""
    ctx = ssl.create_default_context()
    try:
        req = urllib.request.Request(
            "https://api.1fichier.com/v1/file/ls.cgi",
            data=json.dumps({"folder_id": folder_id}).encode(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode())
            res = {}
            for it in data.get("items", []):
                res[it.get("filename")] = it.get("url")
            return res
    except Exception:
        return {}

def set_files_inline(api_key, urls):
    """Ativa inline=1 para todos os URLs passados."""
    if not urls:
        return
    ctx = ssl.create_default_context()
    try:
        req = urllib.request.Request(
            "https://api.1fichier.com/v1/file/chattr.cgi",
            data=json.dumps({"urls": urls, "inline": 1}).encode(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            pass
    except Exception:
        pass

def trigger_catalog_sync(api_key):
    """Dispara a sincronização da mediateca com o hospidy."""
    ctx = ssl.create_default_context()
    try:
        req = urllib.request.Request(
            f"{PLAY_BASE}/sync",
            data=b"{}",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "dvd-rip-1fichier/1.0"}
        )
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read().decode())
            total = data.get("total_files", 0)
            print(f"\n{GREEN}✅ Catálogo play.hospidy.com sincronizado com sucesso ({total} ficheiros)!{NC}")
    except Exception as e:
        print(f"\n{YELLOW}⚠️  Aviso ao sincronizar catálogo hospidy: {e}{NC}")

def upload_album_to_1fichier(out_dir, artist, album, zip_path=None):
    """Cria a pasta do álbum em 'Musica', envia todas as faixas FLAC, M3U e ZIP com inline=1."""
    try:
        sys.path.insert(0, HOME)
        sys.path.insert(0, os.path.join(PREFIX, "bin"))
        import dvd_1fichier as f1
    except ImportError:
        f1 = None

    cfg = f1.load_config() if f1 and hasattr(f1, "load_config") else {}
    api_key = cfg.get("api_key")
    if not api_key or api_key == "api_key":
        api_key = "ziK1L_3nBri0nZ=yxRfSA8GJpMkaFPW0"

    print(f"\n{CYAN}{BOLD}☁️  A iniciar upload do álbum para o 1fichier (Pasta Música)...{NC}")
    musica_folder_id = get_musica_folder_id(api_key)
    album_folder_name = f"{artist} - {album}"
    album_folder_id = get_or_create_album_folder(api_key, musica_folder_id, album_folder_name)
    target_did = album_folder_id if album_folder_id else musica_folder_id

    print(f"📁 Pasta 1fichier       : {BOLD}{album_folder_name}{NC} (ID: {target_did})")

    existing = get_folder_files(api_key, target_did)

    files_to_send = sorted([os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.endswith(".flac") or f.endswith(".m3u")])
    if zip_path and os.path.isfile(zip_path):
        files_to_send.append(zip_path)

    to_upload = [fp for fp in files_to_send if os.path.basename(fp) not in existing]
    print(f"📦 Ficheiros já no servidor : {len(existing)}")
    print(f"🚀 Ficheiros a enviar       : {len(to_upload)}\n")

    t_start = time.time()
    for idx, fp in enumerate(to_upload, start=1):
        fn = os.path.basename(fp)
        sz = os.path.getsize(fp) / (1024 * 1024)
        print(f"[{idx}/{len(to_upload)}] 🚀 {BOLD}{fn}{NC} ({sz:.1f} MB)... ", end="", flush=True)
        t_fstart = time.time()

        try:
            node_host, upload_id = f1.get_upload_node(api_key)
        except Exception:
            time.sleep(3)
            node_host, upload_id = f1.get_upload_node(api_key)

        upload_url = f"https://{node_host}/upload.cgi?id={upload_id}"
        cmd = [
            "curl", "-s",
            "--tcp-nodelay",
            "-H", f"Authorization: Bearer {api_key}",
            "-F", f"did={target_did}",
            "-F", f"file[]=@{fp};filename={fn}",
            upload_url
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        dt = time.time() - t_fstart
        speed = sz / dt if dt > 0 else 0
        print(f"{GREEN}OK{NC} ({dt:.1f}s - {speed:.1f} MB/s)")
        time.sleep(0.5)

    # Ativar inline: 1
    print(f"\n⚙️  A ativar modo 'inline: 1' para todas as faixas...")
    final_files = get_folder_files(api_key, target_did)
    set_files_inline(api_key, list(final_files.values()))

    # Sincronizar catálogo
    trigger_catalog_sync(api_key)

    total_time = time.time() - t_start
    print(f"\n{BOLD}======================================================================{NC}")
    print(f"🎉 {GREEN}{BOLD}TODAS AS {len(final_files)} FAIXAS ESTÃO NO 1FICHIER E PRONTAS PARA STREAMING!{NC}")
    print(f"   Tempo total de envio: {int(total_time//60):02d}:{int(total_time%60):02d}")
    print(f"   Pasta: {PLAY_BASE}/{album_folder_name}/")
    print(f"======================================================================\n")

def print_help():
    print(f"""{BOLD}Uso:{NC} cd-rip [OPÇÕES]

{BOLD}Opções:{NC}
  --1fichier   Envia cada faixa FLAC + M3U + ZIP para a pasta 'Musica' no 1fichier com streaming ativado
  --upload     Envia o arquivo ZIP completo do álbum para o Transfer.it
  --all        Executa extração + 1fichier + Transfer.it + ejeção automática
  --eject      Ejeta o tabuleiro do leitor de CD após a conclusão
  -h, --help   Mostra esta mensagem de ajuda
""")

def main():
    if "-h" in sys.argv or "--help" in sys.argv:
        print_help()
        sys.exit(0)

    print(f"{BOLD}======================================================================{NC}")
    print(f"  {CYAN}{BOLD}cd-rip v1.1{NC}: Extrator de CD de Áudio para FLAC (Lossless Bit-Perfect)")
    print(f"{BOLD}======================================================================{NC}")

    dev = get_usb_device()
    if not dev:
        print(f"{RED}❌ ERRO: Leitor ótico USB não detetado!{NC}")
        print("Verifica a ligação USB-C OTG.")
        sys.exit(1)

    print(f"✅ Leitor USB detetado : {GREEN}{dev}{NC}")

    # Test permission
    perm_test = subprocess.run(["termux-usb", "-e", "echo OK", dev], capture_output=True, text=True)
    if "OK" not in perm_test.stdout:
        print(f"\n{YELLOW}📱 A solicitar autorização USB no ecrã do Android...{NC}")
        print(f"👉 {BOLD}Clica em 'OK' / 'Permitir' no telemóvel para avançar.{NC}")
        subprocess.run(["am", "start", "-n", "com.termux/.app.TermuxActivity"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["termux-usb", "-r", "-e", "echo OK", dev], capture_output=True, text=True)

    # Read TOC
    toc_script = os.path.join(HOME, "_run_toc.sh")
    with open(toc_script, "w") as f:
        f.write(f"#!/bin/bash\nexec {CD_ENGINE} \"$1\" toc\n")
    os.chmod(toc_script, 0o755)

    proc = subprocess.run(["termux-usb", "-e", toc_script, dev], capture_output=True, text=True)
    try:
        toc_json_str = proc.stdout.strip()
        idx = toc_json_str.find("{")
        if idx >= 0:
            toc = json.loads(toc_json_str[idx:])
        else:
            raise ValueError("No JSON found")
    except Exception as e:
        print(f"{RED}❌ Erro ao ler a Tabela de Conteúdos (TOC) do CD.{NC}")
        print(proc.stdout)
        print(proc.stderr)
        sys.exit(1)

    num_tracks = toc["tracks"]
    lead_out = toc["lead_out"]
    track_list = toc["track_list"]

    audio_tracks = [t for t in track_list if t.get("audio", False)]
    print(f"💿 Faixas de áudio     : {BOLD}{len(audio_tracks)}{NC} de {num_tracks} faixas totais")

    # Metadata lookup
    print(f"🌐 A consultar metadados no MusicBrainz...", end="", flush=True)
    discid, toc_str = calculate_discid(1, num_tracks, lead_out, track_list)
    artist, album, date, mb_titles = fetch_musicbrainz(discid, toc_str)

    if not artist or not album:
        print(f" {YELLOW}[Não encontrado]{NC}")
        artist = "Unknown Artist"
        album = f"CD_Album_{time.strftime('%Y%m%d_%H%M%S')}"
        date = ""
    else:
        print(f" {GREEN}[Identificado]{NC}")

    print(f"🎵 Artista             : {BOLD}{artist}{NC}")
    print(f"📀 Álbum               : {BOLD}{album}{NC} ({date if date else 'Ano N/D'})")

    # Output directory
    safe_artist = sanitize_filename(artist)
    safe_album = sanitize_filename(album)
    out_dir = f"/sdcard/ADVD/Musica/{safe_artist}/{safe_album}"
    music_dir = f"/sdcard/Music/{safe_artist}/{safe_album}"
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(music_dir, exist_ok=True)
    print(f"📂 Pasta de Destino    : {CYAN}{out_dir}{NC}\n")

    # Create job TSV file
    job_file = os.path.join(HOME, "cd_job.tsv")
    playlist_files = []
    with open(job_file, "w") as jf:
        for t in audio_tracks:
            t_num = t["num"]
            t_title = mb_titles.get(t_num, f"Track {t_num:02d}")
            safe_title = sanitize_filename(t_title)
            flac_filename = f"{t_num:02d} - {safe_title}.flac"
            flac_path = os.path.join(out_dir, flac_filename)
            playlist_files.append(flac_filename)
            jf.write(f"{t_num}\t{artist}\t{album}\t{t_title}\t{date}\t{flac_path}\n")

    # Write M3U playlist
    m3u_path = os.path.join(out_dir, f"{safe_artist} - {safe_album}.m3u")
    with open(m3u_path, "w") as mf:
        mf.write("#EXTM3U\n")
        for fn in playlist_files:
            mf.write(f"{fn}\n")

    # Create rip runner script
    rip_script = os.path.join(HOME, "_run_rip.sh")
    do_eject = "--eject" if ("--eject" in sys.argv or "--all" in sys.argv) else ""
    with open(rip_script, "w") as f:
        f.write(f"#!/bin/bash\nexec {CD_ENGINE} \"$1\" rip \"{job_file}\" {do_eject}\n")
    os.chmod(rip_script, 0o755)

    # Launch ripping engine via termux-usb
    print(f"{BOLD}A iniciar motor de extração em tempo real...{NC}")
    subprocess.run(["termux-usb", "-e", rip_script, dev])

    # Mirror to /sdcard/Music for Android media players
    try:
        import shutil
        for f in os.listdir(out_dir):
            src = os.path.join(out_dir, f)
            dst = os.path.join(music_dir, f)
            if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src):
                shutil.copy2(src, dst)
    except Exception:
        pass

    # Automatically create album ZIP for sharing / backup
    zip_name = f"{safe_artist}_{safe_album}_{date if date else 'CD'}_FLAC.zip"
    zip_path = f"/sdcard/ADVD/{zip_name}"
    try:
        import zipfile
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
            for root, dirs, files in os.walk(out_dir):
                for f in sorted(files):
                    fp = os.path.join(root, f)
                    arc = os.path.join(f"{safe_artist} - {safe_album}", f)
                    zf.write(fp, arc)
        print(f"📦 Arquivo ZIP gerado em /sdcard/ADVD/: {BOLD}{zip_name}{NC}")
    except Exception as e:
        print(f"{YELLOW}Aviso ao gerar ZIP: {e}{NC}")

    # Cloud uploads
    if "--upload" in sys.argv or "--all" in sys.argv:
        print(f"\n☁️  A iniciar upload automático para o Transfer.it...")
        up_tr = os.path.join(PREFIX, "bin", "dvd_upload.py")
        if not os.path.isfile(up_tr):
            up_tr = os.path.join(HOME, "dvd_upload.py")
        if os.path.isfile(up_tr) and os.path.exists(zip_path):
            subprocess.run(["python3", up_tr, zip_path])

    if "--1fichier" in sys.argv or "--all" in sys.argv:
        upload_album_to_1fichier(out_dir, safe_artist, safe_album, zip_path=zip_path)

    print(f"\n{BOLD}======================================================================{NC}")
    print(f"🎉 {GREEN}{BOLD}PROCESSO DE EXTRAÇÃO CONCLUÍDO!{NC}")
    print(f"   Ficheiros locais : {CYAN}{out_dir}{NC}")
    print(f"   WebDAV local     : {CYAN}http://192.168.86.26:8080/Musica/{safe_artist}/{safe_album}/{NC}")
    print(f"======================================================================\n")

if __name__ == "__main__":
    main()
