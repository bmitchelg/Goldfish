"""Measurement labeling standard (v4.1, spec item 6 — Brandon,
approved 2026-10-31): no naked numbers.

Every figure the report and the dashboard display carries, as part
of its label:
  (a) what was counted or tested, in plain words;
  (b) its unit;
  (c) its sample / denominator;
  (d) what KIND of number it is — measured in the test games, a
      formula's recommendation, or a stated rule of thumb. Kinds are
      never blended silently in one label.

Conditions that change a number's meaning (solo games, no opponent,
the tool's play rules) are part of the label. This module is the
single registry: report.py and dashboard.py both consume it, so a
metric's label cannot drift between the two surfaces.

Brandon's test: "34" alone fails; "34 oz of water in the bucket"
passes.
"""

from __future__ import annotations

from dataclasses import dataclass

MEASURED = "measured"
FORMULA = "formula"
RULE = "rule"
SETTINGS = "settings"

KIND_TEXT = {
    MEASURED: "measured in the test games",
    FORMULA: "a formula's recommendation (Karsten) — not measured in the games",
    RULE: "a stated rule of thumb",
    SETTINGS: "run settings, not a measurement",
}

SOLO = "solo games, no opponent, played by the tool's play rules"


@dataclass(frozen=True)
class Label:
    what: str                 # what was counted, plain words
    unit: str                 # the unit the number is in
    kind: str = MEASURED
    sample: str = ""          # sample/denominator; "{n}" = games/trials
    conditions: str = ""      # meaning-changing conditions


# The registry. Keys are metric names used by report.py / dashboard.py.
LABELS: dict[str, Label] = {
    # ---- run header / settings -------------------------------------
    "library_size": Label(
        "cards in the library (the deck without its commander)",
        "cards", SETTINGS, "counted from the decklist"),
    "land_count": Label(
        "lands in the deck", "lands out of the whole deck", SETTINGS,
        "counted from the decklist"),
    "trials": Label(
        "test games played per stress setting", "games", SETTINGS, "",
        SOLO),
    "turns_simulated": Label(
        "turns played in each test game", "turns per game", SETTINGS),
    "seed": Label(
        "the shuffle seed — the same seed always deals the same "
        "shuffles, so two runs with the same seed are comparable "
        "game by game", "a whole number", SETTINGS),
    "keep_rule": Label(
        "the rule that decides whether an opening hand is kept",
        "a written rule", RULE, "",
        "the two free mulligans are Brandon's deliberate house rule"),
    "data_date": Label(
        "the date of the Scryfall card data the run used",
        "a date", SETTINGS),
    # ---- manabase audit (Karsten) ----------------------------------
    "karsten_lands": Label(
        "lands the formula says this deck's spell costs need",
        "lands", FORMULA, "computed from the decklist — no games played"),
    "karsten_sources": Label(
        "mana sources of this color the formula says the deck needs "
        "to cast its most demanding card of that color on curve",
        "sources", FORMULA, "computed from the decklist — no games played"),
    "sources_counted": Label(
        "mana sources of this color actually in the deck",
        "sources", MEASURED, "counted from the decklist"),
    "karsten_deficit": Label(
        "how many more sources of this color the formula recommends "
        "than the deck has (a negative number means the deck has more "
        "than the formula asks for)", "sources", FORMULA,
        "the formula's requirement minus the counted sources"),
    "avg_mv_spells": Label(
        "average mana value of the deck's spells (lands excluded)",
        "mana value", MEASURED, "averaged over every spell in the decklist"),
    "cheap_ramp": Label(
        "ramp spells costing 2 mana or less in the deck",
        "cards", MEASURED, "counted from the decklist"),
    "cantrips": Label(
        "cheap card-draw spells (cantrips) costing 2 mana or less "
        "in the deck", "cards", MEASURED, "counted from the decklist"),
    # ---- mulligans --------------------------------------------------
    "mulligan_stage": Label(
        "opening hands that reached this mulligan stage, how many the "
        "keep rule kept, and how many games ended their mulligans here",
        "games", MEASURED, "out of {n} test games", SOLO),
    "keep_rate_stage": Label(
        "share of opening hands reaching this stage that the keep "
        "rule kept", "% of hands at this stage", MEASURED,
        "hands reaching the stage, over {n} test games", SOLO),
    "final_hand_size": Label(
        "games that finished mulliganing with this many cards in hand",
        "games", MEASURED, "out of {n} test games", SOLO),
    "by_final_size_spells": Label(
        "spells cast by turn 7, averaged over games that kept this "
        "final hand size", "spells per game", MEASURED,
        "games with that final hand size (n shown; a size nobody "
        "reached is reported as n=0, never estimated)", SOLO),
    "by_final_size_commander": Label(
        "the turn the commander was first cast, over games that kept "
        "this final hand size", "turns", MEASURED,
        "games with that final hand size", SOLO),
    "by_final_size_mana_util": Label(
        "share of available mana actually spent across turns 1–7, "
        "over games that kept this final hand size",
        "a share from 0 to 1", MEASURED,
        "games with that final hand size", SOLO),
    # ---- distributions of firsts / events ---------------------------
    "first_event": Label(
        "the turn this first happened in a test game: the middle "
        "(median) game, the average game, and in how many games it "
        "happened at all", "turns, and % of games", MEASURED,
        "out of {n} test games", SOLO),
    "first_creature_detail": Label(
        "average mana value and power of the first creature cast",
        "mana value / power", MEASURED,
        "averaged over games where a creature was cast", SOLO),
    # ---- commander ---------------------------------------------------
    "commander_recasts": Label(
        "times the commander was cast again after its first cast",
        "casts per game (average)", MEASURED, "over {n} test games", SOLO),
    "commander_turns_in_play": Label(
        "share of all simulated turns with the commander on the "
        "battlefield", "% of turns", MEASURED, "over {n} test games", SOLO),
    "commander_tax": Label(
        "extra mana paid to commander tax", "mana per game (average)",
        MEASURED, "over {n} test games", SOLO),
    "commander_mana_share": Label(
        "share of all mana spent in the game that went to casting "
        "the commander", "% of mana spent", MEASURED,
        "over {n} test games", SOLO),
    "commander_cast_by_turn": Label(
        "games where the commander had been cast by this turn",
        "% of games", MEASURED, "out of {n} test games", SOLO),
    # ---- per-turn curves ---------------------------------------------
    "curve_spells": Label(
        "spells cast on this turn", "spells per game (average)",
        MEASURED, "over {n} test games", SOLO),
    "curve_cum_spells": Label(
        "spells cast from the start of the game through this turn",
        "spells per game (average)", MEASURED, "over {n} test games", SOLO),
    "curve_mana_available": Label(
        "mana available on this turn from untapped sources",
        "mana (average)", MEASURED, "over {n} test games", SOLO),
    "curve_mana_spent": Label(
        "mana actually spent on this turn", "mana (average)", MEASURED,
        "over {n} test games", SOLO),
    "curve_mana_utilization": Label(
        "share of the turn's available mana that was actually spent",
        "a share from 0 to 1", MEASURED, "over {n} test games", SOLO),
    "curve_unspent": Label(
        "mana left unspent on this turn", "mana (average)", MEASURED,
        "over {n} test games", SOLO),
    "curve_creatures": Label(
        "creatures on the battlefield on this turn",
        "creatures (average)", MEASURED, "over {n} test games", SOLO),
    "curve_power": Label(
        "total power of creatures on the battlefield on this turn "
        "(board power — not a win prediction)",
        "power (average and middle game)", MEASURED,
        "over {n} test games", SOLO),
    "curve_toughness": Label(
        "total toughness of creatures on the battlefield on this turn",
        "toughness (average)", MEASURED, "over {n} test games", SOLO),
    "curve_hand": Label(
        "cards in hand at the end of this turn", "cards (average)",
        MEASURED, "over {n} test games", SOLO),
    "curve_lands": Label(
        "lands on the battlefield on this turn", "lands (average)",
        MEASURED, "over {n} test games", SOLO),
    "curve_cards_seen": Label(
        "different cards seen from the deck by this turn",
        "cards (average)", MEASURED, "over {n} test games", SOLO),
    "curve_avg_mv_cast": Label(
        "average mana value of spells cast on this turn",
        "mana value", MEASURED, "over {n} test games", SOLO),
    # ---- composition ---------------------------------------------------
    "casts_per_game": Label(
        "spells that left the hand and were cast in an average test "
        "game", "spells per game", MEASURED, "over {n} test games",
        SOLO + "; a cast spell's effects are never simulated — only "
        "the cast is counted"),
    "fresh_cast_rate": Label(
        "casts that happened on the very turn the card was drawn",
        "% of all casts", MEASURED, "over {n} test games", SOLO),
    "casts_by_type": Label(
        "share of all casts that were this card type",
        "% of casts", MEASURED, "over {n} test games", SOLO),
    "casts_by_pip_color": Label(
        "share of all casts whose mana cost includes this color",
        "% of casts", MEASURED, "over {n} test games", SOLO),
    "mana_spent_by_color": Label(
        "share of all mana spent that paid for this color",
        "% of mana spent", MEASURED, "over {n} test games", SOLO),
    "casts_by_mv": Label(
        "share of all casts at this mana value (the curve the deck "
        "actually played, not the one printed in the decklist)",
        "% of casts", MEASURED, "over {n} test games", SOLO),
    "casts_by_tag": Label(
        "spells carrying this Scryfall function tag that were cast",
        "casts per game", MEASURED, "over {n} test games",
        SOLO + "; tags are counted only — a cast draw spell draws "
        "nothing in the simulation"),
    # ---- lands & mana facts --------------------------------------------
    "tapped_plays_per_game": Label(
        "land plays that entered the battlefield tapped",
        "land plays per game", MEASURED, "over {n} test games", SOLO),
    "tapped_shortfalls_per_game": Label(
        "turns where a land entering tapped left the deck short of "
        "mana it otherwise had", "events per game", MEASURED,
        "over {n} test games", SOLO),
    "fetch_uses_per_game": Label(
        "fetch lands (Evolving Wilds family) used to search for a "
        "basic land", "fetches per game", MEASURED,
        "over {n} test games", SOLO),
    "fetch_colors": Label(
        "colors of basic land the fetches chose, over all games",
        "fetches", MEASURED, "over {n} test games", SOLO),
    "flood": Label(
        "games that ended with 3 or more lands still in hand",
        "% of games", MEASURED, "out of {n} test games", SOLO),
    "lands_in_final_hand": Label(
        "lands left in hand at the end of the game",
        "lands (average)", MEASURED, "over {n} test games", SOLO),
    "priced_out_turns": Label(
        "turns with cards in hand where nothing in hand could be "
        "paid for", "turns per game", MEASURED, "over {n} test games",
        SOLO),
    "cleanup_discards": Label(
        "cards discarded at end of turn for having more than 7 in "
        "hand", "cards per game", MEASURED, "over {n} test games", SOLO),
    "no_missed_drop": Label(
        "games with no missed land drop through this turn",
        "% of games", MEASURED, "out of {n} test games", SOLO),
    # ---- per-card --------------------------------------------------------
    "per_card_seen": Label(
        "games where this card was in the opening hand or drawn",
        "% of games", MEASURED, "out of {n} test games", SOLO),
    "per_card_cast": Label(
        "games where this card was cast at least once",
        "% of games", MEASURED, "out of {n} test games", SOLO),
    "per_card_cast_by": Label(
        "games where this card had been cast by this turn",
        "% of games", MEASURED, "out of {n} test games", SOLO),
    "per_card_median_cast_turn": Label(
        "the middle turn this card was cast on, over games where it "
        "was cast at all", "turns", MEASURED, "games where it was cast",
        SOLO),
    "per_card_stranded": Label(
        "games where this card was seen in hand but never cast and "
        "never discarded", "% of games", MEASURED,
        "out of {n} test games", SOLO),
    "per_card_stranded_reason": Label(
        "stranded games split by the counted reason: at no point was "
        "the card payable while in hand ('never had the mana'), or "
        "the game ended / the cast plan never picked it ('sim ended')",
        "games", MEASURED, "out of {n} test games", SOLO),
    "per_card_discarded_bottomed": Label(
        "games where this card was discarded to hand size, or "
        "bottomed in a mulligan", "games", MEASURED,
        "out of {n} test games", SOLO),
    "trapped_turns": Label(
        "turns this card sat in hand while the deck's mana could "
        "have paid for it but the cast plan spent the mana elsewhere",
        "turns per game (average)", MEASURED, "over {n} test games",
        SOLO),
    "pitch_rate": Label(
        "share of this card's appearances where it was discarded or "
        "bottomed instead of kept toward casting",
        "% of appearances", MEASURED, "over {n} test games", SOLO),
    # ---- Tier 1 models ---------------------------------------------------
    "mue": Label(
        "share of available mana actually spent inside this turn "
        "window (pooled = all mana in the window; per-game = each "
        "game's own share, then averaged)",
        "% of mana", MEASURED, "over {n} test games", SOLO),
    "castability_rate": Label(
        "games where the game's mana could have paid this spell on "
        "its earliest possible turn (its mana value), judging the "
        "spell on its own against the untapped mana at that point "
        "of the same game", "% of games", MEASURED,
        "out of {n} test games", SOLO),
    "castability_delay": Label(
        "turns between this spell's earliest possible turn and when "
        "it first became payable; games where it never became "
        "payable count as one turn past the end (a lower bound)",
        "turns (average)", MEASURED, "over {n} test games", SOLO),
    "castability_never": Label(
        "games where this spell never became payable at all",
        "% of games", MEASURED, "out of {n} test games", SOLO),
    "hypergeometric": Label(
        "exact chance of having seen at least k cards of this "
        "category after seeing n cards of the library",
        "% chance", FORMULA,
        "calculated exactly from the decklist counts — no games played"),
    "perturbation_cast_rate": Label(
        "average on-curve cast rate across the deck's spells for "
        "this variant of the decklist", "% of spells", MEASURED,
        "the same {n} games with the same seeds as the base deck — "
        "only the lands changed"),
    "perturbation_deltas": Label(
        "difference from the base deck in the same paired games",
        "percentage points / turns", MEASURED,
        "paired games, same seeds"),
    "tapland_tempo_tax": Label(
        "the cost of tapped lands, measured by the paired variant "
        "where every tapped land became an untapped basic",
        "percentage points of cast rate / turns of delay", MEASURED,
        "paired games, same seeds"),
    "paired_test": Label(
        "a statistical test on the differences between the same "
        "games played under two settings (same seeds) — the "
        "p-value is the chance of a difference at least this large "
        "if the settings truly made no difference",
        "a p-value from 0 to 1", RULE, "paired games, same seeds"),
    "mann_whitney": Label(
        "a statistical test comparing two separate batches of games "
        "(not paired) — the p-value is the chance of a split at "
        "least this large if the decks truly performed the same",
        "a p-value from 0 to 1", RULE, "two independent sets of games"),
    # ---- regime deltas ---------------------------------------------------
    "regime_delta_turns": Label(
        "how much later or earlier the commander was first cast "
        "under this stress setting than in normal games",
        "turns (average difference)", MEASURED,
        "the same {n} games with the same seeds under both settings"),
    "regime_delta_tax": Label(
        "difference in commander tax paid under this stress setting",
        "mana per game (average difference)", MEASURED,
        "paired games, same seeds"),
    "regime_delta_power": Label(
        "difference in total board power on this turn under this "
        "stress setting (board power — not a win prediction)",
        "power (average difference)", MEASURED,
        "paired games, same seeds"),
    "d1_recast": Label(
        "after the commander was removed once: games where it was "
        "cast again, and how many turns that took",
        "% of removals / turns", MEASURED, "over {n} test games", SOLO),
    "d2_rebuild": Label(
        "after the turn-6 board wipe: turns until total power "
        "returned to its pre-wipe level, turns until spells per turn "
        "recovered, and what was spent during the rebuild",
        "turns / mana / cards", MEASURED, "over {n} test games", SOLO),
    # ---- end of game -------------------------------------------------------
    "end_power": Label(
        "total power of creatures on the battlefield at the end of "
        "the final turn (board power — not a win prediction)",
        "power (average and middle game)", MEASURED,
        "over {n} test games", SOLO),
    "end_creatures": Label(
        "creatures on the battlefield at the end of the final turn",
        "creatures (average)", MEASURED, "over {n} test games", SOLO),
    "end_perms_by_type": Label(
        "permanents of this type on the battlefield at the end of "
        "the final turn", "permanents (average)", MEASURED,
        "over {n} test games", SOLO),
    "end_cards_seen": Label(
        "different cards seen from the deck in a whole game, and "
        "that as a share of the whole library",
        "cards (average) / % of the library", MEASURED,
        "over {n} test games", SOLO),
}

# Every figure the primary (non-dump) tables of report.py and
# dashboard.py display must have a registry entry; the test suite
# checks this list against the registry.
DISPLAYED_KEYS = [
    "library_size", "land_count", "trials", "turns_simulated", "seed",
    "keep_rule", "data_date",
    "karsten_lands", "karsten_sources", "sources_counted",
    "karsten_deficit", "avg_mv_spells", "cheap_ramp", "cantrips",
    "mulligan_stage", "keep_rate_stage", "final_hand_size",
    "by_final_size_spells", "by_final_size_commander",
    "by_final_size_mana_util",
    "first_event", "first_creature_detail",
    "commander_recasts", "commander_turns_in_play", "commander_tax",
    "commander_mana_share", "commander_cast_by_turn",
    "curve_spells", "curve_cum_spells", "curve_mana_available",
    "curve_mana_spent", "curve_mana_utilization", "curve_unspent",
    "curve_creatures", "curve_power", "curve_toughness", "curve_hand",
    "curve_lands", "curve_cards_seen", "curve_avg_mv_cast",
    "casts_per_game", "fresh_cast_rate", "casts_by_type",
    "casts_by_pip_color", "mana_spent_by_color", "casts_by_mv",
    "casts_by_tag",
    "tapped_plays_per_game", "tapped_shortfalls_per_game",
    "fetch_uses_per_game", "fetch_colors", "flood",
    "lands_in_final_hand", "priced_out_turns", "cleanup_discards",
    "no_missed_drop",
    "per_card_seen", "per_card_cast", "per_card_cast_by",
    "per_card_median_cast_turn", "per_card_stranded",
    "per_card_stranded_reason", "per_card_discarded_bottomed",
    "trapped_turns", "pitch_rate",
    "mue", "castability_rate", "castability_delay", "castability_never",
    "hypergeometric", "perturbation_cast_rate", "perturbation_deltas",
    "tapland_tempo_tax", "paired_test", "mann_whitney",
    "regime_delta_turns", "regime_delta_tax", "regime_delta_power",
    "d1_recast", "d2_rebuild",
    "end_power", "end_creatures", "end_perms_by_type", "end_cards_seen",
]


def label(key: str) -> Label:
    return LABELS[key]


def kind_text(kind: str) -> str:
    return KIND_TEXT[kind]


def caption(key: str, n: int | None = None) -> str:
    """One-line full label for a metric: what was counted — unit;
    sample; kind; conditions. `{n}` in the sample text is replaced
    with the run's game count when given."""
    lab = LABELS[key]
    sample = lab.sample
    if "{n}" in sample:
        sample = sample.format(n=n if n is not None else "all")
    parts = [f"{lab.what} — {lab.unit}"]
    if sample:
        parts.append(sample)
    parts.append(kind_text(lab.kind))
    if lab.conditions:
        parts.append(lab.conditions)
    return "; ".join(parts) + "."
