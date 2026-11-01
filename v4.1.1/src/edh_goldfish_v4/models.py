"""Tier 1 research models (v4.0.2).

Every model here comes from the Tier 1 section of
rebuild-spec/RESEARCH_MODEL_INVENTORY.md and was verified online
against an authoritative source BEFORE implementation (Brandon's math
gate, 2026-10-31); BUILD_REPORT's v4.0.2 section records the source per
model. Two Tier 1 entries are deliberately NOT here:

- T1-05 Compounded Effective Mana Spent (M_comp) and T1-17 (its
  P20/P80): the Reference attributes the (T-t+1)-weighted formula to
  Frank Karsten, but no such metric could be found in Karsten's
  published work (his mana-curve research uses TOTAL mana spent over
  the first T turns, unweighted). Unverifiable -> stopped, not
  improvised. See BUILD_REPORT.
- Models already implemented elsewhere in v4 (Karsten audits T1-02/03
  in measurements.manabase_audit, MCF T1-04 in manasolver, per-stage
  keep counts T1-11 in measurements, Wilson/bootstrap/median machinery
  T1-14/15/16 in stats, CRN pairing T1-18 in simulator.run_trials,
  stress-regime paired differences T1-22 in measurements.regime_deltas)
  are wired into the outputs rather than reimplemented.

Conventions that had to be settled to implement the verified
definitions (all disclosed in the dashboard and BUILD_REPORT):

- Castability (T1-07/T1-08, Salubrious Snail's definitions, verified at
  salubrioussnail.com/manabase-tool): t_castable(c) is the earliest turn
  at or after the spell's target turn on which the trial's mana could
  have paid the spell's full cost ON ITS OWN. We evaluate it against
  the untapped mana units present at the start of the turn's casting
  phase (the step-2 count, before any casting) in the same simulated
  game — so ramp permanents cast on earlier turns count, exactly as in
  Snail's analyzer. Target turn = max(mana value, 1): a 0-MV spell is
  judged against turn 1. X in a cost counts as 0 (the sim's own
  convention). A spell that never becomes payable inside the simulated
  horizon is reported via pct_never_castable; its delay enters the
  mean delay as a lower bound using horizon+1 as the castable turn.
- MUE (T1-06): the documents disagree on the window — Vol. 1 uses
  turns 1-5, the Reference turns 2-6 — so BOTH windows are reported,
  labeled by source. The documents' pass/warning bands are calibrated
  targets (not part of the arithmetic) and are NOT imported.
- Trapped turns (T1-12) count, for a nonland card in hand, each turn
  that starts with the card in hand (draw step included) and ends
  without the card being cast that turn; a cleanup discard still
  counts its turn. Vol. 1's cut-candidate cutoffs are NOT imported.
- Pitch rate (T1-13) counts cleanup discards plus mulligan bottoms as
  pitch events, over recorded draw appearances. Accounting is per card
  NAME (as everywhere in v4): with singleton copies this is exact; for
  basic lands (excluded here — lands are not pitched) it would merge
  copies.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from . import stats
from .cards import COLOR_ORDER
from .manasolver import can_pay
from .scryfall import RAMP_TAGS


class _Unit:
    __slots__ = ("colors",)

    def __init__(self, colors):
        self.colors = colors


def _cards_by_name(deck) -> dict:
    out = {}
    for c in deck.all_cards:
        out.setdefault(c.name, c)
    return out


def _dist_summary(values) -> dict:
    vals = list(values)
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "mean": stats.mean(vals),
            "median": stats.median(vals),
            "p25": stats.percentile(vals, 25),
            "p75": stats.percentile(vals, 75),
            "min": min(vals), "max": max(vals)}


# --------------------------------------------------------------------- MUE

def mana_utilization_efficiency(trials, turns: int) -> dict:
    """T1-06: share of available mana actually spent over an early
    window, per trial (distribution) and pooled (sum spent / sum
    available). Both document windows are reported."""
    out = {}
    for label, window in (("turns_1_5_vol1", range(1, 6)),
                          ("turns_2_6_reference", range(2, 7))):
        per_trial = []
        spent_sum = 0
        avail_sum = 0
        for tr in trials:
            s = sum(tr.snapshots[t - 1].mana_spent
                    for t in window if t <= len(tr.snapshots))
            a = sum(tr.snapshots[t - 1].mana_available
                    for t in window if t <= len(tr.snapshots))
            spent_sum += s
            avail_sum += a
            if a > 0:
                per_trial.append(s / a)
        out[label] = {
            "per_trial": _dist_summary(per_trial),
            "pooled_ratio": (spent_sum / avail_sum) if avail_sum else 0.0,
        }
    return out


# -------------------------------------------------------------- castability

def _cost_signature(cost):
    return (cost.generic,
            tuple(sorted(tuple(sorted(p)) for p in cost.pips)),
            cost.colorless_pips,
            tuple(sorted(cost.twobrid)),
            cost.x_count)


def _t_castable(units_by_turn, cost, target_turn: int, turns: int):
    for t in range(target_turn, turns + 1):
        if t - 1 < len(units_by_turn) and can_pay(units_by_turn[t - 1], cost) is not None:
            return t
    return None


def castability(deck, trials, turns: int) -> dict:
    """T1-07 On-Curve Cast Rate CR(c) and T1-08 Expected Cast Delay for
    every distinct nonland card in the library. Commander excluded (it
    is not a library spell and carries commander tax)."""
    by_name = _cards_by_name(deck)
    spells = {}
    for c in deck.library:
        if not c.is_land:
            spells.setdefault(c.name, c)
    sig_groups: dict = defaultdict(list)
    for name, c in spells.items():
        sig_groups[_cost_signature(c.cost)].append(name)
    # Per trial, build the unit shims once, then evaluate every cost
    # signature against them (spells sharing a cost share the result).
    sig_results: dict = {sig: [] for sig in sig_groups}
    sig_rep = {sig: spells[names[0]] for sig, names in sig_groups.items()}
    for tr in trials:
        units_by_turn = [[_Unit(u) for u in turn_units]
                         for turn_units in tr.turn_units]
        for sig, rep in sig_rep.items():
            target = max(rep.mana_value, 1)
            sig_results[sig].append(
                _t_castable(units_by_turn, rep.cost, target, turns))
    out = {}
    for name, c in spells.items():
        target = max(c.mana_value, 1)
        per_trial = sig_results[_cost_signature(c.cost)]
        n = len(per_trial)
        on_curve = sum(1 for t in per_trial if t is not None and t <= target)
        never = sum(1 for t in per_trial if t is None)
        delays = [max(0, (t if t is not None else turns + 1) - target)
                  for t in per_trial]
        delays_when = [t - target for t in per_trial if t is not None]
        lo, hi = stats.wilson(on_curve, n)
        out[name] = {
            "mv": c.mana_value,
            "types": sorted(c.types),
            "n_trials": n,
            "cast_rate": {
                "k": on_curve, "n": n,
                "pct": (100.0 * on_curve / n) if n else 0.0,
                "wilson95": [100.0 * lo, 100.0 * hi],
            },
            "mean_delay_turns": stats.mean(delays) if delays else 0.0,
            "mean_delay_when_castable": (stats.mean(delays_when)
                                         if delays_when else None),
            "pct_never_castable": (100.0 * never / n) if n else 0.0,
        }
    return out


def castability_summary(cast: dict) -> dict:
    """Unweighted across-spell means (the perturbation matrix's rows are
    built from these; disclosed as unweighted spell means)."""
    if not cast:
        return {"n_spells": 0}
    crs = [v["cast_rate"]["pct"] for v in cast.values()]
    delays = [v["mean_delay_turns"] for v in cast.values()]
    return {"n_spells": len(cast),
            "mean_cast_rate_pct": stats.mean(crs),
            "mean_delay_turns": stats.mean(delays)}


def perturbation_matrix(cast_by_variant: dict, base_id: str = "base",
                        tapland_id: str = "no_taplands") -> dict:
    """T1-09 Marginal Land Perturbation Matrix + T1-10 Tapland Tempo
    Tax, from per-variant castability (all variants share the batch's
    seed base — common random numbers, T1-18). Deltas are
    variant minus base, in percentage points (cast rate) and turns
    (delay). The documents' flag thresholds are interpretation rules,
    not arithmetic, and are NOT applied."""
    base = castability_summary(cast_by_variant[base_id]) if base_id in cast_by_variant else {}
    rows = {}
    for vid, cast in cast_by_variant.items():
        s = castability_summary(cast)
        row = dict(s)
        if base and vid != base_id:
            row["delta_cast_rate_pp"] = (s["mean_cast_rate_pct"]
                                         - base["mean_cast_rate_pct"])
            row["delta_delay_turns"] = (s["mean_delay_turns"]
                                        - base["mean_delay_turns"])
        rows[vid] = row
    out = {"base": base_id, "rows": rows}
    if tapland_id in cast_by_variant and base:
        tax = castability_summary(cast_by_variant[tapland_id])
        out["tapland_tempo_tax"] = {
            "cast_rate_gain_pp": (tax["mean_cast_rate_pct"]
                                  - base["mean_cast_rate_pct"]),
            "delay_reduction_turns": (base["mean_delay_turns"]
                                      - tax["mean_delay_turns"]),
            "definition": ("mean cast rate with taplands converted to "
                           "untapped basics minus mean cast rate of the "
                           "base deck (and the corresponding delay "
                           "difference) — T1-10"),
        }
    return out


# ------------------------------------------------------- trapped / pitch

def trapped_and_pitch(deck, trials, turns: int) -> dict:
    """T1-12 Trapped-Turn Score and T1-13 Discard/Pitch Rate, per
    distinct nonland library card."""
    by_name = _cards_by_name(deck)
    trap_vals: dict[str, list] = defaultdict(list)
    appearances: Counter = Counter()
    pitch_events: Counter = Counter()
    for tr in trials:
        kept_names = {c.name for c in tr.kept_hand}
        cast_first: dict[str, int] = {}
        for e in tr.casts:
            if e.is_commander:
                continue
            if e.name not in cast_first or e.turn < cast_first[e.name]:
                cast_first[e.name] = e.turn
        discard_first: dict[str, int] = {}
        for t, nm in tr.discards:
            if nm not in discard_first:
                discard_first[nm] = t
        bottomed_names = set(tr.bottomed)
        for name, info in tr.seen.items():
            card = by_name.get(name)
            if card is None or card.is_land:
                continue
            appearances[name] += 1
            if info["discarded"]:
                pitch_events[name] += 1
            if info["bottomed"]:
                pitch_events[name] += 1
            # Trapped: the card must have reached the playable hand
            # (kept, or drawn during play) on or before turn 5, and not
            # have been bottomed away (a bottomed-only copy never sat
            # in the playable hand).
            in_hand = name in kept_names or info["drawn_turn"] > 0
            if not in_hand or name in bottomed_names and name not in kept_names:
                continue
            drawn = info["drawn_turn"]
            if drawn > 5:
                continue
            entry = max(drawn, 1)
            ct = cast_first.get(name)
            dt = discard_first.get(name)
            trapped = 0
            for t in range(entry, turns + 1):
                if ct is not None and ct <= t:
                    break
                if dt is not None and dt < t:
                    break
                trapped += 1
            trap_vals[name].append(trapped)
    out = {}
    for name, card in by_name.items():
        if card.is_land or name == deck.commander.name:
            continue
        app = appearances.get(name, 0)
        ev = pitch_events.get(name, 0)
        vals = trap_vals.get(name, [])
        out[name] = {
            "trapped_turns_mean": (stats.mean(vals) if vals else None),
            "trapped_n_games": len(vals),
            "pitch_rate": {
                "events": ev, "appearances": app,
                "pct": (100.0 * ev / app) if app else 0.0,
                "wilson95": [100.0 * x for x in stats.wilson(ev, app)],
            },
        }
    return out


# ------------------------------------------------------------ keep rates

def keep_stage_rates(trials) -> dict:
    """T1-11 Per-Stage Keep Rate with Wilson intervals (T1-14). A
    stage's keep rate = hands kept at that stage / hands reaching it."""
    reached: Counter = Counter()
    kept: Counter = Counter()
    for tr in trials:
        seen_labels = set()
        for label, k in tr.stages:
            if label not in seen_labels:
                reached[label] += 1
                seen_labels.add(label)
            if k:
                kept[label] += 1
    out = {}
    for label in reached:
        lo, hi = stats.wilson(kept[label], reached[label])
        out[label] = {"reached": reached[label], "kept": kept[label],
                      "rate_pct": (100.0 * kept[label] / reached[label])
                      if reached[label] else 0.0,
                      "wilson95": [100.0 * lo, 100.0 * hi]}
    return out


# ----------------------------------------------------------- hypergeometric

def hypergeometric_table(deck) -> dict:
    """T1-01: exact hypergeometric probabilities for the deck's own
    composition. Rows: cards seen (opening 7 + one draw per turn,
    before mulligans — a pure deck-math layer, disclosed as such).
    Categories: all lands; for each color in the commander's identity,
    land sources of that color and all sources of that color (Karsten's
    counting: lands at 1.0, a fetch that can fetch a basic of the color
    at 1.0, nonland producers at 1.0 for this probability layer — the
    manabase audit's 0.5-weight convention for cheap nonland sources is
    a separate Karsten convention and is not mixed into exact counts)."""
    N = len(deck.library)
    basic_colors = set()
    for c in deck.library:
        if c.is_land and "Basic" in c.supertypes:
            basic_colors |= set(c.produced_colors)

    def land_sources(color):
        k = 0
        for c in deck.library:
            if not c.is_land:
                continue
            if color in c.produced_colors:
                k += 1
            elif c.is_fetch and color in basic_colors:
                k += 1
        return k

    def all_sources(color):
        k = land_sources(color)
        for c in deck.library:
            if not c.is_land and color in c.produced_colors:
                k += 1
        return k

    categories = {}
    cats = [("lands", sum(1 for c in deck.library if c.is_land),
             (1, 2, 3, 4, 5))]
    for col in COLOR_ORDER:
        if col in deck.commander.color_identity:
            cats.append((f"land_sources_{col}", land_sources(col), (1, 2, 3)))
            cats.append((f"all_sources_{col}", all_sources(col), (1, 2, 3)))
    seen_counts = [7, 8, 9, 10, 11, 12, 13]
    for label, K, ks in cats:
        rows = {}
        for n in seen_counts:
            if n > N:
                continue
            rows[str(n)] = {str(k): round(100.0 * stats.hypergeom_ge(k, N, K, n), 2)
                            for k in ks}
        categories[label] = {"K": K, "N": N, "by_cards_seen": rows}
    return {"categories": categories,
            "note": ("Exact hypergeometric probabilities (percent) of "
                     "having drawn at least k cards of the category "
                     "after seeing n cards. No mulligans, no ramp "
                     "effects: pure deck composition math.")}


# ------------------------------------------------------------- combined

def tier1_models(deck, trials, turns: int) -> dict:
    """The Tier 1 model outputs that are computed per (deck, regime):
    MUE (T1-06), Cast Rate / Cast Delay (T1-07/T1-08), Trapped-Turn
    Score + Pitch Rate (T1-12/T1-13), Per-Stage Keep Rate (T1-11).
    Deck-level models (Karsten T1-02/03, hypergeometric T1-01) and
    comparison models (Wilcoxon T1-19, McNemar T1-20, Mann-Whitney
    T1-21, perturbation matrix T1-09/10) live in their own sections."""
    return {
        "mana_utilization_efficiency": mana_utilization_efficiency(trials, turns),
        "castability": castability(deck, trials, turns),
        "trapped_and_pitch": trapped_and_pitch(deck, trials, turns),
        "keep_stage_rates": keep_stage_rates(trials),
    }
