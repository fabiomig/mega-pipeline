#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import urllib.request
from generate_universal_kodi_library import generate_library

PI_SSH = "pi"
STAGING_DIR = "/tmp/kodi_staging"

def run_cmd(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)

def main():
    print("🚀 A iniciar sincronização e geração universal para o Projetor...")
    if os.path.exists(STAGING_DIR):
        shutil.rmtree(STAGING_DIR)
    os.makedirs(STAGING_DIR, exist_ok=True)

    # 1. Obter catálogos e metadados mais recentes do Raspberry Pi
    print("📥 A sincronizar catalog_cache.json e metadata_cache.json do Raspberry Pi...")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    subprocess.run(["scp", f"{PI_SSH}:/home/fabio/transfer-tunnel/catalog_cache.json", script_dir], check=True)
    subprocess.run(["scp", f"{PI_SSH}:/home/fabio/transfer-tunnel/metadata_cache.json", script_dir], check=True)

    # 2. Gerar biblioteca com deduplicação, filtro de ficheiros truncados e metadados ricos
    print("🎬 A gerar biblioteca universal com deduplicação e filtro de integridade...")
    stats = generate_library(STAGING_DIR)
    print(f"📊 Estatísticas geradas: {stats}")

    # 3. Verificar ligação ADB ao Projetor
    print("📡 A ligar ao Projetor (192.168.86.41:5555)...")
    adb_conn = run_cmd("adb connect 192.168.86.41:5555")
    print(f"ADB: {adb_conn.stdout.strip()}")

    # Verificar se adb responde
    adb_devices = run_cmd("adb devices").stdout
    if "192.168.86.41:5555" not in adb_devices or "device" not in adb_devices:
        print("⚠️ O Projetor não está contactável no momento (provavelmente em standby/sleep).")
        print("👉 Assim que ligares o projetor, podes correr: ./sync_and_fix_all.py")
        return

    # 4. Atualizar Skin (Variables.xml, View_Vault_TechList.xml, Includes.xml)
    print("🎨 A atualizar ficheiros da skin 'The Archive'...")
    skin_remote = "/sdcard/Android/data/org.xbmc.kodi/files/.kodi/addons/skin.thearchive/xml"
    run_cmd(f"adb push {os.path.join(script_dir, 'skin.thearchive/xml/Variables.xml')} {skin_remote}/Variables.xml")
    run_cmd(f"adb push {os.path.join(script_dir, 'skin.thearchive/xml/View_Vault_TechList.xml')} {skin_remote}/View_Vault_TechList.xml")
    run_cmd(f"adb push {os.path.join(script_dir, 'skin.thearchive/xml/Includes.xml')} {skin_remote}/Includes.xml")

    # 5. Remover ficheiros corrompidos ou mal categorizados no Projetor (ex: Idade do Gelo truncado, Bluey na pasta Kids)
    print("🧹 A remover ficheiros corrompidos ou mal categorizados do Projetor...")
    run_cmd("adb shell \"rm -rf '/sdcard/KodiMedia/Kids/A Idade do Gelo'* '/sdcard/KodiMedia/Kids/Idade Do Gelo'* '/sdcard/KodiMedia/Kids/Bluey'*\"")

    # 6. Empacotar e enviar via tar stream directo para Android
    print("🚀 A transferir novos ficheiros de biblioteca para o Projetor...")
    run_cmd(f"tar -czf /tmp/kodi_media.tar.gz -C {STAGING_DIR} .")
    run_cmd("adb push /tmp/kodi_media.tar.gz /sdcard/kodi_media.tar.gz")
    print("📦 A extrair no Projetor...")
    run_cmd("adb shell 'tar -xzf /sdcard/kodi_media.tar.gz -C /sdcard/KodiMedia/ && rm /sdcard/kodi_media.tar.gz && chmod -R 777 /sdcard/KodiMedia'")

    # 7. Disparar Limpeza e Atualização das bibliotecas no Kodi
    print("🔄 A disparar VideoLibrary.Clean e VideoLibrary.Scan via JSON-RPC...")
    try:
        req_clean = urllib.request.Request("http://192.168.86.41:8080/jsonrpc", data=json.dumps({"jsonrpc": "2.0", "method": "VideoLibrary.Clean", "id": 1}).encode(), headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req_clean, timeout=5)
        
        req_v = urllib.request.Request("http://192.168.86.41:8080/jsonrpc", data=json.dumps({"jsonrpc": "2.0", "method": "VideoLibrary.Scan", "id": 2}).encode(), headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req_v, timeout=5)

        req_m = urllib.request.Request("http://192.168.86.41:8080/jsonrpc", data=json.dumps({"jsonrpc": "2.0", "method": "AudioLibrary.Scan", "id": 3}).encode(), headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req_m, timeout=5)
    except Exception as e:
        print(f"⚠️ Erro ao contactar Kodi JSON-RPC: {e}")

    print("🎉 Sincronização concluída com sucesso total!")

if __name__ == "__main__":
    main()
