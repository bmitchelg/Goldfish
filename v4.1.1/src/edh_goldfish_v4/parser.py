"""Decklist text parsing + deck assembly (classification comes from
cards.py structured fields and the session tag map — never oracle text)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .cards import Card, card_from_scryfall

_LINE_RE = re.compile(r"^(\d+)\s*x?\s+(.+?)\s*$")
_SET_SUFFIX_RE = re.compile(r"\s*\([A-Za-z0-9]{2,6}\)\s*(?:[0-9A-Za-z★*]+)?\s*$")
_FOIL_RE = re.compile(r"\s*\*F\*\s*$")


def clean_name(raw: str) -> str:
    name = _FOIL_RE.sub("", raw.strip())
    name = _SET_SUFFIX_RE.sub("", name).strip()
    return name


@dataclass
class Decklist:
    commander_names: list[str] = field(default_factory=list)
    mainboard: list[tuple[int, str]] = field(default_factory=list)  # (count, name)


def parse_decklist(text: str) -> Decklist:
    dl = Decklist()
    section = "main"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("//"):
            tag = line[2:].strip().lower()
            if "commander" in tag:
                section = "commander"
            elif "sideboard" in tag:
                section = "side"
            else:
                section = "main"
            continue
        low = line.lower()
        if low.startswith("commander:"):
            dl.commander_names.append(clean_name(line.split(":", 1)[1]))
            continue
        if low.startswith("sb:"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        count, name = int(m.group(1)), clean_name(m.group(2))
        if section == "commander":
            dl.commander_names.append(name)
        elif section == "main":
            dl.mainboard.append((count, name))
    return dl


@dataclass
class Deck:
    commanders: list[Card]
    library: list[Card]          # the 99 (expanded copies)
    name: str = "Deck"

    @property
    def commander(self) -> Card:
        return self.commanders[0]

    @property
    def all_cards(self) -> list[Card]:
        return self.commanders + self.library


def build_deck(decklist: Decklist, card_jsons: dict[str, dict],
               tag_map: dict[str, frozenset], name: str = "Deck") -> Deck:
    def make(card_name: str) -> Card:
        card = card_from_scryfall(card_jsons[card_name])
        card.tags = frozenset(t for t, oids in tag_map.items()
                              if card.oracle_id in oids)
        return card

    commanders = [make(n) for n in decklist.commander_names]
    library: list[Card] = []
    for count, card_name in decklist.mainboard:
        for _ in range(count):
            library.append(make(card_name))
    return Deck(commanders=commanders, library=library, name=name)
