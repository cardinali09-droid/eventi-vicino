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
