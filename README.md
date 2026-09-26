# MEGA to 1fichier Cloud Pipeline 🚀

Pipeline automatizada para transferências de alta velocidade na cloud (GitHub Actions) entre a MEGA e o 1fichier, com zero consumo de largura de banda doméstica.

## Características
- **Download MEGA:** Desencriptação direta em memória/disco via AES-128-CTR a velocidades de datacenter (50–100+ MB/s).
- **Normalização de Títulos:** Limpa automaticamente tags de release groups (ex: `[Puto+luna0099]`), checksums CRC (ex: `[e7ccf325]`) e resoluções redundantes.
- **Upload 1fichier:** Transmissão direta para servidores dedicados 1fichier.
- **Streaming Instantâneo:** Ativa automaticamente a flag `inline: 1` para permitir streaming direto no Kodi / VLC / Hospidy.
- **Sincronização:** Dispara atualização de catálogo em `play.hospidy.com`.

## Como Utilizar
1. No separador **Actions** do repositório, seleciona o workflow **MEGA to 1fichier Cloud Transfer**.
2. Clica em **Run workflow**.
3. Cola um ou mais links da MEGA (um por linha).
4. (Opcional) Especifica o ID da pasta do 1fichier (o valor padrão `22726720` corresponde a *Sailor Moon Crystal*).
5. Clica em **Run workflow**.

---

# Túnel Multipart com Password (RAR -> ISO) 🧩

Para conjuntos de links MEGA que são **partes de um arquivo RAR dividido e protegido por password**
(ex: `Nome.part01.rar` … `Nome.part10.rar`). O `mega_multipart_transfer.py`:
- Agrupa as partes pelo nome base (deteta `.partNN.rar` e `.rar/.r00/.r01`).
- Descarrega + desencripta (AES-128-CTR) cada parte.
- Extrai com a password (`7z` / `unar` / `unrar`; instala automaticamente no Colab).
- Envia **apenas o `.iso`/`.img`** para o 1fichier, ativa `inline: 1` e limpa o disco.

## Correr no Google Colab (recomendado)
```python
# 1) Obter o código (o script importa mega_cloud_transfer.py, por isso clona o repo inteiro)
!git clone https://github.com/<O_TEU_USER>/Tunel.git
%cd Tunel

# 2) Suporte RAR (rápido; o script também instala sozinho se for root)
!apt-get -qq install -y p7zip-full p7zip-rar unar
!pip install -q cryptography

# 3) Configurar e correr
import os
os.environ["FICHIER_API_KEY"] = "<A_TUA_API_KEY_1FICHIER>"
os.environ["FOLDER_ID"]       = "22726720"                       # pasta de destino
os.environ["ARCHIVE_PASSWORDS"] = "goldtuganime.biz,goldtuganime.com"
os.environ["MEGA_LINKS"] = """
https://mega.co.nz/#!lloW3BSa!...
https://mega.co.nz/#!c4ZCwRjT!...
"""
!python3 mega_multipart_transfer.py
```

Dica: corre primeiro com `--dry-run` para ver o agrupamento das partes sem descarregar nada:
```python
!python3 mega_multipart_transfer.py --dry-run
```

## Alternativa: GitHub Actions
Workflow **MEGA Multipart RAR to 1fichier (ISO)** — aceita os links, o `folder_id` e as passwords.

## Variáveis
| Variável | Descrição | Padrão |
| :--- | :--- | :--- |
| `MEGA_LINKS` | Links MEGA (um por linha) | — |
| `FICHIER_API_KEY` | API key do 1fichier (ou `~/.1fichier_token`) | — |
| `FOLDER_ID` | Pasta de destino no 1fichier | `22726720` |
| `ARCHIVE_PASSWORDS` | Passwords separadas por vírgula | `goldtuganime.biz,goldtuganime.com` |
| `WORK_DIR` | Pasta temporária | `/tmp/mega_multipart` |
