"""Build an up-to-date movie dataset from the official IMDb datasets.

IMDb publishes daily refreshed dumps of its whole catalogue at
https://datasets.imdbws.com/ (free for personal / non-commercial use, see
https://developer.imdb.com/non-commercial-datasets/). They cover every film
IMDb knows about, from the 1890s up to releases announced for this year.

Pipeline (memory-friendly, the big files are read in chunks):

    title.ratings   -> keep titles with >= ``min_votes`` votes
    title.basics    -> keep feature films (titleType == "movie"), non-adult
    title.crew      -> directors
    title.principals-> top-billed cast
    name.basics     -> person names

Optionally, ``tmdb_enrich`` adds plot overview, tagline and keywords from TMDB
for the most-voted films, which noticeably improves recommendation quality.
"""

from __future__ import annotations

import csv
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from .http import HTTPError, download
from .tmdb import TMDB

IMDB_BASE = "https://datasets.imdbws.com"
IMDB_FILES = ["title.basics", "title.ratings", "title.crew", "title.principals", "name.basics"]
CHUNK = 2_000_000


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _read(path: Path, usecols: list[str], chunksize: int | None = None):
    return pd.read_csv(
        path,
        sep="\t",
        usecols=usecols,
        dtype=str,
        na_values="\\N",
        keep_default_na=False,
        quoting=csv.QUOTE_NONE,
        chunksize=chunksize,
        compression="gzip",
    )


def fetch_imdb(raw_dir: Path, refresh: bool = False) -> dict[str, Path]:
    """Download the IMDb dumps into ``raw_dir`` (skips files already present)."""
    paths = {}
    for name in IMDB_FILES:
        dest = raw_dir / f"{name}.tsv.gz"
        if refresh or not dest.exists():
            _log(f"Downloading {name} ...")
            download(f"{IMDB_BASE}/{name}.tsv.gz", dest)
        paths[name] = dest
    return paths


def build_from_imdb(raw_dir: Path, min_votes: int = 50, cast_size: int = 6, max_directors: int = 2) -> pd.DataFrame:
    raw_dir = Path(raw_dir)
    f = {name: raw_dir / f"{name}.tsv.gz" for name in IMDB_FILES}
    for name, p in f.items():
        if not p.exists():
            raise FileNotFoundError(f"Missing {p}. Run with download enabled first.")

    _log("Reading ratings ...")
    ratings = _read(f["title.ratings"], ["tconst", "averageRating", "numVotes"])
    ratings["numVotes"] = pd.to_numeric(ratings["numVotes"], errors="coerce").fillna(0).astype(int)
    ratings = ratings[ratings["numVotes"] >= min_votes]

    _log("Filtering feature films ...")
    keep = set(ratings["tconst"])
    parts = []
    for chunk in _read(f["title.basics"], ["tconst", "titleType", "primaryTitle", "isAdult", "startYear", "runtimeMinutes", "genres"], CHUNK):
        chunk = chunk[(chunk["titleType"] == "movie") & (chunk["isAdult"] != "1") & chunk["tconst"].isin(keep)]
        parts.append(chunk.drop(columns=["titleType", "isAdult"]))
    movies = pd.concat(parts, ignore_index=True).merge(ratings, on="tconst")
    keep = set(movies["tconst"])
    _log(f"  {len(movies):,} films with >= {min_votes} votes")

    _log("Reading directors ...")
    crew = _read(f["title.crew"], ["tconst", "directors"])
    crew = crew[crew["tconst"].isin(keep)].dropna(subset=["directors"])
    crew["directors"] = crew["directors"].str.split(",").str[:max_directors]
    directors = crew.explode("directors")

    _log("Reading cast (largest file, takes a few minutes) ...")
    parts = []
    for chunk in _read(f["title.principals"], ["tconst", "ordering", "nconst", "category"], CHUNK):
        chunk = chunk[chunk["category"].isin(["actor", "actress", "self"]) & chunk["tconst"].isin(keep)]
        parts.append(chunk[["tconst", "ordering", "nconst"]])
    cast = pd.concat(parts, ignore_index=True)
    cast["ordering"] = cast["ordering"].astype(int)
    cast = cast.sort_values(["tconst", "ordering"]).groupby("tconst").head(cast_size)

    _log("Reading names ...")
    needed = set(cast["nconst"]) | set(directors["directors"])
    names = {}
    for chunk in _read(f["name.basics"], ["nconst", "primaryName"], CHUNK):
        chunk = chunk[chunk["nconst"].isin(needed)]
        names.update(zip(chunk["nconst"], chunk["primaryName"]))

    def joined(df: pd.DataFrame, id_col: str) -> pd.Series:
        df = df.assign(name=df[id_col].map(names)).dropna(subset=["name"])
        return df.groupby("tconst", sort=False)["name"].agg("|".join)

    movies["director"] = movies["tconst"].map(joined(directors, "directors"))
    movies["cast"] = movies["tconst"].map(joined(cast, "nconst"))

    out = pd.DataFrame(
        {
            "imdb_id": movies["tconst"],
            "title": movies["primaryTitle"],
            "year": pd.to_numeric(movies["startYear"], errors="coerce").astype("Int64"),
            "genres": movies["genres"].fillna("").str.replace(",", "|"),
            "director": movies["director"].fillna(""),
            "cast": movies["cast"].fillna(""),
            "runtime": pd.to_numeric(movies["runtimeMinutes"], errors="coerce").astype("Int64"),
            "rating": pd.to_numeric(movies["averageRating"], errors="coerce"),
            "votes": movies["numVotes"],
        }
    )
    return out.sort_values("votes", ascending=False).reset_index(drop=True)


def tmdb_enrich(df: pd.DataFrame, top: int, tmdb: TMDB, workers: int = 8) -> pd.DataFrame:
    """Add tmdb_id, overview, tagline and keywords for the ``top`` most-voted films."""
    df = df.copy()
    for col in ["tmdb_id", "overview", "tagline", "keywords"]:
        if col not in df.columns:
            df[col] = pd.NA if col == "tmdb_id" else ""
    df["tmdb_id"] = df["tmdb_id"].astype("Int64")
    targets = df.sort_values("votes", ascending=False).head(top)

    def fetch(row):
        try:
            hit = tmdb.find_by_imdb(row.imdb_id)
            if not hit:
                return row.Index, None
            d = tmdb.details(hit["id"])
            kws = " ".join(k["name"].replace(" ", "") for k in (d.get("keywords") or {}).get("keywords", []))
            return row.Index, (int(hit["id"]), d.get("overview") or "", d.get("tagline") or "", kws)
        except HTTPError:
            return row.Index, None

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for idx, val in pool.map(fetch, targets.itertuples()):
            done += 1
            if val:
                df.loc[idx, ["tmdb_id", "overview", "tagline", "keywords"]] = val
            if done % 500 == 0:
                _log(f"  TMDB enrichment: {done:,}/{len(targets):,}")
    return df


def build(
    out: Path,
    raw_dir: Path,
    min_votes: int = 50,
    download_files: bool = True,
    refresh: bool = False,
    enrich_top: int = 0,
) -> Path:
    if download_files:
        fetch_imdb(raw_dir, refresh=refresh)
    df = build_from_imdb(raw_dir, min_votes=min_votes)
    if enrich_top:
        tmdb = TMDB.from_env()
        if tmdb is None:
            _log("Skipping TMDB enrichment: set TMDB_API_KEY or TMDB_READ_TOKEN.")
        else:
            _log(f"Enriching top {enrich_top:,} films from TMDB ...")
            df = tmdb_enrich(df, enrich_top, tmdb)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    years = df["year"].dropna()
    _log(f"Wrote {len(df):,} films ({years.min()}-{years.max()}) to {out}")
    return out
