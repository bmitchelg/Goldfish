"""Batch dashboard (v4.0.2): `goldfish4-dashboard <batch.json> [...]`.

HARD RULE (the dashboard contract): every number displayed is computed
FROM the per-game records in the batch JSON, in this process, using
the same measurement/model code as `goldfish4 analyze`. The batch file
stores no aggregates; if a future file ever carried any, this script
would ignore them.

Structure is the same for every batch: a labeled header (deck,
commander, tool version, run date, data date, games per
variant/regime counted from the records, seed, keep rule), then a
plain-language summary layer, then the COMPLETE data underneath —
every measurement section and every Tier 1 model output. One
self-contained HTML file, no external assets, phone-readable.

One deliberate exclusion, disclosed in the page itself: Tier 1 models
T1-05 (M_comp) and T1-17 (its P20/P80) are NOT shown because their
formula could not be verified against an authoritative source
(Brandon's math gate) — see BUILD_REPORT.
"""

from __future__ import annotations

import argparse
import html
import os
import sys
from collections import Counter, defaultdict

from . import __version__, measurements, models, stats
from .batch import card_from_index, load_batch
from .labels import caption as _label_caption
from .parser import Deck
from .simulator import CastEvent, Snapshot, TrialResult


# ------------------------------------------------------- reconstruction

def _decode_unit(code: str) -> frozenset:
    if code == "C":
        return frozenset()
    return frozenset(code)


def record_to_trial(rec: dict, by_name: dict) -> TrialResult:
    snaps = [Snapshot(
        turn=d["turn"], spells_cast=d["spells_cast"],
        mana_available=d["mana_available"], mana_spent=d["mana_spent"],
        lands=d["lands"], creatures=d["creatures"], power=d["power"],
        toughness=d["toughness"], perms_by_type=dict(d["perms_by_type"]),
        sources_by_color=dict(d["sources_by_color"]),
        hand_size=d["hand_size"], cards_seen=d["cards_seen"],
        commander_in_play=d["commander_in_play"],
        land_colors=frozenset(d["land_colors"]),
        land_colors_nofetch=frozenset(d["land_colors_nofetch"]))
        for d in rec["turns"]]
    casts = [CastEvent(
        turn=e["turn"], name=e["name"], type_key=e["type_key"],
        types=tuple(e["types"]), mv=e["mv"], colors=tuple(e["colors"]),
        tags=tuple(e["tags"]), mana_spent=e["mana_spent"],
        spent_colors=tuple(e["spent_colors"]),
        is_commander=e["is_commander"], drawn_turn=e["drawn_turn"],
        power=e["power"], toughness=e["toughness"]) for e in rec["casts"]]
    return TrialResult(
        regime=rec["regime"],
        stages=[(label, kept) for label, kept in rec["mulligan"]["stages"]],
        final_hand_size=rec["mulligan"]["final_hand_size"],
        bottomed=list(rec["mulligan"]["bottomed"]),
        kept_hand=[by_name[n] for n in rec["mulligan"]["kept_hand"]],
        thrown_hands=[[by_name[n] for n in h]
                      for h in rec["mulligan"]["thrown_hands"]],
        casts=casts,
        land_plays=[(d["turn"], d["name"], d["tapped"],
                     d["fetched_color"], d["is_fetch"])
                    for d in rec["land_plays"]],
        snapshots=snaps,
        commander_cast_turns=list(rec["commander"]["cast_turns"]),
        commander_tax_paid=rec["commander"]["tax_paid"],
        removal_turn=rec["commander"]["removal_turn"],
        wipe=(None if rec["wipe"] is None else
              (rec["wipe"]["turn"], rec["wipe"]["pre_power"],
               rec["wipe"]["pre_spells"])),
        discards=[(d["turn"], d["name"]) for d in rec["discards"]],
        payable_names=set(rec["payable_names"]),
        turn_units=[[_decode_unit(u) for u in turn]
                    for turn in rec["castability_units"]],
        seen={d["name"]: {"drawn_turn": d["drawn_turn"],
                          "bottomed": d["bottomed"],
                          "discarded": d["discarded"]}
              for d in rec["seen"]},
        final_hand=[by_name[n] for n in rec["final_hand"]],
        priced_out=[(d["turn"], tuple(d["mvs"]))
                    for d in rec["priced_out"]],
        missed_drop_turns=list(rec["missed_drop_turns"]),
        tapped_shortfalls=rec["tapped_shortfalls"])


class BatchView:
    """One loaded batch file, fully recomputed from its records."""

    def __init__(self, path: str):
        self.path = path
        data, status = load_batch(path)
        self.meta = data["meta"]
        self.status = status
        self.turns = self.meta["turns"]
        cards = {n: card_from_index(d)
                 for n, d in self.meta["cards"].items()}
        self.decks: dict[str, Deck] = {}
        for v in self.meta["variants"]:
            library = []
            for e in v["decklist"]:
                library += [cards[e["name"]]] * e["count"]
            self.decks[v["id"]] = Deck(
                commanders=[cards[n]
                            for n in self.meta["deck"]["commanders"]],
                library=library, name=self.meta["deck"]["name"])
        grouped: dict = defaultdict(list)
        self.counts: Counter = Counter()
        for rec in data["games"]:
            grouped[(rec["variant"], rec["regime"])].append(rec)
            self.counts[(rec["variant"], rec["regime"])] += 1
        self.trials: dict = {}
        for (vid, regime), recs in grouped.items():
            recs.sort(key=lambda r: r["game_index"])
            deck = self.decks[vid]
            by_name = {c.name: c for c in deck.all_cards}
            self.trials[(vid, regime)] = [
                record_to_trial(r, by_name) for r in recs]
        self._agg: dict = {}
        self._manabase: dict = {}
        self._hyper: dict = {}

    def variant_ids(self) -> list:
        return [v["id"] for v in self.meta["variants"]]

    def regimes(self) -> list:
        return list(self.meta["regimes"])

    def aggregate(self, variant: str, regime: str) -> dict:
        key = (variant, regime)
        if key not in self._agg:
            self._agg[key] = measurements.aggregate(
                self.decks[variant], self.trials[key], self.turns)
        return self._agg[key]

    def manabase(self, variant: str) -> dict:
        if variant not in self._manabase:
            self._manabase[variant] = measurements.manabase_audit(
                self.decks[variant])
        return self._manabase[variant]

    def hypergeometric(self, variant: str) -> dict:
        if variant not in self._hyper:
            self._hyper[variant] = models.hypergeometric_table(
                self.decks[variant])
        return self._hyper[variant]

    def deltas(self, variant: str, regime: str) -> dict:
        base_regime = self.regimes()[0]
        return measurements.regime_deltas(
            self.trials[(variant, base_regime)],
            self.trials[(variant, regime)],
            self.turns, self.meta["seed_base"])

    def d0(self, variant: str = "base") -> dict:
        return self.aggregate(variant, self.regimes()[0])


# ------------------------------------------------------- paired tests

def _first_cast_capped(tr, turns: int) -> int:
    return tr.commander_cast_turns[0] if tr.commander_cast_turns else turns + 1


def _clean_through_5(tr) -> bool:
    return all(t > 5 for t in tr.missed_drop_turns)


def paired_variant_tests(base_trials, other_trials, turns: int) -> dict:
    """Paired comparisons between two variants at the same regime
    (common seeds make the pairing valid): Wilcoxon signed-rank
    (T1-19) on per-game differences, McNemar (T1-20) on binary gates."""
    m = min(len(base_trials), len(other_trials))
    b, o = base_trials[:m], other_trials[:m]
    spells_diff = [len(x.casts) - len(y.casts) for x, y in zip(o, b)]
    cast_diff = [_first_cast_capped(x, turns) - _first_cast_capped(y, turns)
                 for x, y in zip(o, b)]

    def gate(fn):
        # mcnemar(b, c) with the base variant as the first group:
        # b = gate passed in base only, c = passed in variant only.
        # (In zip(o, b), x is the variant trial, y the base trial.)
        bb = sum(1 for x, y in zip(o, b) if fn(y) and not fn(x))
        cc = sum(1 for x, y in zip(o, b) if fn(x) and not fn(y))
        return stats.mcnemar(bb, cc)

    return {
        "n_pairs": m,
        "spells_cast_wilcoxon": stats.wilcoxon_signed_rank(spells_diff),
        "spells_cast_mean_diff": stats.mean(spells_diff) if spells_diff else 0.0,
        "commander_first_cast_wilcoxon": stats.wilcoxon_signed_rank(cast_diff),
        "commander_cast_mcnemar": gate(lambda tr: bool(tr.commander_cast_turns)),
        "no_missed_drop_through_5_mcnemar": gate(_clean_through_5),
    }


# ------------------------------------------------------------- rendering

CSS = """
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
margin:0 auto;max-width:62rem;padding:1rem;line-height:1.45;color:#1a1a1a}
h1{font-size:1.5rem} h2{font-size:1.25rem;margin-top:2rem;border-bottom:2px solid #444;padding-bottom:.2rem}
h3{font-size:1.05rem;margin-top:1.4rem}
table{border-collapse:collapse;margin:.5rem 0 1rem;font-size:.86rem;max-width:100%}
th,td{border:1px solid #bbb;padding:.28rem .5rem;text-align:left;vertical-align:top}
th{background:#eee}
details{margin:.6rem 0} summary{cursor:pointer;font-weight:600;padding:.3rem 0}
.scroll{overflow-x:auto}
.warn{background:#ffe9a8;border:1px solid #c90;padding:.6rem .8rem;border-radius:.4rem}
.lbl{color:#555;margin:.2rem 0 .4rem}
.plain{background:#eef4ff;border:1px solid #9db8e8;border-radius:.4rem;padding:.4rem 1rem}
.dump th{background:#f7f7f7;font-weight:600;white-space:nowrap}
small{color:#555}
"""


def esc(x) -> str:
    return html.escape(str(x))


def fnum(x, nd=2) -> str:
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, int):
        return f"{x:,}"
    return f"{x:.{nd}f}"


def fpct(x, nd=1) -> str:
    return "—" if x is None else f"{x:.{nd}f}%"


def frate(rate) -> str:
    """Format a {k, n, pct, wilson95} rate dict."""
    if not rate:
        return "—"
    lo, hi = rate["wilson95"]
    return (f"{rate['pct']:.1f}% ({rate['k']}/{rate['n']}; "
            f"95% CI {lo:.1f}–{hi:.1f})")


def table(cols, rows) -> str:
    head = "".join(f"<th>{esc(c)}</th>" for c in cols)
    body = "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
        for row in rows)
    return (f"<div class='scroll'><table><thead><tr>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table></div>")


def dump(obj, depth=0) -> str:
    """Render any nested measurement structure completely — the
    guarantee that nothing measured is omitted from the page."""
    if depth > 7:
        return esc(obj)
    if isinstance(obj, dict):
        if not obj:
            return "<em>none</em>"
        rows = "".join(
            f"<tr><th>{esc(k)}</th><td>{dump(v, depth + 1)}</td></tr>"
            for k, v in obj.items())
        return f"<table class='dump'>{rows}</table>"
    if isinstance(obj, (list, tuple)):
        if not obj:
            return "<em>none</em>"
        if (all(isinstance(x, dict) for x in obj)
                and all(set(x) == set(obj[0]) for x in obj)):
            cols = list(obj[0].keys())
            head = "".join(f"<th>{esc(c)}</th>" for c in cols)
            body = "".join(
                "<tr>" + "".join(f"<td>{dump(x[c], depth + 1)}</td>"
                                 for c in cols) + "</tr>" for x in obj)
            return (f"<div class='scroll'><table><thead><tr>{head}</tr>"
                    f"</thead><tbody>{body}</tbody></table></div>")
        if all(isinstance(x, (int, float, str)) or x is None for x in obj):
            return esc(", ".join(fnum(x) if isinstance(x, (int, float))
                                 else str(x) for x in obj))
        return ("<ul>" + "".join(f"<li>{dump(x, depth + 1)}</li>"
                                 for x in obj) + "</ul>")
    if isinstance(obj, float):
        return f"{obj:.4g}"
    return esc(obj)


def _lbl(key: str, n=None) -> str:
    """Full measurement label for a metric (v4.1 label registry):
    what was counted — unit; sample; kind; conditions."""
    return (f"<p class='lbl'><small>{esc(_label_caption(key, n))}"
            f"</small></p>")


def details(title: str, inner: str, open_: bool = False) -> str:
    return (f"<details{' open' if open_ else ''}><summary>{esc(title)}"
            f"</summary>{inner}</details>")


# ------------------------------------------------------------- header

def render_header(view: BatchView, idx: int) -> str:
    meta = view.meta
    parts = [f"<h2>Batch {idx + 1}: {esc(meta['deck']['name'])} — "
             f"commander {esc(meta['deck']['commander'])}</h2>"]
    if not view.status["complete"]:
        parts.append(
            "<p class='warn'><strong>INCOMPLETE BATCH.</strong> This "
            "run did not finish (or the file had to be recovered). The "
            "numbers below cover only the games present in the file, "
            "counted in the table here.</p>")
    vids = view.variant_ids()
    regs = view.regimes()
    rows = []
    for vid in vids:
        rows.append([esc(vid)] + [fnum(view.counts.get((vid, r), 0))
                                   for r in regs])
    parts.append("<h3>Games in this file (counted from the records)</h3>")
    parts.append(table(["Variant"] + regs, rows))
    info = [
        ("Deck", meta["deck"]["name"]),
        ("Commander", meta["deck"]["commander"]),
        ("Tool version (batch)", meta["tool_version"]),
        ("Dashboard version", __version__),
        ("Run date", meta["created_at"]),
        ("Card data", meta["data_source"]),
        ("Data date", meta.get("data_date")),
        ("Seed base", meta["seed_base"]),
        ("Seed policy", meta["seed_policy"]),
        ("Keep rule", meta["keep_rule"]),
        ("Turns simulated", meta["turns"]),
        ("Decklist file", meta["deck"]["source_filename"]),
        ("Decklist SHA-256", meta["deck"]["decklist_sha256"][:16] + "…"),
        ("Batch file", os.path.basename(view.path)),
    ]
    parts.append(table(["Field", "Value"],
                       [[esc(k), esc(v)] for k, v in info]))
    parts.append(_lbl("keep_rule"))
    return "".join(parts)


# ------------------------------------------------------------- summary

def _mean_casts(trials) -> float:
    return stats.mean([len(tr.casts) for tr in trials]) if trials else 0.0


def render_summary(view: BatchView) -> str:
    meta = view.meta
    base_regime = view.regimes()[0]
    trials = view.trials.get(("base", base_regime), [])
    if not trials:
        return ("<h2>What this means, in plain language</h2>"
                "<div class='plain'><p>This batch file contains no "
                "completed games for the base deck yet, so there is "
                "nothing to summarize.</p></div>")
    agg = view.d0("base")
    cmd = agg["commander"]
    fc = cmd["first_cast"]
    pe = fc.get("pct_ever")
    pct_ever = pe["pct"] if isinstance(pe, dict) else (pe or 0)
    mana = view.manabase("base")
    sentences = []
    sentences.append(
        f"In {len(trials)} games of the base deck under normal "
        f"conditions ({base_regime}), {esc(meta['deck']['commander'])} "
        f"was cast in {pct_ever:.1f}% of "
        f"games, typically on turn {fnum(fc.get('median'), 0)} "
        f"(average turn {fnum(fc.get('mean'), 1)}). The deck averaged "
        f"{_mean_casts(trials):.1f} spells cast per game.")
    sentences.append(
        f"The deck runs {mana['lands_actual']} lands; Karsten's "
        f"published formula suggests about "
        f"{mana['karsten_recommended_lands']:.1f} for this mana curve "
        f"and ramp package — a gap of "
        f"{mana['karsten_recommended_lands'] - mana['lands_actual']:+.1f}.")
    mue = agg["models"]["mana_utilization_efficiency"]
    sentences.append(
        f"Across turns 1–5 the deck spent "
        f"{100 * mue['turns_1_5_vol1']['pooled_ratio']:.0f}% of the mana "
        f"it had available (unspent mana early is normal — there is "
        f"not always a spell to spend it on).")
    cast = agg["models"]["castability"]
    if cast:
        by_cr = sorted(cast.items(), key=lambda kv: kv[1]["cast_rate"]["pct"])
        worst, best = by_cr[0], by_cr[-1]
        sentences.append(
            f"Easiest card to cast on its earliest possible turn: "
            f"{esc(best[0])} ({best[1]['cast_rate']['pct']:.0f}%). "
            f"Hardest: {esc(worst[0])} "
            f"({worst[1]['cast_rate']['pct']:.0f}% on curve, average "
            f"delay {worst[1]['mean_delay_turns']:.1f} turns).")
    pc = agg["per_card"]
    stranded = sorted(((v.get("stranded_games", 0), n)
                       for n, v in pc.items()), reverse=True)
    if stranded and stranded[0][0]:
        sentences.append(
            f"The card most often left stranded in hand was "
            f"{esc(stranded[0][1])} (in {stranded[0][0]} of "
            f"{len(trials)} games).")
    vids = [v for v in view.variant_ids()
            if view.trials.get((v, base_regime))]
    if len(vids) > 1:
        casts_by_variant = {vid: view.d0(vid)["models"]["castability"]
                            for vid in vids}
        pm = models.perturbation_matrix(casts_by_variant)
        tax = pm.get("tapland_tempo_tax")
        if tax:
            sentences.append(
                f"Turning this deck's tapped lands into untapped basics "
                f"would raise the average on-curve cast rate by "
                f"{tax['cast_rate_gain_pp']:+.1f} percentage points "
                f"(the tapland tempo tax) and cut the average cast "
                f"delay by {tax['delay_reduction_turns']:.2f} turns.")
    for regime in view.regimes()[1:]:
        if not view.trials.get(("base", regime)):
            continue
        d = view.deltas("base", regime)
        diff = d["commander_first_cast_turn_diff"]["mean_diff"]
        sentences.append(
            f"Under {regime}, the commander's first cast moved "
            f"{diff:+.2f} turns on average versus {base_regime} "
            f"(paired games, same seeds).")
    body = "".join(f"<p>{s}</p>" for s in sentences)
    return (f"<h2>What this means, in plain language</h2>"
            f"<div class='plain'>{body}</div>"
            f"<p><small>Everything below is the complete measured data "
            f"behind those sentences — nothing is summarized away. "
            f"Every number is recomputed from the per-game records in "
            f"the batch file.</small></p>")


# ------------------------------------------------- detailed sections

def _dist_row(label, d):
    if not d:
        return [esc(label), "—", "—", "—", "—"]
    ci = d.get("median_ci95") or [None, None]
    return [esc(label), fpct(d.get("pct_ever")), fnum(d.get("median"), 1),
            f"{fnum(ci[0], 1)}–{fnum(ci[1], 1)}", fnum(d.get("mean"), 2)]


def _section_commander(agg) -> str:
    cmd = agg["commander"]
    rows = [_dist_row("First cast", cmd["first_cast"])]
    parts = ["<h5>Commander</h5>", _lbl("first_event", agg.get("n_trials")),
             table(["Event", "Happened", "Median turn", "Median 95% CI",
                    "Mean turn"], rows)]
    by_turn = cmd["pct_cast_by_turn"]
    parts.append(table(
        ["Turn"] + [str(t) for t in by_turn],
        [["Cast by then, %"] + [f"{by_turn[t]['pct']:.0f}"
                                 for t in by_turn]]))
    parts.append(table(
        ["Measure", "Value"],
        [["Recasts per game (mean)", fnum(cmd["recasts_mean"])],
         ["Turns with commander in play (avg share)",
          fpct(100 * cmd["pct_turns_in_play"])],
         ["Commander tax paid per game (mean)", fnum(cmd["tax_paid_mean"])],
         ["Share of all mana spent on the commander",
          fpct(cmd["mana_share_pct"])]]))
    parts.append(details("Commander — full data", dump(cmd)))
    return "".join(parts)


def _section_curves(agg) -> str:
    curves = agg["curves"]
    cols = ["Turn", "Spells (cum mean)", "Mana avail", "Mana spent",
            "Unspent", "Mana used %", "Creatures", "Power", "Power (med)",
            "Toughness", "Lands", "Hand", "Cards seen", "Avg MV cast"]
    rows = []
    for t in sorted(curves, key=int):
        c = curves[t]
        rows.append([t, fnum(c["cum_spells_mean"]), fnum(c["mana_available_mean"]),
                     fnum(c["mana_spent_mean"]), fnum(c["unspent_mean"]),
                     fpct(100 * c["mana_utilization_mean"]),
                     fnum(c["creatures_mean"]), fnum(c["power_mean"]),
                     fnum(c["power_median"], 1), fnum(c["toughness_mean"]),
                     fnum(c["lands_mean"]), fnum(c["hand_mean"]),
                     fnum(c["cards_seen_mean"]),
                     fnum(c["avg_mv_cast"])])
    return ("<h5>Per-turn curves (cumulative spells; other columns are "
            "that turn's board)</h5>"
            + _lbl("curve_spells", agg.get("n_trials"))
            + _lbl("curve_mana_utilization", agg.get("n_trials"))
            + table(cols, rows)
            + details("Curves — full data (incl. permanents by type and "
                      "sources by color per turn)", dump(curves)))


def _section_firsts(agg) -> str:
    f = agg["firsts"]
    rows = [_dist_row("First creature cast", f["first_creature"]),
            _dist_row("All commander colors among lands",
                      f["color_complete_turn"]),
            _dist_row("All commander colors among lands (ignoring fetches)",
                      f["color_complete_turn_ignoring_fetches"]),
            _dist_row("First empty hand", f["first_empty_hand"])]
    for th, d in f["power_thresholds"].items():
        rows.append(_dist_row(f"Board power reaches {th}", d))
    for th, d in f["cumulative_drawn_thresholds"].items():
        rows.append(_dist_row(f"{th} cards drawn (beyond opening)", d))
    for th, d in f["cumulative_mana_spent_thresholds"].items():
        rows.append(_dist_row(f"{th} total mana spent", d))
    return ("<h5>Firsts</h5>" + _lbl("first_event", agg.get("n_trials"))
            + table(["Event", "Happened", "Median turn", "Median 95% CI",
                     "Mean turn"], rows)
            + details("Firsts — full data (by type, by tag, by mana value)",
                      dump(f)))


def _section_per_card(agg) -> str:
    pc = agg["per_card"]
    tp = agg["models"]["trapped_and_pitch"]
    cols = ["Card", "MV", "Types", "Seen", "Cast", "By T3", "By T5",
            "By T7", "Median cast turn", "Stranded", "Stranded: never "
            "had mana", "Stranded: sim ended", "Discarded (games)",
            "Bottomed (games)", "Trapped turns (mean)", "Pitch rate"]
    rows = []
    order = sorted(pc.items(), key=lambda kv: -kv[1]["seen_pct"]["k"])
    for name, v in order:
        t = tp.get(name, {})
        pitch = t.get("pitch_rate", {})
        rows.append([
            esc(name), fnum(v["mv"], 0), esc(", ".join(v["types"])),
            frate(v["seen_pct"]), frate(v["cast_pct_of_trials"]),
            fpct(v["cast_by_t3_pct"]["pct"]), fpct(v["cast_by_t5_pct"]["pct"]),
            fpct(v["cast_by_t7_pct"]["pct"]), fnum(v["median_cast_turn"], 1),
            frate(v["stranded_pct_of_trials"]),
            fnum(v["stranded_never_had_mana"]), fnum(v["stranded_sim_ended"]),
            fnum(v["discarded_games"]), fnum(v["bottomed_games"]),
            fnum(t.get("trapped_turns_mean")),
            (f"{pitch['pct']:.1f}% ({pitch['events']}/{pitch['appearances']})"
             if pitch else "—")])
    return ("<h5>Per-card results (every card in the deck)</h5>"
            "<p><small>Seen/Cast/Stranded are Wilson-interval rates over "
            "all games. Trapped turns and pitch rate are Tier 1 models "
            "T1-12/T1-13; lands have no trapped/pitch values (lands are "
            "never pitched).</small></p>"
            + _lbl("per_card_stranded", agg.get("n_trials"))
            + table(cols, rows))


def _section_mulligans(agg) -> str:
    m = agg["mulligans"]
    rates = agg["models"]["keep_stage_rates"]
    rows = []
    for label, s in m["stages"].items():
        r = rates.get(label, {})
        ci = r.get("wilson95", [None, None])
        rows.append([esc(label), fnum(s["reached"]), fnum(s["rule_kept"]),
                     fnum(s["ended_here"]),
                     (f"{r['rate_pct']:.1f}% (CI {ci[0]:.0f}–{ci[1]:.0f})"
                      if r else "—")])
    parts = ["<h5>Mulligans</h5>", _lbl("mulligan_stage", agg.get("n_trials")),
             table(["Stage", "Reached", "Kept by the rule", "Mulliganing "
                    "stopped here", "Keep rate at this stage (T1-11)"],
                   rows)]
    parts.append(details(
        "Mulligans — full data (final hand sizes, kept/thrown hand "
        "composition, results by final hand size, bottomed cards)",
        dump({k: v for k, v in m.items() if k != "stages"})))
    return "".join(parts)


def _section_lands_mana(agg) -> str:
    lm = agg["lands_mana"]
    rows = [_dist_row("First missed land drop", lm["first_missed_drop"])]
    parts = ["<h5>Lands and mana</h5>",
             _lbl("tapped_plays_per_game", agg.get("n_trials")),
             _lbl("fetch_uses_per_game", agg.get("n_trials")),
             table(["Event", "Happened", "Median turn", "Median 95% CI",
                    "Mean turn"], rows)]
    meas = [[f"No missed drop through turn {t[8:]}",
             frate(r)] for t, r in lm["no_missed_drop"].items()]
    meas += [
        ["Tapped land plays per game", fnum(lm["tapped_plays_per_game"])],
        ["Tapped-shortfall events per game",
         fnum(lm["tapped_shortfall_events_per_game"])],
        ["Fetch uses per game", fnum(lm["fetch_uses_per_game"])],
        ["Flood (3+ lands in final hand)", frate(lm["flood_pct_games_3plus_lands_in_final_hand"])],
        ["Lands in final hand (mean)", fnum(lm["avg_lands_in_final_hand"])],
        ["Priced-out turns per game", fnum(lm["priced_out_turns_per_game"])],
        ["Cleanup discards per game", fnum(lm["discards_to_cleanup_per_game"])],
    ]
    parts.append(table(["Measure", "Value"], meas))
    parts.append(details("Lands and mana — full data (incl. fetch colors)",
                         dump(lm)))
    return "".join(parts)


def _section_models(agg) -> str:
    md = agg["models"]
    parts = ["<h5>Tier 1 models</h5>"]
    mue = md["mana_utilization_efficiency"]
    rows = []
    for label, w in (("Turns 1–5 (Vol. 1 window)", mue["turns_1_5_vol1"]),
                     ("Turns 2–6 (Reference window)",
                      mue["turns_2_6_reference"])):
        pt = w["per_trial"]
        rows.append([label, fpct(100 * w["pooled_ratio"]),
                     fpct(100 * pt.get("mean", 0)), fpct(100 * pt.get("median", 0)),
                     f"{fpct(100 * pt.get('p25', 0))} – {fpct(100 * pt.get('p75', 0))}"])
    parts.append("<h6>Mana Utilization Efficiency (T1-06)</h6>")
    parts.append(_lbl("mue", agg.get("n_trials")))
    parts.append(table(["Window", "Pooled", "Per-game mean", "Per-game "
                        "median", "Per-game middle half"], rows))
    cast = md["castability"]
    rows = []
    for name, v in sorted(cast.items(),
                          key=lambda kv: kv[1]["cast_rate"]["pct"]):
        cr = v["cast_rate"]
        rows.append([esc(name), fnum(v["mv"], 0), frate(cr),
                     fnum(v["mean_delay_turns"]),
                     fnum(v["mean_delay_when_castable"]),
                     fpct(v["pct_never_castable"])])
    parts.append("<h6>On-Curve Cast Rate and Expected Cast Delay "
                 "(T1-07 / T1-08)</h6>")
    parts.append("<p><small>Cast rate = share of games where the game's "
                 "mana could have paid this spell on its earliest "
                 "possible turn (turn = its mana value), judging the "
                 "spell on its own against the untapped mana at that "
                 "point of the same game. Delay counts games where it "
                 "never became payable as lasting one turn past the "
                 "end of the simulation (a lower bound).</small></p>")
    parts.append(_lbl("castability_rate", agg.get("n_trials")))
    parts.append(table(["Card", "MV", "Cast rate", "Mean delay (turns)",
                        "Mean delay when payable", "Never payable"], rows))
    return "".join(parts)


def _section_regime_block(view: BatchView, variant: str, regime: str) -> str:
    agg = view.aggregate(variant, regime)
    n = view.counts.get((variant, regime), 0)
    parts = [f"<h4>{esc(variant)} × {esc(regime)} — {n} games</h4>"]
    parts.append(_section_commander(agg))
    parts.append(_section_curves(agg))
    parts.append(_section_firsts(agg))
    comp = agg["composition"]
    parts.append("<h5>What got cast (composition)</h5>"
                 + _lbl("casts_per_game", agg.get("n_trials")) + table(
        ["Measure", "Value"],
        [["Total casts", fnum(comp["total_casts"])],
         ["Casts per game", fnum(comp["casts_per_game"])],
         ["Cast the turn it was drawn", frate(comp["fresh_cast_rate"])]])
        + details("Composition — full data (by type, pip colors, mana "
                  "spent by color, mana-value buckets, casts and mana "
                  "by function tag)", dump(comp)))
    parts.append(_section_per_card(agg))
    parts.append("<h5>First-card frequency</h5>" + details(
        "Which card was the first of its kind, per category",
        dump(agg["first_card_frequency"]), open_=True))
    parts.append(_section_mulligans(agg))
    parts.append(_section_lands_mana(agg))
    parts.append(_section_models(agg))
    parts.append("<h5>Regime-specific results</h5>"
                 + dump(agg["regime_extras"]))
    parts.append("<h5>End state (final turn)</h5>" + dump(agg["end_snapshot"]))
    return "".join(parts)


def _section_manabase_hyper(view: BatchView, variant: str) -> str:
    mana = view.manabase(variant)
    parts = [f"<h4>Manabase audit — {esc(variant)} (Karsten)</h4>"]
    parts.append(_lbl("karsten_lands"))
    parts.append(table(
        ["Measure", "Value"],
        [["Lands in deck", fnum(mana["lands_actual"])],
         ["Karsten recommended lands", fnum(mana["karsten_recommended_lands"])],
         ["Average mana value of spells", fnum(mana["avg_mv_spells"], 3)],
         ["Cheap ramp (MV ≤ 2)", fnum(mana["cheap_ramp_le2"])],
         ["Cantrips (MV ≤ 2)", fnum(mana["cantrips_le2"])]]))
    rows = []
    for color, p in mana["pips"].items():
        rows.append([esc(color), fnum(p["sources"], 1),
                     fnum(p["required"]), esc(p["peak_demand_card"] or "—"),
                     fnum(p["single_pip_cards"]), fnum(p["double_pip_cards"]),
                     fnum(p["triple_pip_cards"]), fnum(p["deficit"], 1)])
    parts.append(table(
        ["Color", "Sources", "Karsten required", "Peak-demand card",
         "1-pip cards", "2-pip cards", "3-pip cards", "Deficit"], rows))
    parts.append("<p><small>Required = sources Karsten's matrix says a "
                 "deck needs to cast the peak-demand card on curve. "
                 "A blank required means no card demands that color. "
                 "See the methods note about this table.</small></p>")
    hyper = view.hypergeometric(variant)
    parts.append(_lbl("hypergeometric"))
    parts.append(f"<h4>Hypergeometric probabilities — {esc(variant)} "
                 f"(T1-01)</h4>")
    parts.append(f"<p><small>{esc(hyper['note'])}</small></p>")
    for label, cat in hyper["categories"].items():
        ks = sorted({k for row in cat["by_cards_seen"].values()
                     for k in row}, key=int)
        rows = []
        for n_seen, row in sorted(cat["by_cards_seen"].items(), key=lambda kv: int(kv[0])):
            rows.append([n_seen] + [fpct(row.get(k)) for k in ks])
        parts.append(f"<h6>{esc(label)} — {cat['K']} in the "
                     f"{cat['N']}-card library: % chance of at least k "
                     f"after seeing n cards</h6>")
        parts.append(table(["Cards seen \\ at least"] +
                           [f"≥{k}" for k in ks], rows))
    return "".join(parts)


def _section_deltas(view: BatchView, variant: str) -> str:
    base = view.regimes()[0]
    parts = []
    for regime in view.regimes()[1:]:
        if not view.trials.get((variant, base)) or \
                not view.trials.get((variant, regime)):
            continue  # incomplete batch: this comparison has no games
        d = view.deltas(variant, regime)
        w = d["commander_first_cast_wilcoxon"]
        mc = d["commander_cast_mcnemar"]
        cfd = d["commander_first_cast_turn_diff"]
        rows = [
            ["Commander first cast, mean shift (turns)",
             f"{cfd['mean_diff']:+.2f} (95% CI {cfd['ci95'][0]:+.2f} to "
             f"{cfd['ci95'][1]:+.2f})"],
            ["Wilcoxon signed-rank on that shift (T1-19)",
             f"W+ = {fnum(w['w_plus'], 1)}, n = {w['n']}, "
             f"p = {w['p_two_sided']:.4f} ({esc(w['method'])})"],
            ["McNemar, commander cast at all (T1-20)",
             f"b = {mc['b']} (only in {base}), c = {mc['c']} (only in "
             f"{regime}), χ² = {mc['chi2']:.2f}, p = {mc['p_chi2']:.4f}, "
             f"exact p = {mc['p_exact']:.4f}"],
            ["Commander tax paid, mean shift",
             f"{d['commander_tax_paid_diff']['mean_diff']:+.2f}"],
        ]
        parts.append(f"<h4>{esc(variant)}: {esc(regime)} vs {esc(base)} "
                     f"(paired, same seeds)</h4>")
        parts.append(_lbl("paired_test"))
        parts.append(table(["Measure", "Value"], rows))
        parts.append(details(f"{regime} vs {base} — full paired data "
                             f"(power and spells per turn)",
                             dump({k: v for k, v in d.items()
                                   if k in ("power", "spells_cast")})))
    return "".join(parts)


# ------------------------------------------- cross-variant / cross-batch

def _section_variants(view: BatchView) -> str:
    vids = view.variant_ids()
    if len(vids) < 2:
        return ""
    base_regime = view.regimes()[0]
    vids = [v for v in vids if view.trials.get((v, base_regime))]
    if len(vids) < 2:
        return ("<h3>Perturbation comparison</h3><p><small>This batch "
                "file does not yet contain games for two or more "
                "variants, so no comparison can be computed.</small></p>")
    parts = [f"<h3>Perturbation comparison ({esc(base_regime)})</h3>"]
    parts.append("<p><small>All variants ran the same games with the "
                 "same seeds, so differences are paired: the same "
                 "shuffles, land changes only.</small></p>")
    casts = {vid: view.aggregate(vid, base_regime)["models"]["castability"]
             for vid in vids}
    pm = models.perturbation_matrix(casts)
    rows = []
    for vid in vids:
        r = pm["rows"][vid]
        rows.append([esc(vid), fnum(r.get("n_spells")),
                     fpct(r.get("mean_cast_rate_pct")),
                     fnum(r.get("mean_delay_turns")),
                     (f"{r['delta_cast_rate_pp']:+.2f}" if "delta_cast_rate_pp" in r else "—"),
                     (f"{r['delta_delay_turns']:+.3f}" if "delta_delay_turns" in r else "—")])
    parts.append(_lbl("perturbation_cast_rate",
                        view.counts.get(("base", base_regime))))
    parts.append("<h4>Marginal Land Perturbation Matrix (T1-09)</h4>")
    parts.append(table(["Variant", "Spells measured", "Mean cast rate",
                        "Mean delay (turns)", "Δ cast rate vs base (pp)",
                        "Δ delay vs base"], rows))
    tax = pm.get("tapland_tempo_tax")
    if tax:
        parts.append(
            f"<p><strong>Tapland Tempo Tax (T1-10):</strong> converting "
            f"taplands to untapped basics changes the mean cast rate by "
            f"{tax['cast_rate_gain_pp']:+.2f} percentage points and the "
            f"mean delay by {-tax['delay_reduction_turns']:+.3f} turns "
            f"(negative delay = faster).</p>")
    base_trials = view.trials[("base", base_regime)]
    for vid in vids[1:]:
        tests = paired_variant_tests(base_trials,
                                     view.trials[(vid, base_regime)],
                                     view.turns)
        w1 = tests["spells_cast_wilcoxon"]
        w2 = tests["commander_first_cast_wilcoxon"]
        mc1 = tests["commander_cast_mcnemar"]
        mc2 = tests["no_missed_drop_through_5_mcnemar"]
        rows = [
            ["Spells cast per game, mean difference",
             f"{tests['spells_cast_mean_diff']:+.2f}"],
            ["Wilcoxon on spells cast (T1-19)",
             f"W+ = {fnum(w1['w_plus'], 1)}, n = {w1['n']}, "
             f"p = {w1['p_two_sided']:.4f} ({esc(w1['method'])})"],
            ["Wilcoxon on commander first-cast turn (T1-19)",
             f"W+ = {fnum(w2['w_plus'], 1)}, n = {w2['n']}, "
             f"p = {w2['p_two_sided']:.4f} ({esc(w2['method'])})"],
            ["McNemar: commander cast at all (T1-20)",
             f"b = {mc1['b']} (base only), c = {mc1['c']} (variant only), "
             f"χ² = {mc1['chi2']:.2f}, p = {mc1['p_chi2']:.4f}, "
             f"exact p = {mc1['p_exact']:.4f}"],
            ["McNemar: no missed land drop through turn 5 (T1-20)",
             f"b = {mc2['b']} (base only), c = {mc2['c']} (variant only), "
             f"χ² = {mc2['chi2']:.2f}, p = {mc2['p_chi2']:.4f}, "
             f"exact p = {mc2['p_exact']:.4f}"],
        ]
        parts.append(f"<h4>Paired tests: {esc(vid)} vs base</h4>")
        parts.append(table(["Test", "Result"], rows))
    return "".join(parts)


def _batch_headline(view: BatchView) -> dict:
    trials = view.trials.get(("base", view.regimes()[0]), [])
    mana = view.manabase("base")
    fc = view.d0("base")["commander"]["first_cast"] if trials else {}
    return {
        "deck": view.meta["deck"]["name"],
        "commander": view.meta["deck"]["commander"],
        "games": len(trials),
        "lands": mana["lands_actual"],
        "karsten": mana["karsten_recommended_lands"],
        "cmd_median": fc.get("median"),
        "cmd_pct": fc.get("pct_ever"),
        "casts_per_game": _mean_casts(trials),
        "first_casts": [_first_cast_capped(tr, view.turns) for tr in trials],
        "casts_list": [len(tr.casts) for tr in trials],
    }


def _section_cross_batch(views) -> str:
    if len(views) < 2:
        return ""
    heads = [_batch_headline(v) for v in views]
    parts = ["<h2>Cross-batch comparison</h2>"]
    rows = [[esc(h["deck"]), esc(h["commander"]), fnum(h["games"]),
             fnum(h["lands"]), fnum(h["karsten"], 1),
             fnum(h["cmd_median"], 1), fpct(h["cmd_pct"]),
             fnum(h["casts_per_game"])] for h in heads]
    parts.append(table(["Deck", "Commander", "Games (base, first regime)",
                        "Lands", "Karsten lands", "Commander median turn",
                        "Commander cast %", "Casts / game"], rows))
    rows = []
    for i in range(len(views)):
        for j in range(i + 1, len(views)):
            a, b = heads[i], heads[j]
            u1 = stats.mann_whitney_u(a["first_casts"], b["first_casts"])
            u2 = stats.mann_whitney_u(a["casts_list"], b["casts_list"])
            rows.append([
                f"{esc(a['deck'])} vs {esc(b['deck'])}",
                f"U = {fnum(u1['u'], 1)}, p = {u1['p_two_sided']:.4f} "
                f"({esc(u1['method'])})",
                f"U = {fnum(u2['u'], 1)}, p = {u2['p_two_sided']:.4f} "
                f"({esc(u2['method'])})"])
    parts.append("<h3>Mann-Whitney U tests (T1-21, independent samples)</h3>")
    parts.append(_lbl("mann_whitney"))
    parts.append("<p><small>Commander first-cast turns count a game where "
                 "the commander was never cast as one turn past the end "
                 "of the simulation, in both decks.</small></p>")
    parts.append(table(["Comparison", "Commander first-cast turn",
                        "Casts per game"], rows))
    return "".join(parts)


METHODS_NOTE = """
<h2>Methods, and what is not here</h2>
<ul>
<li><strong>Every number on this page is recomputed from the per-game
records</strong> in the batch file, using the same measurement code as
<code>goldfish4 analyze</code>. The batch file stores no aggregates.</li>
<li><strong>Function tags are counting-only.</strong> No game-state
effect is ever inferred from a tag: the simulator measures the deck's
skeleton (lands, mana, costs, types). Late-game card-flow numbers are
therefore conservative underestimates.</li>
<li><strong>Labels (v4.1).</strong> Every primary table carries a
plain-language label line — what was counted, its unit, its sample,
and its kind — from the single label registry both this dashboard
and the text report use. The "full data" expanders are the exception:
they show the raw measurement dictionaries the labeled tables were
built from (every value in them is a count, share, or distribution
over the same games, with the same meanings as the labeled tables).</li>
<li><strong>Not included: Compounded Effective Mana Spent (M_comp,
T1-05) and its P20/P80 (T1-17).</strong> The research documents
attribute a turn-weighted compounding formula to Frank Karsten, but no
such metric could be found in Karsten's published work when the
formulas were verified online (his mana-curve research uses total,
unweighted, mana spent). Under the rule that unverifiable math is
never improvised, the model was stopped rather than guessed. Every
other Tier 1 model is on this page.</li>
<li><strong>Karsten source table caveat.</strong> The colored-source
matrix is the table shipped in v4.0.1; Karsten's original article
could not be re-fetched during the v4.0.2 verification pass and some
secondary sources quote different cells, so the table is flagged for
re-verification against the primary source rather than silently
changed (deck composition advice in Brandon's standing rules rests on
Volume 1, not on this matrix).</li>
<li><strong>Cast rate / delay</strong> judge each spell on its own
against the untapped mana present at that point of the same simulated
game (Salubrious Snail's definitions); ramp cast in earlier turns
counts. Spells that never become payable count one turn past the
simulation's end in the mean delay (a lower bound) and are also
reported directly as "never payable".</li>
<li><strong>Statistics.</strong> Wilson score intervals for rates;
exact median confidence intervals; percentile bootstrap for paired
mean differences; Wilcoxon signed-rank with exact sign-flip p-values
for n ≤ 50 pairs (normal approximation with tie and continuity
corrections beyond); McNemar's chi-squared with the exact binomial
p-value alongside; Mann-Whitney U with exact p-values for small
tie-free samples. All implemented from verified published definitions
with zero third-party dependencies (no scipy).</li>
</ul>
"""


# ------------------------------------------------------------- assembly

def render(views) -> str:
    parts = ["<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
             "<meta name='viewport' content='width=device-width, "
             "initial-scale=1'><title>EDH-Goldfish dashboard</title>"
             f"<style>{CSS}</style></head><body>",
             "<h1>EDH-Goldfish batch dashboard</h1>"]
    for i, view in enumerate(views):
        parts.append(render_header(view, i))
        parts.append(render_summary(view))
        parts.append(_section_variants(view))
        parts.append("<h2>Complete measured data</h2>")
        for vid in view.variant_ids():
            parts.append(_section_manabase_hyper(view, vid))
            for regime in view.regimes():
                if view.counts.get((vid, regime), 0) == 0:
                    continue
                open_ = (vid == "base" and regime == view.regimes()[0])
                parts.append(details(
                    f"{vid} × {regime} — full results",
                    _section_regime_block(view, vid, regime), open_=open_))
            parts.append(_section_deltas(view, vid))
    parts.append(_section_cross_batch(views))
    parts.append(METHODS_NOTE)
    parts.append("</body></html>")
    return "".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="goldfish4-dashboard",
        description="Render a self-contained HTML dashboard from one or "
                    "more goldfish4 batch JSON files. Every number is "
                    "recomputed from the per-game records. With "
                    "--compare SLUG, render the version comparison for "
                    "an archived deck instead (v4.1).")
    ap.add_argument("batch_json", nargs="*",
                    help="batch JSON file(s) written by `goldfish4 batch`")
    ap.add_argument("--out", default=None,
                    help="output HTML file (default: <first batch name>"
                         ".dashboard.html next to the input)")
    ap.add_argument("--compare", metavar="DECK_SLUG", default=None,
                    help="render the version compare view for this "
                         "archived deck slug (see `goldfish4 versions`)")
    ap.add_argument("--versions", default=None,
                    help="with --compare: comma-separated version "
                         "numbers to show (default: the 3 latest)")
    ap.add_argument("--data-dir", default=None,
                    help="with --compare: the data dir holding the "
                         "version archive (default: the standard "
                         "bulk-data dir)")
    args = ap.parse_args(argv)
    if args.compare:
        from .bulkdata import DEFAULT_DATA_DIR
        from .compare import render_compare
        data_dir = args.data_dir or DEFAULT_DATA_DIR
        wanted = None
        if args.versions:
            try:
                wanted = [int(x) for x in args.versions.split(",")
                          if x.strip()]
            except ValueError:
                ap.error("--versions must be comma-separated numbers")
        out = args.out
        if out is None:
            from .archive import store_root
            out = os.path.join(store_root(data_dir), args.compare,
                               "compare.html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(render_compare(data_dir, args.compare, wanted))
        print(f"wrote {out} (version comparison for {args.compare})")
        return 0
    if not args.batch_json:
        ap.error("give at least one batch JSON file, or --compare SLUG")
    views = [BatchView(p) for p in args.batch_json]
    out = args.out
    if out is None:
        stem = os.path.splitext(os.path.basename(args.batch_json[0]))[0]
        out = os.path.join(os.path.dirname(os.path.abspath(args.batch_json[0])),
                           stem + ".dashboard.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(render(views))
    total = sum(sum(v.counts.values()) for v in views)
    print(f"wrote {out} ({total} game records across "
          f"{len(views)} batch file(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
