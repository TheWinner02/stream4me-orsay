#!/usr/bin/env python3
"""
Stream4Me Backend Bridge for Samsung Orsay Kodi TV.
Gestore universale per navigazione ad albero Kodi, scraping multi-canale e risoluzione flussi video.
"""

import sys
import os
import re
import time
import urllib.parse

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

# Auto-detect venv se presente localmente o nel server adiacente
possible_venvs = [
    os.path.join(CURRENT_DIR, "venv", "lib", f"python{sys.version_info.major}.{sys.version_info.minor}", "site-packages"),
    os.path.join(CURRENT_DIR, "..", "kodi-orsay", "venv", "lib", f"python{sys.version_info.major}.{sys.version_info.minor}", "site-packages"),
    os.path.join(CURRENT_DIR, "..", "SamsungServer", "venv", "lib", "python3.12", "site-packages"),
    "/home/thewinner02/SamsungServer/venv/lib/python3.12/site-packages"
]
for vpath in possible_venvs:
    if os.path.exists(vpath) and vpath not in sys.path:
        sys.path.insert(0, vpath)

# Configura ambiente Kodi / sakee universale
kodi_home = os.path.expanduser("~")
os.environ["KODI_HOME"] = kodi_home
userdata_dir = os.path.join(kodi_home, "userdata")
os.makedirs(userdata_dir, exist_ok=True)
os.environ["KODI_PROFILE"] = userdata_dir

# La root dell'addon è CURRENT_DIR (dove si trova addon.xml)
ADDON_DIR = CURRENT_DIR
if ADDON_DIR not in sys.path:
    sys.path.insert(0, ADDON_DIR)

# Inizializza sakee (emulatore runtime Kodi)
old_cwd = os.getcwd()
os.chdir(ADDON_DIR)
try:
    import sakee
except Exception as e:
    print(f"[STREAM4ME] Errore import sakee: {e}", flush=True)

from core.item import Item
from platformcode import platformtools, config
from core import servertools
import channelselector
from channels import streamingcommunity
from servers import streamingcommunityws

# Disattiva scraper TMDB sincrono per rendere la navigazione nei canali istantanea e senza errori
try:
    config.set_setting('tmdb_active', False)
except Exception:
    pass

os.chdir(old_cwd)

_cache = {
    "movies": {"data": [], "timestamp": 0},
    "series": {"data": [], "timestamp": 0}
}

def clean_title(title):
    if not title:
        return "Senza Titolo"
    # Rimuove tutti i tag Kodi come [B], [/B], [COLOR 0xFF...], [/COLOR], ecc.
    t = re.sub(r'\[/?[a-zA-Z0-9_ =#]+\]', '', str(title)).strip()
    return t if t else "Senza Titolo"

def format_thumbnail(thumb_url, fallback="assets/icons/estuary_movies.png"):
    if not thumb_url:
        return fallback
    thumb_str = str(thumb_url)
    if thumb_str.startswith("http://") or thumb_str.startswith("https://"):
        return "/img?url=" + urllib.parse.quote(thumb_str, safe='')
    if thumb_str.startswith("assets/icons/movies.png"):
        return "assets/icons/estuary_movies.png"
    if thumb_str.startswith("assets/icons/tvshows.png"):
        return "assets/icons/estuary_tvshows.png"
    if thumb_str.startswith("assets/icons/addons.png"):
        return "assets/icons/estuary_addons.png"
    return thumb_str

def get_root_menu():
    """Restituisce le categorie principali del menu radice di Kodi."""
    return [
        {
            "id": "root_movies",
            "title": "Film",
            "plot": "Esplora film per canale, novità, categorie e più visti.",
            "channel": "channelselector",
            "action": "filterchannels",
            "channel_type": "movie",
            "url": "",
            "folder": True,
            "badge": "18 Canali",
            "thumbnail": "assets/icons/movies.png"
        },
        {
            "id": "root_series",
            "title": "Serie TV",
            "plot": "Tutte le serie TV italiane e internazionali con stagioni ed episodi.",
            "channel": "channelselector",
            "action": "filterchannels",
            "channel_type": "tvshow",
            "url": "",
            "folder": True,
            "badge": "19 Canali",
            "thumbnail": "assets/icons/tvshows.png"
        },
        {
            "id": "root_anime",
            "title": "Anime",
            "plot": "Anime in streaming ITA e SUB-ITA (AnimeSaturn, AnimeUnity, AnimeWorld).",
            "channel": "channelselector",
            "action": "filterchannels",
            "channel_type": "anime",
            "url": "",
            "folder": True,
            "badge": "5 Canali",
            "thumbnail": "assets/icons/movies.png"
        },
        {
            "id": "root_live",
            "title": "Canali TV Live",
            "plot": "Dirette TV in streaming: Pluto TV (130+ canali gratuiti), Rai Play, Mediaset.",
            "channel": "channelselector",
            "action": "filterchannels",
            "channel_type": "live",
            "url": "",
            "folder": True,
            "badge": "6 Canali",
            "thumbnail": "assets/icons/tvshows.png"
        },
        {
            "id": "root_channels",
            "title": "Tutti i Canali",
            "plot": "Elenco completo di tutti i 40+ canali e provider di Stream4Me.",
            "channel": "channelselector",
            "action": "filterchannels",
            "channel_type": "all",
            "url": "",
            "folder": True,
            "badge": "40+ Canali",
            "thumbnail": "assets/icons/addons.png"
        },
        {
            "id": "root_search",
            "title": "Cerca...",
            "plot": "Ricerca globale per titolo o attore in tutti i canali.",
            "channel": "search",
            "action": "search",
            "url": "",
            "folder": False,
            "is_search": True,
            "badge": "Cerca",
            "thumbnail": "assets/icons/search.png"
        }
    ]

def navigate(channel='root', action='', url='', extra='', channel_type='', query='', args='', category='', page=''):
    """
    Router universale per navigare nella struttura ad albero di Kodi / Stream4Me.
    Restituisce un array di elementi JSON pronti per la TV Orsay.
    """
    print(f"[NAVIGATE] ch={channel}, act={action}, type={channel_type}, url={url[:60]}, args={args}, q={query}", flush=True)

    # 1. Menu Radice
    if channel in ['root', ''] and not action:
        return get_root_menu()

    # 2. Ricerca
    if channel == 'search' or action == 'search':
        if not query:
            return []
        return search(query)

    # 3. Channel Selector (Categorie)
    if channel in ['channelselector', 'addons', 'addon']:
        if channel in ['addons', 'addon'] or channel_type in ['addons', 'addon']:
            return [
                {
                    "id": "addon_stream4me_all",
                    "title": "Stream4Me - Tutti i Canali",
                    "plot": "Accesso all'intero catalogo di tutti i canali e scraper di Stream4Me.",
                    "channel": "channelselector",
                    "action": "filterchannels",
                    "channel_type": "all",
                    "url": "",
                    "folder": True,
                    "badge": "24 Canali",
                    "thumbnail": "assets/icons/addons.png"
                },
                {
                    "id": "addon_movies",
                    "title": "Add-on Video: Canali Film",
                    "plot": "Canali dedicati allo streaming di film: StreamingCommunity, CB01, Casacinema, Altadefinizione...",
                    "channel": "channelselector",
                    "action": "filterchannels",
                    "channel_type": "movie",
                    "url": "",
                    "folder": True,
                    "badge": "18 Canali",
                    "thumbnail": "assets/icons/movies.png"
                },
                {
                    "id": "addon_series",
                    "title": "Add-on Video: Canali Serie TV",
                    "plot": "Canali dedicati alle serie TV: StreamingCommunity, GuardaserieX, Eurostreaming, CB01...",
                    "channel": "channelselector",
                    "action": "filterchannels",
                    "channel_type": "tvshow",
                    "url": "",
                    "folder": True,
                    "badge": "19 Canali",
                    "thumbnail": "assets/icons/tvshows.png"
                },
                {
                    "id": "addon_anime",
                    "title": "Add-on Video: Canali Anime",
                    "plot": "Canali anime streaming: AnimeWorld, AnimeSaturn, AnimeUnity, ToonItalia...",
                    "channel": "channelselector",
                    "action": "filterchannels",
                    "channel_type": "anime",
                    "url": "",
                    "folder": True,
                    "badge": "5 Canali",
                    "thumbnail": "assets/icons/movies.png"
                },
                {
                    "id": "addon_live",
                    "title": "Add-on Video: Canali TV Live",
                    "plot": "Canali TV lineari in diretta: Pluto TV (136 canali), Rai Play, Mediaset...",
                    "channel": "channelselector",
                    "action": "filterchannels",
                    "channel_type": "live",
                    "url": "",
                    "folder": True,
                    "badge": "6 Canali",
                    "thumbnail": "assets/icons/livetv.png"
                },
                {
                    "id": "addon_music",
                    "title": "Add-on Musica: Radio & Stazioni",
                    "plot": "Stazioni radio online e generi musicali in streaming gratuito: AccuRadio e TuneIn.",
                    "channel": "channelselector",
                    "action": "filterchannels",
                    "channel_type": "music",
                    "url": "",
                    "folder": True,
                    "badge": "Musica Live",
                    "thumbnail": "assets/icons/music.png"
                }
            ]

        if action == 'filterchannels' or channel_type:
            cat = channel_type or 'all'
            raw_channels = channelselector.filterchannels(cat) or []
            results = []

            # Scorciatoie veloci in primo piano in base alla categoria
            if cat == 'movie':
                results.append({
                    "id": "quick_sc_movies",
                    "title": "★ StreamingCommunity - Film Popolari",
                    "plot": "I migliori film del momento in streaming HD con audio italiano.",
                    "channel": "streamingcommunity",
                    "action": "peliculas",
                    "url": "https://streamingcommunityz.build/it/movies",
                    "folder": True,
                    "badge": "In Evidenza",
                    "thumbnail": format_thumbnail("https://raw.githubusercontent.com/Stream4me/media/master/resources/thumb/streamingcommunity.png")
                })
                results.append({
                    "id": "quick_cb01_movies",
                    "title": "★ CB01 - Ultime Uscite Cinema HD",
                    "plot": "Novità cinema e streaming ad altissima definizione da Cineblog01.",
                    "channel": "cineblog01",
                    "action": "peliculas",
                    "url": "https://cb01one.com/",
                    "folder": True,
                    "badge": "Novità HD",
                    "thumbnail": format_thumbnail("https://raw.githubusercontent.com/Stream4me/media/master/resources/thumb/cb01.png")
                })
            elif cat == 'tvshow':
                results.append({
                    "id": "quick_sc_series",
                    "title": "★ StreamingCommunity - Serie Popolari",
                    "plot": "Le serie TV più seguite con tutte le stagioni ed episodi completi.",
                    "channel": "streamingcommunity",
                    "action": "peliculas",
                    "url": "https://streamingcommunityz.build/it/tv-shows",
                    "folder": True,
                    "badge": "In Evidenza",
                    "thumbnail": format_thumbnail("https://raw.githubusercontent.com/Stream4me/media/master/resources/thumb/streamingcommunity.png")
                })
            elif cat == 'anime':
                results.append({
                    "id": "quick_anime_saturn",
                    "title": "★ AnimeSaturn - Anime In Corso",
                    "plot": "Gli anime della stagione corrente aggiornati ogni settimana.",
                    "channel": "animesaturn",
                    "action": "peliculas",
                    "url": "https://www.animesaturn.net/ongoing",
                    "folder": True,
                    "badge": "In Corso",
                    "thumbnail": format_thumbnail("https://raw.githubusercontent.com/Stream4me/media/master/resources/thumb/animesaturn.png")
                })
            elif cat == 'live':
                results.append({
                    "id": "quick_rai_live",
                    "title": "★ Rai Play - Dirette TV (Rai 1, 2, 3...)",
                    "plot": "Tutti i canali televisivi Rai in diretta streaming.",
                    "channel": "raiplay",
                    "action": "live",
                    "url": "",
                    "args": "/palinsesto/onAir.json",
                    "folder": True,
                    "badge": "Dirette Rai",
                    "thumbnail": format_thumbnail("https://raw.githubusercontent.com/Stream4me/media/master/resources/thumb/raiplay.png")
                })
                results.append({
                    "id": "quick_mediaset_live",
                    "title": "★ Mediaset Infinity - Dirette TV",
                    "plot": "Canale 5, Italia 1, Rete 4, 20 Mediaset, Focus, Iris in diretta.",
                    "channel": "mediasetplay",
                    "action": "live",
                    "url": "",
                    "folder": True,
                    "badge": "Dirette Mediaset",
                    "thumbnail": format_thumbnail("https://raw.githubusercontent.com/Stream4me/media/master/resources/thumb/mediasetplayinfinity.png")
                })
                results.append({
                    "id": "quick_pluto_live",
                    "title": "★ Pluto TV - Dirette Live (136 Canali)",
                    "plot": "Canali TV lineari tematici in streaming continuo gratuito.",
                    "channel": "plutotv",
                    "action": "live",
                    "url": "https://pluto.tv/us/watch/live-tv/it/live-tv/",
                    "folder": True,
                    "badge": "136 Canali",
                    "thumbnail": format_thumbnail("https://raw.githubusercontent.com/Stream4me/media/master/resources/thumb/plutotv.png")
                })
            elif cat == 'music':
                results.append({
                    "id": "quick_accuradio",
                    "title": "★ AccuRadio - 70+ Canali Radio & Generi",
                    "plot": "Centinaia di canali radio musicali suddivisi per generi ed epoche.",
                    "channel": "accuradio",
                    "action": "mainlist",
                    "url": "https://www.accuradio.com/c/m/json/brands/",
                    "folder": True,
                    "badge": "Musica Live",
                    "thumbnail": format_thumbnail("https://raw.githubusercontent.com/Stream4me/media/master/resources/thumb/accuradio.png")
                })

            for i, ch in enumerate(raw_channels):
                title = clean_title(ch.title)
                # Ignoriamo i canali vuoti o speciali
                if not title or title.startswith("Migrazione"):
                    continue
                results.append({
                    "id": f"ch_{cat}_{ch.channel}_{i}",
                    "title": title,
                    "plot": ch.plot or f"Canale {title}",
                    "channel": ch.channel,
                    "action": ch.action or "mainlist",
                    "url": ch.url or "",
                    "extra": getattr(ch, "extra", "") or "",
                    "args": getattr(ch, "args", "") or "",
                    "category": getattr(ch, "category", "") or "",
                    "page": getattr(ch, "page", "") or "",
                    "folder": True,
                    "badge": "Canale",
                    "thumbnail": format_thumbnail(ch.thumbnail, "assets/icons/addons.png")
                })
            return results

    # 4. Navigazione generica dentro un Canale Stream4Me
    try:
        mod = platformtools.channelImport(channel)
        if not mod:
            print(f"[STREAM4ME] Canale '{channel}' non trovato!", flush=True)
            return []

        fn_action = action or 'mainlist'
        if channel == 'raiplay' and fn_action == 'live' and not args:
            args = '/palinsesto/onAir.json'
        if channel == 'accuradio' and fn_action in ['genres', '']:
            fn_action = 'mainlist'
        if channel == 'cineblog01' and fn_action == 'peliculas' and not url:
            url = getattr(mod, 'host', 'https://cb01one.com/')
        if channel == 'animeworld' and fn_action == 'peliculas' and not args:
            args = 'noorder'
        if not hasattr(mod, fn_action):
            print(f"[STREAM4ME] Canale '{channel}' non ha l'azione '{fn_action}'!", flush=True)
            return []

        it = Item(channel=channel, action=fn_action, url=url)
        if extra: it.extra = extra
        if args: it.args = args
        if category: it.category = category
        if page: it.page = page

        raw_items = getattr(mod, fn_action)(it) or []
        results = []

        for i, item in enumerate(raw_items):
            title = clean_title(item.title)
            if not title or "migrazione" in title.lower():
                continue

            # Determinazione se è una cartella o un video riproducibile
            is_folder = True
            is_playable = False
            item_act = item.action or ""
            stream_direct = getattr(item, 'videourl', None) or getattr(item, 'video_url', None) or getattr(item, 'stream_url', None) or ""

            if stream_direct and (".m3u8" in stream_direct or ".mp4" in stream_direct or ".mp3" in stream_direct or "stitch" in stream_direct):
                is_folder = False
                is_playable = True
            elif item_act in ['findvideos', 'play'] or item.server:
                is_folder = False
                is_playable = True
            elif hasattr(item, "folder") and item.folder is False:
                is_folder = False
                is_playable = True

            # Se è una serie (action 'episodios') o un sottomenu, è una cartella!
            if item_act in ['episodios', 'temporadas', 'check', 'seasons', 'menu', 'category', 'genres', 'submenu']:
                is_folder = True
                is_playable = False

            thumb = format_thumbnail(item.contentThumbnail or item.thumbnail or getattr(item, "fanart", None))
            year = str(item.infoLabels.get("year", "")) if hasattr(item, "infoLabels") and item.infoLabels else ""
            rating = ""
            if hasattr(item, "infoLabels") and item.infoLabels and item.infoLabels.get("rating"):
                try:
                    rating = str(round(float(item.infoLabels["rating"]), 1))
                except Exception:
                    rating = str(item.infoLabels["rating"])

            item_args = item.args if hasattr(item, "args") and item.args is not None else ""
            item_extra = item.extra if hasattr(item, "extra") and item.extra is not None else ""
            item_cat = item.category if hasattr(item, "category") and item.category is not None else ""
            item_page = str(item.page) if hasattr(item, "page") and item.page is not None else ""
            effective_url = stream_direct or item.url or ""

            entry = {
                "id": f"{channel}_{fn_action}_{i}",
                "title": title,
                "plot": item.plot or "",
                "channel": item.channel or channel,
                "action": item_act,
                "url": effective_url,
                "extra": item_extra,
                "args": item_args,
                "category": item_cat,
                "page": item_page,
                "server": item.server or "",
                "folder": is_folder,
                "is_playable": is_playable,
                "year": year,
                "rating": rating,
                "badge": "Episodio" if "episod" in item_act or "x" in title[:5] else ("Cartella" if is_folder else "Video HD"),
                "thumbnail": thumb,
                "poster": thumb
            }

            if is_playable:
                entry["stream"] = f"/api/resolve?channel={entry['channel']}&action={entry['action']}&url={urllib.parse.quote(entry['url'], safe='')}&server={entry['server']}"

            results.append(entry)

        return results
    except Exception as e:
        print(f"[STREAM4ME-NAV-ERR] Errore navigazione {channel}.{action}: {e}", flush=True)
        return []

def resolve_video(channel, action, url, server=None):
    """
    Risolve un elemento video selezionato in una URL HLS diretta pronta per il proxy.
    """
    print(f"[RESOLVE-VID] ch={channel}, act={action}, srv={server}, url={url[:80]}", flush=True)

    # Caso 1: È già un flusso HLS diretto (.m3u8 o stream di Pluto TV)
    if url and (".m3u8" in url or "pluto.tv/v2/stitch" in url):
        return url

    # Caso 2: StreamingCommunity
    if channel == "streamingcommunity" or "streamingcommunity" in url or server == "streamingcommunityws":
        if "watch" in url:
            # È una pagina film, estraiamo l'iframe
            try:
                it = Item(channel='streamingcommunity', action='findvideos', url=url)
                videos = streamingcommunity.findvideos(it) or []
                if videos:
                    url = videos[0].url
                    print(f"[RESOLVE-VID] Estratto iframe StreamingCommunity: {url}", flush=True)
            except Exception as e:
                print(f"[RESOLVE-VID] Errore findvideos SC: {e}", flush=True)

        if "iframe" in url or server == "streamingcommunityws":
            try:
                exists, msg = streamingcommunityws.test_video_exists(url)
                if exists:
                    resolved = streamingcommunityws.get_video_url(url)
                    if resolved and len(resolved) > 0:
                        direct = resolved[0][1]
                        print(f"[RESOLVE-VID] StreamingCommunity HLS: {direct}", flush=True)
                        return direct
            except Exception as e:
                print(f"[RESOLVE-VID] Errore streamingcommunityws: {e}", flush=True)

    # Caso 3: Server esplicito (es. mixdrop, supervideo, akvideo, etc.)
    if server:
        try:
            vids, ok, err = servertools.resolve_video_urls_for_playing(server, url)
            if vids and len(vids) > 0:
                direct = vids[0][1]
                print(f"[RESOLVE-VID] Server {server} risolto: {direct}", flush=True)
                return direct
        except Exception as e:
            print(f"[RESOLVE-VID] Errore risoluzione server {server}: {e}", flush=True)

    # Caso 4: Canale con metodo findvideos
    try:
        mod = platformtools.channelImport(channel)
        if mod and hasattr(mod, 'findvideos'):
            it = Item(channel=channel, action=action, url=url)
            if channel == 'raiplay':
                it.video_url = url + '.json' if not url.endswith('.json') else url
            vids = mod.findvideos(it) or []
            print(f"[RESOLVE-VID] Trovati {len(vids)} server in {channel}", flush=True)
            for v in vids:
                if not v.server or not v.url:
                    continue
                # Se è directo
                if v.server == "directo":
                    return v.url
                # Risolvi con servertools
                try:
                    res, ok, err = servertools.resolve_video_urls_for_playing(v.server, v.url)
                    if res and len(res) > 0:
                        direct = res[0][1]
                        print(f"[RESOLVE-VID] Risolto da {v.server}: {direct}", flush=True)
                        return direct
                except Exception:
                    pass
    except Exception as e:
        print(f"[RESOLVE-VID] Errore findvideos generico: {e}", flush=True)

    return None

def resolve_stream(watch_url):
    """Wrapper di retrocompatibilità per server.py"""
    return resolve_video("streamingcommunity", "findvideos", watch_url, "streamingcommunityws")

def get_movies(force_refresh=False):
    now = time.time()
    if not force_refresh and _cache["movies"]["data"] and (now - _cache["movies"]["timestamp"] < 1800):
        return _cache["movies"]["data"]

    print("[STREAM4ME] Scraping film in evidenza da StreamingCommunity...", flush=True)
    try:
        it = Item(channel='streamingcommunity', action='peliculas', url='https://streamingcommunityz.build/it/movies')
        raw_items = streamingcommunity.peliculas(it) or []
        movies = []
        for i, item in enumerate(raw_items):
            if not item.url or "watch" not in item.url:
                continue
            title = clean_title(item.contentTitle or item.title)
            poster = format_thumbnail(item.contentThumbnail or item.thumbnail)
            plot = item.plot or "Nessuna trama disponibile."
            year = str(item.infoLabels.get("year", "2024")) if hasattr(item, "infoLabels") and item.infoLabels else "2024"
            rating = str(round(item.infoLabels.get("rating", 7.8), 1)) if hasattr(item, "infoLabels") and item.infoLabels and "rating" in item.infoLabels else "7.8"

            movies.append({
                "id": f"sc_m_{i}",
                "title": title,
                "year": year,
                "rating": rating,
                "genre": "Film Streaming",
                "plot": plot,
                "poster": poster,
                "thumbnail": poster,
                "watch_url": item.url,
                "stream": f"/stream?url={urllib.parse.quote(item.url, safe='')}",
                "quality": "HD 1080p / 720p",
                "folder": False,
                "is_playable": True
            })

        if movies:
            _cache["movies"]["data"] = movies
            _cache["movies"]["timestamp"] = now
            return movies
    except Exception as e:
        print(f"[STREAM4ME] Errore scraping film: {e}", flush=True)

    return _cache["movies"]["data"]

def get_series(force_refresh=False):
    now = time.time()
    if not force_refresh and _cache["series"]["data"] and (now - _cache["series"]["timestamp"] < 1800):
        return _cache["series"]["data"]

    print("[STREAM4ME] Scraping serie TV in evidenza da StreamingCommunity...", flush=True)
    try:
        it = Item(channel='streamingcommunity', action='peliculas', url='https://streamingcommunityz.build/it/tv-shows')
        raw_items = streamingcommunity.peliculas(it) or []
        series = []
        for i, item in enumerate(raw_items):
            if not item.url:
                continue
            title = clean_title(item.contentTitle or item.title)
            poster = format_thumbnail(item.contentThumbnail or item.thumbnail)
            plot = item.plot or "Nessuna trama disponibile."
            year = str(item.infoLabels.get("year", "2024")) if hasattr(item, "infoLabels") and item.infoLabels else "2024"
            rating = str(round(item.infoLabels.get("rating", 8.0), 1)) if hasattr(item, "infoLabels") and item.infoLabels and "rating" in item.infoLabels else "8.0"

            series.append({
                "id": f"sc_s_{i}",
                "title": title,
                "year": year,
                "rating": rating,
                "genre": "Serie TV",
                "plot": plot,
                "poster": poster,
                "thumbnail": poster,
                "watch_url": item.url,
                "channel": "streamingcommunity",
                "action": "episodios",
                "url": item.url,
                "quality": "HD Serie",
                "folder": True,
                "is_playable": False
            })

        if series:
            _cache["series"]["data"] = series
            _cache["series"]["timestamp"] = now
            return series
    except Exception as e:
        print(f"[STREAM4ME] Errore scraping serie: {e}", flush=True)

    return _cache["series"]["data"]

def search(query):
    print(f"[STREAM4ME] Ricerca per: '{query}'...", flush=True)
    results = []
    try:
        it = Item(channel='streamingcommunity', action='search')
        raw_items = streamingcommunity.search(it, query) or []
        for i, item in enumerate(raw_items):
            if not item.url:
                continue
            title = clean_title(item.contentTitle or item.title)
            poster = format_thumbnail(item.contentThumbnail or item.thumbnail)
            plot = item.plot or "Nessuna trama disponibile."
            year = str(item.infoLabels.get("year", "2024")) if hasattr(item, "infoLabels") and item.infoLabels else "2024"
            rating = str(round(item.infoLabels.get("rating", 7.5), 1)) if hasattr(item, "infoLabels") and item.infoLabels and "rating" in item.infoLabels else "7.5"

            is_folder = True
            is_playable = False
            act = item.action or "findvideos"
            if act in ["findvideos", "play"] or "watch" in item.url:
                is_folder = False
                is_playable = True

            results.append({
                "id": f"sc_q_{i}",
                "title": title,
                "year": year,
                "rating": rating,
                "genre": "Risultato Ricerca",
                "plot": plot,
                "poster": poster,
                "thumbnail": poster,
                "channel": item.channel or "streamingcommunity",
                "action": act,
                "url": item.url,
                "folder": is_folder,
                "is_playable": is_playable,
                "stream": f"/api/resolve?channel=streamingcommunity&action={act}&url={urllib.parse.quote(item.url, safe='')}&server=streamingcommunityws" if is_playable else "",
                "quality": "HD"
            })
        print(f"[STREAM4ME] Trovati {len(results)} risultati per '{query}'.", flush=True)
    except Exception as e:
        print(f"[STREAM4ME] Errore ricerca '{query}': {e}", flush=True)

    return results

if __name__ == "__main__":
    print("Test Stream4Me Backend:")
    root = navigate('root')
    print("Elementi Root:", len(root))
    for r in root:
        print(" ", r["title"], "| type:", r["channel_type"] if "channel_type" in r else r["channel"])
