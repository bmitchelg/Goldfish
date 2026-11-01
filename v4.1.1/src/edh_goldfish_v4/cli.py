"""goldfish4 CLI: analyze a Commander decklist, or run the manabase audit.

Examples:
  goldfish4 analyze deck.txt --trials 1000 --out report.md --json report.json
  goldfish4 manabase deck.txt
  goldfish4 refresh-data [--if-due]

Data comes from Scryfall's offline bulk files when present in the data
dir (default ~/workspace/mtg/goldfish-cli/data/bulk); otherwise the
session falls back to the live API. See bulkdata.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .bulkdata import DEFAULT_DATA_DIR, check_due, download_bulk
from .keeprules import config_from_dict, default_config
from .measurements import aggregate, manabase_audit, regime_deltas
from .parser import build_deck, parse_decklist
from .report import render
from .scryfall import FUNCTION_TAGS, ScryfallSession
from .simulator import REGIMES, run_trials

V4_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_deck(path: str, session: ScryfallSession, with_tags: bool = True):
    with open(path) as f:
        dl = parse_decklist(f.read())
    if not dl.commander_names:
        raise SystemExit("decklist has no commander section (use a '// Commander' header)")
    names = sorted({n for _, n in dl.mainboard} | set(dl.commander_names))
    card_jsons = session.fetch_cards(names)
    tag_map = session.fetch_all_tags() if with_tags else {}
    name = os.path.splitext(os.path.basename(path))[0]
    return build_deck(dl, card_jsons, tag_map, name=name)


def _keep_config(args) -> object:
    if args.keep_config:
        with open(args.keep_config) as f:
            return config_from_dict(json.load(f))
    return default_config()


def _regimes_arg(args) -> list:
    """Resolve the regime selection (v4.1): --regimes picks the set;
    --no-d1 / --no-d2 switch D1 / D2 off (spec item 5). D3 is NEVER
    toggleable (settled by Brandon, 2026-10-31): asking to disable it
    is an error, as is an unknown regime token. Returns the enabled
    regimes in canonical order."""
    if getattr(args, "no_d3", False):
        raise SystemExit("--no-d3 refused: the D3 tax regime is not "
                         "toggleable (settled by Brandon, 2026-10-31)")
    regimes = [r.strip() for r in args.regimes.split(",") if r.strip()]
    for r in regimes:
        if r not in REGIMES:
            raise SystemExit(f"unknown regime: {r} "
                             f"(valid: {', '.join(REGIMES)})")
    disabled = set()
    if getattr(args, "no_d1", False):
        disabled.add("D1")
    if getattr(args, "no_d2", False):
        disabled.add("D2")
    return [r for r in REGIMES if r in regimes and r not in disabled]


def _regime_flags(p) -> None:
    """Shared regime arguments for analyze and batch."""
    p.add_argument("--regimes", default=",".join(REGIMES))
    p.add_argument("--no-d1", action="store_true",
                   help="switch off the D1 regime (commander removed "
                        "once) for this run; D1 is on by default")
    p.add_argument("--no-d2", action="store_true",
                   help="switch off the D2 regime (board wipe) for "
                        "this run; D2 is on by default")
    p.add_argument("--no-d3", action="store_true",
                   help="REFUSED if used: D3 (commander tax) is not "
                        "toggleable — it always runs when selected")


def _session(args) -> ScryfallSession:
    log = lambda *a: print(*a, file=sys.stderr)
    return ScryfallSession(
        cache_dir=None if args.no_cache else os.path.join(V4_DIR, ".cardcache"),
        log=log,
        data_dir=None if args.no_bulk else args.data_dir)


def cmd_analyze(args) -> int:
    log = lambda *a: print(*a, file=sys.stderr)
    session = _session(args)
    deck = _load_deck(args.deck, session)
    cfg = _keep_config(args)
    regimes = _regimes_arg(args)
    trials_by_regime = {}
    agg_by_regime = {}
    for regime in regimes:
        log(f"running {args.trials} trials, regime {regime}...")
        trials_by_regime[regime] = run_trials(
            deck, cfg, args.trials, args.seed, regime=regime, turns=args.turns)
        agg_by_regime[regime] = aggregate(deck, trials_by_regime[regime], args.turns)
    deltas = {}
    if "D0" in trials_by_regime:
        for regime in regimes:
            if regime != "D0":
                deltas[regime] = regime_deltas(
                    trials_by_regime["D0"], trials_by_regime[regime],
                    args.turns, args.seed)
    payload = {
        "version": __version__,
        "data_source": session.data_disclosure(),
        "deck": {"name": deck.name, "commander": deck.commander.name,
                 "library_size": len(deck.library),
                 "land_count": sum(1 for c in deck.library if c.is_land)},
        "trials": args.trials, "turns": args.turns, "seed": args.seed,
        "regimes_enabled": regimes,
        "regimes_disabled": [r for r in ("D1", "D2") if r not in regimes],
        "keep_rule": cfg.describe(),
        "manabase": manabase_audit(deck),
        "regimes": agg_by_regime,
        "deltas": deltas,
    }
    md = render(payload)
    if args.out:
        with open(args.out, "w") as f:
            f.write(md)
        log(f"wrote {args.out}")
    else:
        print(md)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(payload, f, indent=1, default=str)
        log(f"wrote {args.json}")
    return 0


def cmd_batch(args) -> int:
    """v4.0.2: run a batch and stream one self-contained record per
    finished game into the --out JSON file (valid after every game —
    see batch.py). Render the result with goldfish4-dashboard."""
    from . import batch as batch_mod
    from .cards import card_from_scryfall

    log = lambda *a: print(*a, file=sys.stderr)
    session = _session(args)
    with open(args.deck, "rb") as f:
        decklist_bytes = f.read()
    dl = parse_decklist(decklist_bytes.decode("utf-8"))
    if not dl.commander_names:
        raise SystemExit("decklist has no commander section (use a '// Commander' header)")
    extra_names = ["Wastes", "Plains", "Island", "Swamp", "Mountain", "Forest"]
    names = sorted({n for _, n in dl.mainboard} | set(dl.commander_names)
                   | set(extra_names))
    card_jsons = session.fetch_cards(names)
    tag_map = session.fetch_all_tags()
    name = os.path.splitext(os.path.basename(args.deck))[0]
    deck = build_deck(dl, card_jsons, tag_map, name=name)
    extra_cards = {}
    for nm in extra_names:
        c = card_from_scryfall(card_jsons[nm])
        c.tags = frozenset(t for t, oids in tag_map.items()
                           if c.oracle_id in oids)
        extra_cards[nm] = c
    cfg = _keep_config(args)
    regimes = _regimes_arg(args)
    variants = batch_mod.build_variants(deck, extra_cards,
                                        args.perturbations)
    meta = batch_mod.build_meta(
        deck, variants,
        source_filename=os.path.basename(args.deck),
        decklist_bytes=decklist_bytes,
        data_disclosure=session.data_disclosure(), cfg=cfg,
        seed=args.seed, turns=args.turns, games=args.games,
        regimes=regimes)

    def progress(done, total):
        if done % 100 == 0 or done == total:
            log(f"batch: {done}/{total} games written")

    n = batch_mod.run_batch(variants, cfg, args.games, args.seed, regimes,
                            args.turns, meta, args.out, progress=progress)
    log(f"wrote {args.out} ({n} games, {len(variants)} variant(s) x "
        f"{len(regimes)} regime(s))")
    # v4.1 (spec item 7): every batch is archived as the deck's next
    # version under <data-dir>/../versions/<deck-slug>/v<N>/
    # (v4.1.1: the archive is a sibling of the bulk data dir).
    from . import archive as archive_mod
    try:
        rec = archive_mod.archive_batch(
            args.data_dir, args.out, args.deck,
            deck.name, deck.commander.name)
        log(f"archived as version v{rec['version']} of "
            f"{rec['slug']} ({rec['dir']})")
    except OSError as e:
        log(f"WARNING: version archiving failed ({e}); the batch file "
            f"at {args.out} is intact but this run is not archived")
    return 0


def cmd_manabase(args) -> int:
    session = _session(args)
    print(session.data_disclosure(), file=sys.stderr)
    deck = _load_deck(args.deck, session)
    audit = {"data_source": session.data_disclosure(), **manabase_audit(deck)}
    print(json.dumps(audit, indent=1))
    return 0


def cmd_versions(args) -> int:
    """v4.1: list archived deck versions (spec item 7)."""
    from . import archive as archive_mod
    slugs = ([args.deck_slug] if getattr(args, "deck_slug", None)
             else archive_mod.list_decks(args.data_dir))
    if not slugs:
        print("no archived versions yet (run `goldfish4 batch` first)")
        return 0
    for slug in slugs:
        versions = archive_mod.list_versions(args.data_dir, slug)
        if not versions:
            print(f"{slug}: no archived versions")
            continue
        first = versions[0]["meta"]
        print(f"{slug} — {first.get('deck_name', '?')} "
              f"(commander {first.get('commander', '?')})")
        for v in versions:
            m = v["meta"]
            print(f"  v{v['version']}: archived {m.get('archived_at', '?')}"
                  f" from {m.get('batch_source', '?')}")
    return 0


def cmd_refresh_data(args) -> int:
    if args.if_due:
        due, reason = check_due(args.data_dir)
        if not due:
            print(f"refresh-data: not due — {reason}")
            return 0
        print(f"refresh-data: due — {reason}")
    try:
        meta = download_bulk(args.data_dir, log=print)
    except Exception as e:
        print(f"refresh-data: FAILED — {e}", file=sys.stderr)
        return 1
    for t, info in meta.items():
        print(f"refresh-data: {t} updated_at {info['updated_at']} "
              f"downloaded to {args.data_dir}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="goldfish4",
                                description=f"EDH-Goldfish CLI v{__version__}")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("analyze", help="run goldfish trials and emit a report")
    pa.add_argument("deck")
    pa.add_argument("--trials", type=int, default=1000)
    pa.add_argument("--turns", type=int, default=12)
    pa.add_argument("--seed", type=int, default=12345)
    _regime_flags(pa)
    pa.add_argument("--keep-config", help="JSON file with keep-rule config")
    pa.add_argument("--out", help="markdown report path (default: stdout)")
    pa.add_argument("--json", help="also write the full results as JSON")
    pa.add_argument("--no-cache", action="store_true",
                    help="do not use the raw Scryfall card-JSON disk cache")
    pa.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                    help="Scryfall bulk-data dir (offline when files present)")
    pa.add_argument("--no-bulk", action="store_true",
                    help="ignore bulk data; fetch live from the Scryfall API")
    pa.set_defaults(fn=cmd_analyze)

    pm = sub.add_parser("manabase", help="Karsten manabase audit only (JSON)")
    pm.add_argument("deck")
    pm.add_argument("--no-cache", action="store_true")
    pm.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                    help="Scryfall bulk-data dir (offline when files present)")
    pm.add_argument("--no-bulk", action="store_true",
                    help="ignore bulk data; fetch live from the Scryfall API")
    pm.set_defaults(fn=cmd_manabase)

    pb = sub.add_parser("batch",
                        help="run a batch, streaming one record per game "
                             "into a JSON file (v4.0.2)")
    pb.add_argument("deck")
    pb.add_argument("--games", type=int, default=1000,
                    help="games per variant x regime configuration")
    pb.add_argument("--out", required=True,
                    help="batch JSON path — required: you choose the "
                         "location and filename")
    pb.add_argument("--turns", type=int, default=12)
    pb.add_argument("--seed", type=int, default=12345)
    _regime_flags(pb)
    pb.add_argument("--perturbations", action="store_true",
                    help="add the perturbation variants (+1 Wastes, "
                         "+1 basic of the deck's most-pipped color, "
                         "taplands become basics) to the same batch")
    pb.add_argument("--keep-config", help="JSON file with keep-rule config")
    pb.add_argument("--no-cache", action="store_true",
                    help="do not use the raw Scryfall card-JSON disk cache")
    pb.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                    help="Scryfall bulk-data dir (offline when files present)")
    pb.add_argument("--no-bulk", action="store_true",
                    help="ignore bulk data; fetch live from the Scryfall API")
    pb.set_defaults(fn=cmd_batch)

    pv = sub.add_parser("versions",
                        help="list archived deck versions (v4.1)")
    pv.add_argument("deck_slug", nargs="?", default=None,
                    help="only this deck's slug (default: all decks)")
    pv.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                    help="Scryfall bulk-data dir (the version archive "
                         "lives in the 'versions' folder beside it)")
    pv.set_defaults(fn=cmd_versions)

    pr = sub.add_parser("refresh-data",
                        help="download Scryfall bulk data (oracle_cards + "
                             "oracle_tags) for offline use")
    pr.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    pr.add_argument("--if-due", action="store_true",
                    help="download only when a Standard release's refresh "
                         "window (release minus 4 days) has arrived and the "
                         "local copy predates it")
    pr.set_defaults(fn=cmd_refresh_data)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
