"""Command-line interface for Recommend-It."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_DATA = os.environ.get("RECOMMEND_IT_DATA", "data/movies.csv.gz")
COMMANDS = {"recommend", "watch", "search", "build-data"}


def _engine(args):
    from .engine import MovieRecommender

    return MovieRecommender(args.data)


def _fmt_movie(m, i=None) -> str:
    bits = [m.label]
    if m.rating is not None:
        bits.append(f"★ {m.rating:.1f}")
    if m.genres:
        bits.append(m.genres)
    if m.score is not None:
        bits.append(f"match {m.score:.2f}")
    prefix = f"{i:>3}. " if i is not None else ""
    return prefix + "  ·  ".join(bits)


def _fmt_watch(w, indent="       ") -> list[str]:
    lines = []
    free = w.free_offers
    paid = [o for o in w.offers if not o.is_free]
    if free:
        lines.append(f"{indent}🆓 Free: " + ", ".join(f"{o.provider} ({o.kind_label})" for o in free))
        for o in free:
            if o.url and "archive.org/details" in o.url:
                lines.append(f"{indent}   ▶ {o.url}")
    if paid:
        by_kind = {}
        for o in paid:
            by_kind.setdefault(o.kind_label, []).append(o.provider)
        lines.append(f"{indent}💳 " + "; ".join(f"{k}: {', '.join(v[:5])}" for k, v in by_kind.items()))
    if not w.offers:
        lines.append(f"{indent}No streaming offers found in {w.region}.")
    if w.tmdb_link:
        lines.append(f"{indent}🔗 {w.tmdb_link}")
    else:
        lines.append(f"{indent}🔎 " + "  ".join(f"{n}: {u}" for n, u in w.search_links))
    return lines


def cmd_recommend(args) -> int:
    engine = _engine(args)
    matched, recs = engine.recommend(args.movie, top_n=args.top)
    watch = []
    if args.watch:
        from .watch import where_to_watch_many

        watch = where_to_watch_many(recs, region=args.region)
        if args.free_only:
            keep = [i for i, w in enumerate(watch) if w.free_offers]
            recs, watch = [recs[i] for i in keep], [watch[i] for i in keep]

    if args.json:
        out = {"matched": matched.to_dict(), "recommendations": [r.to_dict() for r in recs]}
        if watch:
            for r, w in zip(out["recommendations"], watch):
                r["watch"] = w.to_dict()
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    print(f"\nBecause you liked {matched.label}, you might enjoy:\n")
    for i, r in enumerate(recs, 1):
        print(_fmt_movie(r, i))
        if watch:
            print("\n".join(_fmt_watch(watch[i - 1])))
    if not recs:
        print("  (nothing to show" + (" that streams free in your region" if args.free_only else "") + ")")
    if watch and watch[0].notes:
        print(f"\n  {watch[0].notes[0]}")
    print()
    return 0


def cmd_watch(args) -> int:
    from .watch import where_to_watch

    title, year, imdb_id, tmdb_id = args.movie, None, None, None
    if Path(args.data).exists():
        m, _ = _engine(args).resolve(args.movie)
        title, year, imdb_id, tmdb_id = m.title, m.year, m.imdb_id, m.tmdb_id
    w = where_to_watch(title, year, imdb_id, tmdb_id, region=args.region)
    if args.json:
        print(json.dumps(w.to_dict(), indent=2, ensure_ascii=False))
        return 0
    label = f"{title} ({year})" if year else title
    print(f"\nWhere to watch {label} in {w.region}:\n")
    print("\n".join(_fmt_watch(w, indent="  ")))
    for n in w.notes:
        print(f"\n  {n}")
    print()
    return 0


def cmd_search(args) -> int:
    hits = _engine(args).search(args.query, limit=args.limit)
    if args.json:
        print(json.dumps([h.to_dict() for h in hits], indent=2, ensure_ascii=False))
    elif not hits:
        print(f"No titles match '{args.query}'.")
    else:
        for i, h in enumerate(hits, 1):
            print(_fmt_movie(h, i) + (f"  ·  dir. {h.director}" if h.director else ""))
    return 0


def cmd_build(args) -> int:
    if args.source == "wikidata":
        from .wikidata import build as build_wikidata

        build_wikidata(out=Path(args.out), raw_dir=Path(args.raw_dir) / "wikidata", refresh=args.refresh)
        return 0

    from .dataset import build

    build(
        out=Path(args.out),
        raw_dir=Path(args.raw_dir),
        min_votes=args.min_votes,
        download_files=not args.no_download,
        refresh=args.refresh,
        enrich_top=args.tmdb_enrich,
    )
    return 0


def interactive(args) -> int:
    engine = _engine(args)
    print("Recommend-It: type a movie title (add 'Title (Year)' to be exact), or 'q' to quit.")
    while True:
        try:
            q = input("\n🎬 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if q.lower() in {"q", "quit", "exit"}:
            return 0
        if not q:
            continue
        try:
            matched, recs = engine.recommend(q, top_n=args.top)
        except ValueError as e:
            print(e)
            continue
        print(f"\nBecause you liked {matched.label}:")
        for i, r in enumerate(recs, 1):
            print(_fmt_movie(r, i))


def build_parser() -> argparse.ArgumentParser:
    from .watch import default_region

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data", default=DEFAULT_DATA, help=f"dataset CSV (default: {DEFAULT_DATA})")
    common.add_argument("--json", action="store_true", help="machine-readable JSON output")

    p = argparse.ArgumentParser(prog="recommend-it", description="Content-based movie recommendations + where to watch.")
    p.add_argument("--data", default=DEFAULT_DATA, help=argparse.SUPPRESS)
    p.add_argument("--top", type=int, default=10, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command")

    r = sub.add_parser("recommend", parents=[common], help="recommend films similar to one you like")
    r.add_argument("movie", help='title, optionally with year: "Dune (2021)"')
    r.add_argument("--top", type=int, default=10, help="number of recommendations (default: 10)")
    r.add_argument("--watch", action="store_true", help="also show where each film can be streamed")
    r.add_argument("--free-only", action="store_true", help="with --watch: keep only films streaming free")
    r.add_argument("--region", default=default_region(), help="2-letter country code (default: $RECOMMEND_IT_REGION or US)")
    r.set_defaults(func=cmd_recommend)

    w = sub.add_parser("watch", parents=[common], help="show where a film can be streamed (free options first)")
    w.add_argument("movie")
    w.add_argument("--region", default=default_region(), help="2-letter country code, e.g. IN, US, GB")
    w.set_defaults(func=cmd_watch)

    s = sub.add_parser("search", parents=[common], help="search titles in the dataset")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(func=cmd_search)

    b = sub.add_parser("build-data", help="build the film dataset (all films up to today)")
    b.add_argument("--source", choices=["imdb", "wikidata"], default="imdb",
                   help="imdb: fast, ratings included, personal use only. "
                        "wikidata: CC0, OK for public websites, ~1h build (default: imdb)")
    b.add_argument("--out", default=DEFAULT_DATA)
    b.add_argument("--raw-dir", default="data/raw", help="where the IMDb .tsv.gz dumps are stored")
    b.add_argument("--min-votes", type=int, default=50, help="skip films with fewer IMDb votes (default: 50)")
    b.add_argument("--refresh", action="store_true", help="re-download dumps even if present")
    b.add_argument("--no-download", action="store_true", help="use dumps already in --raw-dir")
    b.add_argument("--tmdb-enrich", type=int, default=0, metavar="N",
                   help="add plot/keywords from TMDB for the N most-voted films (needs TMDB_API_KEY)")
    b.set_defaults(func=cmd_build)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Backwards compatible: `cli.py "Inception" --top 5` == `cli.py recommend "Inception" --top 5`
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv.insert(0, "recommend")
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "top", 1) < 1:
        parser.error("--top must be at least 1")
    try:
        if args.command is None:
            return interactive(args)
        return args.func(args)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
