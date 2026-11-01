"""Batch runs with per-game streaming capture (v4.0.2).

A batch runs one decklist through several configurations (the base
deck plus optional perturbation variants) x several stress regimes,
and writes ONE JSON file whose "games" array gains one self-contained
record per finished game. The file is valid JSON after every single
game: the writer keeps a tail (closing brackets + run_status) that it
rewrites after each record, flushing and fsyncing each time, so a
killed batch still leaves a usable file with the games completed so
far. This fixes the earlier all-or-nothing design where killing a run
lost everything.

All variants and regimes share the batch seed base: game i of every
configuration uses seed_base * 1_000_003 + i (the simulator's standing
common-random-numbers convention, T1-18), which is what makes the
paired statistics (Wilcoxon, McNemar) valid across configurations.

The dashboard (dashboard.py) recomputes every displayed number from
these per-game records; the file stores NO aggregates.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone

from . import __version__
from .cards import COLOR_ORDER, Card, Cost
from .parser import Deck
from .simulator import run_trials_iter

SCHEMA = "goldfish4-batch/1"
SEED_POLICY = ("per-game seed = seed_base * 1000003 + game_index; the "
               "same seed is used for game_index in every variant and "
               "regime (common random numbers)")

BASIC_FOR_COLOR = {"W": "Plains", "U": "Island", "B": "Swamp",
                   "R": "Mountain", "G": "Forest"}


# ------------------------------------------------------------- variants

def most_pipped_color(deck) -> str | None:
    """The color with the most pips across the commander and all
    library spell costs. A hybrid pip counts toward both of its
    colors; a {2/C} pip counts toward its color. Ties break in WUBRG
    order. None when the deck has no colored pips at all."""
    counts: Counter = Counter()
    for c in deck.all_cards:
        if c.is_land or c.cost is None:
            continue
        for pip in c.cost.pips:
            for col in pip:
                counts[col] += 1
        for col in c.cost.twobrid:
            counts[col] += 1
    best = None
    for col in COLOR_ORDER:
        if counts.get(col, 0) > 0 and (best is None
                                       or counts[col] > counts[best]):
            best = col
    return best


def build_variants(deck: Deck, extra_cards: dict, perturbations: bool) -> list:
    """extra_cards maps card name -> Card for any card a variant needs
    that is not in the deck (Wastes and the five basics). Returns a list
    of {"id", "label", "description", "deck"} dicts."""
    variants = [{"id": "base", "label": "Base deck",
                 "description": "The decklist exactly as given.",
                 "deck": deck}]
    if not perturbations:
        return variants
    wastes = extra_cards["Wastes"]
    variants.append({
        "id": "plus_wastes", "label": "+1 Wastes",
        "description": ("Base deck plus one Wastes added to the library "
                        "(library grows by one card). One of the two "
                        "perturbations in Salubrious Snail's manabase "
                        "analyzer."),
        "deck": Deck(commanders=list(deck.commanders),
                     library=list(deck.library) + [wastes],
                     name=deck.name)})
    color = most_pipped_color(deck)
    if color is not None:
        basic_name = BASIC_FOR_COLOR[color]
        variants.append({
            "id": "plus_basic", "label": f"+1 {basic_name}",
            "description": (f"Base deck plus one {basic_name} — a basic "
                            f"of the deck's most-pipped color ({color}) "
                            "— added to the library (library grows by "
                            "one card). Snail's other perturbation, "
                            "specialized to the deck's dominant color."),
            "deck": Deck(commanders=list(deck.commanders),
                         library=list(deck.library) + [extra_cards[basic_name]],
                         name=deck.name)})
    new_library = []
    replaced = []
    for c in deck.library:
        if c.is_land and c.enters_tapped and not c.is_fetch:
            rep = None
            for col in COLOR_ORDER:
                if col in c.produced_colors:
                    rep = extra_cards[BASIC_FOR_COLOR[col]]
                    break
            if rep is None:
                rep = wastes
            new_library.append(rep)
            replaced.append(f"{c.name} -> {rep.name}")
        else:
            new_library.append(c)
    variants.append({
        "id": "no_taplands", "label": "Taplands become basics",
        "description": ("Every non-fetch land that enters tapped is "
                        "replaced by the untapped basic of the first "
                        "color (WUBRG order) it produces, or Wastes if "
                        "it produces no color. Library size unchanged. "
                        "Replacements: "
                        + ("; ".join(replaced) if replaced else "none")),
        "deck": Deck(commanders=list(deck.commanders),
                     library=new_library, name=deck.name)})
    return variants


# ------------------------------------------------------- card (de)serialization

def _letters(colors) -> list:
    return sorted(colors, key=lambda c: (COLOR_ORDER.index(c)
                                         if c in COLOR_ORDER else 99))


def card_to_index(card: Card) -> dict:
    cost = card.cost
    return {
        "name": card.name, "oracle_id": card.oracle_id,
        "type_line": card.type_line,
        "types": sorted(card.types), "supertypes": sorted(card.supertypes),
        "subtypes": sorted(card.subtypes),
        "cost": None if cost is None else {
            "generic": cost.generic,
            "pips": [_letters(p) for p in cost.pips],
            "colorless_pips": cost.colorless_pips,
            "twobrid": list(cost.twobrid),
            "x_count": cost.x_count},
        "mana_value": card.mana_value,
        "color_identity": _letters(card.color_identity),
        "keywords": list(card.keywords),
        "produced_mana": (list(card.produced_mana)
                          if card.produced_mana is not None else None),
        "power": card.power, "toughness": card.toughness,
        "is_land": card.is_land, "enters_tapped": card.enters_tapped,
        "is_fetch": card.is_fetch, "tags": sorted(card.tags),
    }


def card_from_index(d: dict) -> Card:
    cd = d["cost"]
    cost = None if cd is None else Cost(
        generic=cd["generic"],
        pips=tuple(frozenset(p) for p in cd["pips"]),
        colorless_pips=cd["colorless_pips"],
        twobrid=tuple(cd["twobrid"]),
        x_count=cd["x_count"])
    card = Card(name=d["name"], oracle_id=d["oracle_id"],
                type_line=d["type_line"], types=frozenset(d["types"]),
                supertypes=frozenset(d["supertypes"]),
                subtypes=frozenset(d["subtypes"]), cost=cost,
                mana_value=d["mana_value"],
                color_identity=frozenset(d["color_identity"]),
                keywords=tuple(d["keywords"]),
                produced_mana=(tuple(d["produced_mana"])
                               if d["produced_mana"] is not None else None),
                power=d["power"], toughness=d["toughness"],
                is_land=d["is_land"], enters_tapped=d["enters_tapped"],
                is_fetch=d["is_fetch"])
    card.tags = frozenset(d["tags"])
    return card


def decklist_counts(cards) -> list:
    counts = Counter(c.name for c in cards)
    return [{"name": n, "count": counts[n]} for n in sorted(counts)]


# ------------------------------------------------------------- records

def _unit_code(colors) -> str:
    """Encode one mana unit's payable colors compactly: letters in
    WUBRG order; a colorless-only unit (empty set) encodes as 'C'."""
    if not colors:
        return "C"
    return "".join(_letters(colors))


def _stranded(tr, deck) -> list:
    """Per-game stranded cards, replicating measurements._per_card's
    derivation exactly: seen = kept hand or drawn during play; not
    cast; not discarded. Reason splits on whether the card was ever
    payable at a step-9 check while in hand."""
    by_name = {}
    for c in deck.all_cards:
        by_name.setdefault(c.name, c)
    kept_names = {c.name for c in tr.kept_hand}
    cast_names = {e.name for e in tr.casts if not e.is_commander}
    discarded_names = {nm for _, nm in tr.discards}
    out = []
    for name, info in tr.seen.items():
        if name not in kept_names and info["drawn_turn"] == 0:
            continue  # bottomed-only sighting: never in the playable hand
        card = by_name.get(name)
        if card is None or card.is_land:
            continue
        if name in cast_names or name in discarded_names:
            continue
        out.append({"name": name,
                    "reason": ("payable_but_not_cast"
                               if name in tr.payable_names
                               else "never_had_mana")})
    return sorted(out, key=lambda x: x["name"])


def game_record(variant_id: str, regime: str, game_index: int,
                seed_base: int, tr, deck) -> dict:
    return {
        "variant": variant_id,
        "regime": regime,
        "game_index": game_index,
        "seed": seed_base * 1_000_003 + game_index,
        "mulligan": {
            "stages": [[label, kept] for label, kept in tr.stages],
            "final_hand_size": tr.final_hand_size,
            "kept_hand": [c.name for c in tr.kept_hand],
            "thrown_hands": [[c.name for c in h] for h in tr.thrown_hands],
            "bottomed": list(tr.bottomed),
        },
        "land_plays": [{"turn": t, "name": n, "tapped": tp,
                        "fetched_color": fc, "is_fetch": f}
                       for (t, n, tp, fc, f) in tr.land_plays],
        "turns": [{
            "turn": s.turn, "spells_cast": s.spells_cast,
            "mana_available": s.mana_available,
            "mana_spent": s.mana_spent,
            "mana_unspent": s.mana_available - s.mana_spent,
            "lands": s.lands, "creatures": s.creatures,
            "power": s.power, "toughness": s.toughness,
            "perms_by_type": dict(s.perms_by_type),
            "sources_by_color": dict(s.sources_by_color),
            "hand_size": s.hand_size, "cards_seen": s.cards_seen,
            "commander_in_play": s.commander_in_play,
            "land_colors": _letters(s.land_colors),
            "land_colors_nofetch": _letters(s.land_colors_nofetch),
        } for s in tr.snapshots],
        "castability_units": [[_unit_code(u) for u in turn_units]
                              for turn_units in tr.turn_units],
        "casts": [{
            "turn": e.turn, "name": e.name, "type_key": e.type_key,
            "types": sorted(e.types), "mv": e.mv,
            "colors": _letters(e.colors), "tags": sorted(e.tags),
            "mana_spent": e.mana_spent,
            "spent_colors": list(e.spent_colors),
            "is_commander": e.is_commander, "drawn_turn": e.drawn_turn,
            "power": e.power, "toughness": e.toughness,
        } for e in tr.casts],
        "seen": [{"name": name, "drawn_turn": info["drawn_turn"],
                  "bottomed": info["bottomed"],
                  "discarded": info["discarded"]}
                 for name, info in sorted(tr.seen.items())],
        "commander": {"cast_turns": list(tr.commander_cast_turns),
                      "tax_paid": tr.commander_tax_paid,
                      "removal_turn": tr.removal_turn},
        "wipe": (None if tr.wipe is None else
                 {"turn": tr.wipe[0], "pre_power": tr.wipe[1],
                  "pre_spells": tr.wipe[2]}),
        "discards": [{"turn": t, "name": n} for (t, n) in tr.discards],
        "priced_out": [{"turn": t, "mvs": list(mvs)}
                       for (t, mvs) in tr.priced_out],
        "missed_drop_turns": list(tr.missed_drop_turns),
        "tapped_shortfalls": tr.tapped_shortfalls,
        "payable_names": sorted(tr.payable_names),
        "final_hand": [c.name for c in tr.final_hand],
        "stranded": _stranded(tr, deck),
    }


# ------------------------------------------------------------- writer

class BatchWriter:
    """Streams game records into a JSON file that stays valid after
    every record (see module docstring). Binary mode + explicit byte
    offsets, so card names with non-ASCII characters cannot corrupt
    the bookkeeping."""

    def __init__(self, path: str, meta: dict):
        self.path = path
        self.f = open(path, "wb")
        prefix = ('{"meta": ' + json.dumps(meta, ensure_ascii=False)
                  + ', "games": [\n').encode("utf-8")
        self.f.write(prefix)
        self.records_end = len(prefix)
        self.count = 0
        self._write_tail(complete=False)

    def _write_tail(self, complete: bool):
        tail = ('\n], "run_status": {"complete": %s, "games_written": %d}}'
                % ("true" if complete else "false", self.count)
                ).encode("utf-8")
        self.f.seek(self.records_end)
        self.f.write(tail)
        self.f.truncate()
        self.f.flush()
        os.fsync(self.f.fileno())

    def add_game(self, record: dict):
        line = json.dumps(record, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
        self.f.seek(self.records_end)
        if self.count:
            self.f.write(b",\n")
        self.f.write(line)
        self.records_end = self.f.tell()
        self.count += 1
        self._write_tail(complete=False)

    def finalize(self):
        self._write_tail(complete=True)
        self.f.close()


# ------------------------------------------------------------- loader

def load_batch(path: str):
    """Load a batch file. Returns (data, status) where data has "meta"
    and "games" keys. A strict parse is tried first; if it fails (a
    batch killed between a record write and its tail rewrite), the
    complete single-line records are recovered individually and the
    status marks the file recovered/incomplete."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        data = json.loads(text)
        status = {"complete": bool(data.get("run_status", {})
                                   .get("complete", False)),
                  "recovered": False}
        return data, status
    except json.JSONDecodeError:
        pass
    marker = ', "games": ['
    idx = text.index(marker)
    meta = json.loads(text[len('{"meta": '):idx])
    rest = text[idx + len(marker):]
    games = []
    for line in rest.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("]"):
            break
        if line.endswith(","):
            line = line[:-1]
        try:
            games.append(json.loads(line))
        except json.JSONDecodeError:
            break
    return {"meta": meta, "games": games}, {"complete": False,
                                            "recovered": True}


# ------------------------------------------------------------- run

def build_meta(deck: Deck, variants: list, *, source_filename: str,
               decklist_bytes: bytes, data_disclosure: str, cfg,
               seed: int, turns: int, games: int, regimes) -> dict:
    m = re.search(r"\d{4}-\d{2}-\d{2}", data_disclosure)
    cards_index = {}
    for v in variants:
        for c in v["deck"].all_cards:
            cards_index.setdefault(c.name, card_to_index(c))
    return {
        "schema": SCHEMA,
        "tool_version": __version__,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "deck": {
            "name": deck.name,
            "commander": deck.commander.name,
            "commanders": [c.name for c in deck.commanders],
            "source_filename": source_filename,
            "decklist_sha256": hashlib.sha256(decklist_bytes).hexdigest(),
        },
        "data_source": data_disclosure,
        "data_date": m.group(0) if m else None,
        "keep_rule": cfg.describe(),
        "keep_config": asdict(cfg),
        "seed_base": seed,
        "seed_policy": SEED_POLICY,
        "turns": turns,
        "games_per_config": games,
        "regimes": list(regimes),
        "regimes_enabled": list(regimes),
        "regimes_disabled": [r for r in ("D1", "D2")
                             if r not in regimes],
        "variants": [{"id": v["id"], "label": v["label"],
                      "description": v["description"],
                      "library_size": len(v["deck"].library),
                      "decklist": decklist_counts(v["deck"].library)}
                     for v in variants],
        "cards": cards_index,
        "notes": [
            "Every number a dashboard shows is recomputed from the "
            "per-game records in this file; no aggregates are stored.",
            "Function tags are counting-only: no game-state effect is "
            "ever inferred from a tag. Late-game card-flow numbers are "
            "conservative underestimates (see the v4 design notes).",
        ],
    }


def run_batch(variants: list, cfg, games: int, seed: int, regimes,
              turns: int, meta: dict, out_path: str,
              progress=None) -> int:
    """Run every (variant, regime) for `games` games, streaming each
    finished game into out_path. Returns the number of games written."""
    writer = BatchWriter(out_path, meta)
    total = len(variants) * len(regimes) * games
    done = 0
    try:
        for v in variants:
            for regime in regimes:
                for i, tr in enumerate(run_trials_iter(
                        v["deck"], cfg, games, seed, regime, turns)):
                    writer.add_game(game_record(v["id"], regime, i,
                                                seed, tr, v["deck"]))
                    done += 1
                    if progress:
                        progress(done, total)
    except BaseException:
        # Leave the partial file valid and marked incomplete.
        try:
            writer.f.close()
        except Exception:
            pass
        raise
    writer.finalize()
    return done
