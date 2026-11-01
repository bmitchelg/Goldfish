"""Version archive for batch runs (v4.1, spec item 7).

Every `goldfish4 batch` run is archived in the versions folder,
which is a SIBLING of the bulk-data dir (v4.1.1 — Brandon: "the
deck versions can have their own folder"):

    <data-dir>/../versions/<deck-slug>/v<N>/
        batch.json     — a copy of the batch JSON exactly as written
        decklist.txt   — the exact decklist bytes that were run
        meta.json      — version bookkeeping (see below)

So with the default data dir (~/workspace/mtg/goldfish-cli/data/
bulk) the archive lives at ~/workspace/mtg/goldfish-cli/data/
versions/ — outside the bulk dir, so wiping or re-seeding the bulk
files can never take the deck versions with it. Archives written by
v4.1.0 at the old location (<data-dir>/versions/) are migrated
automatically on first use (see migrate()).

Deck identity = commander + decklist name (the decklist file's stem,
which is what Deck.name carries). Versions are per deck, numbered
v1, v2, ... monotonically: the next number is one more than the
highest existing version directory. Nothing is ever deleted from
the archive — versions that fall out of the compare view's 3-slot
window stay here, and any 3 saved versions can be compared.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone

_VERSION_RE = re.compile(r"^v(\d+)$")


def deck_slug(deck_name: str, commander: str) -> str:
    """Stable directory name for a deck: commander + decklist name,
    lowercased, non-alphanumerics collapsed to single dashes."""
    raw = f"{commander}-{deck_name}".lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return slug or "deck"


def store_root(data_dir: str) -> str:
    """The archive root (v4.1.1): a sibling of the data dir, i.e.
    dirname(data_dir)/versions — for ANY data-dir shape, so a data
    dir named .../data/bulk archives to .../data/versions, and a
    data dir /x/y archives to /x/versions."""
    return os.path.join(os.path.dirname(os.path.abspath(data_dir)),
                        "versions")


def _legacy_root(data_dir: str) -> str:
    """The v4.1.0 archive root: inside the data dir itself."""
    return os.path.join(os.path.abspath(data_dir), "versions")


def migrate(data_dir: str, note=None) -> list:
    """Move a v4.1.0 archive from the old root (<data-dir>/versions/)
    to the new sibling root, deck folder by deck folder, version by
    version. A version that already exists at the destination is
    NEVER overwritten — the old copy is left in place and a note is
    emitted (to stderr by default, so it shows in command output).
    Returns the list of note strings. Safe to call on every archive
    access: once migrated, the old root is empty/absent and this is
    a no-op."""
    if note is None:
        def note(msg):
            print(f"archive: {msg}", file=sys.stderr)
    new_root = store_root(data_dir)
    old_root = _legacy_root(data_dir)
    notes = []
    if os.path.abspath(new_root) == os.path.abspath(old_root):
        return notes
    if not os.path.isdir(old_root):
        return notes
    for slug in sorted(os.listdir(old_root)):
        old_deck = os.path.join(old_root, slug)
        if not os.path.isdir(old_deck):
            continue
        for n in _version_numbers(old_deck):
            src = os.path.join(old_deck, f"v{n}")
            dest = os.path.join(new_root, slug, f"v{n}")
            if os.path.exists(dest):
                msg = (f"migration skipped {slug}/v{n}: already exists "
                       f"at {dest}; old copy left at {src}")
                notes.append(msg)
                note(msg)
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(src, dest)
            msg = f"migrated {slug}/v{n} to {dest}"
            notes.append(msg)
            note(msg)
        try:
            os.rmdir(old_deck)  # only succeeds once it is empty
        except OSError:
            pass
    try:
        os.rmdir(old_root)
    except OSError:
        pass
    return notes


def list_decks(data_dir: str) -> list:
    """Slugs of every deck with at least one archived version."""
    migrate(data_dir)
    root = store_root(data_dir)
    if not os.path.isdir(root):
        return []
    return sorted(d for d in os.listdir(root)
                  if os.path.isdir(os.path.join(root, d))
                  and _version_numbers(os.path.join(root, d)))


def _version_numbers(deck_dir: str) -> list:
    out = []
    if os.path.isdir(deck_dir):
        for d in os.listdir(deck_dir):
            m = _VERSION_RE.match(d)
            if m and os.path.isdir(os.path.join(deck_dir, d)):
                out.append(int(m.group(1)))
    return sorted(out)


def list_versions(data_dir: str, slug: str) -> list:
    """Archived version records for one deck, ascending by number.
    Each record: {version, dir, meta} (meta may be {} if unreadable)."""
    migrate(data_dir)
    deck_dir = os.path.join(store_root(data_dir), slug)
    out = []
    for n in _version_numbers(deck_dir):
        vdir = os.path.join(deck_dir, f"v{n}")
        meta = {}
        mp = os.path.join(vdir, "meta.json")
        if os.path.exists(mp):
            try:
                with open(mp, encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, json.JSONDecodeError):
                meta = {}
        out.append({"version": n, "dir": vdir, "meta": meta})
    return out


def archive_batch(data_dir: str, batch_path: str, decklist_path: str,
                  deck_name: str, commander: str) -> dict:
    """Copy a finished batch run into the archive as the deck's next
    version. Returns the version record {version, dir, slug}."""
    migrate(data_dir)
    slug = deck_slug(deck_name, commander)
    deck_dir = os.path.join(store_root(data_dir), slug)
    os.makedirs(deck_dir, exist_ok=True)
    existing = _version_numbers(deck_dir)
    n = (existing[-1] + 1) if existing else 1
    vdir = os.path.join(deck_dir, f"v{n}")
    os.makedirs(vdir)
    shutil.copyfile(batch_path, os.path.join(vdir, "batch.json"))
    shutil.copyfile(decklist_path, os.path.join(vdir, "decklist.txt"))
    meta = {
        "version": n,
        "slug": slug,
        "deck_name": deck_name,
        "commander": commander,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "batch_source": os.path.basename(batch_path),
        "decklist_source": os.path.basename(decklist_path),
    }
    with open(os.path.join(vdir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    return {"version": n, "dir": vdir, "slug": slug}
