#!/usr/bin/env python3
"""
mega_cloud_transfer.py - Transferência ultrarrápida MEGA -> 1fichier na nuvem (Google Colab / GitHub Actions)
- Salta automaticamente ficheiros que já existam na pasta do 1fichier.
- Suporta rotação automática de IP via Tor (SOCKS5 127.0.0.1:9050) se a MEGA der erro 509 (Bandwidth Limit).
- Descarrega via curl, desencripta AES-128-CTR em disco e envia direto para o 1fichier.
- Normaliza títulos e ativa inline: 1 no 1fichier.
"""

import os
import sys
import re
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


def is_tor_running() -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        res = s.connect_ex(('127.0.0.1', 9050))
        s.close()
        return res == 0
    except Exception:
        return False


def rotate_tor_ip():
    print("🔄 A solicitar novo IP ao Tor (renovando circuito)...")
    try:
        subprocess.run("service tor restart || killall -HUP tor", shell=True, capture_output=True)
    except Exception:
        pass
    time.sleep(3)
    # Verificar novo IP
    try:
        p = subprocess.run(
            ["curl", "-s", "--socks5-hostname", "127.0.0.1:9050", "https://api.ipify.org"],
            capture_output=True, text=True, timeout=10
        )
        if p.returncode == 0 and p.stdout.strip():
            print(f"🌐 Novo IP atribuído pelo Tor: {p.stdout.strip()}")
    except Exception:
        pass


def b64url(s: str) -> bytes:
    s = s.replace('-', '+').replace('_', '/')
    while len(s) % 4 != 0:
        s += '='
    return base64.b64decode(s)


def decrypt_cbc(aes_key: bytes, data: bytes) -> bytes:
    if HAVE_CRYPTOGRAPHY:
        cipher = Cipher(algorithms.AES(aes_key), modes.CBC(b'\x00' * 16))
        return cipher.decryptor().update(data) + cipher.decryptor().finalize()
    elif HAVE_CRYPTODOME:
        return AES.new(aes_key, AES.MODE_CBC, iv=b'\x00' * 16).decrypt(data)
    else:
        raise RuntimeError("Requer 'cryptography' ou 'pycryptodome'")


def normalize_filename(filename: str) -> str:
    """Limpa tags redundantes, grupos e checksums de ficheiros de anime/séries."""
    stem, ext = os.path.splitext(filename)
    clean = re.sub(r'\[[a-fA-F0-9]{8}\]', '', stem)  # Remove CRC hashes como [2bb76809]
    clean = re.sub(r'\[[^\]]+\]', '', clean)          # Remove grupos de release [Puto+luna0099]
    clean = re.sub(r'\((?:1080p|720p|480p|2160p|4k)\)', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\s{2,}', ' ', clean).strip()
    clean = re.sub(r'\s*-\s*$', '', clean)
    if clean:
        return f"{clean}{ext}"
    return filename


def parse_mega_url(url_or_str: str) -> tuple[str, str]:
    clean = url_or_str.strip()
    m = re.search(r'([a-zA-Z0-9_-]{8,12})[#!,/]([a-zA-Z0-9_-]{20,50})', clean)
    if m:
        return m.group(1), m.group(2)
    return "", ""


def get_mega_info(file_id: str, key_str: str, use_tor: bool = False) -> dict:
    raw_key = b64url(key_str)
    aes_key = bytes(raw_key[i] ^ raw_key[i+16] for i in range(16))
    iv_upper = int.from_bytes(raw_key[16:24], byteorder='big')

    payload = [{'a': 'g', 'g': 1, 'p': file_id, 'ssl': 1}]
    data_json = json.dumps(payload)

    if use_tor and is_tor_running():
        cmd = [
            "curl", "-s", "--socks5-hostname", "127.0.0.1:9050",
            "-H", "Content-Type: application/json",
            "-H", "User-Agent: Mozilla/5.0",
            "-d", data_json,
            "https://g.api.mega.co.nz/cs?id=0"
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        res_list = json.loads(proc.stdout)
    else:
        req = urllib.request.Request(
            'https://g.api.mega.co.nz/cs?id=0',
            data=data_json.encode('utf-8'),
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        )
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

    clean_filename = normalize_filename(orig_filename)

    return {
        'file_id': file_id,
        'key_str': key_str,
        'aes_key': aes_key,
        'iv_upper': iv_upper,
        'dl_url': dl_url,
        'size': size,
        'filename': clean_filename,
        'orig_filename': orig_filename
    }


def download_mega_raw(info: dict, enc_path: str, use_tor: bool = False, max_retries: int = 5) -> bool:
    """Descarrega o ficheiro cifrado bruto da MEGA via curl (com suporte a Tor e recuperação 509)."""
    for attempt in range(1, max_retries + 1):
        cmd = ["curl", "-L", "-s", "-S", "--fail", "--connect-timeout", "20", "--retry", "2", "-o", enc_path]
        if use_tor and is_tor_running():
            cmd.extend(["--socks5-hostname", "127.0.0.1:9050"])
        cmd.append(info['dl_url'])

        print(f"📥 A descarregar da MEGA{' [via Tor]' if use_tor else ''} ({info['size']/(1024*1024):.1f} MB)...")
        start_t = time.time()
        p = subprocess.run(cmd, capture_output=True, text=True)

        if p.returncode == 0 and os.path.exists(enc_path) and os.path.getsize(enc_path) > 0:
            elapsed = time.time() - start_t
            spd = (os.path.getsize(enc_path) / (1024 * 1024)) / (elapsed if elapsed > 0 else 0.01)
            print(f"✅ Download MEGA concluído em {elapsed:.1f}s ({spd:.1f} MB/s)")
            return True

        err_out = p.stderr.lower()
        if "509" in err_out or "bandwidth" in err_out or "limit" in err_out:
            print(f"⚠️  MEGA Bandwidth Limit (509) detectado na tentativa {attempt}/{max_retries}!")
            if is_tor_running():
                rotate_tor_ip()
                use_tor = True
                time.sleep(3)
                continue
            else:
                print("💡 Tor não está ativo. A ativar rotação ou aguardar...")
                time.sleep(10)
        else:
            print(f"⚠️  Erro no curl ({p.returncode}): {p.stderr.strip()[:150]}")
            if is_tor_running():
                rotate_tor_ip()
            time.sleep(5)

    return False


def decrypt_file_ctr(enc_path: str, dec_path: str, aes_key: bytes, iv_upper: int):
    """Desencripta AES-128-CTR do ficheiro bruto em disco para o ficheiro final."""
    print("🔓 A desencriptar AES-128-CTR...")
    start_t = time.time()
    iv_int = iv_upper << 64

    if HAVE_CRYPTOGRAPHY:
        iv_bytes = iv_int.to_bytes(16, byteorder='big')
        cipher = Cipher(algorithms.AES(aes_key), modes.CTR(iv_bytes))
        decryptor = cipher.decryptor()
        def dec(c): return decryptor.update(c)
    elif HAVE_CRYPTODOME:
        ctr = Counter.new(128, initial_value=iv_int)
        cipher = AES.new(aes_key, AES.MODE_CTR, counter=ctr)
        def dec(c): return cipher.decrypt(c)
    else:
        raise RuntimeError("Sem biblioteca de criptografia")

    with open(enc_path, "rb") as fin, open(dec_path, "wb") as fout:
        while True:
            chunk = fin.read(512 * 1024)
            if not chunk:
                break
            fout.write(dec(chunk))

    elapsed = time.time() - start_t
    print(f"✅ Desencriptado com sucesso em {elapsed:.2f}s!")


def get_1fichier_upload_node(api_key: str, retries: int = 5) -> tuple[str, str]:
    url = "https://api.1fichier.com/v1/upload/get_upload_server.cgi"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "CloudMegaTransfer/1.0"
    }
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=b"{}", headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if "url" in data and "id" in data:
                    return data["url"], data["id"]
                if "message" in data and "flood" in str(data["message"]).lower():
                    print(f"⚠️  Flood detectado na 1fichier, a aguardar 60s (tentativa {attempt}/{retries})...")
                    time.sleep(60)
                    continue
        except urllib.error.HTTPError as e:
            if e.code in (429, 403) or "flood" in str(e).lower():
                print(f"⚠️  HTTP {e.code} / Flood na 1fichier, a aguardar 60s...")
                time.sleep(60)
                continue
            time.sleep(5)
    raise RuntimeError("Falha ao obter servidor de upload no 1fichier")


def upload_to_1fichier(file_path: str, filename: str, api_key: str, did: int) -> dict:
    file_size = os.path.getsize(file_path)
    print(f"📡 A alocar nó dedicado 1fichier...")
    node_host, upload_id = get_1fichier_upload_node(api_key)
    print(f"🚀 A enviar '{filename}' ({file_size / (1024*1024):.1f} MB) para {node_host}...")

    start_time = time.time()
    upload_url = f"https://{node_host}/upload.cgi?id={upload_id}"

    cmd = [
        "curl", "-s", "-S",
        "--tcp-nodelay",
        "-H", f"Authorization: Bearer {api_key}",
        "-F", f"file[]=@{file_path};filename={filename}"
    ]
    if did > 0:
        cmd.extend(["-F", f"did={did}"])
    cmd.append(upload_url)

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Erro no curl upload: {proc.stderr}")

    elapsed = time.time() - start_time
    speed = (file_size / (1024 * 1024)) / (elapsed if elapsed > 0 else 0.01)
    print(f"✅ Upload 1fichier concluído em {elapsed:.1f}s ({speed:.1f} MB/s)")

    time.sleep(2)
    end_url = f"https://{node_host}/end.pl?xid={upload_id}"
    req_end = urllib.request.Request(
        end_url,
        headers={"Authorization": f"Bearer {api_key}", "JSON": "1", "User-Agent": "CloudMegaTransfer/1.0"}
    )
    with urllib.request.urlopen(req_end, timeout=60) as resp:
        raw_report = resp.read().decode('utf-8', errors='ignore')

    report_json = json.loads(raw_report)
    if "links" in report_json and report_json["links"]:
        return report_json["links"][0]
    raise RuntimeError(f"1fichier não devolveu links válidos: {raw_report}")


def set_1fichier_inline(api_key: str, download_url: str):
    m = re.search(r'\?([a-zA-Z0-9]+)', download_url)
    if not m:
        return
    file_id = m.group(1)

    url = "https://api.1fichier.com/v1/file/chattr.cgi"
    payload = json.dumps({"urls": [download_url], "inline": 1}).encode('utf-8')
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "CloudMegaTransfer/1.0"
    }
    try:
        req = urllib.request.Request(url, data=payload, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            print(f"🎬 Inline ativado para {file_id}: {data.get('status', 'OK')}")
    except Exception:
        # Se falhar porque já era 1, é perfeitamente normal
        pass


def get_existing_1fichier_files(api_key: str, folder_id: int) -> set[str]:
    """Obtém conjunto de ficheiros já existentes na pasta do 1fichier para evitar downloads repetidos."""
    try:
        url = "https://api.1fichier.com/v1/file/ls.cgi"
        payload = json.dumps({"folder_id": folder_id}).encode('utf-8')
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "CloudMegaTransfer/1.0"
        }
        req = urllib.request.Request(url, data=payload, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return {item.get("filename", "") for item in data.get("items", [])}
    except Exception as e:
        print(f"⚠️  Aviso ao listar ficheiros existentes: {e}")
        return set()


def main():
    api_key = os.environ.get("FICHIER_API_KEY", "").strip()
    if not api_key:
        cfg = os.path.expanduser("~/.1fichier_token")
        if os.path.exists(cfg):
            with open(cfg, "r") as f:
                c = f.read().strip()
                try:
                    api_key = json.loads(c).get("api_key", "")
                except Exception:
                    api_key = c

    if not api_key:
        print("❌ FICHIER_API_KEY não configurada!")
        sys.exit(1)

    folder_id = int(os.environ.get("FOLDER_ID", "22726720")) # Padrão: Sailor Moon Crystal
    links_raw = os.environ.get("MEGA_LINKS", "")

    if len(sys.argv) > 1:
        links_raw = "\n".join(sys.argv[1:])

    links = [line.strip() for line in links_raw.splitlines() if line.strip() and not line.strip().startswith("#")]

    if not links:
        print("ℹ️  Nenhum link MEGA fornecido.")
        return

    # Verificar se Tor está disponível para rotação de IP
    use_tor = is_tor_running()
    if use_tor:
        print("🧅 Tor SOCKS5 detetado em 127.0.0.1:9050! Rotação automática de IP ativa.")
    else:
        print("ℹ️  Tor não detetado (a usar ligação direta).")

    # Obter ficheiros já existentes na pasta do 1fichier
    print("🔍 A verificar ficheiros já existentes na pasta do 1fichier...")
    existing = get_existing_1fichier_files(api_key, folder_id)
    print(f"📂 Ficheiros já presentes no 1fichier ({len(existing)}):")
    for f in sorted(existing):
        print(f"   ✓ {f}")

    print(f"\n🚀 A iniciar processamento de {len(links)} link(s) MEGA...")
    work_dir = Path("/tmp/mega_transfer")
    work_dir.mkdir(parents=True, exist_ok=True)

    success_count = 0

    for idx, link in enumerate(links, 1):
        file_id, key_str = parse_mega_url(link)
        if not file_id or not key_str:
            print(f"⚠️  Link inválido ignorado: {link}")
            continue

        print(f"\n[{idx}/{len(links)}] ==================================================")
        try:
            info = get_mega_info(file_id, key_str, use_tor=use_tor)

            # SALVAGUARDA: Se já foi enviado para o 1fichier, não gastar quota da MEGA!
            if info['filename'] in existing:
                print(f"⏩ {info['filename']} já existe no 1fichier! A saltar...")
                continue

            enc_file = work_dir / f"{file_id}.raw"
            dec_file = work_dir / info['filename']

            # 1. Download bruto da MEGA (com recuperação e rotação de IP se 509)
            ok = download_mega_raw(info, str(enc_file), use_tor=use_tor)
            if not ok:
                print(f"❌ Falha no download da MEGA para {info['filename']}")
                continue

            # 2. Desencriptação local
            decrypt_file_ctr(str(enc_file), str(dec_file), info['aes_key'], info['iv_upper'])
            if enc_file.exists():
                enc_file.unlink()

            # 3. Upload para o 1fichier (direto a 1 Gbps)
            res = upload_to_1fichier(str(dec_file), info['filename'], api_key, folder_id)
            dl_link = res.get("download", "")
            print(f"🎉 Link 1fichier: {dl_link}")

            # 4. Ativar inline
            if dl_link:
                set_1fichier_inline(api_key, dl_link)

            # Adicionar aos já existentes
            existing.add(info['filename'])
            success_count += 1

            # 5. Limpar ficheiro desencriptado
            if dec_file.exists():
                dec_file.unlink()

            time.sleep(2)

        except Exception as e:
            print(f"❌ Erro ao processar {link}: {e}")
            continue

    print(f"\n✨ Ciclo terminado: {success_count} novos ficheiro(s) transferidos com sucesso!")


if __name__ == "__main__":
    main()
