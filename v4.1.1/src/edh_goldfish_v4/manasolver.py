"""Mana payment solver: exact feasibility + the step-9 max-cast plan.

A Unit is 1 mana from one source; it carries the set of colors that source
can produce (the color is chosen when the unit is spent). A multi-mana
source (only Sol Ring under the v4 rules) expands to multiple units.

Feasibility is exact bipartite matching (Kuhn's algorithm): pip slots and
generic requirements on one side, units on the other. A unit can pay a pip
if it can produce one of the pip's acceptable colors; any unit can pay
generic; a {C} pip accepts only units that can produce colorless. This
fixes the v3 audit item 25 (a 2-mana source can pay two pips of its
color, because it expands to two units).
"""

from __future__ import annotations

from itertools import combinations

from .cards import Cost, COLOR_ORDER

# A payment item: (card_key, kind, acceptable)
# kind: "pip" (acceptable = frozenset of colors), "generic" (acceptable None)
# Two-brid pips are branched before matching: each becomes either a "pip"
# item for its color or two "generic" items.


def _base_items(key, cost: Cost):
    items = []
    for pip in cost.pips:
        items.append((key, "pip", pip))
    for _ in range(cost.colorless_pips):
        items.append((key, "pip", frozenset("C")))
    for _ in range(cost.generic):
        items.append((key, "generic", None))
    return items


def _match(items, units):
    """Kuhn's algorithm. Returns unit->item-index list or None."""
    match_unit = [-1] * len(units)

    def augment(item_idx, seen):
        acc = items[item_idx][2]
        for ui, unit in enumerate(units):
            if seen[ui]:
                continue
            if acc is not None and not (unit.colors & acc):
                continue
            seen[ui] = True
            if match_unit[ui] == -1 or augment(match_unit[ui], seen):
                match_unit[ui] = item_idx
                return True
        return False

    for i in range(len(items)):
        if not augment(i, [False] * len(units)):
            return None
    return match_unit


def _try_items(units, items):
    if len(items) > len(units):
        return None
    m = _match(items, units)
    if m is None:
        return None
    payments = []
    for ui, item_idx in enumerate(m):
        if item_idx >= 0:
            key, kind, acc = items[item_idx]
            payments.append((key, ui, kind, acc))
    return payments


def pay_plan(units, costs: dict):
    """If every cost in `costs` (key -> Cost) can be paid simultaneously
    from `units`, return the payment list [(key, unit_idx, kind, acc)];
    else None. Two-brid pips branch: pip-of-color or 2 generic (branching
    capped at 5 two-brid pips; beyond that they are treated as color pips —
    vanishingly rare in practice)."""
    base = []
    twobrids = []
    for key, cost in costs.items():
        base.extend(_base_items(key, cost))
        for color in cost.twobrid:
            twobrids.append((key, color))
    if len(twobrids) > 5:
        items = list(base)
        for key, color in twobrids:
            items.append((key, "pip", frozenset(color)))
        return _try_items(units, items)

    def rec(i, items):
        if i == len(twobrids):
            return _try_items(units, items)
        key, color = twobrids[i]
        r = rec(i + 1, items + [(key, "pip", frozenset(color))])
        if r is not None:
            return r
        return rec(i + 1, items + [(key, "generic", None), (key, "generic", None)])

    return rec(0, list(base))


def can_pay(units, cost: Cost):
    """Units indices that pay this single cost, or None."""
    p = pay_plan(units, {0: cost})
    if p is None:
        return None
    return sorted(ui for _, ui, _, _ in p)


def max_cast_plan(units, costs: dict, mvs: dict):
    """Step 9: the largest set of cards payable from `units`.
    Tie-break: the plan spending the most mana (summed mana value).
    Returns (subset_keys_tuple, payments). Falls back to a greedy plan if
    the candidate count makes exact enumeration explode (documented
    approximation; in the skeleton sim hands are small so this is rare)."""
    total = len(units)
    candidates = [k for k, c in costs.items() if c.min_mana <= total]
    if not candidates:
        return (), None
    if len(candidates) > 12:
        return _greedy_plan(units, candidates, costs, mvs)
    for size in range(len(candidates), 0, -1):
        best = None
        best_mv = -1
        best_pay = None
        for combo in combinations(candidates, size):
            sub = {k: costs[k] for k in combo}
            pay = pay_plan(units, sub)
            if pay is not None:
                mv_sum = sum(mvs[k] for k in combo)
                if mv_sum > best_mv:
                    best, best_mv, best_pay = combo, mv_sum, pay
        if best is not None:
            return tuple(best), best_pay
    return (), None


def _greedy_plan(units, candidates, costs, mvs):
    chosen = []
    for k in sorted(candidates, key=lambda k: (costs[k].min_mana, -mvs[k])):
        trial = chosen + [k]
        if pay_plan(units, {kk: costs[kk] for kk in trial}) is not None:
            chosen = trial
    if not chosen:
        return (), None
    return tuple(chosen), pay_plan(units, {k: costs[k] for k in chosen})


def payment_color(unit, kind, acc) -> str:
    """The color a unit is counted as having produced for a payment:
    the pip's color when paying a single-color pip; otherwise the unit's
    first producible color in WUBRG order (colorless last). Accounting
    convention only — disclosed in the report."""
    if kind == "pip" and acc is not None and len(acc) == 1:
        return next(iter(acc))
    for c in COLOR_ORDER + "C":
        if c in unit.colors:
            return c
    return "C"
