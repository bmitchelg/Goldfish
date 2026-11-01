"""Markdown report rendering. Every number here is a count, a share, or
a distribution of counted events — no composite scores.

v4.1 (spec item 6): every figure carries its full label from the
labels.py registry — what was counted, its unit, its sample, and its
kind (measured / formula / rule of thumb)."""

from __future__ import annotations

from .labels import caption as _label_caption


def _cap(key: str, n: int | None = None) -> str:
    return f"> {_label_caption(key, n)}"


def _pct(x) -> str:
    return f"{x:.1f}%" if isinstance(x, (int, float)) else "—"


def _num(x, nd=2) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _dist_str(d: dict) -> str:
    if not d or d.get("n_events", 0) == 0:
        return f"never (0/{'?'}) — 0.0% of trials"
    ci = d.get("median_ci95")
    ci_s = f" (95% CI {ci[0]:.0f}–{ci[1]:.0f})" if ci else ""
    return (f"median turn {_num(d.get('median'), 1)}{ci_s}, "
            f"mean {_num(d.get('mean'))}, in {_pct(d.get('pct_ever'))} of trials")


DISCLOSURES = """\
## How to read this report (method & disclosures)

- **Skeleton simulation.** This tool models lands, mana costs, and the
  play policy exactly, but — by design — a cast spell's *effects* are
  never simulated: a cast ramp spell adds no lands, a cast draw spell
  draws no cards, a tutor fetches nothing. Scryfall tags are used for
  COUNTING ONLY (e.g. "ramp-tagged cards cast per game" is a fact).
  Consequence: **late-game card-flow numbers (cards seen, spells in
  later turns) are conservative underestimates** for decks with real
  draw and ramp.
- **Play policy** (fixed, per turn): play a land; cast the commander if
  payable from the untapped sources counted at that moment (tax
  included; the decision is never reopened later in the turn); then cast
  the maximum number of cards the remaining mana can pay for, colors
  respected. When several max-count plans tie, the plan spending the
  most mana is chosen.
- **Mulligans:** the mulligan setup (two free mulligans plus the
  keep-rule defaults) is a **deliberate house rule of Brandon's**,
  chosen knowingly over the strict rule — a socially acceptable
  practice for mulligans. (The Comprehensive Rules grant exactly one
  free mulligan, in multiplayer games only, CR 103.5c.) Beyond the
  free mulligans the London procedure is exact (CR 103.5): every
  redraw is a full 7 cards, then one card is bottomed per mulligan
  taken beyond the free ones. Bottoming policy: highest mana value
  first, lands last, ties by name.
- **Multiplayer-pod assumption:** the simulated player draws a card on
  turn 1 (true for multiplayer games, CR 103.8c; two-player games skip
  the first player's first draw).
- **Cleanup discard (CR 514.1)** is modeled: at end of turn, hands over
  7 discard down to 7 (highest mana value first; never a land while a
  nonland can be discarded; ties by name). Discard policy
  Brandon-approved (2026-10-31).
- **Commander tax** counts previous casts from the command zone only
  (CR 903.8); the commander is only ever cast from the zone here.
- **Mana sources:** non-land permanents with a Scryfall produced-mana
  field are usable from the turn AFTER they are cast (amount = 1, the
  types listed; undefined type = 1 colorless). That next-turn wait is
  a **deliberate conservative design choice, not a rules claim**: some
  artifacts similar to Sol Ring enter tapped, and since the parser
  reads no oracle text it cannot tell which ones, so we play safe vs
  sorry — by the actual rules most mana rocks are usable the turn they
  are cast, so this understates same-turn mana slightly. Sol Ring is
  the single exception because its data is certain: 2 colorless, does
  not enter tapped, usable the turn it is cast. Lands are usable
  the turn played unless they enter tapped; "enters tapped unless..."
  lands always enter tapped (condition ignored, by design). Evolving
  Wilds-family fetches are modeled (the family's exact shared text is
  the one agreed exception): the fetched basic enters tapped.
- **Not modeled:** phyrexian mana's life-payment option (phyrexian pips
  are color pips), snow mana (treated as generic), X spells are priced
  at X = 0, creatures with */variable power count as 0 power, split and
  modal DFC cards use the front face's cost and type line.
- **Stress regimes** are plain differences, never a blended score:
  D1 = commander removed once (start of the turn after its first cast);
  D2 = board wipe at the start of turn 6 destroying all non-commander
  creatures; D3 = commander tax starts at +2 (as if already cast once
  from the zone) and grows normally.
"""


def render(payload: dict) -> str:
    d = payload["deck"]
    lines = []
    lines.append(f"# Goldfish Report v4 — {d['name']}")
    lines.append("")
    lines.append(f"- Commander: **{d['commander']}**")
    lines.append(f"- Library: {d['library_size']} cards · Lands: {d['land_count']} · "
                 f"Trials per regime: {payload['trials']} · Turns simulated: {payload['turns']} · "
                 f"Seed: {payload['seed']}")
    lines.append(f"- Keep rule: {payload['keep_rule']}")
    if payload.get("data_source"):
        lines.append(f"- {payload['data_source']}")
    lines.append("")
    lines.append(DISCLOSURES)

    # Manabase
    mb = payload["manabase"]
    lines.append("## Manabase audit (Karsten)")
    lines.append("")
    lines.append(_cap("karsten_lands"))
    lines.append("")
    lines.append(f"- Land check: your deck has {mb['lands_actual']} lands. "
                 f"Karsten's formula calculates that decks with these "
                 f"spell costs want {mb['karsten_recommended_lands']} "
                 f"lands (avg spell MV {mb['avg_mv_spells']}, "
                 f"cheap ramp <=2 MV: {mb['cheap_ramp_le2']}, cantrips <=2 MV: {mb['cantrips_le2']}). "
                 f"A formula recommendation, not a measurement from the games.")
    lines.append("")
    lines.append("| Color | Sources | Karsten req. | Deficit | Peak demand |")
    lines.append("|---|---|---|---|---|")
    for col, p in mb["pips"].items():
        req = p["required"] if p["required"] is not None else "no demand"
        lines.append(f"| {col} | {p['sources']} | {req} | "
                     f"{p['deficit'] if p['deficit'] is not None else '—'} | "
                     f"{p['peak_demand_card'] or '—'} |")
    lines.append("")
    lines.append(_cap("karsten_deficit"))
    lines.append("")

    agg = payload["regimes"]["D0"]
    n0 = agg["n_trials"]
    lines.append(f"## Mulligans (D0, {n0} trials)")
    lines.append("")
    lines.append(_cap("mulligan_stage", n0))
    lines.append("")
    lines.append("| Stage | Reached | Kept by rule | Ended here |")
    lines.append("|---|---|---|---|")
    for label, s in agg["mulligans"]["stages"].items():
        lines.append(f"| {label} | {s['reached']} | {s['rule_kept']} | {s['ended_here']} |")
    lines.append("")
    lines.append("Final hand sizes: " + ", ".join(
        f"{k} cards: {v}" for k, v in agg["mulligans"]["final_size_dist"].items()))
    lines.append("")
    lines.append("Results by final hand size (measured — a size with no "
                 "trials is reported as n=0, never estimated):")
    lines.append("")
    lines.append(_cap("by_final_size_spells", n0))
    lines.append("")
    lines.append("| Hand size | n | Spells by T7 (mean) | Commander first cast | Mana util. T1–7 |")
    lines.append("|---|---|---|---|---|")
    for size, s in agg["mulligans"]["by_final_size"].items():
        if s.get("n", 0) == 0:
            lines.append(f"| {size} | 0 | — | — | — |")
        else:
            lines.append(f"| {size} | {s['n']} | {_num(s['mean_spells_by_t7'])} | "
                         f"{_dist_str(s['commander_first_cast'])} | "
                         f"{_num(s['mean_mana_utilization_t1_7'], 3)} |")
    lines.append("")

    # Firsts
    f = agg["firsts"]
    lines.append("## Firsts (D0)")
    lines.append("")
    lines.append(_cap("first_event", n0))
    lines.append("")
    lines.append(f"- First creature cast: {_dist_str(f['first_creature'])}"
                 f" (avg MV {_num(f.get('first_creature_avg_mv'))}, "
                 f"avg power {_num(f.get('first_creature_avg_power'))})")
    for tname, dd in f["first_by_type"].items():
        lines.append(f"- First {tname.lower()}: {_dist_str(dd)}")
    lines.append(f"- Color-complete (all commander colors from lands): {_dist_str(f['color_complete_turn'])}")
    lines.append(f"- Color-complete ignoring fetched lands: {_dist_str(f['color_complete_turn_ignoring_fetches'])}")
    lines.append(f"- Hand first empty: {_dist_str(f['first_empty_hand'])}")
    for th, dd in f["power_thresholds"].items():
        lines.append(f"- Total power first >= {th}: {_dist_str(dd)}")
    for th, dd in f["cumulative_mana_spent_thresholds"].items():
        lines.append(f"- Cumulative mana spent first >= {th}: {_dist_str(dd)}")
    lines.append("")
    lines.append("First cast carrying each Scryfall tag (median turn / % of trials):")
    lines.append("")
    for tag, dd in f["first_by_tag"].items():
        lines.append(f"- {tag}: {_dist_str(dd)}")
    lines.append("")

    # Commander
    c = agg["commander"]
    lines.append("## Commander (D0)")
    lines.append("")
    lines.append(_cap("first_event", n0))
    lines.append("")
    lines.append(f"- First cast: {_dist_str(c['first_cast'])}")
    lines.append(f"- Recasts per game (mean): {_num(c['recasts_mean'])} · "
                 f"share of turns in play: {_pct(100 * c['pct_turns_in_play'])} · "
                 f"tax paid per game (mean): {_num(c['tax_paid_mean'])} · "
                 f"share of all mana spent: {_pct(c['mana_share_pct'])}")
    lines.append("- Cast by turn: " + ", ".join(
        f"T{t}: {_pct(r['pct'])}" for t, r in c["pct_cast_by_turn"].items()))
    lines.append("")

    # Curves
    lines.append("## Per-turn curves (D0, means unless noted)")
    lines.append("")
    lines.append(_cap("curve_spells", n0))
    lines.append(_cap("curve_mana_utilization", n0))
    lines.append(_cap("curve_power", n0))
    lines.append("")
    lines.append("| Turn | Spells | Cum. spells | Mana avail. | Mana spent | Util. | "
                 "Unspent | Creatures | Power (med) | Hand | Lands | Avg MV cast |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for t, cv in agg["curves"].items():
        lines.append(
            f"| {t} | {_num(cv['spells_mean'])} | {_num(cv['cum_spells_mean'])} | "
            f"{_num(cv['mana_available_mean'])} | {_num(cv['mana_spent_mean'])} | "
            f"{_num(cv['mana_utilization_mean'], 3)} | {_num(cv['unspent_mean'])} | "
            f"{_num(cv['creatures_mean'])} | {_num(cv['power_mean'])} ({_num(cv['power_median'], 1)}) | "
            f"{_num(cv['hand_mean'])} | {_num(cv['lands_mean'])} | {_num(cv['avg_mv_cast'])} |")
    lines.append("")

    # Composition
    comp = agg["composition"]
    lines.append("## What got cast (D0)")
    lines.append("")
    lines.append(f"- Spells cast per game: {_num(comp['casts_per_game'])} spells — "
                 f"in an average test game, that's how many spells left "
                 f"your hand and were cast.")
    lines.append(_cap("casts_per_game", n0))
    lines.append(f"- Fresh-cast rate (cast the turn drawn): {_pct(comp['fresh_cast_rate']['pct'])}")
    lines.append(_cap("casts_by_tag", n0))
    lines.append("- By type: " + ", ".join(
        f"{k} {_pct(v['pct'])}" for k, v in comp["by_type"].items()))
    lines.append("- By pip colors: " + ", ".join(
        f"{k} {_pct(v['pct'])}" for k, v in comp["by_pip_colors"].items()))
    lines.append("- Mana spent by color: " + ", ".join(
        f"{k} {_pct(v['pct'])}" for k, v in comp["mana_spent_by_color"].items()))
    lines.append("- Realized curve (share of casts by MV, 7 = 7+): " + ", ".join(
        f"MV{k}: {_pct(v['pct'])}" for k, v in comp["by_mv_bucket"].items()))
    lines.append("- Casts per game by tag: " + ", ".join(
        f"{k} {_num(v)}" for k, v in comp["casts_by_tag_per_game"].items()))
    lines.append("")

    # Lands & mana
    lm = agg["lands_mana"]
    lines.append("## Lands & mana facts (D0)")
    lines.append("")
    lines.append(_cap("tapped_plays_per_game", n0))
    lines.append(_cap("fetch_uses_per_game", n0))
    lines.append("")
    lines.append(f"- First missed land drop: {_dist_str(lm['first_missed_drop'])}")
    lines.append("- No missed drop: " + ", ".join(
        f"{k}: {_pct(v['pct'])}" for k, v in lm["no_missed_drop"].items()))
    lines.append(f"- Tapped land plays per game: {_num(lm['tapped_plays_per_game'])} · "
                 f"tapped-land shortfall events per game: {_num(lm['tapped_shortfall_events_per_game'])}")
    lines.append(f"- Fetch uses per game: {_num(lm['fetch_uses_per_game'])}"
                 + ((" · colors fetched: " + ", ".join(
                     f"{k}: {v}" for k, v in lm["fetch_colors"].items()))
                    if lm["fetch_colors"] else ""))
    lines.append(f"- Flood: {_pct(lm['flood_pct_games_3plus_lands_in_final_hand']['pct'])} of games "
                 f"end with 3+ lands in hand (avg {_num(lm['avg_lands_in_final_hand'])})")
    lines.append(f"- Priced-out turns per game (cards in hand, nothing castable): "
                 f"{_num(lm['priced_out_turns_per_game'])}")
    lines.append(f"- Discards to cleanup (hand > 7 at end of turn) per game: "
                 f"{_num(lm['discards_to_cleanup_per_game'])}")
    lines.append("")

    # Per-card
    lines.append("## Card-by-card (D0) — worst stranded first")
    lines.append("")
    lines.append("Stranded = seen in hand, never cast, not discarded to "
                 "cleanup. Reasons are counted, not guessed: 'never had "
                 "the mana' = at no step-9 was the card individually "
                 "payable while it sat in hand; otherwise the simulation "
                 "ended first / the max-cast plan never selected it.")
    lines.append("")
    lines.append(_cap("per_card_stranded", n0))
    lines.append("")
    lines.append("| Card | MV | Seen % | Cast % | By T3 | By T5 | By T7 | Med. cast turn | Stranded % | Never-mana | Sim-ended |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    rows = sorted(agg["per_card"].items(),
                  key=lambda kv: -kv[1]["stranded_pct_of_trials"]["pct"])
    for name_, s in rows[:30]:
        lines.append(
            f"| {name_} | {s['mv']} | {_pct(s['seen_pct']['pct'])} | "
            f"{_pct(s['cast_pct_of_trials']['pct'])} | {_pct(s['cast_by_t3_pct']['pct'])} | "
            f"{_pct(s['cast_by_t5_pct']['pct'])} | {_pct(s['cast_by_t7_pct']['pct'])} | "
            f"{_num(s['median_cast_turn'], 1)} | {_pct(s['stranded_pct_of_trials']['pct'])} | "
            f"{s['stranded_never_had_mana']} | {s['stranded_sim_ended']} |")
    lines.append("")
    lines.append("(Full per-card table for every card is in the JSON output.)")
    lines.append("")

    # Regimes
    if len(payload["regimes"]) > 1:
        lines.append("## Stress regimes — plain differences vs D0 (paired by seed)")
        lines.append("")
        lines.append(_cap("regime_delta_turns", payload["trials"]))
        lines.append("")
        for regime, r_agg in payload["regimes"].items():
            if regime == "D0":
                continue
            lines.append(f"### {regime}")
            ex = r_agg.get("regime_extras", {})
            if regime in ex and regime == "D1":
                e = ex["D1"]
                lines.append(f"- Commander removed in {e['trials_with_removal']} trials; "
                             f"recast in {_pct(e['recast_rate']['pct'])} of them; "
                             f"recast delay: {_dist_str(e['recast_delay_turns'])}")
            if regime in ex and regime == "D2":
                e = ex["D2"]
                lines.append(f"- Wipe at start of turn {e['wipe_turn']}: rebuild latency "
                             f"(turns until total power returns to pre-wipe level): "
                             f"{_dist_str(e['rebuild_latency_turns'])}")
                lines.append(f"- Spells-per-turn recovery latency: "
                             f"{_dist_str(e['spells_recovery_latency_turns'])} · "
                             f"mana spent during rebuild (mean): {_num(e['mana_spent_during_rebuild_mean'])} · "
                             f"cards cast during rebuild (mean): {_num(e['cards_cast_during_rebuild_mean'])}")
            d = payload.get("deltas", {}).get(regime)
            if d:
                lines.append(f"- Commander first-cast turn difference: mean "
                             f"{_num(d['commander_first_cast_turn_diff']['mean_diff'])} turns "
                             f"(95% CI {_num(d['commander_first_cast_turn_diff']['ci95'][0])} to "
                             f"{_num(d['commander_first_cast_turn_diff']['ci95'][1])}; "
                             f"{d['commander_first_cast_turn_diff']['note']})")
                lines.append(f"- Extra commander tax paid per game (mean): "
                             f"{_num(d['commander_tax_paid_diff']['mean_diff'])}")
                pw = d["power"]
                lines.append("- Total power difference by turn (mean, 95% CI): " + ", ".join(
                    f"T{t}: {_num(v['mean_diff'])} [{_num(v['ci95'][0])}, {_num(v['ci95'][1])}]"
                    for t, v in pw.items()))
            lines.append("")

    # End snapshot
    e = agg["end_snapshot"]
    lines.append("## End-of-game snapshot (D0, final turn)")
    lines.append("")
    lines.append(_cap("end_power", n0))
    lines.append("")
    lines.append(f"- Total board power: mean {_num(e['power']['mean'])}, median "
                 f"{_num(e['power']['median'], 1)} — this is board power, NOT a win prediction.")
    lines.append(f"- Creatures (mean): {_num(e['creatures_mean'])} · permanents by type (mean): "
                 + ", ".join(f"{k} {_num(v)}" for k, v in e["perms_by_type_mean"].items()))
    lines.append(f"- Cards seen per game (mean): {_num(e['cards_seen_mean'])} "
                 f"({_pct(e['pct_library_seen'])} of the library)")
    lines.append("")
    return "\n".join(lines)
