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
sudo_user = os.environ.get("SUDO_USER")
if sudo_user and os.path.exists(f"/home/{sudo_user}"):
    real_home = f"/home/{sudo_user}"
else:
    real_home = os.path.expanduser("~")
    if real_home == "/root" and os.path.exists("/home/thewinner02"):
        real_home = "/home/thewinner02"

kodi_home = os.environ.get("KODI_HOME") or real_home
os.environ["KODI_HOME"] = kodi_home
userdata_dir = os.environ.get("KODI_PROFILE") or os.path.join(kodi_home, "userdata")
os.makedirs(userdata_dir, exist_ok=True)
os.environ["KODI_PROFILE"] = userdata_dir

# La root dell'addon è CURRENT_DIR (dove si trova addon.xml) o sottocartella stream4me
if os.path.exists(os.path.join(CURRENT_DIR, "stream4me", "addon.xml")):
    ADDON_DIR = os.path.join(CURRENT_DIR, "stream4me")
else:
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

# Configura TMDB e timeout di rete resilienti per recupero trame su tutti i canali
try:
    config.set_setting('tmdb_active', True)
    config.set_setting('tmdb_plus_info', True)
    config.set_setting('httptools_timeout', 15)
    from core import httptools
    httptools.HTTPTOOLS_DEFAULT_DOWNLOAD_TIMEOUT = 15
except Exception as e:
    print(f"[STREAM4ME] Config TMDB/Timeout warning: {e}", flush=True)

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
    t = re.sub(r'^(?:Add-on\s+(?:Video|Musica)\s*:\s*|Add-on\s*:\s*)', '', t, flags=re.I).strip()
    return t if t else "Senza Titolo"

def extract_plot(item):
    """
    Estrae la trama in modo esaustivo da tutti i possibili campi Kodi / S4Me.
    """
    raw_plot = (
        getattr(item, 'plot', None) or
        getattr(item, 'contentPlot', None) or
        (item.infoLabels.get('plot') if hasattr(item, 'infoLabels') and item.infoLabels else None) or
        (item.infoLabels.get('plotoutline') if hasattr(item, 'infoLabels') and item.infoLabels else None) or
        getattr(item, 'synopsis', None) or
        getattr(item, 'description', None) or
        ""
    )
    if not raw_plot:
        return ""
    clean_p = re.sub(r'\[/?[a-zA-Z0-9_ =#]+\]', '', str(raw_plot))
    clean_p = re.sub(r'<[^>]+>', '', clean_p).strip()
    return clean_p

def enrich_missing_plots(results, raw_items=None):
    """
    Se alcuni elementi multimediali (film/serie/episodi) non hanno trama fornita dal canale,
    interroga TMDB in parallelo per ottenere sinossi, valutazione e locandina in italiano.
    """
    from concurrent import futures
    to_enrich = []
    for i, entry in enumerate(results):
        if entry.get("is_next_page"):
            continue
        if not entry.get("plot") and entry.get("title"):
            raw_it = entry.get("_raw_item") or (raw_items[i] if raw_items and i < len(raw_items) else None)
            is_media = (
                entry.get("is_playable") or
                entry.get("action") in ['findvideos', 'play', 'episodios', 'check', 'temporadas'] or
                (raw_it and getattr(raw_it, 'contentType', '') in ['movie', 'tvshow'])
            )
            if not is_media:
                continue
            t_low = entry["title"].lower()
            if any(w in t_low for w in ['pagina successiva', 'next page', 'successivo', 'avanti', 'cerca', 'categorie', 'generi', 'canale', 'archivio', 'opzioni']):
                continue
            to_enrich.append(entry)

    if not to_enrich:
        return

    def worker(ent):
        try:
            from core import tmdb
            clean_name = re.sub(r'\[.*?\]|\(.*?\)', '', ent["title"]).strip()
            if not clean_name or len(clean_name) < 2:
                return
            raw_it = ent.get("_raw_item")
            c_type = getattr(raw_it, 'contentType', 'movie') if raw_it else 'movie'
            if c_type not in ['movie', 'tvshow']:
                c_type = 'tvshow' if ent.get('folder') or ent.get('action') == 'episodios' else 'movie'

            enrich_it = raw_it.clone() if raw_it else Item()
            enrich_it.contentType = c_type
            enrich_it.contentTitle = clean_name
            enrich_it.year = ent.get("year", "")

            tmdb.set_infoLabels(enrich_it, seekTmdb=True, forced=True)
            if hasattr(enrich_it, "infoLabels") and enrich_it.infoLabels:
                p = enrich_it.infoLabels.get("plot")
                if p:
                    ent["plot"] = re.sub(r'\[/?[a-zA-Z0-9_ =#]+\]', '', str(p)).strip()
                if not ent.get("rating") and enrich_it.infoLabels.get("rating"):
                    try:
                        ent["rating"] = str(round(float(enrich_it.infoLabels["rating"]), 1))
                    except Exception:
                        ent["rating"] = str(enrich_it.infoLabels["rating"])
                if not ent.get("year") and enrich_it.infoLabels.get("year"):
                    ent["year"] = str(enrich_it.infoLabels["year"])
                if (not ent.get("thumbnail") or "estuary" in ent["thumbnail"]) and enrich_it.infoLabels.get("thumbnail"):
                    ent["thumbnail"] = format_thumbnail(enrich_it.infoLabels["thumbnail"])
                    ent["poster"] = ent["thumbnail"]
        except Exception:
            pass

    with futures.ThreadPoolExecutor(max_workers=min(len(to_enrich), 8)) as executor:
        list(executor.map(worker, to_enrich))

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
    if thumb_str.startswith("assets/icons/livetv.png"):
        return "assets/icons/estuary_tv.png"
    if thumb_str.startswith("assets/icons/music.png"):
        return "assets/icons/estuary_radio.png"
    if thumb_str.startswith("assets/icons/favorites.png"):
        return "assets/icons/estuary_favourites.png"
    if thumb_str.startswith("assets/icons/search.png"):
        return "assets/icons/estuary_search.png"
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

_PAGINATION_CACHE = {}
_ITEM_PAYLOAD_CACHE = {}

def navigate(channel='root', action='', url='', extra='', channel_type='', query='', args='', category='', page='', records_token=''):
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
                    "id": "addon_movies",
                    "title": "Film",
                    "plot": "Canali dedicati allo streaming di film: StreamingCommunity, CB01, Casacinema, Altadefinizione...",
                    "channel": "channelselector",
                    "action": "filterchannels",
                    "channel_type": "movie",
                    "url": "",
                    "folder": True,
                    "badge": "18 Canali",
                    "thumbnail": "assets/icons/estuary_movies.png"
                },
                {
                    "id": "addon_series",
                    "title": "Serie TV",
                    "plot": "Canali dedicati alle serie TV: StreamingCommunity, Guardaserie, Eurostreaming, CB01...",
                    "channel": "channelselector",
                    "action": "filterchannels",
                    "channel_type": "tvshow",
                    "url": "",
                    "folder": True,
                    "badge": "19 Canali",
                    "thumbnail": "assets/icons/estuary_tvshows.png"
                },
                {
                    "id": "addon_anime",
                    "title": "Anime",
                    "plot": "Canali anime streaming: AnimeWorld, AnimeSaturn, AnimeUnity, ToonItalia...",
                    "channel": "channelselector",
                    "action": "filterchannels",
                    "channel_type": "anime",
                    "url": "",
                    "folder": True,
                    "badge": "5 Canali",
                    "thumbnail": "assets/icons/estuary_movies.png"
                },
                {
                    "id": "addon_live",
                    "title": "TV Live",
                    "plot": "Canali TV lineari in diretta: Pluto TV (136 canali), Rai Play, Mediaset...",
                    "channel": "channelselector",
                    "action": "filterchannels",
                    "channel_type": "live",
                    "url": "",
                    "folder": True,
                    "badge": "6 Canali",
                    "thumbnail": "assets/icons/estuary_tv.png"
                },
                {
                    "id": "addon_music",
                    "title": "Radio & Musica",
                    "plot": "Stazioni radio online e generi musicali in streaming gratuito: AccuRadio e TuneIn.",
                    "channel": "channelselector",
                    "action": "filterchannels",
                    "channel_type": "music",
                    "url": "",
                    "folder": True,
                    "badge": "Musica Live",
                    "thumbnail": "assets/icons/estuary_radio.png"
                },
                {
                    "id": "addon_stream4me_all",
                    "title": "Tutti i Canali",
                    "plot": "Accesso all'intero catalogo di tutti i canali e scraper di Stream4Me in ordine alfabetico.",
                    "channel": "channelselector",
                    "action": "filterchannels",
                    "channel_type": "all",
                    "url": "",
                    "folder": True,
                    "badge": "40+ Canali",
                    "thumbnail": "assets/icons/estuary_addons.png"
                },
                {
                    "id": "addon_favorites",
                    "title": "Preferiti",
                    "plot": "I tuoi film, serie ed episodi preferiti salvati per un accesso rapido.",
                    "channel": "special",
                    "action": "favorites",
                    "channel_type": "favorites",
                    "url": "",
                    "folder": True,
                    "badge": "I Tuoi Contenuti",
                    "thumbnail": "assets/icons/estuary_favourites.png"
                },
                {
                    "id": "addon_search",
                    "title": "Cerca",
                    "plot": "Cerca film, serie e anime per titolo in tutti i canali contemporaneamente.",
                    "channel": "search",
                    "action": "search",
                    "channel_type": "search",
                    "url": "",
                    "folder": True,
                    "badge": "Globale",
                    "thumbnail": "assets/icons/estuary_search.png"
                }
            ]

        if action == 'filterchannels' or channel_type:
            cat = channel_type or 'all'
            raw_channels = channelselector.filterchannels(cat) or []
            results = []

            # Scorciatoie veloci e ricerca contestuale in base alla categoria
            if cat == 'movie':
                results.append({
                    "id": "search_movies",
                    "title": "🔍 Cerca nei Film...",
                    "plot": "Cerca qualsiasi film per titolo o attore nei canali cinema (StreamingCommunity, Casacinema, Altadefinizione).",
                    "channel": "search",
                    "action": "search",
                    "channel_type": "movie",
                    "folder": False,
                    "is_search": True,
                    "badge": "Cerca Film",
                    "thumbnail": "assets/icons/estuary_search.png"
                })
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
                    "id": "search_series",
                    "title": "🔍 Cerca nelle Serie TV...",
                    "plot": "Cerca serie televisive per titolo in tutti i canali serie.",
                    "channel": "search",
                    "action": "search",
                    "channel_type": "tvshow",
                    "folder": False,
                    "is_search": True,
                    "badge": "Cerca Serie",
                    "thumbnail": "assets/icons/estuary_search.png"
                })
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
                    "id": "search_anime",
                    "title": "🔍 Cerca negli Anime...",
                    "plot": "Cerca anime ITA e Sub-ITA (AnimeSaturn, AnimeWorld, AnimeUnity).",
                    "channel": "search",
                    "action": "search",
                    "channel_type": "anime",
                    "folder": False,
                    "is_search": True,
                    "badge": "Cerca Anime",
                    "thumbnail": "assets/icons/estuary_search.png"
                })
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
            elif cat == 'all':
                results.append({
                    "id": "search_all",
                    "title": "🔍 Cerca Globale...",
                    "plot": "Ricerca in tutti i provider e canali Stream4Me.",
                    "channel": "search",
                    "action": "search",
                    "channel_type": "all",
                    "folder": False,
                    "is_search": True,
                    "badge": "Cerca Tutto",
                    "thumbnail": "assets/icons/estuary_search.png"
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

    # 4. Ricerca Globale o Filtrata per Sezione / Canale
    if channel == 'search' or action == 'search':
        return search_content(query, channel_type=channel_type, specific_channel=channel if channel not in ['search', 'root', 'channelselector'] else None)

    # 5. Navigazione generica dentro un Canale Stream4Me
    try:
        mod = platformtools.channelImport(channel)
        if not mod:
            print(f"[STREAM4ME] Canale '{channel}' non trovato!", flush=True)
            return []

        fn_action = action or 'mainlist'
        if fn_action == 'search':
            return search_content(query, channel_type=channel_type, specific_channel=channel)

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
        if page:
            if isinstance(page, str) and page.strip().isdigit():
                it.page = int(page.strip())
            else:
                try:
                    it.page = int(page)
                except Exception:
                    it.page = page
        if records_token and records_token in _PAGINATION_CACHE:
            it.records = _PAGINATION_CACHE[records_token]

        raw_items = getattr(mod, fn_action)(it) or []
        results = []

        for i, item in enumerate(raw_items):
            title = clean_title(item.title)
            if not title or "migrazione" in title.lower():
                continue

            item_act = item.action or ""
            t_low = title.lower()

            # Salta elementi di sistema Kodi (aggiunta a videoteca, configurazione canale, ecc.)
            if item_act in ['addToLibrary', 'add_serie_to_library', 'add_tvshow', 'channel_config', 'config', 'trailer', 'setting']:
                continue
            if any(ign in t_low for ign in ['add to video library', 'aggiungi alla videoteca', 'channel configuration', 'configura canale']):
                continue

            # Rilevamento Next Page / Pagina Successiva
            is_next = (
                getattr(item, 'nextPage', False) or
                getattr(item, 'next_page', False) or
                any(kw in t_low for kw in ['next page', 'pagina successiva', 'successivo', 'pagina seguente', 'avanti >', 'avanti >>']) or
                t_low.strip().startswith('>>') or t_low.strip().endswith('>>') or t_low.strip().endswith(' >')
            )

            # Determinazione se è una cartella o un video riproducibile
            is_folder = True
            is_playable = False
            stream_direct = getattr(item, 'videourl', None) or getattr(item, 'video_url', None) or getattr(item, 'stream_url', None) or ""

            if is_next:
                title = "▶ Pagina Successiva"
                is_folder = True
                is_playable = False
            elif stream_direct and (".m3u8" in stream_direct or ".mp4" in stream_direct or ".mp3" in stream_direct or "stitch" in stream_direct):
                is_folder = False
                is_playable = True
            elif item_act in ['findvideos', 'play'] or item.server:
                is_folder = False
                is_playable = True
            elif hasattr(item, "folder") and item.folder is False:
                is_folder = False
                is_playable = True

            # Se è una serie (action 'episodios') o un sottomenu, è una cartella!
            if not is_next and item_act in ['episodios', 'temporadas', 'check', 'seasons', 'menu', 'category', 'genres', 'submenu']:
                is_folder = True
                is_playable = False

            thumb = "assets/icons/cat_recently_updated.png" if is_next else format_thumbnail(item.contentThumbnail or item.thumbnail or getattr(item, "fanart", None))
            year = "" if is_next else (str(item.infoLabels.get("year", "")) if hasattr(item, "infoLabels") and item.infoLabels else "")
            rating = ""
            if not is_next and hasattr(item, "infoLabels") and item.infoLabels and item.infoLabels.get("rating"):
                try:
                    rating = str(round(float(item.infoLabels["rating"]), 1))
                except Exception:
                    rating = str(item.infoLabels["rating"])

            item_args = item.args if hasattr(item, "args") and item.args is not None else ""
            item_extra = item.extra if hasattr(item, "extra") and item.extra is not None else ""
            item_cat = item.category if hasattr(item, "category") and item.category is not None else ""
            item_page = str(item.page) if hasattr(item, "page") and item.page is not None else ""
            effective_url = stream_direct or item.url or ""

            plot = "Visualizza la pagina successiva di contenuti del canale." if is_next else extract_plot(item)

            # Estrazione stagione, episodio e numero per la corretta riproduzione dei seriali
            content_season = getattr(item, 'contentSeason', None)
            content_episode = getattr(item, 'contentEpisode', None) or getattr(item, 'contentEpisodeNumber', None)
            item_season = getattr(item, 'season', None) or content_season
            item_episode = getattr(item, 'episode', None) or content_episode
            item_num = getattr(item, 'number', None)

            if not is_next and (item_season is None or item_episode is None):
                m_se = re.search(r'\b(\d+)x0*(\d+)\b', title)
                if m_se:
                    if item_season is None: item_season = int(m_se.group(1))
                    if item_episode is None: item_episode = int(m_se.group(2))
                else:
                    m_ep = re.search(r'(?:Ep\.|Episodio|\bE\b|\bEp\b)\s*0*(\d+)\b', title, re.IGNORECASE)
                    if m_ep:
                        if item_episode is None: item_episode = int(m_ep.group(1))
                        if item_season is None: item_season = 1

            if not is_next and not item_num and item_episode is not None:
                item_num = str(item_episode)

            # Memorizza payload nel cache server per evitare url query strings enormi
            item_token = ""
            if not is_next:
                item_payload = {}
                if getattr(item, 'data', None): item_payload['data'] = item.data
                if getattr(item, 'other', None): item_payload['other'] = item.other
                if getattr(item, 'extra', None): item_payload['extra'] = item.extra
                if getattr(item, 'server_links', None): item_payload['server_links'] = item.server_links
                if getattr(item, 'contentTitle', None): item_payload['contentTitle'] = item.contentTitle
                if getattr(item, 'fulltitle', None): item_payload['fulltitle'] = item.fulltitle
                if item_season is not None: item_payload['season'] = item_season
                if item_episode is not None: item_payload['episode'] = item_episode
                if item_num is not None: item_payload['number'] = item_num
                if title: item_payload['title'] = title

                if item_payload:
                    if len(_ITEM_PAYLOAD_CACHE) > 500:
                        _ITEM_PAYLOAD_CACHE.clear()
                    item_token = f"it_{int(time.time()*1000)}_{i}"
                    _ITEM_PAYLOAD_CACHE[item_token] = item_payload

            is_ep = "episod" in item_act or bool(re.search(r'\b\d+x\d+\b', title)) or (item_episode is not None and not is_folder)
            badge = "Pagina +" if is_next else ("Episodio" if is_ep else ("Cartella" if is_folder else "Video HD"))

            entry = {
                "id": f"{channel}_{fn_action}_{i}",
                "title": title,
                "plot": plot,
                "channel": item.channel or channel,
                "action": item_act,
                "url": effective_url,
                "extra": item_extra,
                "args": item_args,
                "category": item_cat,
                "page": item_page,
                "server": item.server or "",
                "season": item_season if item_season is not None else "",
                "episode": item_episode if item_episode is not None else "",
                "number": item_num or "",
                "item_token": item_token,
                "folder": is_folder,
                "is_playable": is_playable,
                "is_next_page": is_next,
                "year": year,
                "rating": rating,
                "badge": badge,
                "thumbnail": thumb,
                "poster": thumb,
                "_raw_item": item
            }

            if is_next and getattr(item, 'records', None):
                if len(_PAGINATION_CACHE) > 100:
                    _PAGINATION_CACHE.clear()
                rec_token = f"rec_{int(time.time()*1000)}_{i}"
                _PAGINATION_CACHE[rec_token] = item.records
                entry["records_token"] = rec_token

            if is_playable:
                stream_params = {
                    "channel": entry["channel"],
                    "action": entry["action"],
                    "url": entry["url"],
                    "server": entry["server"]
                }
                if entry["title"]: stream_params["title"] = entry["title"]
                if entry["season"] != "": stream_params["season"] = str(entry["season"])
                if entry["episode"] != "": stream_params["episode"] = str(entry["episode"])
                if entry["number"]: stream_params["number"] = str(entry["number"])
                if entry["item_token"]: stream_params["item_token"] = entry["item_token"]
                entry["stream"] = "/api/resolve?" + urllib.parse.urlencode(stream_params)

            results.append(entry)

        # Arricchisce eventuali film o serie sprovvisti di trama interrogando TMDB in parallelo
        try:
            enrich_missing_plots(results)
        except Exception as e:
            print(f"[STREAM4ME] enrich_missing_plots error: {e}", flush=True)

        # Deduplicazione sicura per evitare vecchie memorie e duplicati
        seen_keys = set()
        deduped = []
        for ent in results:
            ent.pop("_raw_item", None)
            if ent.get("is_next_page"):
                if not any(x.get("is_next_page") for x in deduped):
                    deduped.append(ent)
                continue
            key = (ent.get("channel"), ent.get("action"), ent.get("url"), str(ent.get("season")), str(ent.get("episode")), ent.get("title", "").strip().lower())
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(ent)

        return deduped
    except Exception as e:
        print(f"[STREAM4ME-NAV-ERR] Errore navigazione {channel}.{action}: {e}", flush=True)
        return []

def resolve_video(channel, action, url, server=None, title=None, season=None, episode=None, number=None, item_token=None):
    """
    Risolve un elemento video selezionato in una URL HLS/MP4 diretta pronta per il proxy.
    Supporta fedelmente la selezione di qualsiasi episodio (primo o ultimo) e server streaming.
    """
    payload = _ITEM_PAYLOAD_CACHE.get(item_token, {}) if item_token else {}
    if not title:
        title = payload.get("title") or ""
    if season is None or season == "":
        season = payload.get("season")
    if episode is None or episode == "":
        episode = payload.get("episode")
    if not number:
        number = payload.get("number")

    print(f"[RESOLVE-VID] ch={channel}, act={action}, ep={season}x{episode}, srv={server}, url={url[:80]}", flush=True)

    # Caso 1: È già un flusso HLS diretto (.m3u8 o stream di Pluto TV)
    if url and (".m3u8" in url or "pluto.tv/v2/stitch" in url):
        return url

    # Prepara oggetto Item completo per i canali Kodi
    it = Item(channel=channel, action=action, url=url, server=server or "")
    if title:
        it.title = title
        it.contentTitle = title
        it.fulltitle = title

    if season is not None and str(season).isdigit():
        it.season = int(season)
        it.contentSeason = int(season)
    if episode is not None and str(episode).isdigit():
        it.episode = int(episode)
        it.contentEpisode = int(episode)
        it.contentEpisodeNumber = int(episode)
    if number:
        it.number = str(number)
    elif episode:
        it.number = str(episode)

    if payload.get("data"):
        it.data = payload["data"]
    if payload.get("other"):
        it.other = payload["other"]
    if payload.get("extra"):
        it.extra = payload["extra"]
    if payload.get("server_links"):
        it.server_links = payload["server_links"]
    if payload.get("contentTitle"):
        it.contentTitle = payload["contentTitle"]

    # Fallback estrazione s/e dal titolo se non forniti
    if (getattr(it, 'contentSeason', None) is None or getattr(it, 'contentEpisode', None) is None) and it.title:
        m_se = re.search(r'\b(\d+)x0*(\d+)\b', it.title)
        if m_se:
            it.season = int(m_se.group(1))
            it.contentSeason = int(m_se.group(1))
            it.episode = int(m_se.group(2))
            it.contentEpisode = int(m_se.group(2))
            if not getattr(it, 'number', None):
                it.number = str(it.contentEpisode)
        else:
            m_ep = re.search(r'(?:Ep\.|Episodio|\bE|\bEp)\s*0*(\d+)\b', it.title, re.I)
            if m_ep:
                it.episode = int(m_ep.group(1))
                it.contentEpisode = int(m_ep.group(1))
                if getattr(it, 'contentSeason', None) is None:
                    it.season = 1
                    it.contentSeason = 1
                if not getattr(it, 'number', None):
                    it.number = str(it.contentEpisode)

    # Caso 2: StreamingCommunity
    if channel == "streamingcommunity" or "streamingcommunity" in url or server == "streamingcommunityws":
        # Se l'URL è della serie (/titles/...) e non è già un iframe, trova l'episodio specifico!
        if ("watch" in url or "/titles/" in url) and "iframe" not in url:
            try:
                if getattr(it, 'contentEpisode', None) or getattr(it, 'episode', None):
                    eps = streamingcommunity.episodios(it) or []
                    target_ep = int(getattr(it, 'contentEpisode', None) or getattr(it, 'episode', None))
                    target_se = int(getattr(it, 'contentSeason', None) or getattr(it, 'season', None) or 1)
                    found_url = None
                    for ep_item in eps:
                        ep_num = getattr(ep_item, 'contentEpisodeNumber', None) or getattr(ep_item, 'episode', None)
                        ep_se = getattr(ep_item, 'contentSeason', None) or getattr(ep_item, 'season', None) or 1
                        if ep_num and int(ep_num) == target_ep and int(ep_se) == target_se:
                            found_url = ep_item.url
                            print(f"[RESOLVE-VID] SC Trovato episodio specifico {target_se}x{target_ep}: {found_url}", flush=True)
                            break
                    if not found_url:
                        for ep_item in eps:
                            ep_num = getattr(ep_item, 'contentEpisodeNumber', None) or getattr(ep_item, 'episode', None)
                            if ep_num and int(ep_num) == target_ep:
                                found_url = ep_item.url
                                print(f"[RESOLVE-VID] SC Trovato episodio (solo num) {target_ep}: {found_url}", flush=True)
                                break
                    if found_url:
                        url = found_url
                else:
                    # È un film
                    videos = streamingcommunity.findvideos(it) or []
                    if videos:
                        url = videos[0].url
                        print(f"[RESOLVE-VID] Estratto iframe StreamingCommunity: {url}", flush=True)
            except Exception as e:
                print(f"[RESOLVE-VID] Errore findvideos/episodios SC: {e}", flush=True)

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
    if server and server != "streamingcommunityws":
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
            if channel == 'raiplay':
                it.video_url = url + '.json' if not url.endswith('.json') else url
            vids = mod.findvideos(it) or []
            print(f"[RESOLVE-VID] Trovati {len(vids)} server/video in {channel}", flush=True)

            # Se ci sono più vids e abbiamo un episodio cercato, filtra per episodio
            if len(vids) > 1 and (getattr(it, 'contentEpisode', None) or getattr(it, 'episode', None)):
                wanted = int(getattr(it, 'contentEpisode', None) or getattr(it, 'episode', None))
                filtered = []
                for v in vids:
                    ve = getattr(v, 'contentEpisode', None) or getattr(v, 'episode', None)
                    if ve and int(ve) == wanted:
                        filtered.append(v)
                    elif v.title:
                        m = re.search(r'(?:Ep\.|Episodio|\bE|x)\s*0*(\d+)', v.title, re.I)
                        if m and int(m.group(1)) == wanted:
                            filtered.append(v)
                if filtered:
                    vids = filtered
                    print(f"[RESOLVE-VID] Filtrati {len(vids)} video per episodio {wanted}", flush=True)

            for v in vids:
                # Se mod ha metodo 'play' o v.action == 'play' (es. AnimeSaturn, Casacinema vidxgo, ecc.)
                if hasattr(mod, 'play') or getattr(v, 'action', '') == 'play':
                    try:
                        play_fn = getattr(mod, 'play', None)
                        if play_fn:
                            play_res = play_fn(v)
                            if play_res:
                                play_item = play_res[0] if isinstance(play_res, list) else play_res
                                d_url = getattr(play_item, 'url', None)
                                if d_url and (d_url.startswith('http://') or d_url.startswith('https://')):
                                    print(f"[RESOLVE-VID] Risolto tramite {channel}.play(): {d_url}", flush=True)
                                    return d_url
                    except Exception as p_err:
                        print(f"[RESOLVE-VID] {channel}.play() fallito: {p_err}", flush=True)

                if not getattr(v, 'server', None) or not getattr(v, 'url', None):
                    continue

                # Se è directo
                if v.server == "directo":
                    if v.url.startswith(('http://', 'https://')):
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

def search_content(query, channel_type='all', specific_channel=None):
    """
    Ricerca robusta e multi-thread nei canali Stream4Me, filtrata per sezione o canale specifico.
    """
    if not query:
        return []

    query = query.strip()
    print(f"[STREAM4ME-SEARCH] Query: '{query}', type: '{channel_type}', channel: '{specific_channel}'", flush=True)

    target_channels = []
    if specific_channel and specific_channel not in ['search', 'root', 'channelselector']:
        target_channels = [specific_channel]
    elif channel_type == 'movie':
        target_channels = ['streamingcommunity', 'casacinema', 'altadefinizione01']
    elif channel_type == 'tvshow':
        target_channels = ['streamingcommunity', 'casacinema']
    elif channel_type == 'anime':
        target_channels = ['animesaturn', 'animeworld', 'animeunity']
    elif channel_type == 'live':
        target_channels = ['plutotv', 'raiplay', 'mediasetplay']
    else:  # 'all'
        target_channels = ['streamingcommunity', 'casacinema', 'animesaturn', 'animeworld']

    raw_results = []
    from concurrent import futures

    def query_single_channel(ch_name):
        try:
            mod = platformtools.channelImport(ch_name)
            if not mod or not hasattr(mod, 'search'):
                return []
            it = Item(channel=ch_name, action='search')
            try:
                items = mod.search(it, query) or []
            except TypeError:
                items = mod.search(it, texto=query) or []
            return items
        except Exception as e:
            print(f"[SEARCH-ERR] Errore canale {ch_name} per '{query}': {e}", flush=True)
            return []

    with futures.ThreadPoolExecutor(max_workers=min(len(target_channels), 5)) as executor:
        future_to_ch = {executor.submit(query_single_channel, ch): ch for ch in target_channels}
        for fut in futures.as_completed(future_to_ch):
            try:
                ch_items = fut.result()
                if ch_items:
                    raw_results.extend(ch_items)
            except Exception:
                pass

    results = []
    seen_titles = set()

    for i, item in enumerate(raw_results):
        t = clean_title(item.title)
        if not t or "migrazione" in t.lower() or t == "Senza Titolo":
            continue

        item_act = item.action or ""
        t_low = t.lower()

        # Salta elementi di sistema Kodi
        if item_act in ['addToLibrary', 'add_serie_to_library', 'add_tvshow', 'channel_config', 'config', 'trailer', 'setting']:
            continue
        if any(ign in t_low for ign in ['add to video library', 'aggiungi alla videoteca', 'channel configuration', 'configura canale']):
            continue

        c_type = getattr(item, 'contentType', '')
        if channel_type == 'movie' and c_type == 'tvshow':
            continue
        if channel_type == 'tvshow' and c_type == 'movie':
            continue

        norm_t = re.sub(r'[^a-zA-Z0-9]', '', t.lower())
        if norm_t in seen_titles:
            continue
        seen_titles.add(norm_t)

        stream_direct = getattr(item, 'videourl', None) or getattr(item, 'video_url', None) or getattr(item, 'stream_url', None) or ""
        is_folder = True
        is_playable = False

        if stream_direct and (".m3u8" in stream_direct or ".mp4" in stream_direct or ".mp3" in stream_direct or "stitch" in stream_direct):
            is_folder = False
            is_playable = True
        elif item_act in ['findvideos', 'play'] or item.server:
            is_folder = False
            is_playable = True
        elif hasattr(item, "folder") and item.folder is False:
            is_folder = False
            is_playable = True

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

        plot = extract_plot(item)

        content_season = getattr(item, 'contentSeason', None)
        content_episode = getattr(item, 'contentEpisode', None) or getattr(item, 'contentEpisodeNumber', None)
        item_season = getattr(item, 'season', None) or content_season
        item_episode = getattr(item, 'episode', None) or content_episode
        item_num = getattr(item, 'number', None)

        if item_season is None or item_episode is None:
            m_se = re.search(r'\b(\d+)x0*(\d+)\b', t)
            if m_se:
                if item_season is None: item_season = int(m_se.group(1))
                if item_episode is None: item_episode = int(m_se.group(2))
            else:
                m_ep = re.search(r'(?:Ep\.|Episodio|\bE\b|\bEp\b)\s*0*(\d+)\b', t, re.IGNORECASE)
                if m_ep:
                    if item_episode is None: item_episode = int(m_ep.group(1))
                    if item_season is None: item_season = 1

        if not item_num and item_episode is not None:
            item_num = str(item_episode)

        item_token = ""
        item_payload = {}
        if getattr(item, 'data', None): item_payload['data'] = item.data
        if getattr(item, 'other', None): item_payload['other'] = item.other
        if getattr(item, 'extra', None): item_payload['extra'] = item.extra
        if getattr(item, 'server_links', None): item_payload['server_links'] = item.server_links
        if getattr(item, 'contentTitle', None): item_payload['contentTitle'] = item.contentTitle
        if getattr(item, 'fulltitle', None): item_payload['fulltitle'] = item.fulltitle
        if item_season is not None: item_payload['season'] = item_season
        if item_episode is not None: item_payload['episode'] = item_episode
        if item_num is not None: item_payload['number'] = item_num
        if t: item_payload['title'] = t

        if item_payload:
            if len(_ITEM_PAYLOAD_CACHE) > 500:
                _ITEM_PAYLOAD_CACHE.clear()
            item_token = f"srch_{int(time.time()*1000)}_{i}"
            _ITEM_PAYLOAD_CACHE[item_token] = item_payload

        badge = "Episodio" if "episod" in item_act or "x" in t[:5] else ("Cartella" if is_folder else "Video HD")
        if item.channel:
            badge += f" • {item.channel.capitalize()}"

        entry = {
            "id": f"search_{item.channel or 'res'}_{i}",
            "title": t,
            "plot": plot,
            "channel": item.channel or specific_channel or "streamingcommunity",
            "action": item_act,
            "url": stream_direct or item.url or "",
            "extra": getattr(item, "extra", "") or "",
            "args": getattr(item, "args", "") or "",
            "category": getattr(item, "category", "") or "",
            "page": str(getattr(item, "page", "")) or "",
            "server": item.server or "",
            "season": item_season if item_season is not None else "",
            "episode": item_episode if item_episode is not None else "",
            "number": item_num or "",
            "item_token": item_token,
            "folder": is_folder,
            "is_playable": is_playable,
            "year": year,
            "rating": rating,
            "badge": badge,
            "thumbnail": thumb,
            "poster": thumb,
            "_raw_item": item
        }

        if is_playable:
            stream_params = {
                "channel": entry["channel"],
                "action": entry["action"],
                "url": entry["url"],
                "server": entry["server"]
            }
            if entry["title"]: stream_params["title"] = entry["title"]
            if entry["season"] != "": stream_params["season"] = str(entry["season"])
            if entry["episode"] != "": stream_params["episode"] = str(entry["episode"])
            if entry["number"]: stream_params["number"] = str(entry["number"])
            if entry["item_token"]: stream_params["item_token"] = entry["item_token"]
            entry["stream"] = "/api/resolve?" + urllib.parse.urlencode(stream_params)

        results.append(entry)

    # Arricchimento trame per i risultati privi di sinossi
    try:
        enrich_missing_plots(results)
    except Exception as e:
        print(f"[SEARCH] enrich_missing_plots error: {e}", flush=True)

    for ent in results:
        ent.pop("_raw_item", None)

    print(f"[STREAM4ME-SEARCH] Trovati {len(results)} risultati finali per '{query}'.", flush=True)
    return results

def search(query, channel_type='all', specific_channel=None):
    return search_content(query, channel_type=channel_type, specific_channel=specific_channel)

if __name__ == "__main__":
    print("Test Stream4Me Backend:")
    root = navigate('root')
    print("Elementi Root:", len(root))
    for r in root:
        print(" ", r["title"], "| type:", r["channel_type"] if "channel_type" in r else r["channel"])
