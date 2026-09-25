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
