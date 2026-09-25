#!/usr/bin/env python3
"""
mega_cloud_transfer.py - Transferência ultrarrápida MEGA -> 1fichier na nuvem (GitHub Actions)
- Descarrega diretamente da rede MEGA CDN e desencripta AES-128-CTR.
- Normaliza o nome do ficheiro (remove tags [Puto+luna0099][CRC], mantendo título e episódio).
- Envia diretamente para o cluster de upload da 1fichier (via curl turbo / multipart).
- Ativa a flag inline: 1 no 1fichier para reprodução imediata.
- Notifica o catálogo play.hospidy.com.
"""

import os
import sys
import re
import json
import time
import base64
import ssl
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


def get_mega_info(file_id: str, key_str: str) -> dict:
    raw_key = b64url(key_str)
    aes_key = bytes(raw_key[i] ^ raw_key[i+16] for i in range(16))
    iv_upper = int.from_bytes(raw_key[16:24], byteorder='big')

    payload = [{'a': 'g', 'g': 1, 'p': file_id, 'ssl': 1}]
    req = urllib.request.Request(
        'https://g.api.mega.co.nz/cs?id=0',
        data=json.dumps(payload).encode('utf-8'),
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


def download_and_decrypt_mega(info: dict, dest_path: str):
    print(f"📥 A descarregar e desencriptar da MEGA: {info['filename']} ({info['size'] / (1024*1024):.1f} MB)...")
    req = urllib.request.Request(info['dl_url'], headers={"User-Agent": "Mozilla/5.0"})
    
    iv_int = info['iv_upper'] << 64
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
        raise RuntimeError("Sem módulo de criptografia disponível")

    start_time = time.time()
    downloaded = 0
    buf_size = 256 * 1024

    with urllib.request.urlopen(req, timeout=30) as remote, open(dest_path, "wb") as local_f:
        while True:
            chunk = remote.read(buf_size)
            if not chunk:
                break
            local_f.write(decrypt_chunk(chunk))
            downloaded += len(chunk)

    elapsed = time.time() - start_time
    speed = (downloaded / (1024 * 1024)) / (elapsed if elapsed > 0 else 0.01)
    print(f"✅ Download & Desencriptação concluídos: {downloaded / (1024*1024):.1f} MB em {elapsed:.1f}s ({speed:.1f} MB/s)")


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
    print(f"📡 A obter servidor dedicado 1fichier...")
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
        raise RuntimeError(f"Erro no curl: {proc.stderr}")

    elapsed = time.time() - start_time
    speed = (file_size / (1024 * 1024)) / (elapsed if elapsed > 0 else 0.01)
    print(f"✅ Upload concluído em {elapsed:.1f}s ({speed:.1f} MB/s)")

    # Aguardar processamento do nó
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
    except Exception as e:
        print(f"⚠️  Não foi possível definir inline para {file_id}: {e}")


def notify_catalog_sync():
    try:
        req = urllib.request.Request(
            "https://play.hospidy.com/sync",
            data=b"{}",
            headers={"Content-Type": "application/json", "User-Agent": "CloudMegaTransfer/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            print("🔄 Sincronização do catálogo play.hospidy.com acionada!")
    except Exception:
        pass


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

    print(f"🚀 A iniciar processamento de {len(links)} ficheiro(s)...")
    work_dir = Path("/tmp/mega_transfer")
    work_dir.mkdir(parents=True, exist_ok=True)

    success_count = 0

    for idx, link in enumerate(links, 1):
        file_id, key_str = parse_mega_url(link)
        if not file_id or not key_str:
            print(f"⚠️  Link inválido ignorado: {link}")
            continue

        print(f"\n[{idx}/{len(links)}] ----------------------------------------")
        try:
            info = get_mega_info(file_id, key_str)
            dest_file = work_dir / info['filename']

            # 1. Download & Decrypt da MEGA
            download_and_decrypt_mega(info, str(dest_file))

            # 2. Upload para 1fichier
            res = upload_to_1fichier(str(dest_file), info['filename'], api_key, folder_id)
            dl_link = res.get("download", "")
            print(f"🎉 Link 1fichier: {dl_link}")

            # 3. Ativar inline
            if dl_link:
                set_1fichier_inline(api_key, dl_link)

            success_count += 1

            # 4. Apagar ficheiro temporário
            if dest_file.exists():
                dest_file.unlink()

            # Pausa de cortesia anti-rate-limit 1fichier
            time.sleep(2)

        except Exception as e:
            print(f"❌ Erro ao processar {link}: {e}")
            continue

    if success_count > 0:
        notify_catalog_sync()
        print(f"\n✨ Concluído com sucesso: {success_count}/{len(links)} ficheiros enviados!")


if __name__ == "__main__":
    main()
