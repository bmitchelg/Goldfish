"""The goldfish simulator: Brandon's 10-step play policy, exactly.

Turn procedure (per spec):
 1. Play one land if in hand (fetch lands resolve immediately).
 2. Count untapped mana by color.
 3. Commander on the battlefield? YES -> skip to 7. NO -> 4.
 4. Compare commander cost (amount + colors, tax included) vs sources.
 5. Cast the commander if payable. (Judged ONLY on the step-2 count;
    nothing later in the turn reopens this — Sol Ring included.)
 6. Re-check (falls out of the flow).
 7. Recount usable mana by color.
 8. Check each hand card's cost.
 9. Calculate the maximum number of cards castable (colors respected,
    sources assigned to pips by the solver).
10. Cast them one by one. Sol Ring's mana re-runs steps 7-9.

Also modeled, per the Comprehensive Rules digest:
 - London mulligans exactly (103.5): full-7 redraws, bottom N.
 - Commander tax counts casts from the command zone only (903.8); the
   sim only ever casts the commander from the zone.
 - Cleanup discard (514.1): end of turn, discard down to 7. Policy
   (disclosed assumption): highest mana value first; never discard a
   land while a nonland can be discarded; ties by card name.
 - Turn-1 draw: multiplayer-pod assumption (103.8c).
 - Mana is recomputed per casting window (106.4/500.5).

Tags never cause game-state effects (counting-only principle).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .cards import Card, Cost, COLOR_ORDER
from .keeprules import KeepConfig
from .manasolver import pay_plan, can_pay, max_cast_plan, payment_color

PERMANENT_TYPES = {"Creature", "Artifact", "Enchantment", "Planeswalker", "Battle"}
REGIMES = ("D0", "D1", "D2", "D3")
WIPE_TURN = 6


class UnitState:
    """One mana from one source, for one turn."""
    __slots__ = ("colors", "used")

    def __init__(self, colors):
        self.colors = colors
        self.used = False


@dataclass
class HCard:
    card: Card
    drawn_turn: int  # 0 = kept opening hand


@dataclass
class Permanent:
    card: Card
    cast_turn: int
    is_commander: bool = False


@dataclass
class LandInPlay:
    card: Card
    entered_turn: int
    tapped_on_entry: bool
    fetched: bool


@dataclass
class CastEvent:
    turn: int
    name: str
    type_key: str
    types: tuple
    mv: int
    colors: tuple          # cost-pip colors, sorted WUBRG
    tags: tuple
    mana_spent: int
    spent_colors: tuple
    is_commander: bool
    drawn_turn: int
    power: int | None
    toughness: int | None


@dataclass
class Snapshot:
    turn: int
    spells_cast: int
    mana_available: int
    mana_spent: int
    lands: int
    creatures: int
    power: int
    toughness: int
    perms_by_type: dict
    sources_by_color: dict
    hand_size: int
    cards_seen: int
    commander_in_play: bool
    land_colors: frozenset
    land_colors_nofetch: frozenset


@dataclass
class TrialResult:
    regime: str
    stages: list = field(default_factory=list)       # (label, kept)
    final_hand_size: int = 0
    bottomed: list = field(default_factory=list)     # card names
    kept_hand: list = field(default_factory=list)    # Cards
    thrown_hands: list = field(default_factory=list) # list[list[Card]]
    casts: list = field(default_factory=list)        # CastEvent
    land_plays: list = field(default_factory=list)   # (turn, name, tapped, fetched_color|None, is_fetch)
    snapshots: list = field(default_factory=list)    # Snapshot
    commander_cast_turns: list = field(default_factory=list)
    commander_tax_paid: int = 0
    removal_turn: int | None = None                  # D1
    wipe: tuple | None = None                        # (turn, pre_power, pre_spells)
    discards: list = field(default_factory=list)     # (turn, name)
    payable_names: set = field(default_factory=set)  # ever payable at a step 9 while in hand
    turn_units: list = field(default_factory=list)   # per turn: color sets of the step-2 untapped units (v4.0.2, for castability models)
    seen: dict = field(default_factory=dict)         # name -> {"drawn_turn","bottomed","discarded"}
    final_hand: list = field(default_factory=list)   # Cards
    priced_out: list = field(default_factory=list)   # (turn, tuple of MVs in hand)
    missed_drop_turns: list = field(default_factory=list)
    tapped_shortfalls: int = 0


def _stage_label(taken: int, free: int) -> str:
    if taken == 0:
        return "initial"
    if taken <= free:
        return f"free {taken}"
    return f"London {7 - (taken - free)}"


def _choose_bottom(hand: list[Card], n: int) -> list[Card]:
    """London bottoming policy (disclosed): highest mana value first,
    lands last; ties by name."""
    if n <= 0:
        return []
    ordered = sorted(hand, key=lambda c: (c.is_land, -c.mana_value, c.name))
    return ordered[:n]


def _taxed_cost(base: Cost, tax: int) -> Cost:
    return Cost(generic=base.generic + tax, pips=base.pips,
                colorless_pips=base.colorless_pips, twobrid=base.twobrid,
                x_count=base.x_count)


class _Game:
    def __init__(self, deck, cfg: KeepConfig, rng: random.Random,
                 regime: str, turns: int):
        self.deck = deck
        self.cfg = cfg
        self.rng = rng
        self.regime = regime
        self.turns = turns
        self.res = TrialResult(regime=regime)
        self.library: list[Card] = []
        self.hand: list[HCard] = []
        self.lands: list[LandInPlay] = []
        self.perms: list[Permanent] = []
        self.cmd_casts_from_zone = 1 if regime == "D3" else 0  # D3: tax starts at +2
        self.cmd_in_play = False
        self.cmd_first_cast: int | None = None
        self.d1_removed = False
        self.cards_seen = 0
        self.prev_tapped_colors: frozenset | None = None

    # -- opening ---------------------------------------------------------
    def _opening(self):
        res = self.res
        taken = 0
        while True:
            lib = self.deck.library[:]
            self.rng.shuffle(lib)
            hand7, rest = lib[:7], lib[7:]
            keeps = self.cfg.keeps(
                hand7, commander_identity=self.deck.commander.color_identity)
            res.stages.append((_stage_label(taken, self.cfg.free_mulligans), keeps))
            if keeps or taken >= self.cfg.max_mulligans:
                bottom_n = min(6, max(0, taken - self.cfg.free_mulligans))
                bottomed = _choose_bottom(hand7, bottom_n)
                bset = {id(c) for c in bottomed}
                kept = [c for c in hand7 if id(c) not in bset]
                self.library = rest + bottomed  # bottomed cards go to the bottom
                self.hand = [HCard(c, 0) for c in kept]
                res.bottomed = [c.name for c in bottomed]
                res.kept_hand = kept
                res.final_hand_size = len(kept)
                for c in kept:
                    res.seen.setdefault(c.name, {"drawn_turn": 0, "bottomed": False, "discarded": False})
                for c in bottomed:
                    e = res.seen.setdefault(c.name, {"drawn_turn": 0, "bottomed": False, "discarded": False})
                    e["bottomed"] = True
                self.cards_seen = len(kept) + len(bottomed)
                return
            res.thrown_hands.append(hand7)
            taken += 1

    # -- helpers -----------------------------------------------------------
    def _mark_seen(self, card: Card, turn: int):
        self.res.seen.setdefault(
            card.name, {"drawn_turn": turn, "bottomed": False, "discarded": False})

    def _needed_colors(self) -> set:
        need = set()
        for h in self.hand:
            if not h.card.is_land:
                need |= set(h.card.colors)
        if not self.cmd_in_play:
            need |= set(self.deck.commander.colors)
        return need

    def _library_basics(self) -> dict:
        """{color: a basic land card of that color} for basics actually
        remaining in the library right now (v4.1: fetch choices can
        only land on basics that are really there)."""
        out = {}
        for c in self.library:
            if c.is_land and "Basic" in c.supertypes:
                for col in c.produced_colors:
                    out.setdefault(col, c)
        return out

    def _pip_demand(self) -> dict:
        """Colored-pip demand: pips across the non-land cards in hand
        (a hybrid pip counts toward both of its colors) plus the
        commander's own cost pips (Brandon's fetch procedure, step 6 /
        logic fix (iii))."""
        from collections import Counter
        demand: Counter = Counter()
        cards = [h.card for h in self.hand if not h.card.is_land]
        cards.append(self.deck.commander)
        for card in cards:
            if card.cost is None:
                continue
            for pip in card.cost.pips:
                for col in pip:
                    demand[col] += 1
            for col in card.cost.twobrid:
                demand[col] += 1
        return demand

    def _covered_colors(self) -> set:
        """Colors producible from lands on the battlefield (tapped or
        untapped — a tapped source still produces its color) PLUS the
        colors of lands held in hand (v4.1 logic fix (i): a color you
        hold a land for is already covered)."""
        covered = set()
        for l in self.lands:
            covered |= set(l.card.produced_colors)
        for h in self.hand:
            if h.card.is_land:
                covered |= set(h.card.produced_colors)
        return covered

    def _fetch_color_choice(self):
        """Brandon's fetch-target procedure (decided 2026-10-31, with
        the three confirmed logic fixes):
          - candidates are colors of basics actually in the library;
          - "missing" = a commander COST color not producible from
            battlefield lands and not held as a land in hand;
          - color identity is the outer boundary, never the priority;
          - missing colors are ranked by pip demand (hand + the
            commander's cost), ties in WUBRG order;
          - if no cost color is missing, the choice is by the same
            pip-demand ranking among the identity's fetchable colors.
        Returns a color letter or None (fetch finds nothing)."""
        basics = self._library_basics()
        if not basics:
            return None
        identity = set(self.deck.commander.color_identity) & set(COLOR_ORDER)
        candidates = {c for c in basics if c in identity}
        demand = self._pip_demand()

        def rank(col):
            return (-demand.get(col, 0),
                    COLOR_ORDER.index(col) if col in COLOR_ORDER else 99)

        covered = self._covered_colors()
        cost_colors = set(self.deck.commander.colors) & set(COLOR_ORDER)
        missing = [c for c in cost_colors
                   if c not in covered and c in candidates]
        if missing:
            return min(missing, key=rank)
        if candidates:
            return min(candidates, key=rank)
        # No identity-colored basic remains (e.g. only Wastes): the
        # fetch may still take any basic actually in the library.
        return min(basics, key=rank)

    def _play_land(self, turn: int):
        """Step 1. Returns (played, entered_tapped, produced colors)."""
        res = self.res
        in_hand = [h for h in self.hand if h.card.is_land]
        if not in_hand:
            res.missed_drop_turns.append(turn)
            self.prev_tapped_colors = None
            return
        need = self._needed_colors()
        fetchable = set(self._library_basics())

        def score(h: HCard):
            c = h.card
            if c.is_fetch:
                cov = 1 if (fetchable & need) else 0
                return (1, -cov, c.name)  # fetch -> tapped basic: counts as tapped
            cov = len(set(c.produced_colors) & need)
            return (0 if not c.enters_tapped else 1, -cov, c.name)

        chosen = min(in_hand, key=score)
        self.hand.remove(chosen)
        card = chosen.card
        if card.is_fetch:
            # Brandon's fetch model: the fetch land taps and is
            # sacrificed (it never enters `self.lands` — it is gone);
            # the chosen basic is put onto the battlefield tapped;
            # then the library is shuffled (v4.1: the shuffle was
            # missing in v4.0.2 and is now modeled).
            color = self._fetch_color_choice()
            basic = self._library_basics().get(color) if color else None
            if basic is not None:
                self.library.remove(basic)
                self.lands.append(LandInPlay(basic, turn, True, True))
                res.land_plays.append((turn, card.name, True, color, True))
                self.prev_tapped_colors = frozenset(basic.produced_colors)
                self.rng.shuffle(self.library)
            else:
                res.land_plays.append((turn, card.name, True, None, True))
                self.prev_tapped_colors = None
            return
        self.lands.append(LandInPlay(card, turn, card.enters_tapped, False))
        res.land_plays.append((turn, card.name, card.enters_tapped, None, False))
        self.prev_tapped_colors = frozenset(card.produced_colors) if card.enters_tapped else None

    def _build_units(self, turn: int) -> list[UnitState]:
        units: list[UnitState] = []
        for l in self.lands:
            colors = l.card.produced_colors
            if not colors:
                continue
            if l.tapped_on_entry and l.entered_turn == turn:
                continue  # entered tapped this turn: unusable
            for _ in range(l.card.source_amount):
                units.append(UnitState(colors))
        for p in self.perms:
            colors = p.card.produced_colors
            if not colors:
                continue
            if p.cast_turn >= turn:
                continue  # non-land sources: usable from the turn AFTER cast
            for _ in range(p.card.source_amount):
                units.append(UnitState(colors))
        return units

    def _apply_payments(self, units, payments, key):
        spent_colors = []
        n = 0
        unit_by_idx = units
        for k, ui, kind, acc in payments:
            if k != key:
                continue
            u = unit_by_idx[ui]
            u.used = True
            n += 1
            spent_colors.append(payment_color(u, kind, acc))
        return n, spent_colors

    def _record_cast(self, turn: int, hcard: HCard, mana_spent: int,
                     spent_colors, is_commander: bool):
        c = hcard.card
        self.res.casts.append(CastEvent(
            turn=turn, name=c.name, type_key=c.type_key(),
            types=tuple(sorted(c.types)), mv=c.mana_value,
            colors=tuple(sorted(c.colors, key=lambda x: COLOR_ORDER.index(x) if x in COLOR_ORDER else 9)),
            tags=tuple(sorted(c.tags)), mana_spent=mana_spent,
            spent_colors=tuple(spent_colors), is_commander=is_commander,
            drawn_turn=hcard.drawn_turn, power=c.power, toughness=c.toughness))

    # -- main loop ---------------------------------------------------------
    def run(self) -> TrialResult:
        self._opening()
        res = self.res
        commander = self.deck.commander
        for turn in range(1, self.turns + 1):
            # D2: board wipe at the start of the wipe turn (creatures only,
            # commander exempt — disclosed choice).
            if self.regime == "D2" and turn == WIPE_TURN and res.wipe is None:
                pre_power = res.snapshots[-1].power if res.snapshots else 0
                prev_spells = [s.spells_cast for s in res.snapshots[-2:]]
                pre_spells = sum(prev_spells) / len(prev_spells) if prev_spells else 0.0
                self.perms = [p for p in self.perms
                              if p.is_commander or "Creature" not in p.card.types]
                res.wipe = (turn, pre_power, pre_spells)
            # D1: commander removed once, at the start of the turn after
            # its first cast.
            if (self.regime == "D1" and not self.d1_removed
                    and self.cmd_first_cast is not None
                    and turn == self.cmd_first_cast + 1 and self.cmd_in_play):
                self.perms = [p for p in self.perms if not p.is_commander]
                self.cmd_in_play = False
                self.d1_removed = True
                res.removal_turn = turn
            # Draw step (multiplayer assumption: draw on turn 1, CR 103.8c).
            if self.library:
                c = self.library.pop(0)
                self.hand.append(HCard(c, turn))
                self._mark_seen(c, turn)
                self.cards_seen += 1
            # Steps 1-2
            self._play_land(turn)
            units = self._build_units(turn)
            # v4.0.2: record the step-2 unit colors (pre-spend) for the
            # castability models (T1-07/T1-08). Pure recording — no RNG
            # or policy effect.
            res.turn_units.append([frozenset(u.colors) for u in units])
            mana_available = len(units)
            mana_spent = 0
            spells_this_turn = 0
            # Steps 3-6: commander (skipped entirely if already in play).
            if not self.cmd_in_play:
                tax = 2 * self.cmd_casts_from_zone
                cost = _taxed_cost(commander.cost, tax)
                pay = pay_plan(units, {"cmd": cost})
                if pay is not None:
                    n, spent_colors = self._apply_payments(units, pay, "cmd")
                    mana_spent += n
                    spells_this_turn += 1
                    res.commander_tax_paid += tax
                    self.cmd_casts_from_zone += 1
                    self.cmd_in_play = True
                    if self.cmd_first_cast is None:
                        self.cmd_first_cast = turn
                    res.commander_cast_turns.append(turn)
                    self.perms.append(Permanent(commander, turn, True))
                    self._record_cast(turn, HCard(commander, 0), n, spent_colors, True)
            # Steps 7-9 preparation: snapshot the recount for payable checks.
            step7_units = [UnitState(u.colors) for u in units if not u.used]
            for h in self.hand:
                if not h.card.is_land and can_pay(step7_units, h.card.cost) is not None:
                    res.payable_names.add(h.card.name)
            # Steps 9-10: plan, cast one by one, re-plan after each cast
            # (mandatory after Sol Ring; harmless otherwise — the max-count
            # invariant is preserved because we always pick from an optimal plan).
            step10_casts = 0
            while True:
                live_units = [u for u in units if not u.used]
                costs = {}
                mvs = {}
                hand_index = {}
                for i, h in enumerate(self.hand):
                    if h.card.is_land:
                        continue
                    costs[i] = h.card.cost
                    mvs[i] = h.card.mana_value
                    hand_index[i] = h
                if not costs:
                    break
                # Sol Ring exception, applied first: a purely static step-9
                # plan could strand the Ring (a bigger spell wins the
                # most-mana tie-break), yet casting it can only grow the
                # pool (+2 for 1) and never reduces the max cast count.
                # So: if it is payable, cast it now, then re-plan (steps 7-9).
                sr_pick = None
                for i, h in hand_index.items():
                    if h.card.name == "Sol Ring":
                        sr_pay = pay_plan(live_units, {i: h.card.cost})
                        if sr_pay is not None:
                            sr_pick = (i, sr_pay)
                        break
                if sr_pick is not None:
                    pick, payments = sr_pick
                else:
                    plan, payments = max_cast_plan(live_units, costs, mvs)
                    if not plan:
                        break
                    pick = plan[0]
                n, spent_colors = self._apply_payments(live_units, payments, pick)
                mana_spent += n
                spells_this_turn += 1
                hcard = hand_index[pick]
                self.hand.remove(hcard)
                card = hcard.card
                self._record_cast(turn, hcard, n, spent_colors, False)
                step10_casts += 1
                if card.types & PERMANENT_TYPES:
                    self.perms.append(Permanent(card, turn, False))
                if card.name == "Sol Ring":
                    # Exception: usable the same turn it is cast (2 colorless).
                    units.append(UnitState(frozenset("C")))
                    units.append(UnitState(frozenset("C")))
                    mana_available += 2
            if step10_casts == 0:
                rest_mvs = tuple(sorted(h.card.mana_value for h in self.hand
                                        if not h.card.is_land))
                if rest_mvs:
                    res.priced_out.append((turn, rest_mvs))
            # Tapped-land shortfall (spec E32), for the land that entered
            # tapped LAST turn: with one extra mana of its color at this
            # turn's step 7, would a leftover card have been payable?
            if getattr(self, "_pending_tapped", None) is not None:
                colors, _t = self._pending_tapped
                shortfall = False
                for h in self.hand:
                    if h.card.is_land:
                        continue
                    if can_pay(step7_units, h.card.cost) is None:
                        for col in colors:
                            extra = step7_units + [UnitState(frozenset(col))]
                            if can_pay(extra, h.card.cost) is not None:
                                shortfall = True
                                break
                    if shortfall:
                        break
                if shortfall:
                    res.tapped_shortfalls += 1
                self._pending_tapped = None
            # Cleanup step (CR 514.1): discard down to 7.
            if len(self.hand) > 7:
                excess = len(self.hand) - 7
                ordered = sorted(self.hand,
                                 key=lambda h: (h.card.is_land, -h.card.mana_value, h.card.name))
                for h in ordered[:excess]:
                    self.hand.remove(h)
                    res.discards.append((turn, h.card.name))
                    e = res.seen.get(h.card.name)
                    if e is not None:
                        e["discarded"] = True
            self._snapshot(turn, spells_this_turn, mana_available, mana_spent)
            # Stash this turn's tapped land; its shortfall is evaluated
            # during next turn's casting window (above).
            self._pending_tapped = (self.prev_tapped_colors, turn) if self.prev_tapped_colors else None
        res.final_hand = [h.card for h in self.hand]
        return res

    def _snapshot(self, turn: int, spells_cast: int, mana_available: int, mana_spent: int):
        perms_by_type: dict[str, int] = {}
        creatures = 0
        power = 0
        toughness = 0
        for p in self.perms:
            for t in p.card.types:
                perms_by_type[t] = perms_by_type.get(t, 0) + 1
            if "Creature" in p.card.types:
                creatures += 1
                power += p.card.power or 0
                toughness += p.card.toughness or 0
        sources_by_color: dict[str, int] = {}
        for l in self.lands:
            for col in l.card.produced_colors:
                sources_by_color[col] = sources_by_color.get(col, 0) + 1
        for p in self.perms:
            for col in p.card.produced_colors:
                sources_by_color[col] = sources_by_color.get(col, 0) + 1
        land_colors = set()
        land_colors_nf = set()
        for l in self.lands:
            land_colors |= set(l.card.produced_colors)
            if not l.fetched:
                land_colors_nf |= set(l.card.produced_colors)
        self.res.snapshots.append(Snapshot(
            turn=turn, spells_cast=spells_cast, mana_available=mana_available,
            mana_spent=mana_spent, lands=len(self.lands), creatures=creatures,
            power=power, toughness=toughness, perms_by_type=perms_by_type,
            sources_by_color=sources_by_color, hand_size=len(self.hand),
            cards_seen=self.cards_seen, commander_in_play=self.cmd_in_play,
            land_colors=frozenset(land_colors),
            land_colors_nofetch=frozenset(land_colors_nf)))


def run_trials_iter(deck, cfg: KeepConfig, n_trials: int, seed: int,
                    regime: str = "D0", turns: int = 12):
    """Yield TrialResults one at a time (v4.0.2: lets batch runs stream
    each finished game to disk). Identical seeds to run_trials."""
    for i in range(n_trials):
        rng = random.Random(seed * 1_000_003 + i)
        yield _Game(deck, cfg, rng, regime, turns).run()


def run_trials(deck, cfg: KeepConfig, n_trials: int, seed: int,
               regime: str = "D0", turns: int = 12) -> list[TrialResult]:
    """Common-seed pairing: trial i uses the same seed in every regime."""
    return list(run_trials_iter(deck, cfg, n_trials, seed, regime, turns))
