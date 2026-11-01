"""Card model built ONLY from Scryfall structured fields.

No oracle text is parsed for game functions. The single exception: lands,
where two agreed text checks are applied at classification time (the
Evolving Wilds fetch template, and "enters tapped"). Oracle text is not
retained on the Card object after classification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

COLOR_ORDER = "WUBRG"
MAIN_TYPES = ("Land", "Creature", "Artifact", "Enchantment", "Planeswalker",
              "Battle", "Instant", "Sorcery", "Kindred")
SUPERTYPES = {"Legendary", "Basic", "Snow", "World", "Ongoing", "Elite"}

_TOKEN_RE = re.compile(r"\{([^}]*)\}")


@dataclass(frozen=True)
class Cost:
    """A parsed mana cost. Pips are requirements; each pip is the set of
    colors that can pay it (hybrid = 2 colors, phyrexian = its color;
    the life-payment option for phyrexian is not modeled — see report)."""
    generic: int = 0
    pips: tuple = ()            # tuple[frozenset[str]]
    colorless_pips: int = 0     # {C} pips: payable only with colorless mana
    twobrid: tuple = ()         # tuple[str] colors of {2/C} pips
    x_count: int = 0

    @property
    def mana_value(self) -> int:
        return self.generic + len(self.pips) + self.colorless_pips + 2 * len(self.twobrid)

    @property
    def min_mana(self) -> int:
        """Cheapest possible payment (two-brid paid as 1 colored)."""
        return self.generic + len(self.pips) + self.colorless_pips + len(self.twobrid)

    @property
    def colors(self) -> frozenset:
        s = set(self.twobrid)
        for p in self.pips:
            s |= set(p)
        return frozenset(s)


def parse_cost(cost_str: Optional[str]) -> Cost:
    if not cost_str:
        return Cost()
    generic = 0
    pips = []
    colorless = 0
    twobrid = []
    x = 0
    for tok in _TOKEN_RE.findall(cost_str):
        tok = tok.strip().upper()
        if tok.isdigit():
            generic += int(tok)
        elif tok in ("X", "Y", "Z"):
            x += 1
        elif len(tok) == 1 and tok in COLOR_ORDER:
            pips.append(frozenset(tok))
        elif tok == "C":
            colorless += 1
        elif tok == "S":
            generic += 1  # snow pip: treated as generic (disclosed)
        elif "/" in tok:
            a, b = tok.split("/", 1)
            if a in COLOR_ORDER and b in COLOR_ORDER:
                pips.append(frozenset((a, b)))       # hybrid
            elif a in COLOR_ORDER and b == "P":
                pips.append(frozenset(a))            # phyrexian: color pip
            elif a == "2" and b in COLOR_ORDER:
                twobrid.append(b)                    # two-brid
            elif a == "2" and b == "P":
                generic += 2                         # {2/P}: treat as generic 2 (rare)
            else:
                generic += 1                         # unknown split: never crash
        else:
            generic += 1                             # unknown symbol (e.g. {H}): never crash
    return Cost(generic=generic, pips=tuple(pips), colorless_pips=colorless,
                twobrid=tuple(twobrid), x_count=x)


def _parse_pt(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None  # "*", "1+*", etc. -> unknown; totals treat as 0 (disclosed)


def split_type_line(type_line: str):
    left, _, right = (type_line or "").partition("—")
    words = left.split()
    types = frozenset(w for w in words if w in MAIN_TYPES)
    supers = frozenset(w for w in words if w in SUPERTYPES)
    subtypes = frozenset(right.split()) if right else frozenset()
    return types, supers, subtypes


# --- The two agreed land text checks (the ONLY oracle-text reads in v4) ---

def land_enters_tapped(name: str, oracle_text: str) -> bool:
    """True if the land's own text says it enters tapped, including the
    'enters tapped unless...' wording (the condition is NOT honored —
    Brandon's simplification: unless-lands always enter tapped)."""
    if not oracle_text:
        return False
    lname = (name or "").lower()
    for sentence in re.split(r"(?<=[.!])\s+", oracle_text):
        s = sentence.lower()
        if "tapped" not in s:
            continue
        if "enters" not in s and "comes into play" not in s:
            continue
        if lname and lname in s:
            return True
        if "this land" in s or "this permanent" in s or "it enters" in s:
            return True
    return False


def is_fetch_land(oracle_text: str) -> bool:
    """The Evolving Wilds template family: sacrifice this land, search your
    library for a basic land card, put it onto the battlefield (tapped per
    the template), then shuffle."""
    if not oracle_text:
        return False
    t = oracle_text.lower()
    return ("sacrifice this" in t
            and "search your library for a basic land card" in t
            and "onto the battlefield" in t
            and "shuffle" in t)


@dataclass
class Card:
    name: str
    oracle_id: str
    type_line: str
    types: frozenset
    supertypes: frozenset
    subtypes: frozenset
    cost: Cost
    mana_value: int
    color_identity: frozenset
    keywords: tuple = ()
    produced_mana: Optional[tuple] = None   # tuple of letters from Scryfall, or None
    power: Optional[int] = None
    toughness: Optional[int] = None
    is_land: bool = False
    enters_tapped: bool = False
    is_fetch: bool = False
    tags: frozenset = frozenset()

    @property
    def colors(self) -> frozenset:
        """Colors of the cost pips (NOT color identity)."""
        return self.cost.colors

    @property
    def produced_colors(self) -> frozenset:
        if not self.produced_mana:
            return frozenset()
        return frozenset(c for c in self.produced_mana if c in COLOR_ORDER + "C")

    @property
    def is_mana_source(self) -> bool:
        return bool(self.produced_colors)

    @property
    def source_amount(self) -> int:
        """Mana produced per use. Default 1 (Scryfall lists types, not a
        count). Sol Ring exception (Brandon): 2 colorless."""
        if self.name == "Sol Ring":
            return 2
        return 1

    def type_key(self) -> str:
        """Composition key: main types in canonical order, supertypes dropped,
        so 'Artifact Creature' is its own line."""
        order = ["Land", "Creature", "Artifact", "Enchantment",
                 "Planeswalker", "Battle", "Instant", "Sorcery", "Kindred"]
        return " ".join(t for t in order if t in self.types) or "Unknown"


def card_from_scryfall(data: dict) -> Card:
    """Build a Card from a raw Scryfall card JSON object (structured fields
    only). For multi-face cards the front face supplies cost/type/text."""
    face = data
    if data.get("card_faces"):
        face = data["card_faces"][0]
    type_line = face.get("type_line", data.get("type_line", ""))
    types, supers, subtypes = split_type_line(type_line)
    cost = parse_cost(face.get("mana_cost", data.get("mana_cost", "")))
    is_land = "Land" in types
    oracle_text = face.get("oracle_text", data.get("oracle_text", "")) or ""
    name = face.get("name", data.get("name", ""))
    produced = data.get("produced_mana", face.get("produced_mana"))
    card = Card(
        name=name,
        oracle_id=data.get("oracle_id", ""),
        type_line=type_line,
        types=types,
        supertypes=supers,
        subtypes=subtypes,
        cost=cost,
        mana_value=cost.mana_value if not is_land else 0,
        color_identity=frozenset(data.get("color_identity", [])),
        keywords=tuple(data.get("keywords", face.get("keywords", []))),
        produced_mana=tuple(produced) if produced else None,
        power=_parse_pt(face.get("power")),
        toughness=_parse_pt(face.get("toughness")),
        is_land=is_land,
        enters_tapped=land_enters_tapped(name, oracle_text) if is_land else False,
        is_fetch=is_fetch_land(oracle_text) if is_land else False,
    )
    return card
