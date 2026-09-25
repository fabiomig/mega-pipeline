import os
import json
import re
import urllib.parse

def clean_xml(text):
    if not text:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")

def extract_tmdb_id(url):
    if not url:
        return ""
    m = re.search(r'/(?:movie|tv)/(\d+)', url)
    return m.group(1) if m else ""

def generate_library(output_dir):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cat_path = "/home/fabio/transfer-tunnel/catalog_cache.json" if os.path.exists("/home/fabio/transfer-tunnel/catalog_cache.json") else os.path.join(script_dir, "catalog_cache.json")
    meta_path = "/home/fabio/transfer-tunnel/metadata_cache.json" if os.path.exists("/home/fabio/transfer-tunnel/metadata_cache.json") else os.path.join(script_dir, "metadata_cache.json")

    with open(cat_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    # Offsets para Pokémon temporadas 2 a 11
    POKEMON_OFFSETS = {
        2: 83,   # 84 -> 1
        3: 118,  # 119 -> 1
        4: 159,  # 160 -> 1
        5: 211,  # 212 -> 1
        6: 276,  # 277 -> 1
        7: 316,  # 317 -> 1
        8: 368,  # 369 -> 1
        9: 422,  # 423 -> 1
        10: 469, # 470 -> 1
        11: 521, # 522 -> 1
    }

    # ISO Séries info
    ISO_SERIES_TMDB = {
        "DBD-0E-SW1_DES": ("Dragon Ball Z", 12971, "https://www.themoviedb.org/tv/12971", 1, 1, "A Chegada de Raditz"),
        "GADGET_VOL1": ("Inspector Gadget", 2422, "https://www.themoviedb.org/tv/2422", 1, 1, "Winter Sports / Monster Lake"),
        "HORSELAND_GANHAR_OU_PERDER": ("Horseland", 11830, "https://www.themoviedb.org/tv/11830", 1, 1, "Ganhar ou Perder"),
        "MONSUNO": ("Monsuno", 46890, "https://www.themoviedb.org/tv/46890", 1, 1, "Clash")
    }

    stats = {"movies": 0, "kids": 0, "episodes": 0, "music": 0, "total_files": 0, "skipped_corrupted": 0, "skipped_duplicates": 0}
    seen_urls = set()
    seen_movie_destinations = set()

    # Processar cada pasta do catálogo
    for folder_name, items in catalog["folders"].items():
        for item in items:
            raw_filename = item.get("filename", "")
            stream_url = item.get("stream_url") or item.get("url")
            if not raw_filename or not stream_url:
                continue

            # Salvaguarda 1: Deduplicação por stream_url
            if stream_url in seen_urls:
                stats["skipped_duplicates"] += 1
                continue
            seen_urls.add(stream_url)

            # Salvaguarda 2: Rejeição de ficheiros truncados ou com erro de transferência (< 10MB para vídeo)
            size = item.get("size", 0)
            base, ext = os.path.splitext(raw_filename)
            ext_lower = ext.lower()
            if ext_lower in {".mkv", ".avi", ".mp4", ".iso", ".wmv", ".m4v"} and size < 10 * 1024 * 1024:
                print(f"⚠️ Ficheiro corrompido/truncado ignorado da biblioteca: {raw_filename} ({size} bytes)")
                stats["skipped_corrupted"] += 1
                continue
            if ext_lower in {".flac", ".mp3", ".wav"} and size < 100 * 1024:
                stats["skipped_corrupted"] += 1
                continue

            info = meta.get(raw_filename, {})
            mtype = info.get("media_type")

            # Caso 1: ISOs de Séries
            if base in ISO_SERIES_TMDB:
                show_title, tmdb_id, tmdb_url, season, ep, ep_title = ISO_SERIES_TMDB[base]
                show_dir = os.path.join(output_dir, "Series", show_title)
                season_dir = os.path.join(show_dir, f"Season {season:02d}")
                os.makedirs(season_dir, exist_ok=True)

                # tvshow.nfo
                tvshow_nfo = os.path.join(show_dir, "tvshow.nfo")
                if not os.path.exists(tvshow_nfo):
                    with open(tvshow_nfo, "w", encoding="utf-8") as f_nfo:
                        f_nfo.write(f'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n<tvshow>\n    <title>{clean_xml(show_title)}</title>\n    <id>{tmdb_id}</id>\n</tvshow>\n{tmdb_url}\n')

                # Episode strm & nfo
                ep_base = f"{show_title} - S{season:02d}E{ep:02d}"
                strm_path = os.path.join(season_dir, f"{ep_base}.strm")
                nfo_path = os.path.join(season_dir, f"{ep_base}.nfo")

                with open(strm_path, "w", encoding="utf-8") as f_strm:
                    f_strm.write(f"#EXTINF:0,{ep_title}\n")
                    f_strm.write(stream_url.strip() + "\n")
                with open(nfo_path, "w", encoding="utf-8") as f_nfo:
                    f_nfo.write(f'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n<episodedetails>\n    <title>{clean_xml(ep_title)}</title>\n    <season>{season}</season>\n    <episode>{ep}</episode>\n</episodedetails>\n')
                stats["episodes"] += 1
                stats["total_files"] += 2
                continue

            # Caso 2: Séries (Pokémon, Bluey, Séries em geral)
            is_tv = (mtype == "tvshow") or ("pokemon" in raw_filename.lower()) or bool(re.search(r"S\d+E\d+", raw_filename, re.I))
            if is_tv:
                m_s = re.search(r"^(.*?)\s*-\s*S(\d+)E(\d+)(?:\s*-\s*(.*?))?$", base, re.I)

                # Nome da série
                if info.get("show_name"):
                    show_name = info["show_name"]
                elif "pokemon" in raw_filename.lower():
                    show_name = "Pokemon"
                elif m_s:
                    show_name = m_s.group(1).strip()
                else:
                    show_name = "Series"

                season = info.get("season") or (int(m_s.group(2)) if m_s else 1)
                orig_ep = info.get("episode") or (int(m_s.group(3)) if m_s else 1)
                clean_title = info.get("clean_title") or (m_s.group(4).strip() if (m_s and m_s.group(4)) else f"Episódio {orig_ep}")

                show_tmdb = info.get("show_tmdb_url")
                if not show_tmdb:
                    if show_name.lower() == "bluey":
                        show_tmdb = "https://www.themoviedb.org/tv/82728"
                    elif "cronica" in raw_filename.lower():
                        show_tmdb = "https://www.themoviedb.org/tv/42705"
                    else:
                        show_tmdb = "https://www.themoviedb.org/tv/60572"

                tmdb_id = extract_tmdb_id(show_tmdb) or ("82728" if show_name.lower() == "bluey" else "60572")

                # Calcular episódio relativo para Pokémon
                if show_name == "Pokemon" and season in POKEMON_OFFSETS:
                    rel_ep = orig_ep - POKEMON_OFFSETS[season]
                    if rel_ep <= 0:
                        rel_ep = orig_ep
                else:
                    rel_ep = orig_ep

                show_dir = os.path.join(output_dir, "Series", show_name)
                season_dir = os.path.join(show_dir, f"Season {season:02d}")
                os.makedirs(season_dir, exist_ok=True)

                # tvshow.nfo
                tvshow_nfo = os.path.join(show_dir, "tvshow.nfo")
                if not os.path.exists(tvshow_nfo):
                    disp_name = "Pokémon" if show_name == "Pokemon" else ("Pokémon Crónicas" if show_name == "Pokemon Cronicas" else show_name)
                    with open(tvshow_nfo, "w", encoding="utf-8") as f_nfo:
                        f_nfo.write(f'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n<tvshow>\n    <title>{clean_xml(disp_name)}</title>\n    <id>{tmdb_id}</id>\n</tvshow>\n{show_tmdb}\n')

                safe_title = re.sub(r'[\\/*?:"<>|]', "", clean_title).strip()
                file_base = f"{show_name} - S{season:02d}E{rel_ep:02d} - {safe_title}"

                strm_path = os.path.join(season_dir, f"{file_base}.strm")
                nfo_path = os.path.join(season_dir, f"{file_base}.nfo")

                with open(strm_path, "w", encoding="utf-8") as f_strm:
                    f_strm.write(f"#EXTINF:0,{clean_title}\n")
                    f_strm.write(stream_url.strip() + "\n")
                with open(nfo_path, "w", encoding="utf-8") as f_nfo:
                    f_nfo.write(f'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n<episodedetails>\n    <title>{clean_xml(clean_title)}</title>\n    <season>{season}</season>\n    <episode>{rel_ep}</episode>\n</episodedetails>\n')
                stats["episodes"] += 1
                stats["total_files"] += 2
                continue

            # Caso 3: Filmes e Filmes Infantis (Kids)
            if mtype == "movie" or folder_name in ("Filmes", "Kids"):
                category = "Kids" if (folder_name == "Kids" or info.get("category") == "Kids") else "Filmes"
                clean_title = info.get("clean_title") or base
                orig_title = info.get("original_title") or info.get("orig")
                year = info.get("year")
                studio = info.get("studio")
                plot = info.get("overview") or info.get("plot")
                tmdb_url = info.get("tmdb_url", "")
                tmdb_id = extract_tmdb_id(tmdb_url) or info.get("tmdb_id")

                year_str = f" ({year})" if year and f"({year})" not in clean_title else ""
                movie_folder_name = f"{clean_title}{year_str}"
                safe_folder_name = re.sub(r'[\\/*?:"<>|]', "", movie_folder_name).strip()
                movie_key = (category, safe_folder_name.lower())
                if movie_key in seen_movie_destinations:
                    stats["skipped_duplicates"] += 1
                    continue
                seen_movie_destinations.add(movie_key)

                movie_dir = os.path.join(output_dir, category, safe_folder_name)
                os.makedirs(movie_dir, exist_ok=True)

                strm_path = os.path.join(movie_dir, f"{safe_folder_name}.strm")
                nfo_path = os.path.join(movie_dir, f"{safe_folder_name}.nfo")

                with open(strm_path, "w", encoding="utf-8") as f_strm:
                    f_strm.write(f"#EXTINF:0,{clean_title}\n")
                    f_strm.write(stream_url.strip() + "\n")

                with open(nfo_path, "w", encoding="utf-8") as f_nfo:
                    f_nfo.write('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n<movie>\n')
                    f_nfo.write(f'    <title>{clean_xml(clean_title)}</title>\n')
                    if orig_title:
                        f_nfo.write(f'    <originaltitle>{clean_xml(orig_title)}</originaltitle>\n')
                    if year:
                        f_nfo.write(f'    <year>{year}</year>\n')
                    if studio:
                        f_nfo.write(f'    <studio>{clean_xml(studio)}</studio>\n')
                    if plot:
                        f_nfo.write(f'    <plot>{clean_xml(plot)}</plot>\n')
                    if tmdb_id:
                        f_nfo.write(f'    <id>{tmdb_id}</id>\n')
                    f_nfo.write('</movie>\n')
                    if tmdb_url:
                        f_nfo.write(f'{tmdb_url}\n')

                if category == "Kids":
                    stats["kids"] += 1
                else:
                    stats["movies"] += 1
                stats["total_files"] += 2
                continue

            # Arquivos utilitários de música (ignorar da criação de faixas)
            if mtype in ("music_archive", "artwork", "playlist") or ext.lower() in {".zip", ".rar", ".7z", ".m3u", ".m3u8", ".jpg", ".jpeg", ".png"}:
                continue

            # Caso 4: Música
            if mtype == "music" or folder_name == "Musica" or ext.lower() in {".flac", ".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus"}:
                artist = info.get("artist") or "Vários Artistas"
                album = info.get("album") or "Singles & Raridades"
                year = info.get("year")
                track = info.get("track", 1)
                title = info.get("clean_title") or base

                year_str = f" ({year})" if year else ""
                album_dir = os.path.join(output_dir, "Musica", artist, f"{album}{year_str}")
                os.makedirs(album_dir, exist_ok=True)

                safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()
                track_file = f"{track:02d} - {safe_title}.strm"
                strm_path = os.path.join(album_dir, track_file)

                # Copiar capa de alta resolução para o álbum se existir localmente
                covers_dir = os.path.join(script_dir, "album_covers")
                folder_jpg = os.path.join(album_dir, "folder.jpg")
                cover_jpg = os.path.join(album_dir, "cover.jpg")
                if not os.path.exists(folder_jpg) and os.path.exists(covers_dir):
                    matched_cover = None
                    if "Greatest Hits II" in album:
                        matched_cover = os.path.join(covers_dir, "queen_gh2.jpg")
                    elif "Greatest Hits" in album:
                        matched_cover = os.path.join(covers_dir, "queen_gh1.jpg")
                    elif "Wembley" in album:
                        matched_cover = os.path.join(covers_dir, "queen_wembley.jpg")
                    elif "Miguel" in artist or "Crónicas" in album:
                        matched_cover = os.path.join(covers_dir, "miguel_araujo.jpg")
                    
                    if matched_cover and os.path.exists(matched_cover):
                        import shutil
                        shutil.copyfile(matched_cover, folder_jpg)
                        shutil.copyfile(matched_cover, cover_jpg)

                with open(strm_path, "w", encoding="utf-8") as f_strm:
                    f_strm.write(f"#EXTINF:0,{track:02d} - {safe_title}\n")
                    f_strm.write(stream_url.strip() + "\n")
                stats["music"] += 1
                stats["total_files"] += 1
                continue

    print("Gerada biblioteca com sucesso!")
    print(json.dumps(stats, indent=2))
    return stats

if __name__ == "__main__":
    out = "/tmp/kodi_universal_lib"
    import shutil
    if os.path.exists(out):
        shutil.rmtree(out)
    generate_library(out)
