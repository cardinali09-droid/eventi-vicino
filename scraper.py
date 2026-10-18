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
