"""Customizable mulligan keep rules (Brandon, 2026-10-31).

A keep rule is a list of condition GROUPS combined by OR: a hand keeps
when ANY group fully passes, and a group passes when EVERY condition in
it is met. (The original single-group form is still supported: a config
with plain `conditions` is one group.) Each condition: at least
`min_count` cards in hand match all of the condition's filters:
  - type: one of land/creature/instant/sorcery/planeswalker/enchantment/
    artifact/battle (saga is NOT a type; sagas are enchantments). A card
    counts toward every type on its type line.
  - color: the card's mana-cost pips include this color (cost pips, NOT
    color identity; a hybrid pip counts toward both of its colors).
  - land_color (v4.1): the card is a land that produces this color
    (its Scryfall produced_mana colors). An Evolving Wilds-family
    fetch land is a WILDCARD: it counts as any color in the
    commander's color identity (it can fetch a source of that color),
    which is why keep evaluation takes the commander's identity.
  - dual_land (v4.1): the card is a land producing 2+ colors. A fetch
    land also counts when the commander's color identity has 2+
    colors (the same wildcard logic: it can fetch a dual-colored
    mana base's missing piece — Edison's reading, confirmed for
    v4.1 by Brandon).
  - mv: mana-value filter — "<= v", "== v", or a [lo, hi] range.
  - tag_any: the card carries at least one of these Scryfall tags.
  - function: a named function condition. Currently only "ramp",
    defined by Brandon (2026-10-31) as a two-prong check — see
    is_ramp() below. (This replaces the earlier fixed tag list
    ramp/land-ramp/mana-rock/mana-dork for keep-rule purposes.)

Mulligan procedure: the default config has TWO free mulligans — that is a
DELIBERATE HOUSE RULE — Brandon chooses this setup knowingly over the strict rule (CR 103.5c grants exactly one free mulligan, in
multiplayer games only). Beyond the free mulligans, the London procedure
applies exactly (CR 103.5): every redraw is a full 7 cards, then the
player bottoms one card per mulligan taken beyond the free ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .cards import COLOR_ORDER, Card

KEEP_TYPES = ("land", "creature", "instant", "sorcery", "planeswalker",
              "enchantment", "artifact", "battle")
_TYPE_CANON = {
    "land": "Land", "creature": "Creature", "instant": "Instant",
    "sorcery": "Sorcery", "planeswalker": "Planeswalker",
    "enchantment": "Enchantment", "artifact": "Artifact", "battle": "Battle",
}


def is_ramp(card: Card) -> bool:
    """Brandon's ramp definition for keep rules (2026-10-31). A card is
    ramp if EITHER:
      (a) any of its Scryfall tags contains the word "ramp" in any
          combination (substring match — covers ramp, land-ramp, and
          any future compound tag), OR
      (b) it is a NON-LAND card whose Scryfall produced_mana field is
          non-empty (structured data explicitly stating it taps for
          mana — this catches mana rocks and mana dorks, whose tags
          do not contain "ramp").
    Lands are deliberately excluded from prong (b): they are counted by
    the land condition, not as ramp."""
    if any("ramp" in t for t in card.tags):
        return True
    return (not card.is_land) and bool(card.produced_mana)


def _land_produced_colors(card: Card) -> frozenset:
    """Colors (WUBRG only) a land produces, per Scryfall produced_mana."""
    return frozenset(c for c in card.produced_colors if c in COLOR_ORDER)


@dataclass(frozen=True)
class KeepCondition:
    min_count: int
    type: str | None = None
    color: str | None = None
    tag_any: tuple = ()
    function: str | None = None    # currently only "ramp" (see is_ramp)
    mv_op: str | None = None       # "<=", "==", "range"
    mv_value: int | None = None
    mv_hi: int | None = None
    land_color: str | None = None  # v4.1: land producing this color
    dual_land: bool = False        # v4.1: land producing 2+ colors

    def matches(self, card: Card,
                identity: frozenset = frozenset()) -> bool:
        if self.land_color is not None or self.dual_land:
            if not card.is_land:
                return False
            produced = _land_produced_colors(card)
            if self.land_color is not None:
                if self.land_color not in produced:
                    # Fetch lands are wildcards: they count as any
                    # color in the commander's color identity.
                    if not (card.is_fetch and self.land_color in identity):
                        return False
            if self.dual_land:
                if len(produced) < 2:
                    # The same wildcard logic: a fetch can satisfy a
                    # dual-land condition in a 2+-color deck.
                    dual_identity = sum(1 for c in identity
                                        if c in COLOR_ORDER) >= 2
                    if not (card.is_fetch and dual_identity):
                        return False
        if self.type is not None:
            if _TYPE_CANON[self.type] not in card.types:
                return False
        if self.color is not None:
            if self.color not in card.colors:
                return False
        if self.tag_any:
            if not (set(self.tag_any) & set(card.tags)):
                return False
        if self.function is not None:
            if self.function == "ramp":
                if not is_ramp(card):
                    return False
            else:
                raise ValueError(f"unknown keep-rule function: {self.function}")
        if self.mv_op == "<=":
            if card.mana_value > self.mv_value:
                return False
        elif self.mv_op == "==":
            if card.mana_value != self.mv_value:
                return False
        elif self.mv_op == "range":
            if not (self.mv_value <= card.mana_value <= self.mv_hi):
                return False
        return True

    def count_in(self, hand: list[Card],
                 identity: frozenset = frozenset()) -> int:
        return sum(1 for c in hand if self.matches(c, identity))

    def describe(self) -> str:
        parts = [f"{self.min_count}+"]
        if self.type:
            parts.append(self.type)
        if self.land_color:
            parts.append(f"land producing {self.land_color}")
        if self.dual_land:
            parts.append("dual land")
        if self.color:
            parts.append(f"color {self.color}")
        if self.tag_any:
            parts.append("tagged " + "/".join(self.tag_any))
        if self.function:
            parts.append(f"function {self.function}")
        if self.mv_op == "<=":
            parts.append(f"MV <= {self.mv_value}")
        elif self.mv_op == "==":
            parts.append(f"MV == {self.mv_value}")
        elif self.mv_op == "range":
            parts.append(f"MV {self.mv_value}-{self.mv_hi}")
        return " ".join(parts)


@dataclass
class KeepConfig:
    free_mulligans: int = 2          # HOUSE RULE (Brandon's, deliberate; strict rules grant one, multiplayer only)
    conditions: list = field(default_factory=list)   # legacy single group
    groups: list = field(default_factory=list)       # OR-combined groups of conditions
    max_mulligans: int = 6           # safety cap; then the hand is force-kept
    label: str | None = None         # optional plain-language rule text for reports

    def _effective_groups(self) -> list:
        groups = [list(g) for g in self.groups]
        if self.conditions:
            groups.append(list(self.conditions))
        return groups

    def keeps(self, hand: list[Card],
              commander_identity: frozenset | None = None) -> bool:
        """`commander_identity` (v4.1) is the commander's color
        identity; land-color conditions need it because fetch lands
        count as wildcards for the identity's colors. Without it,
        fetch lands only count for colors they produce themselves
        (none — so they simply don't satisfy land-color conditions)."""
        identity = commander_identity or frozenset()
        groups = self._effective_groups()
        if not groups:
            return True
        return any(all(c.count_in(hand, identity) >= c.min_count for c in g)
                   for g in groups)

    def describe(self) -> str:
        if self.label:
            keep = self.label
        else:
            groups = self._effective_groups()
            if len(groups) <= 1:
                conds = "; ".join(c.describe() for c in (groups[0] if groups else []))
                keep = conds or "any hand"
            else:
                keep = " OR ".join(
                    "(" + "; ".join(c.describe() for c in g) + ")" for g in groups)
        return (f"{self.free_mulligans} free mulligans (house rule), "
                f"then London; keep = {keep}")


def condition_from_dict(d: dict) -> KeepCondition:
    t = d.get("type")
    if t is not None:
        t = str(t).lower()
        if t == "saga":
            raise ValueError("saga is not a keep-rule type; sagas count as enchantments")
        if t not in KEEP_TYPES:
            raise ValueError(f"unknown keep-rule type: {t}")
    color = d.get("color")
    if color is not None:
        color = str(color).upper()
        if color not in "WUBRG":
            raise ValueError(f"unknown keep-rule color: {color}")
    mv_op = mv_value = mv_hi = None
    if "mv_lte" in d:
        mv_op, mv_value = "<=", int(d["mv_lte"])
    elif "mv_eq" in d:
        mv_op, mv_value = "==", int(d["mv_eq"])
    elif "mv_range" in d:
        lo, hi = d["mv_range"]
        mv_op, mv_value, mv_hi = "range", int(lo), int(hi)
    tags = d.get("tag_any") or ([d["tag"]] if d.get("tag") else [])
    function = d.get("function")
    if function is not None:
        function = str(function).lower()
        if function != "ramp":
            raise ValueError(f"unknown keep-rule function: {function} "
                             "(only 'ramp' is defined)")
    land_color = d.get("land_color")
    if land_color is not None:
        land_color = str(land_color).upper()
        if land_color not in COLOR_ORDER:
            raise ValueError(f"unknown keep-rule land_color: {land_color}")
    dual_land = bool(d.get("dual_land", False))
    if (land_color is not None or dual_land) and t is not None and t != "land":
        raise ValueError("land_color/dual_land conditions are about lands; "
                         "the type filter must be 'land' or omitted")
    return KeepCondition(min_count=int(d.get("min_count", 1)), type=t,
                         color=color, tag_any=tuple(tags), function=function,
                         mv_op=mv_op, mv_value=mv_value, mv_hi=mv_hi,
                         land_color=land_color, dual_land=dual_land)


def config_from_dict(d: dict) -> KeepConfig:
    """Config keys: `free_mulligans`, `max_mulligans`, `label`, plus the
    rule itself as either `conditions` (a single group — the original
    format, still supported) or `groups` (a list of condition lists,
    combined by OR; keep if ANY group fully passes). If both are given,
    the plain `conditions` form one additional group."""
    return KeepConfig(
        free_mulligans=int(d.get("free_mulligans", 2)),
        conditions=[condition_from_dict(c) for c in d.get("conditions", [])],
        groups=[[condition_from_dict(c) for c in g]
                for g in d.get("groups", [])],
        max_mulligans=int(d.get("max_mulligans", 6)),
        label=d.get("label"),
    )


def default_config() -> KeepConfig:
    """Brandon's default (corrected 2026-10-31): 2 free mulligans
    (house rule); keep = (2+ lands AND a ramp card of mana value 3 or
    less) OR (3+ lands, no ramp required). "Ramp" is Brandon's
    two-prong definition — see is_ramp. The MV <= 3 cap applies only
    to the ramp card in the first branch."""
    return KeepConfig(
        free_mulligans=2,
        groups=[
            [KeepCondition(min_count=2, type="land"),
             KeepCondition(min_count=1, function="ramp", mv_op="<=", mv_value=3)],
            [KeepCondition(min_count=3, type="land")],
        ],
        label="2+ lands and a ramp card (MV <= 3), or 3+ lands",
    )
