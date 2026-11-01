"""Scryfall session access.

Data source (Brandon, 2026-10-31 — supersedes the earlier live-fetch
rule): when Scryfall bulk files are present in the session's data dir,
card data and tag membership load OFFLINE from them (see bulkdata.py)
with zero API calls. When they are absent, the original live path runs
unchanged: tag membership is looked up once at run-session start and
held in memory only; raw Scryfall card JSON may be cached on disk (it
is source data, not a classification result).
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
import os
import sys
import time
import urllib.request
import urllib.error
import urllib.parse

API = "https://api.scryfall.com"

# The agreed Scryfall community function tags (otag:).
FUNCTION_TAGS = [
    "ramp", "land-ramp", "mana-rock", "mana-dork",
    "draw", "card-advantage", "draw-engine", "cantrip",
    "tutor", "removal", "spot-removal", "boardwipe", "counterspell",
    "recursion", "sacrifice-outlet", "protection", "anthem",
    "lifegain", "blink", "flicker",
]

RAMP_TAGS = ("ramp", "land-ramp", "mana-rock", "mana-dork")


class ScryfallError(RuntimeError):
    pass


class ScryfallSession:
    def __init__(self, cache_dir: str | None = None, delay: float = 0.1,
                 log=lambda *a: None, data_dir: str | None = None):
        self.cache_dir = cache_dir
        self.delay = delay
        self.log = log
        self.data_dir = data_dir
        self._tag_cache: dict[str, frozenset] = {}  # memory only, per session
        self._bulk = None
        self._bulk_loaded = False

    # -- offline bulk data (preferred when present) -----------------------
    def _bulk_data(self):
        if not self._bulk_loaded:
            self._bulk_loaded = True
            if self.data_dir:
                from .bulkdata import BulkData
                try:
                    self._bulk = BulkData.load(self.data_dir)
                except Exception as e:  # corrupt bulk files: fall back live
                    self.log(f"bulk data unusable ({e}); using live fetch")
                    self._bulk = None
                if self._bulk is not None:
                    self.log(f"using Scryfall bulk data dated "
                             f"{self._bulk.disclosure_date} (offline)")
        return self._bulk

    def data_disclosure(self) -> str:
        """Brandon (2026-10-31): every report ALWAYS prints its data
        date — the bulk files' date when offline, the fetch date when
        live. An always-on back reference, not a staleness warning."""
        bulk = self._bulk_data()
        if bulk is not None:
            return f"Data: Scryfall bulk files dated {bulk.disclosure_date}"
        return (f"Data: live Scryfall API fetch dated "
                f"{date.today().isoformat()}")

    # -- low level ------------------------------------------------------
    def _request(self, url: str, payload: dict | None = None) -> dict:
        data = None
        headers = {"User-Agent": "edh-goldfish-v4/4.0.2",
                   "Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if payload is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                out = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise ScryfallError(f"Scryfall HTTP {e.code} for {url}") from e
        except urllib.error.URLError as e:
            raise ScryfallError(f"Scryfall unreachable: {e}") from e
        time.sleep(self.delay)
        return out

    # -- card data (raw JSON; disk cache allowed) ------------------------
    def _cache_path(self, name: str) -> str | None:
        if not self.cache_dir:
            return None
        os.makedirs(self.cache_dir, exist_ok=True)
        h = hashlib.sha1(name.lower().encode()).hexdigest()[:16]
        return os.path.join(self.cache_dir, h + ".json")

    def fetch_cards(self, names: list[str]) -> dict[str, dict]:
        """Return {requested_name: raw scryfall card json}."""
        bulk = self._bulk_data()
        if bulk is not None:
            result: dict[str, dict] = {}
            missing: list[str] = []
            for n in names:
                card = bulk.card_for_name(n)
                if card is None:
                    missing.append(n)
                else:
                    result[n] = card
            if missing:
                self.log(f"{len(missing)} card(s) not in bulk data; "
                         f"fetching live: {missing}")
                result.update(self._fetch_cards_live(missing))
            return result
        return self._fetch_cards_live(names)

    def _fetch_cards_live(self, names: list[str]) -> dict[str, dict]:
        """Live path: collection endpoint (+ named-endpoint fallback),
        with the raw-JSON disk cache. Unchanged from v4.0.0."""
        result: dict[str, dict] = {}
        missing: list[str] = []
        for n in names:
            p = self._cache_path(n)
            if p and os.path.exists(p):
                with open(p) as f:
                    result[n] = json.load(f)
            else:
                missing.append(n)
        for i in range(0, len(missing), 75):
            chunk = missing[i:i + 75]
            self.log(f"fetching {len(chunk)} cards from Scryfall...")
            resp = self._request(f"{API}/cards/collection",
                                 {"identifiers": [{"name": n} for n in chunk]})
            found = {c["name"]: c for c in resp.get("data", [])}
            for n in chunk:
                card = found.get(n)
                if card is None:
                    # try matching by front-face name
                    for c in resp.get("data", []):
                        faces = c.get("card_faces") or []
                        if faces and faces[0].get("name") == n:
                            card = c
                            break
                        if c["name"].split(" // ")[0] == n:
                            card = c
                            break
                if card is None:
                    # Fallback: the named endpoint resolves full
                    # "Front // Back" decklist names that the collection
                    # endpoint does not match.
                    card = self._fetch_named(n)
                result[n] = card
                p = self._cache_path(n)
                if p:
                    with open(p, "w") as f:
                        json.dump(card, f)
        return result

    def _fetch_named(self, name: str) -> dict:
        url = f"{API}/cards/named?exact={urllib.parse.quote(name)}"
        try:
            return self._request(url)
        except ScryfallError:
            front = name.split(" // ")[0]
            if front != name:
                return self._request(
                    f"{API}/cards/named?exact={urllib.parse.quote(front)}")
            raise ScryfallError(f"card not found on Scryfall: {name}")

    # -- tags (memory only, never persisted) ------------------------------
    def fetch_tag_oracle_ids(self, tag: str) -> frozenset:
        if tag in self._tag_cache:
            return self._tag_cache[tag]
        bulk = self._bulk_data()
        if bulk is not None:
            out = bulk.tag_oracle_ids(tag)
            self._tag_cache[tag] = out
            return out
        ids: set[str] = set()
        url = f"{API}/cards/search?q=otag:{urllib.parse.quote(tag)}&unique=oracle"
        pages = 0
        while url:
            resp = self._request(url)
            for c in resp.get("data", []):
                if c.get("oracle_id"):
                    ids.add(c["oracle_id"])
            pages += 1
            url = resp.get("next_page") if resp.get("has_more") else None
        self.log(f"tag {tag}: {len(ids)} oracles ({pages} pages)")
        out = frozenset(ids)
        self._tag_cache[tag] = out
        return out

    def fetch_all_tags(self, tags=FUNCTION_TAGS) -> dict[str, frozenset]:
        return {t: self.fetch_tag_oracle_ids(t) for t in tags}
