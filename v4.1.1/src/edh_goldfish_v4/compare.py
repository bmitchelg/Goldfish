"""Version compare view (v4.1, spec item 7).

Renders up to 3 archived versions of one deck side by side, columns
ordered oldest -> newest. Colors identify the VERSION, never quality
(oldest green, middle blue, latest black): no improvement coloring,
no percent-change column — the reader reads the numbers and judges.
Each column header names its version in text (version number, date,
games/seed/settings). Decklist differences list ONLY added and
removed cards. If the selected runs differ in games, seed, keep
config, regimes enabled, or bulk-data date, a loud warning banner
says so instead of presenting the numbers as comparable (the view
still renders). Every figure follows the labeling standard
(labels.py).
"""

from __future__ import annotations

import os
from collections import Counter

from . import archive as archive_mod
from . import labels as labels_mod
from .dashboard import CSS, BatchView, details, esc, fnum, table
from .parser import parse_decklist

GREEN, BLUE, BLACK = "#1a7f37", "#1f6feb", "#111111"


def version_colors(k: int) -> list:
    if k <= 1:
        return [BLACK]
    if k == 2:
        return [GREEN, BLACK]
    return [GREEN, BLUE, BLACK]


def decklist_counts(text: str) -> Counter:
    """Card-name counts for a whole decklist (mainboard + commanders)."""
    dl = parse_decklist(text)
    counts: Counter = Counter()
    for count, name in dl.mainboard:
        counts[name] += count
    for name in dl.commander_names:
        counts[name] += 1
    return counts


def decklist_diff(older: Counter, newer: Counter) -> dict:
    """ONLY the changes: cards added (count went up) and removed
    (count went down), each with the count change."""
    added, removed = [], []
    for name in sorted(set(older) | set(newer)):
        d = newer.get(name, 0) - older.get(name, 0)
        if d > 0:
            added.append((name, d))
        elif d < 0:
            removed.append((name, -d))
    return {"added": added, "removed": removed}


_COMPARE_FIELDS = (
    ("games per setting", "games_per_config"),
    ("seed", "seed_base"),
    ("keep rule", "keep_rule"),
    ("regimes enabled", "regimes_enabled"),
    ("card-data date", "data_date"),
)


def comparability(metas: list) -> list:
    """Names of the run settings that DIFFER across the selected
    versions (empty list = fully comparable)."""
    out = []
    for label_text, key in _COMPARE_FIELDS:
        vals = []
        for m in metas:
            v = m.get(key)
            if v is None and key == "regimes_enabled":
                v = m.get("regimes")
            vals.append(jsonable(v))
        if len(set(vals)) > 1:
            out.append(label_text)
    return out


def jsonable(v):
    if isinstance(v, (list, tuple)):
        return ",".join(str(x) for x in v)
    return str(v)


def select_versions(data_dir: str, slug: str, wanted: list | None) -> list:
    """Archive records to compare: the named version numbers, or the
    3 latest. Always sorted oldest -> newest."""
    records = archive_mod.list_versions(data_dir, slug)
    if not records:
        raise SystemExit(f"no archived versions for deck '{slug}' "
                         f"(looked in {archive_mod.store_root(data_dir)})")
    if wanted:
        if len(wanted) > 3:
            raise SystemExit("compare shows at most 3 versions")
        by_num = {r["version"]: r for r in records}
        missing = [n for n in wanted if n not in by_num]
        if missing:
            raise SystemExit(f"deck '{slug}' has no version(s): "
                             + ", ".join(f"v{n}" for n in missing))
        return [by_num[n] for n in sorted(wanted)]
    return records[-3:]


def _pctval(d) -> float | None:
    if isinstance(d, dict):
        return d.get("pct")
    return d


def _metrics(view: BatchView) -> dict:
    agg = view.d0("base")
    mana = view.manabase("base")
    fc = agg["commander"]["first_cast"]
    comp = agg["composition"]
    lm = agg["lands_mana"]
    mue = agg["models"]["mana_utilization_efficiency"]["turns_1_5_vol1"]
    stages = agg["mulligans"]["stages"]
    initial = stages.get("initial", {})
    keep_rate = (100.0 * initial["rule_kept"] / initial["reached"]
                 if initial.get("reached") else None)
    return {
        "land_count": fnum(mana["lands_actual"], 0),
        "karsten_lands": fnum(mana["karsten_recommended_lands"], 1),
        "avg_mv_spells": fnum(mana["avg_mv_spells"]),
        "commander_first_cast_median": fnum(fc.get("median"), 1),
        "commander_first_cast_mean": fnum(fc.get("mean")),
        "commander_cast_pct": (f"{_pctval(fc.get('pct_ever')):.1f}%"
                               if _pctval(fc.get("pct_ever")) is not None
                               else "—"),
        "keep_rate_stage": (f"{keep_rate:.1f}%" if keep_rate is not None
                            else "—"),
        "casts_per_game": fnum(comp["casts_per_game"]),
        "fresh_cast_rate": f"{comp['fresh_cast_rate']['pct']:.1f}%",
        "mue": f"{100 * mue['pooled_ratio']:.1f}%",
        "tapped_plays_per_game": fnum(lm["tapped_plays_per_game"]),
        "fetch_uses_per_game": fnum(lm["fetch_uses_per_game"]),
        "flood": f"{lm['flood_pct_games_3plus_lands_in_final_hand']['pct']:.1f}%",
        "priced_out_turns": fnum(lm["priced_out_turns_per_game"]),
        "end_power": fnum(agg["end_snapshot"]["power"]["mean"]),
        "_casts_by_tag": comp["casts_by_tag_per_game"],
    }


# (registry key for the label, metrics dict key)
METRIC_ROWS = [
    ("land_count", "land_count"),
    ("karsten_lands", "karsten_lands"),
    ("avg_mv_spells", "avg_mv_spells"),
    ("first_event", "commander_first_cast_median"),
    ("first_event", "commander_first_cast_mean"),
    ("first_event", "commander_cast_pct"),
    ("keep_rate_stage", "keep_rate_stage"),
    ("casts_per_game", "casts_per_game"),
    ("fresh_cast_rate", "fresh_cast_rate"),
    ("mue", "mue"),
    ("tapped_plays_per_game", "tapped_plays_per_game"),
    ("fetch_uses_per_game", "fetch_uses_per_game"),
    ("flood", "flood"),
    ("priced_out_turns", "priced_out_turns"),
    ("end_power", "end_power"),
]

ROW_TITLES = {
    "commander_first_cast_median": "Commander first cast — median turn",
    "commander_first_cast_mean": "Commander first cast — mean turn",
    "commander_cast_pct": "Commander cast at all",
    "keep_rate_stage": "Opening hand kept (first hand)",
    "priced_out_turns": "Priced-out turns",
    "end_power": "Board power at end of final turn",
    "mue": "Mana spent, turns 1–5 (pooled)",
}


def _label_cell(key: str, metric_key: str) -> str:
    lab = labels_mod.LABELS[key]
    title = ROW_TITLES.get(metric_key, lab.what)
    kind = ("" if lab.kind == labels_mod.MEASURED
            else f" <em>({labels_mod.kind_text(lab.kind)})</em>")
    return f"{esc(title)} — {esc(lab.unit)}{kind}"


def _version_header(rec: dict, view: BatchView, color: str) -> str:
    meta = view.meta
    date = (meta.get("created_at") or "")[:10] or "?"
    games = meta.get("games_per_config", "?")
    return (f"<span style='color:{color}'>■</span> "
            f"<strong>v{rec['version']}</strong> — {esc(date)} — "
            f"{esc(games)} games per setting, seed "
            f"{esc(meta.get('seed_base'))}")


def render_compare(data_dir: str, slug: str, wanted: list | None) -> str:
    records = select_versions(data_dir, slug, wanted)
    views, counts = [], []
    for rec in records:
        views.append(BatchView(os.path.join(rec["dir"], "batch.json")))
        with open(os.path.join(rec["dir"], "decklist.txt"),
                  encoding="utf-8") as f:
            counts.append(decklist_counts(f.read()))
    colors = version_colors(len(records))
    metas = [v.meta for v in views]
    mismatches = comparability(metas)
    deck_name = metas[0]["deck"]["name"]
    commander = metas[0]["deck"]["commander"]

    parts = ["<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
             "<meta name='viewport' content='width=device-width, "
             "initial-scale=1'><title>Version comparison</title>"
             f"<style>{CSS}</style></head><body>",
             f"<h1>Version comparison — {esc(deck_name)}</h1>",
             f"<p>Commander: {esc(commander)}. Columns run oldest to "
             f"newest, left to right. The colors identify the version "
             f"only — green is the oldest shown, blue the middle, "
             f"black the newest. They never grade the numbers.</p>"]
    if mismatches:
        parts.append(
            "<p class='warn'><strong>NOT FULLY COMPARABLE.</strong> "
            "These runs differ in: " + esc(", ".join(mismatches)) +
            ". Numbers from runs with different settings are shown "
            "side by side below, but differences between the columns "
            "may come from the settings, not from the deck changes.</p>")
    head = ["Measure"] + [_version_header(r, v, c)
                          for r, v, c in zip(records, views, colors)]
    metrics = [_metrics(v) for v in views]
    rows = [[_label_cell(k, mk)] + [m[mk] for m in metrics]
            for k, mk in METRIC_ROWS]
    parts.append("<h2>Headline measurements (base deck, normal "
                 "conditions)</h2>")
    parts.append(table(head, rows))
    tags = sorted({t for m in metrics for t in m["_casts_by_tag"]})
    if tags:
        rows = [[f"{esc(t)} casts — casts per game"]
                + [fnum(m["_casts_by_tag"].get(t)) for m in metrics]
                for t in tags]
        parts.append("<h2>Spells cast by function tag</h2>")
        parts.append("<p class='lbl'><small>"
                     + esc(labels_mod.caption("casts_by_tag"))
                     + "</small></p>")
        parts.append(table(head, rows))
    parts.append("<h2>Decklist changes</h2>")
    if len(records) < 2:
        parts.append("<p>Only one version is archived, so there is "
                     "nothing to diff against.</p>")
    else:
        pairs = []
        if len(records) == 3:
            pairs = [(1, 0), (2, 1), (2, 0)]
        else:
            pairs = [(1, 0)]
        for newer_i, older_i in pairs:
            d = decklist_diff(counts[older_i], counts[newer_i])
            title = (f"Changes in v{records[newer_i]['version']} "
                     f"compared with v{records[older_i]['version']}")
            if not d["added"] and not d["removed"]:
                body = "<p>No card changes.</p>"
            else:
                body = ("<p>Added:</p><ul>" + "".join(
                    f"<li>+ {esc(n)}" + (f" ×{c}" if c > 1 else "") + "</li>"
                    for n, c in d["added"]) + "</ul>"
                    "<p>Removed:</p><ul>" + "".join(
                    f"<li>− {esc(n)}" + (f" ×{c}" if c > 1 else "") + "</li>"
                    for n, c in d["removed"]) + "</ul>")
            parts.append(details(title, body, open_=True))
    parts.append("<p><small>Every figure is recomputed from the "
                 "archived per-game records, with the labeling "
                 "standard applied (what was counted, its unit, and "
                 "its kind). Full per-version dashboards can be "
                 "rendered from each version's batch.json.</small></p>")
    parts.append("</body></html>")
    return "".join(parts)
