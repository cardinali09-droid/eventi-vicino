"""
Scraper per umbriatourism.it/eventi (versione 2)

1. Apre l'elenco eventi e clicca "Load more results" finche' ce ne sono.
2. Raccoglie il link di ogni evento.
3. Apre la pagina di ogni evento e legge i campi "Where" (comune) e
   "When" (date), piu' la tipologia (mercati, vino, musica, feste).
4. Geocodifica ogni comune con Nominatim (OpenStreetMap) usando una cache.
5. Salva tutto in eventi.json (letto dalla pagina web).

Gli eventi gia' terminati vengono scartati.
"""

import json
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).parent
OUTPUT_FILE = BASE_DIR / "eventi.json"
GEOCODE_CACHE_FILE = BASE_DIR / "geocode_cache.json"

EVENTS_URL = "https://www.umbriatourism.it/en/events"

FALLITI = set()
DEBUG_SNIPPETS = []  # per capire cosa non torna se nessun evento viene letto

# Ordine importante: il primo gruppo che trova una parola chiave vince.
CATEGORIA_KEYWORDS = [
    ("vino", ["wine", "vino", "vini", "cantin", "calici", "degustazion", "vendemmia", "enoteca"]),
    ("musica", ["concert", "music", "musica", "jazz", "opera"]),
    ("mercati", ["market", "mercat", "fiera"]),
]


def categoria_da_testo(testo):
    testo = (testo or "").lower()
    for categoria, keywords in CATEGORIA_KEYWORDS:
        if any(k in testo for k in keywords):
            return categoria
    return "feste"


def carica_cache_geocoding():
    if GEOCODE_CACHE_FILE.exists():
        with open(GEOCODE_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def salva_cache_geocoding(cache):
    with open(GEOCODE_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def geocodifica_comune(comune, cache):
    """Ritorna (lat, lon) del comune, oppure (None, None) se non trovato."""
    if comune in cache and cache[comune][0] is not None:
        return tuple(cache[comune])
    if comune in FALLITI:
        return (None, None)
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": f"{comune}, Umbria, Italia", "format": "json", "limit": 1},
            headers={"User-Agent": "eventi-vicino-app/1.0 (uso personale)"},
            timeout=10,
        )
        resp.raise_for_status()
        risultati = resp.json()
        coords = (float(risultati[0]["lat"]), float(risultati[0]["lon"])) if risultati else (None, None)
    except (requests.RequestException, ValueError, KeyError):
        coords = (None, None)
    if coords[0] is not None:
        cache[comune] = coords
    else:
        FALLITI.add(comune)
    time.sleep(1)  # regola di Nominatim: massimo 1 richiesta al secondo
    return coords


def data_iso(gg_mm_aaaa):
    return datetime.strptime(gg_mm_aaaa, "%d/%m/%Y").strftime("%Y-%m-%d")


def raccogli_link(page):
    page.goto(EVENTS_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)

    for _ in range(60):  # tetto di sicurezza
        load_more = page.locator("text=Load more results")
        if load_more.count() == 0:
            break
        try:
            load_more.first.click(timeout=3000)
            page.wait_for_timeout(1200)
        except Exception:
            break

    hrefs = page.eval_on_selector_all("a[href*='/w/']", "els => els.map(e => e.href)")
    visti, link = set(), []
    for h in hrefs:
        if h not in visti:
            visti.add(h)
            link.append(h)
    return link


RE_DOVE_QUANDO = re.compile(
    r"\bwhere\b\s*:?\s*(.{1,500}?)\s*\bwhen\b\s*:?\s*(\d{2}/\d{2}/\d{4})(?:\s*-\s*(\d{2}/\d{2}/\d{4}))?",
    re.IGNORECASE | re.DOTALL,
)


def estrai_luoghi_e_date(corpo):
    """Legge i campi Where/When. Where puo' contenere piu' comuni
    (es. 'Montefalco, Bevagna, Assisi e Amelia'): li separa tutti.
    Ritorna (lista_comuni, inizio, fine) oppure None."""
    trovati = list(RE_DOVE_QUANDO.finditer(corpo))
    if not trovati:
        return None
    m = trovati[-1]
    grezzo = re.split(r"\bwhere\b", m.group(1), flags=re.IGNORECASE)[-1]
    parti = re.split(r"\s*,\s*|\s+e\s+|\s+and\s+|\s*/\s*", grezzo.strip())
    luoghi = []
    for p in parti:
        p = p.strip(" .;\n\t")
        if p and p not in luoghi:
            luoghi.append(p)
    luoghi = luoghi[:15]
    if not luoghi:
        return None
    inizio = data_iso(m.group(2))
    fine = data_iso(m.group(3)) if m.group(3) else inizio
    return luoghi, inizio, fine


def leggi_evento(page, url):
    """Apre la pagina di un evento e ritorna un dizionario, o None se non
    riesce a trovare luoghi e date."""
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)
    corpo = page.inner_text("body")

    letto = estrai_luoghi_e_date(corpo)
    if letto is None:
        if len(DEBUG_SNIPPETS) < 2:
            pos = corpo.lower().rfind("where")
            if pos < 0:
                pos = max(0, corpo.find("View on map"))
            DEBUG_SNIPPETS.append((url, repr(corpo[max(0, pos - 100):pos + 250])))
        return None
    luoghi, inizio, fine = letto

    try:
        titolo = page.locator("h1").last.inner_text().strip()
    except Exception:
        titolo = url.rsplit("/", 1)[-1]

    # Le etichette di tipologia stanno subito prima di "View on map".
    idx = corpo.find("View on map")
    tag_testo = corpo[max(0, idx - 300):idx] if idx > 0 else ""

    return {
        "nome": titolo,
        "categoria": categoria_da_testo(tag_testo + " " + titolo),
        "luoghi": luoghi,
        "inizio": inizio,
        "fine": fine,
        "url": url,
    }


def main():
    oggi = date.today().isoformat()
    eventi = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        print("Raccolgo l'elenco degli eventi ...")
        link = raccogli_link(page)
        print(f"Trovati {len(link)} link. Ora leggo ogni evento (ci vuole qualche minuto).")

        for i, url in enumerate(link, start=1):
            try:
                evento = leggi_evento(page, url)
            except Exception as e:
                print(f"[{i}/{len(link)}] errore, salto: {url} ({type(e).__name__})")
                continue
            if evento is None:
                print(f"[{i}/{len(link)}] saltato (non e' un evento): {url}")
                continue
            if evento["fine"] < oggi:
                print(f"[{i}/{len(link)}] gia' finito: {evento['nome']}")
                continue
            print(f"[{i}/{len(link)}] {evento['nome']} - {', '.join(evento['luoghi'])} - {evento['categoria']}")
            eventi.append(evento)
            time.sleep(0.5)

        browser.close()

    print(f"Eventi validi: {len(eventi)}. Ora cerco le posizioni dei comuni ...")
    cache = carica_cache_geocoding()
    record = []
    non_trovati = set()
    for e in eventi:
        tutti = ", ".join(e["luoghi"])
        data_testo = e["inizio"] if e["inizio"] == e["fine"] else f"{e['inizio']} / {e['fine']}"
        for comune in e["luoghi"]:
            lat, lon = geocodifica_comune(comune, cache)
            if lat is None:
                non_trovati.add(comune)
                continue
            record.append({
                "nome": e["nome"],
                "categoria": e["categoria"],
                "luogo": comune,
                "tutti_i_luoghi": tutti,
                "lat": lat,
                "lon": lon,
                "inizio": e["inizio"],
                "fine": e["fine"],
                "data": data_testo,
                "url": e["url"],
                "fonte": "umbriatourism.it",
            })
    eventi = record
    salva_cache_geocoding(cache)

    if non_trovati:
        print("Comuni non trovati (non compariranno):", ", ".join(sorted(non_trovati)))

    if not eventi:
        # Non sovrascrivo i dati buoni gia' presenti: meglio dati di ieri che una pagina vuota.
        print("ERRORE: nessun evento raccolto, tengo il file precedente.")
        for url, snippet in DEBUG_SNIPPETS:
            print(url)
            print(snippet)
        sys.exit(1)

    pacchetto = {
        "aggiornato": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "eventi": eventi,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(pacchetto, f, ensure_ascii=False, indent=1)

    print(f"Salvate {len(eventi)} localita' evento in {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
