import os
import re
import urllib.request
import urllib.parse
import json
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = "/home/fabio/transfer-tunnel/metadata_cache.json" if os.path.exists("/home/fabio/transfer-tunnel") else os.path.join(SCRIPT_DIR, "metadata_cache.json")
TMDB_API_KEY = "f090bb54758cabf231fb605d3e3e0468"

TITLE_MAPPINGS = {
    # Harry Potter
    "HARRY_POTTER_PHILOSOPHERS_STON": {"title": "Harry Potter e a Pedra Filosofal", "orig": "Harry Potter and the Philosopher's Stone", "year": 2001, "tmdb_id": 671, "studio": "Warner Bros. Pictures", "category": "Filmes"},
    "HARRY_POTTER_CHAMBER_SECRETS": {"title": "Harry Potter e a Câmara dos Segredos", "orig": "Harry Potter and the Chamber of Secrets", "year": 2002, "tmdb_id": 672, "studio": "Warner Bros. Pictures", "category": "Filmes"},
    "HP_AND_THE_PRISONER_OF_AZKABAN": {"title": "Harry Potter e o Prisioneiro de Azkaban", "orig": "Harry Potter and the Prisoner of Azkaban", "year": 2004, "tmdb_id": 673, "studio": "Warner Bros. Pictures", "category": "Filmes"},
    "HARRY_POTTER_GOBLET_OF_FIRE": {"title": "Harry Potter e o Cálice de Fogo", "orig": "Harry Potter and the Goblet of Fire", "year": 2005, "tmdb_id": 674, "studio": "Warner Bros. Pictures", "category": "Filmes"},
    "HP5_ORDER_OF_THE_PHOENIX": {"title": "Harry Potter e a Ordem da Fénix", "orig": "Harry Potter and the Order of the Phoenix", "year": 2007, "tmdb_id": 675, "studio": "Warner Bros. Pictures", "category": "Filmes"},
    "HARRY_POTTER_HALF_BLOOD_PRINCE": {"title": "Harry Potter e o Príncipe Misterioso", "orig": "Harry Potter and the Half-Blood Prince", "year": 2009, "tmdb_id": 767, "studio": "Warner Bros. Pictures", "category": "Filmes"},
    
    # Senhor dos Anéis
    "LOTR_1_D_1": {"title": "O Senhor dos Anéis: A Irmandade do Anel", "orig": "The Lord of the Rings: The Fellowship of the Ring", "year": 2001, "tmdb_id": 120, "studio": "New Line Cinema", "category": "Filmes"},
    "LOTR_2_D_1": {"title": "O Senhor dos Anéis: As Duas Torres", "orig": "The Lord of the Rings: The Two Towers", "year": 2002, "tmdb_id": 121, "studio": "New Line Cinema", "category": "Filmes"},
    "LOTR_RETURN_OF_THE_KING_D1": {"title": "O Senhor dos Anéis: O Regresso do Rei", "orig": "The Lord of the Rings: The Return of the King", "year": 2003, "tmdb_id": 122, "studio": "New Line Cinema", "category": "Filmes"},
    
    # Disney & Outros
    "LKD_TheLionKing_CinemaMaster": {"title": "O Rei Leão", "orig": "The Lion King", "year": 1994, "tmdb_id": 8587, "studio": "Walt Disney Pictures", "category": "Filmes"},
    "LKD-0E-SW1.1_DES": {"title": "O Rei Leão", "orig": "The Lion King", "year": 1994, "tmdb_id": 8587, "studio": "Walt Disney Pictures", "category": "Filmes"},
    "TERROR_NO_AFEGANIST_O": {"title": "Terror no Afeganistão", "orig": "The Beast of War", "year": 2008, "tmdb_id": 14878, "studio": "Columbia Pictures", "category": "Filmes"},
    "RADIO_CITY": {"title": "André Rieu: Live in New York", "orig": "Andre Rieu - Live in New York", "year": 2007, "tmdb_id": 57997, "studio": "André Rieu Studios", "category": "Filmes"},
    "UNTITLED001": {"title": "Uma Aventura na Casa Assombrada", "orig": "Uma Aventura na Casa Assombrada", "year": 2009, "tmdb_id": 47523, "studio": "SIC / VC Filmes", "category": "Kids"},
    
    # Séries ISO
    "DBD-0E-SW1_DES": {"title": "Dragon Ball Z", "orig": "Dragon Ball Z", "year": 1989, "tmdb_id": 12971, "studio": "Toei Animation", "category": "Series"},
    "HORSELAND_GANHAR_OU_PERDER": {"title": "Horseland", "orig": "Horseland", "year": 2006, "tmdb_id": 11830, "studio": "DIC Entertainment", "category": "Series"},
    "GADGET_VOL1": {"title": "Inspector Gadget", "orig": "Inspector Gadget", "year": 1983, "tmdb_id": 2422, "studio": "DIC Entertainment", "category": "Series"},
    "MONSUNO": {"title": "Monsuno", "orig": "Monsuno", "year": 2012, "tmdb_id": 46890, "studio": "Nickelodeon", "category": "Series"},
    
    # Kids Scene Filmes
    "A.Historia.de.uma.Abelha.PORTUGUESE.DVDRip": {"title": "A História de uma Abelha", "year": 2007, "tmdb_id": 585, "studio": "DreamWorks Animation", "category": "Kids"},
    "Action.Man.X.Missions.PORTUGUESE.DVDRip.XviD-DH": {"title": "Action Man - X-Missions", "year": 2005, "tmdb_id": 243754, "studio": "Hasbro", "category": "Kids"},
    "A.Fuga.das.Galinhas.PORTUGUESE.DVDRip.XviD-NoGrp": {"title": "A Fuga das Galinhas", "year": 2000, "tmdb_id": 7443, "studio": "Aardman Animations", "category": "Kids"},
    "Back.to.Gaya.Pequenos.Herois.PORTUGUESE.DVDRip.XviD": {"title": "Back to Gaya - Pequenos Heróis", "year": 2004, "tmdb_id": 13253, "studio": "Ambient Entertainment", "category": "Kids"},
    "A.Historia.de.David.e.Golias.PORTUGUESE.DVDRip.DivX-EVO": {"title": "A História de David e Golias", "year": 1996, "tmdb_id": 144983, "studio": "", "category": "Kids"},
    "A.Procura.De.Nemo.PORTUGUESE.DVDRip.Xvid-NYDIC": {"title": "À Procura de Nemo", "year": 2003, "tmdb_id": 12, "studio": "Pixar", "category": "Kids"},
    "A.Lenda.De.Zorro.PORTUGUESE.DVDRip.XviD": {"title": "A Lenda de Zorro", "year": 2005, "tmdb_id": 1656, "studio": "Columbia Pictures", "category": "Kids"},
    "A.Princesa.E.O.Sapo.2009.PORTUGUESE.DVDRip-EVO": {"title": "A Princesa e o Sapo", "year": 2009, "tmdb_id": 10198, "studio": "Walt Disney Animation Studios", "category": "Kids"},
    "Balburdia.Na.Quinta.PORTUGUESE.DVDRip.DivX-EVO": {"title": "Balbúrdia na Quinta", "year": 2006, "tmdb_id": 9907, "studio": "Nickelodeon Movies", "category": "Kids"},
    "A.Bela.E.O.Monstro.O.Natal.Encantado.PT-PT.DVDRip.XviD-HUPT": {"title": "A Bela e o Monstro - O Natal Encantado", "year": 1997, "tmdb_id": 13353, "studio": "Walt Disney Television Animation", "category": "Kids"},
    "A.Ilha.de.Impys.PORTUGUESE.DVDRip.XviD-SceneRush": {"title": "A Ilha dos Dinossauros", "year": 2006, "tmdb_id": 13256, "studio": "Ambient Entertainment", "category": "Kids"},
    "Astro.Boy.PORTUGUESE.BRRip.XviD.AC3-EVO": {"title": "Astro Boy", "year": 2009, "tmdb_id": 16577, "studio": "Imagi Animation Studios", "category": "Kids"},
    "A.Lenda.de.Despereaux.PORTUGUESE.DVDRip.XViD": {"title": "A Lenda de Despereaux", "year": 2008, "tmdb_id": 11826, "studio": "Universal Pictures", "category": "Kids"},
    "A.Espada.Mágica-A.Lenda.de.Camelot.DVDRip.XviD": {"title": "A Espada Mágica - A Lenda de Camelot", "year": 1998, "tmdb_id": 15174, "studio": "Warner Bros. Feature Animation", "category": "Kids"},
    "Asterix.e.os.Vikings.PORTUGUESE.DVDRip.XviD": {"title": "Astérix e os Vikings", "year": 2006, "tmdb_id": 12660, "studio": "M6 Films", "category": "Kids"},
    "Battle.for.Terra.2007.PORTUGUESE.DVDRip.XviD-RiPMANiA": {"title": "Battle for Terra", "year": 2007, "tmdb_id": 14462, "studio": "Snoot Entertainment", "category": "Kids"},
    "Bionicle.A.Lenda.Renasce.PORTUGUESE.2009.DVDRip.XviD": {"title": "Bionicle: A Lenda Renasce", "year": 2009, "tmdb_id": 22259, "studio": "Universal Studios Home Entertainment", "category": "Kids"},
}

# Disc code patterns for ISOs
ISO_DISC_CODES = {
    "LKD-0E-SW1": ("O Rei Leão", 1994, 8587, "Walt Disney Pictures", "Filmes"),
    "LKD_TheLionKing": ("O Rei Leão", 1994, 8587, "Walt Disney Pictures", "Filmes"),
    "DBD-0E-SW1": ("Dragon Ball Z", 1989, 12971, "Toei Animation", "Series"),
    "HARRY_POTTER_PHILOSOPHERS": ("Harry Potter e a Pedra Filosofal", 2001, 671, "Warner Bros. Pictures", "Filmes"),
    "HARRY_POTTER_CHAMBER": ("Harry Potter e a Câmara dos Segredos", 2002, 672, "Warner Bros. Pictures", "Filmes"),
    "HP_AND_THE_PRISONER": ("Harry Potter e o Prisioneiro de Azkaban", 2004, 673, "Warner Bros. Pictures", "Filmes"),
    "HARRY_POTTER_GOBLET": ("Harry Potter e o Cálice de Fogo", 2005, 674, "Warner Bros. Pictures", "Filmes"),
    "HP5_ORDER": ("Harry Potter e a Ordem da Fénix", 2007, 675, "Warner Bros. Pictures", "Filmes"),
    "HARRY_POTTER_HALF_BLOOD": ("Harry Potter e o Príncipe Misterioso", 2009, 767, "Warner Bros. Pictures", "Filmes"),
    "LOTR_1": ("O Senhor dos Anéis: A Irmandade do Anel", 2001, 120, "New Line Cinema", "Filmes"),
    "LOTR_2": ("O Senhor dos Anéis: As Duas Torres", 2002, 121, "New Line Cinema", "Filmes"),
    "LOTR_RETURN": ("O Senhor dos Anéis: O Regresso do Rei", 2003, 122, "New Line Cinema", "Filmes"),
    "TERROR_NO_AFEGANIST": ("Terror no Afeganistão", 2008, 14878, "Columbia Pictures", "Filmes"),
    "RADIO_CITY": ("André Rieu: Live in New York", 2007, 57997, "André Rieu Studios", "Filmes"),
    "UNTITLED001": ("Uma Aventura na Casa Assombrada", 2009, 47523, "SIC / VC Filmes", "Kids"),
    "GADGET": ("Inspector Gadget", 1983, 2422, "DIC Entertainment", "Series"),
    "MONSUNO": ("Monsuno", 2012, 46890, "Nickelodeon", "Series"),
    "HORSELAND": ("Horseland", 2006, 11830, "DIC Entertainment", "Series")
}

# Mapping de Faixas de Música por Álbum
MIGUEL_ARAUJO_TRACKS = {
    "01 - Cidade grande I (canção de acordar).flac": (1, "Cidade grande I (canção de acordar)"),
    "02 - José.flac": (2, "José"),
    "03 - Romaria das festas de Santa Eufémia.flac": (3, "Romaria das festas de Santa Eufémia"),
    "04 - Balada astral.flac": (4, "Balada astral"),
    "05 - Contamina‐me.flac": (5, "Contamina‐me"),
    "06 - Cartório.flac": (6, "Cartório"),
    "07 - Cidade grande II (canção de remanso).flac": (7, "Cidade grande II (canção de remanso)"),
    "08 - Dona Laura.flac": (8, "Dona Laura"),
    "09 - Recantiga.flac": (9, "Recantiga"),
    "10 - Canção de Salomão.flac": (10, "Canção de Salomão"),
    "11 - Aqui jaz José dos Santos.flac": (11, "Aqui jaz José dos Santos"),
    "12 - Cidade grande III (canção de embalar).flac": (12, "Cidade grande III (canção de embalar)"),
    "13 - Valsa redonda.flac": (13, "Valsa redonda")
}

QUEEN_GH1_TRACKS = {
    "01 - Bohemian Rhapsody.flac": (1, "Bohemian Rhapsody"),
    "02 - Another One Bites the Dust.flac": (2, "Another One Bites the Dust"),
    "03 - Killer Queen.flac": (3, "Killer Queen"),
    "04 - Fat Bottomed Girls.flac": (4, "Fat Bottomed Girls"),
    "05 - Bicycle Race.flac": (5, "Bicycle Race"),
    "06 - You’re My Best Friend.flac": (6, "You’re My Best Friend"),
    "07 - Don’t Stop Me Now.flac": (7, "Don’t Stop Me Now"),
    "08 - Save Me.flac": (8, "Save Me"),
    "09 - Crazy Little Thing Called Love.flac": (9, "Crazy Little Thing Called Love"),
    "10 - Somebody to Love.flac": (10, "Somebody to Love"),
    "11 - Now I’m Here.flac": (11, "Now I’m Here"),
    "12 - Good Old‐Fashioned Lover Boy.flac": (12, "Good Old‐Fashioned Lover Boy"),
    "13 - Play the Game.flac": (13, "Play the Game"),
    "14 - Flash.flac": (14, "Flash"),
    "15 - Seven Seas of Rhye.flac": (15, "Seven Seas of Rhye"),
    "16 - We Will Rock You.flac": (16, "We Will Rock You"),
    "17 - We Are the Champions.flac": (17, "We Are the Champions")
}

QUEEN_GH2_TRACKS = {
    "01 - A Kind of Magic.flac": (1, "A Kind of Magic"),
    "02 - Under Pressure.flac": (2, "Under Pressure"),
    "03 - Radio Ga Ga.flac": (3, "Radio Ga Ga"),
    "04 - I Want It All.flac": (4, "I Want It All"),
    "05 - I Want to Break Free.flac": (5, "I Want to Break Free"),
    "06 - Innuendo.flac": (6, "Innuendo"),
    "07 - It's a Hard Life.flac": (7, "It's a Hard Life"),
    "08 - Breakthru.flac": (8, "Breakthru"),
    "09 - Who Wants to Live Forever.flac": (9, "Who Wants to Live Forever"),
    "10 - Headlong.flac": (10, "Headlong"),
    "11 - The Miracle.flac": (11, "The Miracle"),
    "12 - I'm Going Slightly Mad.flac": (12, "I'm Going Slightly Mad"),
    "13 - The Invisible Man.flac": (13, "The Invisible Man"),
    "14 - Hammer to Fall.flac": (14, "Hammer to Fall"),
    "15 - Friends Will Be Friends.flac": (15, "Friends Will Be Friends"),
    "16 - The Show Must Go On.flac": (16, "The Show Must Go On"),
    "17 - One Vision.flac": (17, "One Vision")
}

QUEEN_WEMBLEY_TRACKS = {
    "01 - Love of My Life.flac": (1, "Love of My Life"),
    "02 - Is This the World We Created.flac": (2, "Is This the World We Created"),
    "03 - (You’re So Square) Baby I Don’t Care.flac": (3, "(You’re So Square) Baby I Don’t Care"),
    "04 - Hello Mary Lou (Goodbye Heart).flac": (4, "Hello Mary Lou (Goodbye Heart)"),
    "05 - Tutti Frutti.flac": (5, "Tutti Frutti"),
    "06 - Gimme Some Lovin’.flac": (6, "Gimme Some Lovin’"),
    "07 - Bohemian Rhapsody.flac": (7, "Bohemian Rhapsody (Live)"),
    "08 - Hammer to Fall.flac": (8, "Hammer to Fall (Live)"),
    "09 - Crazy Little Thing Called Love.flac": (9, "Crazy Little Thing Called Love (Live)"),
    "10 - Big Spender.flac": (10, "Big Spender"),
    "11 - Radio Ga Ga.flac": (11, "Radio Ga Ga (Live)"),
    "12 - We Will Rock You.flac": (12, "We Will Rock You (Live)"),
    "13 - Friends Will Be Friends.flac": (13, "Friends Will Be Friends (Live)"),
    "14 - We Are the Champions.flac": (14, "We Are the Champions (Live)")
}

def probe_iso_remote_volume(url):
    """Lê cirurgicamente 4KB no offset 0x8000 via HTTP Range para obter o Volume ID."""
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"Range": "bytes=32768-36864", "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = r.read()
            if len(data) >= 72 and data[1:6] == b"CD001":
                vol_id = data[40:72].decode("ascii", errors="ignore").strip()
                return vol_id
    except Exception as e:
        sys.stderr.write(f"Aviso probe ISO ({url[:35]}...): {e}\n")
    return None

def query_tmdb_api(query, is_tv=False, year=None):
    """Consulta a API oficial do TMDb para metadados ricos e estúdio."""
    try:
        endpoint = "tv" if is_tv else "movie"
        q = urllib.parse.quote(query)
        year_param = f"&year={year}" if year and not is_tv else (f"&first_air_date_year={year}" if year else "")
        url = f"https://api.themoviedb.org/3/search/{endpoint}?api_key={TMDB_API_KEY}&query={q}{year_param}&language=pt-PT"
        
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("results", [])
            if not results:
                # Tentar sem restrição de ano se não encontrou
                if year:
                    url_noyear = f"https://api.themoviedb.org/3/search/{endpoint}?api_key={TMDB_API_KEY}&query={q}&language=pt-PT"
                    req2 = urllib.request.Request(url_noyear, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req2, timeout=5) as resp2:
                        results = json.loads(resp2.read().decode("utf-8")).get("results", [])

            if results:
                top = results[0]
                tmdb_id = top.get("id")
                # Obter estúdio/companhia de produção na página detalhada
                studio = ""
                director = ""
                try:
                    detail_url = f"https://api.themoviedb.org/3/{endpoint}/{tmdb_id}?api_key={TMDB_API_KEY}&language=pt-PT"
                    req_d = urllib.request.Request(detail_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req_d, timeout=5) as resp_d:
                        detail = json.loads(resp_d.read().decode("utf-8"))
                        companies = detail.get("production_companies", [])
                        if companies:
                            studio = companies[0].get("name", "")
                except Exception:
                    pass

                rel_date = top.get("release_date") or top.get("first_air_date") or ""
                out_year = int(rel_date.split("-")[0]) if rel_date and "-" in rel_date else year

                return {
                    "tmdb_id": tmdb_id,
                    "title": top.get("title") or top.get("name") or query,
                    "original_title": top.get("original_title") or top.get("original_name") or query,
                    "year": out_year,
                    "overview": top.get("overview", ""),
                    "studio": studio,
                    "poster_path": top.get("poster_path", ""),
                    "backdrop_path": top.get("backdrop_path", ""),
                    "tmdb_url": f"https://www.themoviedb.org/{endpoint}/{tmdb_id}"
                }
    except Exception as e:
        sys.stderr.write(f"Aviso busca API TMDb ({query}): {e}\n")
    return None

def resolve_item_metadata(filename, folder_name, stream_url=None):
    """Analisa e enriquece qualquer item (ISO, Vídeo, Série ou Música)."""
    base, ext = os.path.splitext(filename)
    ext_lower = ext.lower()

    # 1. MÚSICA
    is_music = ext_lower in {".flac", ".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus"} or "_flac.zip" in filename.lower()
    if is_music or folder_name == "Musica":
        # Arquivos utilitários e capas
        if ext_lower in {".zip", ".rar", ".7z"}:
            return {
                "media_type": "music_archive",
                "clean_title": base,
                "relative_path": f"Musica/.archives/{base}"
            }
        if ext_lower in {".jpg", ".jpeg", ".png"}:
            return {
                "media_type": "artwork",
                "clean_title": base,
                "relative_path": f"Musica/.artwork/{base}"
            }
        if ext_lower in {".m3u", ".m3u8"}:
            return {
                "media_type": "playlist",
                "clean_title": base,
                "relative_path": f"Musica/.playlists/{base}"
            }

        # Reconhecimento preciso de Miguel Araújo
        if filename in MIGUEL_ARAUJO_TRACKS:
            trk_num, trk_title = MIGUEL_ARAUJO_TRACKS[filename]
            artist = "Miguel Araújo"
            album = "Crónicas da cidade grande"
            year = 2014
            return {
                "media_type": "music",
                "clean_title": trk_title,
                "artist": artist,
                "album": album,
                "track": trk_num,
                "year": year,
                "relative_path": f"Musica/{artist}/{album} ({year})/{trk_num:02d} - {trk_title}"
            }

        # Reconhecimento de Wembley 1986/2003
        if filename in QUEEN_WEMBLEY_TRACKS:
            trk_num, trk_title = QUEEN_WEMBLEY_TRACKS[filename]
            artist = "Queen"
            album = "Live at Wembley Stadium"
            year = 2003
            return {
                "media_type": "music",
                "clean_title": trk_title,
                "artist": artist,
                "album": album,
                "track": trk_num,
                "year": year,
                "relative_path": f"Musica/{artist}/{album} ({year})/{trk_num:02d} - {trk_title}"
            }

        # Reconhecimento de Greatest Hits II (1991)
        if filename in QUEEN_GH2_TRACKS:
            trk_num, trk_title = QUEEN_GH2_TRACKS[filename]
            artist = "Queen"
            album = "Greatest Hits II"
            year = 1991
            return {
                "media_type": "music",
                "clean_title": trk_title,
                "artist": artist,
                "album": album,
                "track": trk_num,
                "year": year,
                "relative_path": f"Musica/{artist}/{album} ({year})/{trk_num:02d} - {trk_title}"
            }

        # Reconhecimento de Greatest Hits (1981)
        if filename in QUEEN_GH1_TRACKS:
            trk_num, trk_title = QUEEN_GH1_TRACKS[filename]
            artist = "Queen"
            album = "Greatest Hits"
            year = 1981
            return {
                "media_type": "music",
                "clean_title": trk_title,
                "artist": artist,
                "album": album,
                "track": trk_num,
                "year": year,
                "relative_path": f"Musica/{artist}/{album} ({year})/{trk_num:02d} - {trk_title}"
            }

        # Fallback inteligente para novas músicas genéricas
        m_trk = re.search(r"^(\d+)\s*-\s*(.*?)$", base)
        trk_num = int(m_trk.group(1)) if m_trk else 1
        trk_title = m_trk.group(2).strip() if m_trk else base

        artist = "Vários Artistas"
        album = "Singles & Raridades"
        year = None

        # Tentar extrair "Artista - Faixa"
        if " - " in trk_title:
            parts = trk_title.split(" - ", 1)
            artist = parts[0].strip()
            trk_title = parts[1].strip()

        year_str = f" ({year})" if year else ""
        return {
            "media_type": "music",
            "clean_title": trk_title,
            "artist": artist,
            "album": album,
            "track": trk_num,
            "year": year,
            "relative_path": f"Musica/{artist}/{album}{year_str}/{trk_num:02d} - {trk_title}"
        }

    # 2. SÉRIES (ex: Pokémon, Bluey, ou qualquer ficheiro com padrão SxxExx ou na pasta Series)
    is_series = folder_name == "Series" or "pokemon" in filename.lower() or bool(re.search(r"S\d+E\d+", filename, re.I))
    if is_series:
        # 2a. Pokémon Crónicas
        m_cron = re.search(r"cronicas?\s*-\s*e?(\d+)(?:\s*-\s*(.*?))?$", base, re.I)
        if m_cron:
            ep_num = int(m_cron.group(1))
            ep_title = m_cron.group(2).strip() if m_cron.group(2) else f"Episódio {ep_num}"
            show_name = "Pokemon Cronicas"
            show_tmdb = "https://www.themoviedb.org/tv/42705"
            rel_path = f"Series/{show_name}/Season 01/{show_name} - S01E{ep_num:02d} - {ep_title}"
            return {
                "media_type": "tvshow",
                "show_name": show_name,
                "season": 1,
                "episode": ep_num,
                "clean_title": ep_title,
                "show_tmdb_url": show_tmdb,
                "studio": "TV Tokyo",
                "relative_path": rel_path
            }

        # 2b. Sailor Moon Crystal (absoluto 1 a 39 ou SxxExx)
        if "sailor moon crystal" in filename.lower():
            m_sm = re.search(r"sailor\s*moon\s*crystal\s*-\s*(?:S(\d+)E(\d+)|(\d{1,3}))(?:\s*-\s*(.*?))?$", base, re.I)
            if m_sm:
                if m_sm.group(1) and m_sm.group(2):
                    season = int(m_sm.group(1))
                    ep_num = int(m_sm.group(2))
                else:
                    abs_ep = int(m_sm.group(3))
                    if abs_ep <= 14:
                        season = 1
                        ep_num = abs_ep
                    elif abs_ep <= 26:
                        season = 2
                        ep_num = abs_ep - 14
                    else:
                        season = 3
                        ep_num = abs_ep - 26
                ep_title = m_sm.group(4).strip() if m_sm.group(4) else f"Act {ep_num}"
                show_name = "Sailor Moon Crystal"
                show_tmdb = "https://www.themoviedb.org/tv/61885"
                rel_path = f"Series/{show_name}/Season {season:02d}/{show_name} - S{season:02d}E{ep_num:02d} - {ep_title}"
                return {
                    "media_type": "tvshow",
                    "show_name": show_name,
                    "season": season,
                    "episode": ep_num,
                    "clean_title": ep_title,
                    "show_tmdb_url": show_tmdb,
                    "studio": "Toei Animation",
                    "relative_path": rel_path
                }

        # 2c. Formato Padrão Show - SxxExx - Title (ex: Bluey, Pokémon, etc.)
        m_s = re.search(r"^(.*?)\s*-\s*S(\d+)E(\d+)(?:\s*-\s*(.*?))?$", base, re.I)
        if m_s:
            raw_show = m_s.group(1).strip()
            s_num = int(m_s.group(2))
            ep_num = int(m_s.group(3))
            ep_title = m_s.group(4).strip() if m_s.group(4) else f"Episódio {ep_num}"

            if "pokemon" in raw_show.lower():
                show_name = "Pokemon"
                show_tmdb = "https://www.themoviedb.org/tv/60572"
                studio = "TV Tokyo"
            elif "bluey" in raw_show.lower():
                show_name = "Bluey"
                show_tmdb = "https://www.themoviedb.org/tv/82728"
                studio = "Ludo Studio / BBC Studios"
            else:
                show_name = raw_show
                show_tmdb = ""
                studio = ""

            rel_path = f"Series/{show_name}/Season {s_num:02d}/{show_name} - S{s_num:02d}E{ep_num:02d} - {ep_title}"
            return {
                "media_type": "tvshow",
                "show_name": show_name,
                "season": s_num,
                "episode": ep_num,
                "clean_title": ep_title,
                "show_tmdb_url": show_tmdb,
                "studio": studio,
                "relative_path": rel_path
            }

    # 3. FILMES E KIDS (ISOs e Vídeos)
    # 3a. Dicionário de Curadoria
    if base in TITLE_MAPPINGS:
        mapping = TITLE_MAPPINGS[base]
        category = mapping.get("category") or folder_name or "Filmes"
        title = mapping["title"]
        year = mapping.get("year")
        tmdb_id = mapping.get("tmdb_id")
        studio = mapping.get("studio", "")
        tmdb_url = f"https://www.themoviedb.org/movie/{tmdb_id}" if tmdb_id else ""
        year_str = f" ({year})" if year and f"({year})" not in title else ""
        rel_path = f"{category}/{title}{year_str}/{title}{year_str}"
        return {
            "media_type": "movie",
            "clean_title": title,
            "year": year,
            "tmdb_id": tmdb_id,
            "tmdb_url": tmdb_url,
            "studio": studio,
            "category": category,
            "relative_path": rel_path
        }

    # 3b. Se for ISO, sondar Volume Descriptor por HTTP Range se stream_url existir
    if ext_lower == ".iso":
        vol_label = probe_iso_remote_volume(stream_url)
        if vol_label:
            for prefix, code_info in ISO_DISC_CODES.items():
                if vol_label.startswith(prefix) or prefix in vol_label:
                    title, year, tmdb_id, studio, category = code_info
                    year_str = f" ({year})" if year else ""
                    rel_path = f"{category}/{title}{year_str}/{title}{year_str}"
                    return {
                        "media_type": "movie" if category != "Series" else "tvshow",
                        "clean_title": title,
                        "year": year,
                        "tmdb_id": tmdb_id,
                        "tmdb_url": f"https://www.themoviedb.org/movie/{tmdb_id}",
                        "studio": studio,
                        "category": category,
                        "relative_path": rel_path
                    }

    # 3c. Fallback de Limpeza e Pesquisa na API Oficial do TMDb
    category = "Kids" if folder_name == "Kids" else "Filmes"
    clean = re.sub(r"\[[^\]]*\]", " ", base)
    clean = re.sub(r"\b(PORTUGUESE|PT-PT|DVDRip|BRRip|BDRip|XviD|DivX|AC3|EVO|HUPT|DH|SceneRush|NYDIC|NoGrp|RiPMANiA)\b", " ", clean, flags=re.I)
    clean = clean.replace(".", " ").replace("_", " ").replace("-", " ").strip()
    m_year = re.search(r"\b(19\d\d|20\d\d)\b", clean)
    year = int(m_year.group(1)) if m_year else None
    if m_year:
        clean = clean[:m_year.start()] + clean[m_year.end():]
    clean = re.sub(r"\s+", " ", clean).strip().title()

    api_res = query_tmdb_api(clean, is_tv=False, year=year)
    if api_res:
        title = api_res["title"]
        year = api_res["year"] or year
        studio = api_res["studio"]
        tmdb_url = api_res["tmdb_url"]
        tmdb_id = api_res["tmdb_id"]
    else:
        title = clean
        studio = ""
        tmdb_url = ""
        tmdb_id = None

    year_str = f" ({year})" if year else ""
    rel_path = f"{category}/{title}{year_str}/{title}{year_str}"
    return {
        "media_type": "movie",
        "clean_title": title,
        "year": year,
        "tmdb_id": tmdb_id,
        "tmdb_url": tmdb_url,
        "studio": studio,
        "category": category,
        "relative_path": rel_path
    }

def get_all_enriched_metadata(items_by_folder):
    """Lê o cache em disco, resolve apenas novos ficheiros e grava atómicamente."""
    cache = {}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    modified = False

    # Limpeza preventiva de entradas legadas ou corrompidas no cache
    for fname, entry in list(cache.items()):
        m_type = entry.get("media_type")
        if m_type in ("music", "music_archive", "artwork", "playlist") or fname.endswith((".flac", ".mp3", ".m4a", ".zip", ".m3u", ".jpg")) or fname in ("RADIO_CITY.iso", "UNTITLED001.iso"):
            del cache[fname]
            modified = True

    for folder_name, items in items_by_folder.items():
        for it in items:
            fname = it.get("filename", "")
            stream_url = it.get("stream_url") or it.get("url")
            if not fname:
                continue
            size = it.get("size", 0)
            ext_lower = os.path.splitext(fname)[1].lower()
            if ext_lower in {".mkv", ".avi", ".mp4", ".iso", ".wmv", ".m4v"} and size < 10 * 1024 * 1024:
                if cache.get(fname, {}).get("media_type") != "corrupted":
                    cache[fname] = {"media_type": "corrupted", "size": size, "reason": "truncated_video"}
                    modified = True
                continue

            needs_update = (
                fname not in cache or
                (cache[fname].get("media_type") == "movie" and "studio" not in cache[fname])
            )
            if needs_update:
                meta = resolve_item_metadata(fname, folder_name, stream_url=stream_url)
                cache[fname] = meta
                modified = True

    if modified:
        try:
            tmp_path = f"{CACHE_FILE}.tmp.{os.getpid()}"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, CACHE_FILE)
            sys.stdout.write(f"✅ Cache de metadados atualizado com sucesso em {CACHE_FILE}\n")
        except Exception as e:
            sys.stderr.write(f"Erro ao gravar {CACHE_FILE}: {e}\n")

    return cache

if __name__ == "__main__":
    cat_path = "/home/fabio/transfer-tunnel/catalog_cache.json" if os.path.exists("/home/fabio/transfer-tunnel/catalog_cache.json") else os.path.join(SCRIPT_DIR, "catalog_cache.json")
    if os.path.exists(cat_path):
        print(f"Lendo catálogo de {cat_path}...")
        with open(cat_path, "r", encoding="utf-8") as f_cat:
            catalog = json.load(f_cat)
        folders = catalog.get("folders", {})
        get_all_enriched_metadata(folders)
        print("Processamento concluído com sucesso!")
    else:
        print(f"Catálogo não encontrado em {cat_path}")

