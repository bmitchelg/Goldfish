"""Aggregation: turn trial results into the concrete measurements of the
spec (sections A-H). Plain arithmetic over event logs and snapshots —
no scores, no composites, no archetypes. Where a subgroup has zero
trials, the report says n=0; nothing is ever fabricated (audit item 4).
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict

from . import models, stats
from .cards import COLOR_ORDER
from .scryfall import RAMP_TAGS
from .simulator import TrialResult, WIPE_TURN


def _dist(values, turns_cap=None):
    """Summary distribution for a list of turn numbers (None = never)."""
    vals = [v for v in values if v is not None]
    out = {"n_events": len(vals), "pct_ever": (100.0 * len(vals) / len(values)) if values else 0.0}
    if vals:
        out["median"] = stats.median(vals)
        out["median_ci95"] = stats.median_ci_exact(vals)
        out["mean"] = stats.mean(vals)
        out["p25"] = stats.percentile(vals, 25)
        out["p75"] = stats.percentile(vals, 75)
    return out


def _rate(k, n):
    lo, hi = stats.wilson(k, n)
    return {"k": k, "n": n, "pct": (100.0 * k / n) if n else 0.0,
            "wilson95": [100.0 * lo, 100.0 * hi]}


# ---------------------------------------------------------------- manabase

def manabase_audit(deck) -> dict:
    """Karsten audit (formulas audit-verified). Input conventions are
    disclosed in the report: cheap ramp = ramp-tagged cards at MV <= 2;
    cantrips = cantrip-tagged cards at MV <= 2; sources count lands at
    1.0, fetch lands that can fetch a basic of the color at 1.0, and
    non-land sources at MV <= 2 at 0.5 (Karsten's convention)."""
    spells = [c for c in deck.library if not c.is_land] + list(deck.commanders)
    avg_mv = stats.mean([c.mana_value for c in spells]) if spells else 0.0
    cheap_ramp = sum(1 for c in deck.library
                     if c.mana_value <= 2 and set(c.tags) & set(RAMP_TAGS))
    cantrips = sum(1 for c in deck.library
                   if c.mana_value <= 2 and "cantrip" in c.tags)
    lands_actual = sum(1 for c in deck.library if c.is_land)
    basic_colors = set()
    for c in deck.library:
        if c.is_land and "Basic" in c.supertypes:
            basic_colors |= set(c.produced_colors)

    pips: dict[str, dict] = {}
    identity = [c for c in COLOR_ORDER if c in deck.commander.color_identity]
    for col in identity:
        sources = 0.0
        for c in deck.library:
            if c.is_land:
                if col in c.produced_colors:
                    sources += 1.0
                elif c.is_fetch and col in basic_colors:
                    sources += 1.0
            elif c.mana_value <= 2 and col in c.produced_colors:
                sources += 0.5
        best_req = None
        best_card = None
        n_single = n_double = n_triple = 0
        for c in spells:
            cnt = 0
            for pip in c.cost.pips:
                if col in pip:
                    cnt += 1
            cnt += sum(1 for t in c.cost.twobrid if t == col)
            if cnt == 0:
                continue
            if cnt == 1:
                n_single += 1
            elif cnt == 2:
                n_double += 1
            else:
                n_triple += 1
            req = stats.karsten_required_sources(c.mana_value, cnt)
            if req is not None and (best_req is None or req > best_req):
                best_req, best_card = req, c.name
        pips[col] = {
            "sources": round(sources, 1),
            "required": best_req,           # None = no demand (never an invented default)
            "peak_demand_card": best_card,
            "single_pip_cards": n_single,
            "double_pip_cards": n_double,
            "triple_pip_cards": n_triple,
            "deficit": (round(best_req - sources, 1)
                        if best_req is not None else None),
        }
    return {
        "lands_actual": lands_actual,
        "avg_mv_spells": round(avg_mv, 3),
        "cheap_ramp_le2": cheap_ramp,
        "cantrips_le2": cantrips,
        "karsten_recommended_lands": round(stats.karsten_lands(avg_mv, cheap_ramp, cantrips), 2),
        "pips": pips,
    }


# ---------------------------------------------------------------- aggregate

def _hand_comp(hands: list[list]) -> dict:
    if not hands:
        return {"n": 0}
    n = len(hands)
    by_type: dict[str, float] = defaultdict(float)
    by_color: dict[str, float] = defaultdict(float)
    lands = 0.0
    mv = 0.0
    for h in hands:
        for c in h:
            if c.is_land:
                lands += 1
            for t in c.types:
                by_type[t] += 1
            for col in c.colors:
                by_color[col] += 1
            mv += c.mana_value
    total = sum(len(h) for h in hands)
    return {"n": n, "avg_lands": lands / n,
            "avg_by_type": {k: v / n for k, v in sorted(by_type.items())},
            "avg_by_color": {k: v / n for k, v in sorted(by_color.items())},
            "avg_mv_per_card": (mv / total) if total else 0.0}


def aggregate(deck, trials: list[TrialResult], turns: int) -> dict:
    n = len(trials)
    cmd_colors = [c for c in COLOR_ORDER if c in deck.commander.color_identity]
    lib_size = len(deck.library)

    # ---- F: mulligans ------------------------------------------------
    stage_reached: Counter = Counter()
    stage_rule_kept: Counter = Counter()
    stage_ended: Counter = Counter()
    size_dist: Counter = Counter()
    for tr in trials:
        seen_stages = set()
        for label, kept in tr.stages:
            if label not in seen_stages:
                stage_reached[label] += 1
                seen_stages.add(label)
            if kept:
                stage_rule_kept[label] += 1
        if tr.stages:
            stage_ended[tr.stages[-1][0]] += 1
        size_dist[tr.final_hand_size] += 1
    stages = {}
    for label in stage_reached:
        stages[label] = {"reached": stage_reached[label],
                         "rule_kept": stage_rule_kept[label],
                         "ended_here": stage_ended[label]}

    by_size: dict[str, dict] = {}
    sizes_present = sorted({tr.final_hand_size for tr in trials} | {7, 6, 5})
    for size in sizes_present:
        sub = [tr for tr in trials if tr.final_hand_size == size]
        if not sub:
            by_size[str(size)] = {"n": 0}   # reported as n=0, never fabricated
            continue
        spells7 = [sum(s.spells_cast for s in tr.snapshots[:7]) for tr in sub]
        cmd_turn = [tr.commander_cast_turns[0] if tr.commander_cast_turns else None
                    for tr in sub]
        util = []
        for tr in sub:
            for s in tr.snapshots[:7]:
                if s.mana_available > 0:
                    util.append(s.mana_spent / s.mana_available)
        by_size[str(size)] = {
            "n": len(sub), "mean_spells_by_t7": stats.mean(spells7),
            "commander_first_cast": _dist(cmd_turn),
            "mean_mana_utilization_t1_7": stats.mean(util) if util else 0.0}

    bottomed_counter: Counter = Counter()
    for tr in trials:
        bottomed_counter.update(tr.bottomed)
    bottomed_cards = deck_cards_by_name(deck)
    bottomed_stats = []
    for name_, cnt in bottomed_counter.most_common():
        c = bottomed_cards.get(name_)
        bottomed_stats.append({"name": name_, "times_bottomed": cnt,
                               "mv": c.mana_value if c else None,
                               "types": sorted(c.types) if c else [],
                               "tags": sorted(c.tags) if c else []})

    # ---- per-turn curves (B) ------------------------------------------
    curves: dict[str, dict] = {}
    for t in range(1, turns + 1):
        snaps = [tr.snapshots[t - 1] for tr in trials if len(tr.snapshots) >= t]
        if not snaps:
            continue
        util = [s.mana_spent / s.mana_available for s in snaps if s.mana_available > 0]
        curves[str(t)] = {
            "spells_mean": stats.mean([s.spells_cast for s in snaps]),
            "spells_median": stats.median([s.spells_cast for s in snaps]),
            "cum_spells_mean": stats.mean(
                [sum(x.spells_cast for x in tr.snapshots[:t]) for tr in trials]),
            "mana_available_mean": stats.mean([s.mana_available for s in snaps]),
            "mana_spent_mean": stats.mean([s.mana_spent for s in snaps]),
            "mana_utilization_mean": stats.mean(util) if util else 0.0,
            "unspent_mean": stats.mean([s.mana_available - s.mana_spent for s in snaps]),
            "creatures_mean": stats.mean([s.creatures for s in snaps]),
            "power_mean": stats.mean([s.power for s in snaps]),
            "power_median": stats.median([s.power for s in snaps]),
            "toughness_mean": stats.mean([s.toughness for s in snaps]),
            "hand_mean": stats.mean([s.hand_size for s in snaps]),
            "cards_seen_mean": stats.mean([s.cards_seen for s in snaps]),
            "lands_mean": stats.mean([s.lands for s in snaps]),
            "perms_by_type_mean": {
                k: stats.mean([s.perms_by_type.get(k, 0) for s in snaps])
                for k in sorted({k for s in snaps for k in s.perms_by_type})},
            "sources_by_color_mean": {
                k: stats.mean([s.sources_by_color.get(k, 0) for s in snaps])
                for k in sorted({k for s in snaps for k in s.sources_by_color})},
            "avg_mv_cast": _avg_mv_cast_on_turn(trials, t),
        }

    # ---- A: firsts ------------------------------------------------------
    def first_turn(pred, trials_=trials):
        out = []
        for tr in trials_:
            v = None
            for e in tr.casts:
                if pred(e):
                    v = e.turn
                    break
            out.append(v)
        return out

    first_creature_events = []
    for tr in trials:
        for e in tr.casts:
            if "Creature" in e.types:
                first_creature_events.append(e)
                break
    first_by_type = {}
    for tname in ("Creature", "Artifact", "Enchantment", "Instant",
                  "Sorcery", "Planeswalker", "Battle"):
        first_by_type[tname] = _dist(first_turn(lambda e, tn=tname: tn in e.types))
    first_land = []
    for tr in trials:
        v = tr.land_plays[0][0] if tr.land_plays else None
        first_land.append(v)
    first_by_type["Land"] = _dist(first_land)

    deck_tags = sorted({tag for c in deck.all_cards for tag in c.tags})
    first_by_tag = {tag: _dist(first_turn(lambda e, tg=tag: tg in e.tags))
                    for tag in deck_tags}
    mv_values = sorted({e.mv for tr in trials for e in tr.casts})
    first_by_mv = {str(mv): _dist(first_turn(lambda e, m=mv: e.mv == m))
                   for mv in mv_values}

    def first_snapshot_turn(pred):
        out = []
        for tr in trials:
            v = None
            for s in tr.snapshots:
                if pred(s):
                    v = s.turn
                    break
            out.append(v)
        return out

    cmd_set = frozenset(cmd_colors)
    color_complete = _dist(first_snapshot_turn(
        lambda s: cmd_set <= set(s.land_colors) if cmd_set else True))
    color_complete_nf = _dist(first_snapshot_turn(
        lambda s: cmd_set <= set(s.land_colors_nofetch) if cmd_set else True))
    first_empty = _dist(first_snapshot_turn(lambda s: s.hand_size == 0))
    power_thr = {str(th): _dist(first_snapshot_turn(lambda s, th=th: s.power >= th))
                 for th in (5, 10, 20, 40)}
    drawn_thr = {str(th): _dist(first_snapshot_turn(lambda s, th=th: s.cards_seen - 7 >= th))
                 for th in (5, 10, 20)}
    mana_thr = {}
    for th in (20, 40, 60):
        vals = []
        for tr in trials:
            cum = 0
            v = None
            for s in tr.snapshots:
                cum += s.mana_spent
                if cum >= th:
                    v = s.turn
                    break
            vals.append(v)
        mana_thr[str(th)] = _dist(vals)

    firsts = {
        "first_creature": _dist([e.turn for e in first_creature_events] +
                                [None] * (n - len(first_creature_events))),
        "first_creature_avg_mv": stats.mean([e.mv for e in first_creature_events]) if first_creature_events else None,
        "first_creature_avg_power": stats.mean([e.power or 0 for e in first_creature_events]) if first_creature_events else None,
        "first_by_type": first_by_type,
        "first_by_tag": first_by_tag,
        "first_by_mv": first_by_mv,
        "color_complete_turn": color_complete,
        "color_complete_turn_ignoring_fetches": color_complete_nf,
        "first_empty_hand": first_empty,
        "power_thresholds": power_thr,
        "cumulative_drawn_thresholds": drawn_thr,
        "cumulative_mana_spent_thresholds": mana_thr,
    }

    # ---- commander (A4, C24, G40) ---------------------------------------
    cmd_first = [tr.commander_cast_turns[0] if tr.commander_cast_turns else None
                 for tr in trials]
    cmd_by_turn = {}
    for t in range(1, turns + 1):
        k = sum(1 for v in cmd_first if v is not None and v <= t)
        cmd_by_turn[str(t)] = _rate(k, n)
    total_mana_all = sum(s.mana_spent for tr in trials for s in tr.snapshots)
    cmd_mana = sum(e.mana_spent for tr in trials for e in tr.casts if e.is_commander)
    commander = {
        "first_cast": _dist(cmd_first),
        "pct_cast_by_turn": cmd_by_turn,
        "recasts_mean": stats.mean([max(0, len(tr.commander_cast_turns) - 1) for tr in trials]),
        "pct_turns_in_play": stats.mean([
            (sum(1 for s in tr.snapshots if s.commander_in_play) / len(tr.snapshots))
            if tr.snapshots else 0.0 for tr in trials]),
        "tax_paid_mean": stats.mean([tr.commander_tax_paid for tr in trials]),
        "mana_share_pct": (100.0 * cmd_mana / total_mana_all) if total_mana_all else 0.0,
    }

    # ---- C: composition ---------------------------------------------------
    all_casts = [e for tr in trials for e in tr.casts]
    total_casts = len(all_casts)
    by_typekey = Counter(e.type_key for e in all_casts)
    by_colors = Counter("".join(e.colors) if e.colors else "Colorless" for e in all_casts)
    spent_color = Counter()
    for e in all_casts:
        spent_color.update(e.spent_colors)
    total_spent_events = sum(e.mana_spent for e in all_casts)
    by_mv = Counter(min(e.mv, 7) for e in all_casts)
    casts_by_tag = Counter()
    mana_by_tag = Counter()
    for e in all_casts:
        for tag in e.tags:
            casts_by_tag[tag] += 1
            mana_by_tag[tag] += e.mana_spent
    fresh = sum(1 for e in all_casts if e.drawn_turn > 0 and e.drawn_turn == e.turn)
    composition = {
        "total_casts": total_casts,
        "casts_per_game": total_casts / n if n else 0.0,
        "by_type": {k: {"casts": v, "pct": 100.0 * v / total_casts}
                    for k, v in by_typekey.most_common()} if total_casts else {},
        "by_pip_colors": {k: {"casts": v, "pct": 100.0 * v / total_casts}
                          for k, v in by_colors.most_common()} if total_casts else {},
        "mana_spent_by_color": {k: {"mana": v, "pct": 100.0 * v / total_spent_events}
                                for k, v in spent_color.most_common()} if total_spent_events else {},
        "by_mv_bucket": {str(k): {"casts": by_mv.get(k, 0),
                                  "pct": 100.0 * by_mv.get(k, 0) / total_casts if total_casts else 0.0}
                         for k in range(0, 8)},
        "casts_by_tag_per_game": {k: v / n for k, v in casts_by_tag.most_common()} if n else {},
        "mana_by_tag_per_game": {k: v / n for k, v in mana_by_tag.most_common()} if n else {},
        "fresh_cast_rate": _rate(fresh, total_casts),
    }

    # ---- D: per-card ------------------------------------------------------
    per_card = _per_card(deck, trials, n)
    first_card_freq = {}
    trackers = {
        "first_creature": lambda e: "Creature" in e.types,
        "first_ramp_tagged": lambda e: bool(set(e.tags) & set(RAMP_TAGS)),
        "first_draw_tagged": lambda e: "draw" in e.tags or "card-advantage" in e.tags,
        "first_removal_tagged": lambda e: "removal" in e.tags or "spot-removal" in e.tags,
        "first_boardwipe_tagged": lambda e: "boardwipe" in e.tags,
        "first_tutor_tagged": lambda e: "tutor" in e.tags,
    }
    for label, pred in trackers.items():
        cnt: Counter = Counter()
        for tr in trials:
            for e in tr.casts:
                if pred(e):
                    cnt[e.name] += 1
                    break
        first_card_freq[label] = cnt.most_common(5)

    # ---- E: lands & mana ---------------------------------------------------
    first_miss = []
    for tr in trials:
        first_miss.append(tr.missed_drop_turns[0] if tr.missed_drop_turns else None)
    no_miss = {}
    for t in (4, 5, 7):
        k = sum(1 for tr in trials
                if all(m > t for m in tr.missed_drop_turns))
        no_miss[f"through_t{t}"] = _rate(k, n)
    fetch_uses = [lp for tr in trials for lp in tr.land_plays if lp[4]]
    fetch_colors = Counter(lp[3] for lp in fetch_uses if lp[3])
    final_lands_in_hand = []
    flood = 0
    for tr in trials:
        c = sum(1 for card in tr.final_hand if card.is_land)
        final_lands_in_hand.append(c)
        if c >= 3:
            flood += 1
    lands_mana = {
        "first_missed_drop": _dist(first_miss),
        "no_missed_drop": no_miss,
        "tapped_plays_per_game": stats.mean(
            [sum(1 for lp in tr.land_plays if lp[2]) for tr in trials]),
        "tapped_shortfall_events_per_game": stats.mean(
            [tr.tapped_shortfalls for tr in trials]),
        "fetch_uses_per_game": len(fetch_uses) / n if n else 0.0,
        "fetch_colors": dict(fetch_colors),
        "flood_pct_games_3plus_lands_in_final_hand": _rate(flood, n),
        "avg_lands_in_final_hand": stats.mean(final_lands_in_hand),
        "priced_out_turns_per_game": stats.mean([len(tr.priced_out) for tr in trials]),
        "discards_to_cleanup_per_game": stats.mean([len(tr.discards) for tr in trials]),
    }

    # ---- H: end snapshot -----------------------------------------------------
    finals = [tr.snapshots[-1] for tr in trials if tr.snapshots]
    end = {
        "final_turn": turns,
        "power": {"mean": stats.mean([s.power for s in finals]),
                  "median": stats.median([s.power for s in finals])},
        "creatures_mean": stats.mean([s.creatures for s in finals]),
        "perms_by_type_mean": {
            k: stats.mean([s.perms_by_type.get(k, 0) for s in finals])
            for k in sorted({k for s in finals for k in s.perms_by_type})},
        "cards_seen_mean": stats.mean([s.cards_seen for s in finals]),
        "pct_library_seen": (100.0 * stats.mean([s.cards_seen for s in finals]) / lib_size)
        if lib_size else 0.0,
        "final_hand_by_type": _hand_comp([tr.final_hand for tr in trials]),
    }

    agg = {
        "n_trials": n,
        "mulligans": {"stages": stages,
                      "final_size_dist": {str(k): v for k, v in sorted(size_dist.items())},
                      "kept_comp": _hand_comp([tr.kept_hand for tr in trials]),
                      "thrown_comp": _hand_comp([h for tr in trials for h in tr.thrown_hands]),
                      "by_final_size": by_size,
                      "bottomed_cards": bottomed_stats},
        "curves": curves,
        "firsts": firsts,
        "commander": commander,
        "composition": composition,
        "per_card": per_card,
        "first_card_frequency": first_card_freq,
        "lands_mana": lands_mana,
        "end_snapshot": end,
    }

    # ---- G: regime extras -------------------------------------------------
    agg["regime_extras"] = _regime_extras(trials, turns)

    # ---- v4.0.2 Tier 1 models (MUE, cast rate/delay, trapped/pitch,
    # per-stage keep rates) — see models.py -------------------------------
    agg["models"] = models.tier1_models(deck, trials, turns)
    return agg


def deck_cards_by_name(deck) -> dict:
    out = {}
    for c in deck.all_cards:
        out.setdefault(c.name, c)
    return out


def _avg_mv_cast_on_turn(trials, t):
    mvs = [e.mv for tr in trials for e in tr.casts if e.turn == t]
    return stats.mean(mvs) if mvs else None


def _per_card(deck, trials, n):
    by_name = deck_cards_by_name(deck)
    agg: dict[str, dict] = {}
    for name in by_name:
        agg[name] = {"seen": 0, "cast": 0, "by3": 0, "by5": 0, "by7": 0,
                     "cast_turns": [], "stranded": 0, "stranded_never_mana": 0,
                     "stranded_mv_sum": 0.0, "discarded": 0, "bottomed": 0}
    for tr in trials:
        kept_names = {c.name for c in tr.kept_hand}
        drawn_names = {nm for nm, e in tr.seen.items() if e["drawn_turn"] > 0}
        discarded_names = {nm for nm, e in tr.seen.items() if e["discarded"]}
        cast_first: dict[str, int] = {}
        for e in tr.casts:
            if e.is_commander:
                continue
            if e.name not in cast_first or e.turn < cast_first[e.name]:
                cast_first[e.name] = e.turn
        inplay_seen = kept_names | drawn_names
        for name, a in agg.items():
            was_bottomed = name in tr.bottomed
            if was_bottomed:
                a["bottomed"] += 1
            if name in discarded_names:
                a["discarded"] += 1
            if name not in inplay_seen:
                continue
            a["seen"] += 1
            if name in cast_first:
                a["cast"] += 1
                ft = cast_first[name]
                a["cast_turns"].append(ft)
                if ft <= 3:
                    a["by3"] += 1
                if ft <= 5:
                    a["by5"] += 1
                if ft <= 7:
                    a["by7"] += 1
            elif name not in discarded_names:
                a["stranded"] += 1
                a["stranded_mv_sum"] += by_name[name].mana_value
                if name not in tr.payable_names:
                    a["stranded_never_mana"] += 1
    out = {}
    for name, a in agg.items():
        out[name] = {
            "mv": by_name[name].mana_value,
            "types": sorted(by_name[name].types),
            "seen_pct": _rate(a["seen"], n),
            "cast_pct_of_trials": _rate(a["cast"], n),
            "cast_by_t3_pct": _rate(a["by3"], n),
            "cast_by_t5_pct": _rate(a["by5"], n),
            "cast_by_t7_pct": _rate(a["by7"], n),
            "median_cast_turn": stats.median(a["cast_turns"]) if a["cast_turns"] else None,
            "stranded_pct_of_trials": _rate(a["stranded"], n),
            "stranded_never_had_mana": a["stranded_never_mana"],
            "stranded_sim_ended": a["stranded"] - a["stranded_never_mana"],
            "discarded_games": a["discarded"],
            "bottomed_games": a["bottomed"],
        }
    return out


def _regime_extras(trials, turns):
    extras: dict[str, dict] = {}
    # D1
    removed = [tr for tr in trials if tr.removal_turn is not None]
    if removed:
        delays = []
        recast = 0
        for tr in removed:
            later = [t for t in tr.commander_cast_turns if t >= tr.removal_turn]
            if later:
                recast += 1
                delays.append(later[0] - tr.removal_turn)
        extras["D1"] = {
            "trials_with_removal": len(removed),
            "recast_rate": _rate(recast, len(removed)),
            "recast_delay_turns": _dist(delays + [None] * (len(removed) - len(delays))),
        }
    # D2
    wiped = [tr for tr in trials if tr.wipe is not None]
    if wiped:
        latencies = []
        spell_lat = []
        rebuild_mana = []
        rebuild_casts = []
        for tr in wiped:
            wt, pre_power, pre_spells = tr.wipe
            lat = None
            for s in tr.snapshots:
                if s.turn >= wt and s.power >= pre_power:
                    lat = s.turn - wt + 1
                    break
            latencies.append(lat)
            sl = None
            for s in tr.snapshots:
                if s.turn >= wt and s.spells_cast >= pre_spells:
                    sl = s.turn - wt + 1
                    break
            spell_lat.append(sl)
            end_turn = (wt + lat - 1) if lat else turns
            seg = [s for s in tr.snapshots if wt <= s.turn <= end_turn]
            rebuild_mana.append(sum(s.mana_spent for s in seg))
            rebuild_casts.append(sum(s.spells_cast for s in seg))
        extras["D2"] = {
            "wipe_turn": WIPE_TURN,
            "rebuild_latency_turns": _dist(latencies),
            "spells_recovery_latency_turns": _dist(spell_lat),
            "mana_spent_during_rebuild_mean": stats.mean(rebuild_mana),
            "cards_cast_during_rebuild_mean": stats.mean(rebuild_casts),
        }
    return extras


# ---------------------------------------------------------------- deltas

def regime_deltas(base_trials, other_trials, turns: int, seed: int) -> dict:
    """Paired (common-seed) plain differences vs D0, with percentile
    bootstrap CIs on the per-trial differences. No composite scores."""
    rng = random.Random(seed)
    m = min(len(base_trials), len(other_trials))
    out: dict[str, dict] = {}
    for metric in ("power", "spells_cast"):
        per_turn = {}
        for t in range(1, turns + 1):
            diffs = []
            for i in range(m):
                a = base_trials[i].snapshots[t - 1]
                b = other_trials[i].snapshots[t - 1]
                diffs.append(getattr(b, metric) - getattr(a, metric))
            lo, hi = stats.bootstrap_mean_ci(diffs, rng, b=300)
            per_turn[str(t)] = {"mean_diff": stats.mean(diffs), "ci95": [lo, hi]}
        out[metric] = per_turn
    cap = turns + 1
    cdiffs = []
    for i in range(m):
        a = base_trials[i].commander_cast_turns[0] if base_trials[i].commander_cast_turns else cap
        b = other_trials[i].commander_cast_turns[0] if other_trials[i].commander_cast_turns else cap
        cdiffs.append(b - a)
    lo, hi = stats.bootstrap_mean_ci(cdiffs, rng, b=300)
    out["commander_first_cast_turn_diff"] = {
        "mean_diff": stats.mean(cdiffs), "ci95": [lo, hi],
        "note": f"never-cast counted as turn {cap} in both regimes"}
    td = [o.commander_tax_paid - b.commander_tax_paid
          for b, o in zip(base_trials, other_trials)]
    out["commander_tax_paid_diff"] = {"mean_diff": stats.mean(td)}
    # v4.0.2 paired significance tests (T1-19 Wilcoxon signed-rank on
    # the same capped first-cast differences; T1-20 McNemar on the
    # cast-at-all gate). Definitions verified online 2026-10-31 — see
    # stats.py and BUILD_REPORT.
    out["commander_first_cast_wilcoxon"] = stats.wilcoxon_signed_rank(cdiffs)
    b_only = sum(1 for i in range(m)
                 if base_trials[i].commander_cast_turns
                 and not other_trials[i].commander_cast_turns)
    c_only = sum(1 for i in range(m)
                 if other_trials[i].commander_cast_turns
                 and not base_trials[i].commander_cast_turns)
    mc = stats.mcnemar(b_only, c_only)
    mc["gate"] = ("commander cast at all: b = cast in base only, "
                  "c = cast in comparison regime only")
    out["commander_cast_mcnemar"] = mc
    return out
