# Stream4Me Orsay

Fork & Headless Backend Bridge di **Stream4Me** (`plugin.video.s4me`), ottimizzato per l'integrazione con Smart TV legacy (Samsung Orsay) e per l'esecuzione come microservizio REST/JSON standalone.

---

## 🌟 Caratteristiche Principali

* **Doppia Modalità:**
  * **Add-on Kodi Standard:** installabile su qualsiasi istanza Kodi (Kodi 19 Matrix, 20 Nexus, 21 Omega).
  * **Headless API Server (`stream4me_backend.py`):** emulazione runtime Kodi leggera tramite `sakee`, che espone canali, scraping, ricerca e risolutori video in JSON senza aprire l'interfaccia grafica.
* **Correzioni e Ottimizzazioni per Orsay / Modern Web:**
  * **Bypass SSL/TLS Legacy:** risolutore DNS over HTTPS (DoH) e gestione cifrari TLS per bypassare blocchi e certificati scaduti.
  * **AnimeWorld:** correzione query di ordinamento `/ongoing` e supporto CDN SweetPixel.
  * **AnimeSaturn:** estrazione automatica flussi diretti MP4/HLS.
  * **StreamingCommunity:** scraping delle locandine ad alta risoluzione (poster CDN) e risoluzione flussi VixCloud.
  * **RaiPlay & La7 Live:** estrazione diretta delle playlist HLS `.m3u8` Akamai broadcast con locandine aggiornate in tempo reale.
  * **Pluto TV:** supporto flussi Stitcher live HLS con icone a colori.
  * **Guardaserie:** supporto attributi lazy-load `data-src` per locandine e schede serie.

---

## 📁 Struttura della Repository

```
stream4me-orsay/
├── addon.xml                 # Descrittore ufficiale add-on Kodi
├── channels/                 # Canali streaming supportati (Film, Serie TV, Anime, Live TV)
├── core/                     # Resolver DNS, scrapertools, httptools, servertools
├── servers/                  # Risolutori host video (VixCloud, MixDrop, Streamtape, SweetPixel, ecc.)
├── stream4me_backend.py      # Micro-bridge headless JSON API per il server Web TV
├── requirements.txt          # Dipendenze Python minime per la modalità headless
└── README.md
```

---

## 🚀 Guida Rapida (Modalità Headless API)

### 1. Installazione Dipendenze
```bash
pip install -r requirements.txt
```

### 2. Utilizzo come Modulo Python
```python
import stream4me_backend

# 1. Recupera film in evidenza
movies = stream4me_backend.get_movies()
print(f"Trovati {len(movies)} film")

# 2. Naviga canali TV in diretta
live_channels = stream4me_backend.navigate(channel="tv", action="mainlist")

# 3. Risolvi uno stream video
stream_url = stream4me_backend.resolve_video(
    channel="animeworld",
    action="findvideos",
    url="https://www.animeworld.ac/play/..."
)
print("Stream pronto per la riproduzione:", stream_url)
```

---

## 📺 Integrazione con Kodi Orsay Server
Questo progetto è progettato per operare in tandem con [kodi-orsay](https://github.com/TheWinner02/kodi-orsay): clonando entrambe le cartelle una accanto all'altra, il server `kodi-orsay` rileva automaticamente `stream4me_backend` e sblocca l'intero catalogo on-demand sulla TV.

---

## ⚖️ Licenza & Disclaimer
Questo progetto è rilasciato sotto licenza **GPL v3**. Il software è fornito a scopo didattico e di ricerca sull'interoperabilità di dispositivi smart legacy. Nessun contenuto multimediale è ospitato direttamente da questo repository.
