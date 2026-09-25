# MEMORY: Arquitetura do Túnel, play.hospidy.com e Pipeline Kodi

**Data:** 25 de Setembro de 2026  
**Localização:** `/Users/fabio/Downloads/Tunel`  
**Objetivo:** Registo consolidado e definitivo do ecossistema de streaming, túneis cloud, indexação de metadados e sincronização do Kodi no Projetor.

---

## 1. Topologia Geral do Sistema

```
[ Unidade Ótica USB / Terminal Mac / Android Termux ]
      │
      ├─ dvd-rip / cd-rip (Extração bit-perfect / FLAC / ISO)
      ├─ dvd-upload (Upload multi-stream para Transfer.it)
      ▼
[ Transfer.it (Armazenamento temporário até 90 dias) ]
      │
      ▼ (Link de partilha: transfer.it/t/HANDLE)
+─────────────────────────────────────────────────────────────────────────────+
| Raspberry Pi 5 @ horta (192.168.86.24 / 100.119.61.36 via Tailscale)        |
| Serviço: transfer-tunnel.service (0.0.0.0:8787)                             |
|                                                                             |
| 1. https://tunel.hospidy.com/tunel/<handle>/<filename>                     |
|    - Resolve nós diretos MEGA da API Transfer.it                            |
|    - Streaming HTTP transparente sem escrita em disco local                  |
|                                                                             |
| 2. Remote Upload via API 1fichier (/v1/remote/request.cgi)                 |
|    - Transferência Cloud-to-Cloud (180 MB/s a 275 MB/s)                      |
|    - Fixação de inline: 1 (/v1/file/chattr.cgi)                             |
|                                                                             |
| 3. https://play.hospidy.com/<file_id>/<safe_encoded_filename>               |
|    - Resolução On-The-Fly: Token 1fichier -> HTTP 302 Redirect para a CDN   |
|    - Stream Token Cache em memória (TTL otimizado)                          |
|    - Suporte nativo a HTTP Range Requests (206 Partial Content)             |
|                                                                             |
| 4. Catálogo Centralizado (GET /api/catalog & POST /sync):                   |
|    - Cache local atómica: catalog_cache.json (< 1ms resposta)               |
|    - Mapeamento das 4 pastas mestras: Filmes, Series, Musica, Kids          |
+─────────────────────────────────────────────────────────────────────────────+
      │
      ▼
+─────────────────────────────────────────────────────────────────────────────+
| Motor de Metadados & Resolução (metadata_resolver.py)                       |
|                                                                             |
| 1. ISOs: Leitura cirúrgica HTTP Range no offset 0x8000 (32 KB)             |
|    - Extrai Volume ID e IFO sem descarregar o ISO (economiza 5 a 8 GB)      |
| 2. Música Hi-Res (FLAC):                                                    |
|    - Parsing de .m3u, tags Vorbis, organização Artista/Álbum/Faixas         |
|    - Filtra lixo (.zip, arquivos utilitários) e injeta capas HD             |
| 3. Consulta TMDb API v3:                                                    |
|    - Título em Português, Estúdio de produção, Ano, Sinopse e Fanarts       |
| 4. Ledger Imutável: metadata_cache.json (0 ms de overhead para ficheiros    |
|    já analisados)                                                           |
+─────────────────────────────────────────────────────────────────────────────+
      │
      ▼
+─────────────────────────────────────────────────────────────────────────────+
| Gerador de Biblioteca Universal (generate_universal_kodi_library.py)       |
|                                                                             |
| Gera estrutura de ficheiros .strm e .nfo em /sdcard/KodiMedia/              |
|  - Filmes: /sdcard/KodiMedia/Filmes/<Título> (<Ano>)/                       |
|  - Séries: /sdcard/KodiMedia/Series/<Show>/Season XX/                       |
|  - Kids:   /sdcard/KodiMedia/Kids/<Título> (<Ano>)/                         |
|  - Música: /sdcard/KodiMedia/Musica/<Artista>/<Álbum>/                      |
|                                                                             |
| Cada .strm contém a diretiva #EXTINF:0,<NomeLimpo> e a URL de stream        |
+─────────────────────────────────────────────────────────────────────────────+
      │
      ▼ (Deploy rápido via ADB em sync_and_fix_all.py: tar.gz -> push -> unpack)
+─────────────────────────────────────────────────────────────────────────────+
| Xiaomi / Fengmi Mi Smart Compact Projector (MiProjM05 / 192.168.86.41:5555)  |
| Kodi 21 Omega (Skin: skin.thearchive)                                       |
|                                                                             |
| 1. advancedsettings.xml: Buffer de 150 MB em RAM, ocultação de .strm        |
| 2. Subtítulos: "Estúdio • Ano • Duração (min)"                              |
| 3. Menu Lateral: Botão "Atualizar Biblioteca" (ID 9009)                     |
|    - Dispara UpdateLibrary(video) e UpdateLibrary(music) com 1 clique       |
| 4. Mini-Player Lateral: Mantém vídeo e música a tocar durante a navegação   |
+─────────────────────────────────────────────────────────────────────────────+
```

---

## 2. O Túnel Transfer.it ➔ 1fichier (`tunel.hospidy.com`)

### O Desafio Inicial
Fazer o upload de imagens ISO completas de DVD (4.7 GB a 8.5 GB) ou MKVs pesados a partir de um telemóvel ou portátil para o 1fichier esgotava a bateria, sobreaquecia o dispositivo e bloqueava a largura de banda de upload residencial.

### A Solução
1. **Upload Local Rápido:** O ficheiro é enviado via `dvd-upload` em conexões multi-stream para o **Transfer.it** (que aceita ficheiros grandes e os guarda durante 90 dias com links `https://transfer.it/t/<handle>`).
2. **Resolução do Nó MEGA:**
   O script `server.py` no Raspberry Pi faz o handshake com a API interna do Transfer.it:
   ```http
   POST https://transfer.it/api/v2/download/request
   {"transfer_handle": "<handle>"}
   ```
   A API devolve a URL direta do nó de armazenamento, tamanho e nome do ficheiro.
3. **Endpoint com Nome Preservado:**
   O túnel expõe a URL pública:
   ```text
   https://tunel.hospidy.com/tunel/<handle>/<nome_real_do_ficheiro.ext>
   ```
   Desta forma, quando o 1fichier faz o remote download, guarda o ficheiro com o nome e extensão corretos (ex: `MONSUNO.iso`, `LKD_TheLionKing_CinemaMaster.mkv`).
4. **Velocidade Cloud-to-Cloud:**
   Os servidores do 1fichier descarregam diretamente da infraestrutura do Transfer.it/MEGA a **180 MB/s a 275 MB/s** (1 GB em ~4 segundos; 3.55 GB em ~96 segundos).


---

## 3. O Túnel MEGA com Desencriptação On-the-Fly (AES-128-CTR)

Diferente do Transfer.it (onde os ficheiros já vêm descompactados/sem cifra nos nós dedicados), os ficheiros alojados diretamente na **MEGA pública** (ex: `https://mega.nz/file/<id>#<key>`) são armazenados cifrados com **AES-128-CTR**. Leitores como o VLC, Kodi ou o 1fichier Remote Upload não conseguem ler estes blocos cifrados diretamente.

### A. Como Funciona a Cifra da MEGA
1. **Chave de 32 bytes (`raw_key`):**
   - Fornecida no fragmento `#` do link da MEGA.
   - A chave AES-128 (16 bytes) é calculada fazendo o XOR da primeira metade com a segunda:
     `aes_key = bytes(raw_key[i] ^ raw_key[i+16] for i in range(16))`
2. **Metadados e Nome do Ficheiro:**
   - O campo `at` devolvido pela API da MEGA é cifrado em **AES-128-CBC** com IV de 16 zeros.
   - O túnel desencripta e extrai o nome real (ex: `Sailor Moon Crystal - 01 (1080p)...mkv`).
3. **Streaming dos Dados em Modo CTR:**
   - O vetor contador (IV) tem na parte alta os 8 bytes de `raw_key[16:24]`, e na parte baixa o índice do bloco de 16 bytes: `block_offset = byte_pos // 16`.
   - Permite calcular o estado exato da cifra para **qualquer posição de byte** arbitrariamente.

### B. A Rota do Túnel MEGA (`/mega/<file_id>/<key_str>[/<filename>]`)
* **URL de Acesso:**
  ```text
  https://tunel.hospidy.com/mega/<file_id>/<key_sem_hash>/<nome_do_ficheiro.ext>
  ```
* **Suporte a Range Requests (`206 Partial Content`):**
  - O cliente (VLC, Kodi ou 1fichier) solicita um intervalo (ex: `bytes=0-1048575` ou saltando para meio do filme).
  - O túnel alinha o pedido a blocos de 16 bytes, descarrega apenas os blocos cifrados da CDN da MEGA, desencripta em RAM (usando `cryptography` ou `Cryptodome`) e despacha os bytes *plaintext* limpos para o cliente.
  - **Zero disco:** Nenhum ficheiro é gravado no Raspberry Pi ou no Mac; o streaming ocorre à velocidade total da rede.
* **Redirecionamento Automático:**
  - Se o utilizador aceder apenas a `/mega/<file_id>/<key_str>`, o servidor responde com `HTTP 302 Redirect` para `/mega/<file_id>/<key_str>/<nome_codificado>`, garantindo que o reprodutor e o 1fichier recebem o nome e extensão corretos.

### C. Utilitário de Ingestão em Lote (`mega_1fichier_upload.py`)
* **Propósito:** Automatiza a análise de listas de links da MEGA, deteção de séries/temporadas, criação da subpasta no 1fichier (ex: `Series / Sailor Moon Crystal` [ID `22726720`]) e submissão das tarefas remotas (`/v1/remote/request.cgi`).
* **Comandos Disponíveis:**
  - `./mega_1fichier_upload.py`: Submete os links pendentes em lotes controlados (predefinição: 5 ficheiros por lote);
  - `./mega_1fichier_upload.py --status`: Apresenta uma tabela em tempo real com o estado de cada tarefa remota;
  - `./mega_1fichier_upload.py --watch`: Monitoriza a conclusão dos downloads, fixa `inline: 1` automaticamente nos ficheiros concluídos e sincroniza o catálogo no `play.hospidy.com`;
  - `./mega_1fichier_upload.py --resolve-only`: Inspeciona os metadados, tamanhos e URLs de túnel sem submeter à API.

---

## 4. O Motor de Streaming e Catálogo (`play.hospidy.com`)

O serviço corre no Raspberry Pi em `server.py` (porta `8787`, encaminhada via túnel Cloudflare).

### A. Streaming Direto com Redirecionamento 302
Ao aceder a:
```text
https://play.hospidy.com/<file_id>/<nome_codificado>
```
1. O servidor consulta a API do 1fichier:
   ```http
   POST https://api.1fichier.com/v1/download/get_token.cgi
   {"url": "https://1fichier.com/?<file_id>", "inline": 1}
   ```
2. O 1fichier devolve a URL CDN temporária (`https://a-XX.1fichier.com/pXXXXXXXX?inline`).
3. O servidor responde com **`HTTP 302 Found`** e cabeçalho `Location: <url_cdn>`.
4. O reprodutor (Kodi, VLC, IINA, Infuse) liga-se diretamente à CDN com suporte total a **HTTP Range Requests (`206 Partial Content`)**, permitindo avançar e recuar sem descarregar o ficheiro inteiro.

### B. Cache de Tokens de Streaming (`STREAM_CACHE`)
Para evitar que múltiplos pedidos de probing do Kodi/VLC (que enviam dezenas de `Range: bytes=0-1024`, `bytes=32768-...` ao abrir o ficheiro) esgotem o rate-limit da API do 1fichier:
* O `server.py` armazena em memória os tokens obtidos com TTL de 240 segundos a 1 hora.
* Pedidos repetidos para o mesmo `file_id` recebem o redirect imediato em **0.01s**.

### C. Atributo `inline: 1` Obrigatório
Por defeito, o 1fichier força `Content-Disposition: attachment`. No momento do upload remoto ou através de `dvd_1fichier.py`, é executada a chamada:
```http
POST https://api.1fichier.com/v1/file/chattr.cgi
{"urls": ["https://1fichier.com/?<file_id>"], "inline": 1}
```
Isto força o 1fichier a enviar os tipos MIME corretos (`video/x-matroska`, `application/x-iso9660-image`, `audio/flac`) e ativa a reprodução online.

### D. Catálogo Estruturado em Cache Local (`GET /api/catalog`)
* O servidor mantém o ficheiro `/home/fabio/transfer-tunnel/catalog_cache.json` e uma cópia em memória com `threading.Lock`.
* `GET /api/catalog` responde em **< 1ms** sem efetuar nenhuma chamada externa ao 1fichier.
* Suporta CORS universal: `Access-Control-Allow-Origin: *`.
* Mapeamento das 4 pastas mestras do 1fichier:
  * `Filmes` (ID: `22692433`) + pasta legada `dvds` (ID: `22685921`)
  * `Series` (ID: `22692434`)
  * `Musica` (ID: `22692435`)
  * `Kids` (ID: `22692436`)
* Codificação de URL via `urllib.parse.quote` para garantir compatibilidade com espaços e caracteres especiais.

### E. Atualização por Demanda (`POST /sync`)
* Endpoint protegido por token (`Authorization: Bearer <API_KEY>` ou `X-API-Key`).
* Dispara consulta em 2 chamadas mínimas ao 1fichier (`folder/ls.cgi` e `file/ls.cgi` com `folder_id: -1`).
* Possui proteção contra flood (debounce de 30 segundos).

---

## 5. O Motor de Metadados (`metadata_resolver.py`)

Para que o catálogo não exiba apenas nomes brutos de ficheiro (ex: `HARRY_POTTER_1.iso` ou `LKD-0E-SW1.1_DES.iso`), foi desenvolvido o `metadata_resolver.py`:

### A. Inspeção Cirúrgica de ISOs via HTTP Range (Offset `0x8000`)
Para evitar descarregar ficheiros de 5 a 8 GB apenas para ler etiquetas:
1. Envia um cabeçalho HTTP:
   ```http
   Range: bytes=32768-36864
   ```
2. Lê exatamente 4.096 bytes no offset padrão ISO 9660 (`0x8000`).
3. Valida o descritor de volume primário (`CD001`).
4. Extrai o **Volume Identifier** (bytes 40 a 72):
   * Ex: `LKD-0E-SW1.1_DES` ➔ *A Lenda de Despereaux (2008)*
   * Ex: `HARRY_POTTER_1` ➔ *Harry Potter e a Pedra Filosofal (2001)*
   * Ex: `HORSELAND_GANHAR_OU_PERDER` ➔ *Horseland: Ganhar ou Perder*

### B. Integração com TMDb API v3
* Pesquisa canónica em português (`language=pt-PT`).
* Extrai metadados completos:
  * **Título em Português** e Título Original;
  * **Estúdio de Produção** (`production_companies[0].name`, ex: *Warner Bros. Pictures*, *Walt Disney Pictures*, *Universal Pictures*);
  * **Ano**, Sinopse em Português, Duração;
  * **TMDb ID** e **IMDb ID** para scrapers nativos do Kodi.

### C. Resolução e Limpeza de Música Hi-Res (FLAC)
* Lê ficheiros `.m3u` e nomes de faixas estruturadas:
  * Ex: `Miguel Araújo - Crónicas da cidade grande.m3u` ➔ Artista: `Miguel Araújo`, Álbum: `Crónicas da cidade grande (2014)`.
  * Ex: `Queen - Greatest Hits.m3u` ➔ Artista: `Queen`, Álbum: `Greatest Hits (1981)`.
* **Filtro de Lixo:** Ficheiros `.zip` são categorizados como arquivos (`music_archive`) e ficheiros `cover.jpg` são promovidos a arte de capa (`artwork`), não aparecendo como faixas na lista.
* Injeta capas de alta definição (600x600 lossless) armazenadas em `album_covers/` como `folder.jpg` e `cover.jpg`.

### D. Livro-Razão Imutável (`metadata_cache.json`)
* O ficheiro `metadata_cache.json` regista todos os ficheiros já analisados.
* Ao re-executar a sincronização, ficheiros em cache são pulados com **0 ms de atraso**.

---

## 6. A Biblioteca do Kodi no Projetor (`generate_universal_kodi_library.py`)

Gera a estrutura física em `/sdcard/KodiMedia/` no projetor.

### A. Formato dos Ficheiros `.strm`
Cada item possui o seu ficheiro `.strm` com uma diretiva de título amigável:
```text
#EXTINF:0,Harry Potter e a Pedra Filosofal
https://play.hospidy.com/vpuhjo8yurk0cvfwh0tf/Harry%20Potter%20e%20a%20Pedra%20Filosofal%20(2001).iso
```

### B. Ficheiros `.nfo` Canónicos
Ao lado de cada `.strm`, é gerado um ficheiro `.nfo` com a estrutura XML oficial:
```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<movie>
    <title>Harry Potter e a Pedra Filosofal</title>
    <originaltitle>Harry Potter and the Philosopher's Stone</originaltitle>
    <year>2001</year>
    <studio>Warner Bros. Pictures</studio>
    <plot>No seu 11º aniversário, Harry Potter descobre que é filho de feiticeiros...</plot>
    <id>671</id>
    <tmdbid>671</tmdbid>
</movie>
```
Isso permite que o Kodi importe títulos, estúdios, anos e sinopses em **menos de 5 segundos**, sem depender de pesquisas na internet.

### C. Otimizações no `advancedsettings.xml`
Instalado em `/sdcard/Android/data/org.xbmc.kodi/files/.kodi/userdata/advancedsettings.xml`:
* `buffermode = 1` (Buffer em todos os sistemas de rede e HTTP).
* `memorysize = 157286400` (**150 MB de buffer em RAM** para reprodução suave sem engasgos).
* `readfactor = 20` (Enche o buffer até 20x mais rápido que a taxa de bits do vídeo).
* `<hideextensions><video>.strm</video><music>.strm</music></hideextensions>` (Esconde a extensão `.strm` de todas as listas).

---

## 7. Fluxos de Sincronização & Comandos Operacionais

### Fluxo Completo de Atualização

```
Etapa 1: Ingestão de Ficheiro
   dvd-1fichier upload <ficheiro>   OU   dvd-transfer (a partir de links Transfer.it)
   (No final, dispara automaticamente POST https://play.hospidy.com/sync)

Etapa 2: Atualização do Catálogo Remoto
   curl -X POST https://play.hospidy.com/sync -H "X-API-Key: <TOKEN>"
   (Atualiza catalog_cache.json no Raspberry Pi)

Etapa 3: Resolução de Metadados e Geração Kodi
   python3 metadata_resolver.py
   python3 generate_universal_kodi_library.py

Etapa 4: Deploy para o Projetor
   python3 sync_and_fix_all.py
   (Gera pacote tar.gz -> Envia por ADB -> Descompacta em /sdcard/KodiMedia/ -> Dispara VideoLibrary.Scan e AudioLibrary.Scan)
```

### Como Atualizar Diretamente no Comando do Projetor
Na skin **The Archive**:
1. Abre o menu lateral esquerdo.
2. Clica no botão **"Atualizar Biblioteca"** (ID `9009`).
3. O Kodi executa imediatamente em segundo plano `UpdateLibrary(video)` e `UpdateLibrary(music)`. Surge uma notificação OSD no canto superior direito: *"The Archive: A sincronizar biblioteca..."*.

### Comandos Rápidos de Linha de Comandos

| Tarefa | Comando |
| :--- | :--- |
| **Sincronização Completa (Mac -> Projetor)** | `python3 sync_and_fix_all.py` |
| **Conectar ADB ao Projetor** | `adb connect 192.168.86.41:5555` |
| **Verificar Serviço no Raspberry Pi** | `ssh pi "systemctl --user status transfer-tunnel.service"` |
| **Sincronizar Catálogo no 1fichier** | `dvd-1fichier sync` (ou `curl -X POST https://play.hospidy.com/sync`) |
| **Consultar Catálogo Atual em JSON** | `curl -s https://play.hospidy.com/api/catalog \| jq .` |
| **Forçar Scan de Vídeo via JSON-RPC** | `curl -s -X POST -H "Content-Type: application/json" -d '{"jsonrpc": "2.0", "method": "VideoLibrary.Scan", "id": 1}' http://192.168.86.41:8080/jsonrpc` |
| **Forçar Scan de Áudio via JSON-RPC** | `curl -s -X POST -H "Content-Type: application/json" -d '{"jsonrpc": "2.0", "method": "AudioLibrary.Scan", "id": 2}' http://192.168.86.41:8080/jsonrpc` |

---

## 8. Invariantes de Engenharia e Guardrails Aprendidos

1. **Salvaguarda contra Ficheiros Corrompidos / Truncados:**
   * Qualquer ficheiro de vídeo com **menos de 10 MB** (como o ficheiro de 34 bytes da *Idade do Gelo*) ou áudio com **menos de 100 KB** é sumariamente rejeitado e marcado como `media_type: "corrupted"` no `metadata_cache.json`.
2. **Precedência Universal de Séries (`SxxExx`):**
   * Qualquer ficheiro que cumpra `re.search(r"S\d+E\d+", filename)` (como a série *Bluey* e *Pokémon*) é tratado incondicionalmente como série de TV com `tvshow.nfo` e pasta canónica `/Series/<Show>/Season XX/`, mesmo que venha na pasta `Kids` ou `Filmes` do 1fichier.
3. **Deduplicação em Duplo Eixo:**
   * Deduplicação rigorosa por URL de stream e por caminho de destino normalizado.
4. **Padrão de Apresentação nos Menus:**
   * Eliminação de nomes de realizadores ou pessoas no subtítulo. O padrão rigoroso é: **`Estúdio • Ano • Duração (min)`**.
5. **Recarregamento da Skin sem Reiniciar (EventServer UDP):**
   * Envio de pacote ACTION `ReloadSkin()` na porta UDP `9777` para recarregar alterações de XML instantaneamente.
6. **Controlo OSD de Áudio e Legendas:**
   * Interface lateral deslizante (`DialogSettings.xml`) permitindo alternar faixas de áudio, compensação de atraso milissegundo a milissegundo e seleção/pesquisa de legendas sem interromper o filme.
7. **Normalização de Títulos & Flag `inline: 1` (Pipeline MEGA -> 1fichier):**
   * **Limpeza Rigorosa:** Todos os ficheiros devem ter tags de grupos `[...]`, hashes CRC32 `[2bb76809]` e resoluções redundantes `(1080p)` limpos tanto no túnel (`Content-Disposition: inline; filename="..."`) como no 1fichier (`/v1/file/chattr.cgi`).
   * **Streaming Contínuo AES-CTR:** O streaming de links MEGA utiliza um descodificador incremental sob uma única conexão HTTP persistente por pedido (tanto para downloads integrais como para Range requests), garantindo débito máximo e evitando desconexões/erro 520 do Cloudflare.
   * **Flag `inline: 1` Obrigatória:** Sempre ativa em todos os ficheiros do 1fichier para permitir streaming direto no navegador, Kodi e VLC sem download forçado.
   * **Mapeamento de Séries (ex: Sailor Moon Crystal):** Episódios absolutos 1 a 39 são mapeados automaticamente para as Temporadas 1 (Acts 1-14), 2 (Acts 1-12) e 3 (Acts 1-13) com TMDb ID 61885 (`Toei Animation`).
