"""Offline Scryfall bulk data (Brandon, 2026-10-31).

Brandon directed this layer, superseding the earlier rule that tags are
fetched live once per run session: when Scryfall's downloadable bulk
files (oracle_cards + oracle_tags) are present in the data dir, card
data and tag membership load from them with ZERO API calls. When they
are absent, the session falls back to the original live-fetch path,
unchanged.

Refresh cadence (Brandon's rule): refresh 4 days before each new
Standard set release date. `goldfish4 refresh-data` downloads
unconditionally; `goldfish4 refresh-data --if-due` downloads only when
a Standard release's refresh window (release date minus 4 days) has
arrived and the local copy predates that window (or there is no local
copy at all).
"""

from __future__ import annotations

import gzip
import json
import os
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

API = "https://api.scryfall.com"
BULK_TYPES = ("oracle_cards", "oracle_tags")
META_NAME = "bulk_meta.json"
DEFAULT_DATA_DIR = os.path.expanduser(
    "~/workspace/mtg/goldfish-cli/data/bulk")
STANDARD_SET_TYPES = ("expansion", "core")
REFRESH_LEAD_DAYS = 4
POST_RELEASE_DAYS = 30
POST_RELEASE_INTERVAL_DAYS = 7

_UA = {"User-Agent": "edh-goldfish-v4/4.0.1", "Accept": "application/json"}


class BulkError(RuntimeError):
    pass


# -- downloads -----------------------------------------------------------

def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=_UA)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise BulkError(f"Scryfall HTTP {e.code} for {url}") from e
    except urllib.error.URLError as e:
        raise BulkError(f"Scryfall unreachable: {e}") from e


def _download_file(url: str, dest: str) -> None:
    req = urllib.request.Request(url, headers=_UA)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp, \
                open(dest, "wb") as f:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
    except urllib.error.URLError as e:
        raise BulkError(f"download failed for {url}: {e}") from e


def fetch_bulk_metadata() -> dict[str, dict]:
    """{type: bulk-data entry} for the types we use, resolved live from
    https://api.scryfall.com/bulk-data (URIs are never hardcoded)."""
    resp = _get_json(f"{API}/bulk-data")
    out = {b["type"]: b for b in resp.get("data", [])
           if b.get("type") in BULK_TYPES}
    missing = [t for t in BULK_TYPES if t not in out]
    if missing:
        raise BulkError(f"bulk-data endpoint did not offer: {missing}")
    return out


def download_bulk(data_dir: str = DEFAULT_DATA_DIR,
                  log=lambda *a: None) -> dict:
    """Download both bulk files into data_dir; record updated_at dates
    from the bulk-data metadata in bulk_meta.json. Returns the meta."""
    meta = fetch_bulk_metadata()
    os.makedirs(data_dir, exist_ok=True)
    out: dict[str, dict] = {}
    for t in BULK_TYPES:
        info = meta[t]
        uri = info.get("jsonl_download_uri") or info.get("download_uri")
        if not uri:
            raise BulkError(f"no download URI for bulk type {t}")
        dest = os.path.join(data_dir, f"{t}.jsonl.gz")
        tmp = dest + ".part"
        log(f"downloading {t} from {uri} ...")
        _download_file(uri, tmp)
        os.replace(tmp, dest)
        out[t] = {"updated_at": info.get("updated_at"), "uri": uri,
                  "downloaded_at": datetime.now(timezone.utc).isoformat()}
        log(f"{t}: bulk updated_at {info.get('updated_at')} -> {dest}")
    with open(os.path.join(data_dir, META_NAME), "w") as f:
        json.dump(out, f, indent=1)
    return out


# -- local files -----------------------------------------------------------

def _find_file(data_dir: str, bulk_type: str) -> str | None:
    for cand in (f"{bulk_type}.jsonl.gz", f"{bulk_type}.jsonl"):
        p = os.path.join(data_dir, cand)
        if os.path.exists(p):
            return p
    return None


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


def read_bulk_dates(data_dir: str) -> dict[str, date | None]:
    """{bulk_type: date of the local copy} — from bulk_meta.json when
    present, else the file's mtime; None when the file is absent."""
    meta = {}
    mp = os.path.join(data_dir, META_NAME)
    if os.path.exists(mp):
        try:
            with open(mp) as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError):
            meta = {}
    out: dict[str, date | None] = {}
    for t in BULK_TYPES:
        d = _parse_date((meta.get(t) or {}).get("updated_at"))
        if d is None:
            p = _find_file(data_dir, t)
            if p:
                d = datetime.fromtimestamp(os.path.getmtime(p)).date()
        out[t] = d
    return out


def _open_jsonl(path: str):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, encoding="utf-8")


# Layouts in the oracle_cards bulk file that are NOT playable game
# cards. Entries with these layouts can share a name (or a front-face
# name) with a real card — e.g. the art-series entry "Mountain //
# Mountain" registers the face name "Mountain", and a token entry can
# shadow a real card of the same name ("Dinosaur Egg"). When that
# happens the bulk index must resolve the name to the real card:
# v4.0.1's first-entry-wins index silently returned the art/token
# entry (type_line "Card // Card", no mana cost), corrupting every
# offline run of an affected deck. Found by the v4.0.2 verification
# cross-check against the v4.0.0 LCI reports (2026-10-31).
_NON_GAME_LAYOUTS = frozenset({"art_series", "token", "double_faced_token"})


def _register_name(index: dict, name: str, card: dict):
    existing = index.get(name)
    if existing is None:
        index[name] = card
    elif (existing.get("layout") in _NON_GAME_LAYOUTS
          and card.get("layout") not in _NON_GAME_LAYOUTS):
        index[name] = card  # a real game card beats an art/token entry


class BulkData:
    """Parsed bulk files: cards by name/oracle_id, tags by oracle_id."""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.cards_by_name: dict[str, dict] = {}
        self.cards_by_oracle: dict[str, dict] = {}
        self.tags: dict[str, frozenset] = {}
        self._tag_children: dict[str, list] = {}   # slug -> child slugs
        self._tag_aliases: dict[str, str] = {}     # alias -> slug
        self._tag_expanded: dict[str, frozenset] = {}
        self.dates: dict[str, date | None] = {}

    @classmethod
    def load(cls, data_dir: str) -> "BulkData | None":
        """Load both bulk files; None when either is absent."""
        if not data_dir:
            return None
        cards_path = _find_file(data_dir, "oracle_cards")
        tags_path = _find_file(data_dir, "oracle_tags")
        if not cards_path or not tags_path:
            return None
        bd = cls(data_dir)
        with _open_jsonl(cards_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                c = json.loads(line)
                name = c.get("name")
                if not name:
                    continue
                _register_name(bd.cards_by_name, name, c)
                faces = c.get("card_faces") or []
                if faces and faces[0].get("name"):
                    # front-face name resolves to the whole card, exactly
                    # like the live collection fallback did
                    _register_name(bd.cards_by_name, faces[0]["name"], c)
                oid = c.get("oracle_id")
                if oid:
                    bd.cards_by_oracle[oid] = c
        tagmap: dict[str, set] = {}
        id_to_slug: dict[str, str] = {}
        raw_children: dict[str, list] = {}
        raw_aliases: dict[str, list] = {}
        with _open_jsonl(tags_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if obj.get("type") not in (None, "oracle"):
                    continue
                # Key tags by SLUG, not display label (v4.0.2 fix): the
                # bulk file's label is a display string with spaces
                # ("mana rock") while every lookup in the codebase —
                # and the live otag: path the tool was built against —
                # uses the hyphenated slug ("mana-rock"). v4.0.1 keyed
                # by label, so most function tags silently resolved to
                # zero cards offline. Found by the v4.0.2 verification
                # cross-check (2026-10-31).
                slug = obj.get("slug") or obj.get("label")
                if slug:
                    if obj.get("id"):
                        id_to_slug[obj["id"]] = slug
                    if obj.get("child_ids"):
                        raw_children[slug] = list(obj["child_ids"])
                    if obj.get("aliases"):
                        raw_aliases[slug] = list(obj["aliases"])
                    ids = tagmap.setdefault(slug, set())
                    for tg in obj.get("taggings") or []:
                        oid = tg.get("oracle_id")
                        if oid:
                            ids.add(oid)
                elif obj.get("tag") and obj.get("oracle_id"):
                    # defensive: flat per-card oracle_tag record shape
                    tagmap.setdefault(obj["tag"], set()).add(obj["oracle_id"])
        bd.tags = {k: frozenset(v) for k, v in tagmap.items()}
        bd._tag_children = {
            slug: [id_to_slug[c] for c in child_ids if c in id_to_slug]
            for slug, child_ids in raw_children.items()}
        # Tag aliases (v4.0.2 fix): the Tagger renames tags over time
        # and keeps the old names as aliases — "boardwipe" is now an
        # alias of "sweeper". Register aliases (verbatim and in
        # slug-normalized form) as alternate lookup keys so the
        # codebase's tag vocabulary keeps resolving; a real slug
        # always wins over an alias.
        for slug, aliases in raw_aliases.items():
            for alias in aliases:
                if not isinstance(alias, str):
                    continue
                for form in (alias, alias.lower().replace(" ", "-")):
                    if form not in bd.tags:
                        bd._tag_aliases.setdefault(form, slug)
        bd.dates = read_bulk_dates(data_dir)
        return bd

    @property
    def disclosure_date(self) -> str:
        """The stalest file's date governs the disclosure line."""
        ds = [d for d in self.dates.values() if d]
        return min(ds).isoformat() if ds else "unknown"

    def card_for_name(self, name: str) -> dict | None:
        return self.cards_by_name.get(name)

    def tag_oracle_ids(self, tag: str) -> frozenset:
        """Oracle ids for a tag INCLUDING its descendant tags — the
        live path's otag: search includes subtags, so the bulk path
        must too for the two data sources to agree (v4.0.2 fix; e.g.
        Arcane Signet is tagged with a child of mana-rock, not with
        mana-rock itself)."""
        if tag in self._tag_expanded:
            return self._tag_expanded[tag]
        tag = self._tag_aliases.get(tag, tag)
        if tag in self._tag_expanded:
            return self._tag_expanded[tag]
        out: set = set()
        seen: set = set()
        stack = [tag]
        while stack:
            t = stack.pop()
            if t in seen:
                continue
            seen.add(t)
            out |= self.tags.get(t, frozenset())
            stack.extend(self._tag_children.get(t, ()))
        res = frozenset(out)
        self._tag_expanded[tag] = res
        return res


# -- refresh cadence -------------------------------------------------------

def fetch_standard_release_dates() -> list[date]:
    """Release dates of Standard-legal sets (expansion/core set types)
    from the Scryfall sets API, past and future, sorted."""
    resp = _get_json(f"{API}/sets")
    out = []
    for s in resp.get("data", []):
        if s.get("set_type") not in STANDARD_SET_TYPES:
            continue
        d = _parse_date(s.get("released_at"))
        if d:
            out.append(d)
    return sorted(out)


def refresh_decision(today: date, release_dates: list[date],
                     local_dates: dict[str, date | None]) -> tuple[bool, str]:
    """Brandon's cadence (refined 2026-10-31), per new Standard set:

    (a) refresh 4 days BEFORE the release date (covers pre-release,
        when early card access happens in person) — due when a set's
        T-4 point has passed and the local copy predates it;
    (b) refresh WEEKLY for 30 days after release (new-set tagging
        matures over the first month) — due when today is within
        [release, release + 30 days] for the newest released set and
        the local copy is 7+ days old.

    Outside any set window, no refresh. One standing exception: no
    complete local copy at all seeds an initial download."""
    if not local_dates or any(v is None for v in local_dates.values()):
        return True, "no complete local bulk copy — initial download"
    local_min = min(local_dates.values())
    for r in release_dates:
        window_start = r - timedelta(days=REFRESH_LEAD_DAYS)
        if window_start <= today and local_min < window_start:
            return True, (f"Standard release {r.isoformat()} pre-release "
                          f"refresh point {window_start.isoformat()} has "
                          f"passed; local copy dated "
                          f"{local_min.isoformat()} predates it")
    released = [r for r in release_dates if r <= today]
    if released:
        newest = max(released)
        if today <= newest + timedelta(days=POST_RELEASE_DAYS):
            age = (today - local_min).days
            if age >= POST_RELEASE_INTERVAL_DAYS:
                return True, (f"post-release weekly refresh for the set "
                              f"released {newest.isoformat()}; local copy "
                              f"is {age} days old")
    return False, (f"no refresh window open (local copy dated "
                   f"{local_min.isoformat()})")


def check_due(data_dir: str = DEFAULT_DATA_DIR,
              today: date | None = None) -> tuple[bool, str]:
    today = today or date.today()
    releases = fetch_standard_release_dates()
    return refresh_decision(today, releases, read_bulk_dates(data_dir))
