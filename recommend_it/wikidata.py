"""Build the film dataset from Wikidata (CC0: free for any use, including public websites).

Unlike the IMDb dumps (personal/non-commercial use only), Wikidata can legally
power a public website. Wikidata has no ratings, so popularity comes from the
number of Wikipedia language editions with an article about the film
("sitelinks").

Pipeline, polite to the public query service (https://query.wikidata.org):

1. List film item IDs per film type (cheap index lookups).
2. Fetch details in batches of film IDs: title, release date, IMDb/TMDB IDs,
   runtime, sitelinks, and genre/director/cast/main-subject *IDs*. Using IDs
   instead of names keeps queries fast and means two actors with the same name
   never get mixed up.
3. Look up English names only for genres and directors (shown in the UI).

Batches are checkpointed to ``raw_dir``, so an interrupted build resumes where
it stopped. A full build takes roughly an hour.
"""

from __future__ import annotations

import csv
import http.client
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "Recommend-It/2.0 (https://github.com/sushmoydatta/Recommend-It) dataset builder"
# film, feature film, animated feature film, documentary film, silent film, 3D film
FILM_TYPES = ["Q11424", "Q24869", "Q202866", "Q93204", "Q226730", "Q29168811"]
BATCH = 2000
LABEL_BATCH = 5000


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def sparql(query: str, retries: int = 6, timeout: float = 120) -> list[dict]:
    """Run a query and return rows as dicts. Retries on rate limits and timeouts."""
    body = urllib.parse.urlencode({"query": query}).encode()
    for attempt in range(retries):
        req = urllib.request.Request(ENDPOINT, data=body, headers={"Accept": "text/csv", "User-Agent": USER_AGENT})
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8")
            # Wikidata allows ~60s of query time per minute per client: pause after
            # slow queries so the next one isn't throttled with a long Retry-After.
            spent = time.time() - started
            if spent > 10:
                time.sleep(min(spent / 2, 30))
            # A query that times out server-side can return 200 with a Java stack trace appended.
            if "java.util.concurrent.TimeoutException" in text or "SPARQL-QUERY:" in text:
                raise TimeoutError("server-side query timeout")
            return list(csv.DictReader(io.StringIO(text)))
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504):
                raise
            wait = int(e.headers.get("Retry-After") or 0) or 15 * (attempt + 1)
        except (TimeoutError, urllib.error.URLError, ConnectionError, http.client.HTTPException, OSError) as e:
            if isinstance(e, TimeoutError) and attempt >= 1:
                raise
            wait = 15 * (attempt + 1)
        _log(f"  Wikidata busy, retrying in {wait}s ...")
        time.sleep(wait)
    raise RuntimeError("Wikidata query failed after retries")


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def film_ids() -> list[str]:
    ids: set[str] = set()
    for t in FILM_TYPES:
        rows = sparql(f"SELECT ?film WHERE {{ ?film wdt:P31 wd:{t} }}")
        ids.update(_qid(r["film"]) for r in rows)
        _log(f"  {t}: {len(rows):,} items")
        time.sleep(2)
    return sorted(ids, key=lambda q: int(q[1:]))


# One row per (film, property, value). Avoids the cross product that GROUP_CONCAT
# over several multi-valued properties causes (a film with 60 cast members and 5
# genres would otherwise expand to 300+ rows before grouping).
DETAILS = """SELECT ?film ?p ?v WHERE {
 VALUES ?film { %s }
 { ?film wikibase:sitelinks ?v BIND("links" AS ?p) }
 UNION { ?film rdfs:label ?v FILTER(LANG(?v) = "en" || LANG(?v) = "mul") BIND(CONCAT("label_", LANG(?v)) AS ?p) }
 UNION { VALUES (?prop ?p) { (wdt:P577 "date") (wdt:P1476 "otitle") (wdt:P345 "imdb") (wdt:P4947 "tmdb")
                            (wdt:P2047 "runtime") (wdt:P136 "genre") (wdt:P57 "director") (wdt:P161 "cast")
                            (wdt:P921 "subject") }
         ?film ?prop ?v }
}"""
MULTI = {"genre": "genres", "director": "directors", "cast": "cast", "subject": "subjects"}


def _group(rows: list[dict]) -> list[dict]:
    films: dict[str, dict] = {}
    for r in rows:
        f = films.setdefault(_qid(r["film"]), {k: [] for k in MULTI.values()})
        p, v = r["p"], r["v"]
        if p in MULTI:
            f[MULTI[p]].append(_qid(v))
        elif p == "date":
            f["released"] = min(f.get("released", v), v)
        else:
            f.setdefault(p, v)
    out = []
    for qid, f in films.items():
        out.append(
            {
                "film": qid,
                "title": f.get("label_en") or f.get("label_mul") or "",
                "otitle": f.get("otitle", ""),
                "imdb_id": f.get("imdb", ""),
                "tmdb_id": f.get("tmdb", ""),
                "released": f.get("released", ""),
                "runtime": f.get("runtime", ""),
                "links": f.get("links", "0"),
                **{k: "|".join(dict.fromkeys(f[k])) for k in MULTI.values()},
            }
        )
    return out


def _details(ids: list[str]) -> list[dict]:
    try:
        return _group(sparql(DETAILS % " ".join(f"wd:{q}" for q in ids)))
    except (TimeoutError, RuntimeError):
        if len(ids) <= 100:
            raise
        mid = len(ids) // 2
        _log(f"  batch too slow, splitting {len(ids)} -> {mid} + {len(ids) - mid}")
        return _details(ids[:mid]) + _details(ids[mid:])


def labels(qids: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    qids = sorted(q for q in qids if q)
    for i in range(0, len(qids), LABEL_BATCH):
        chunk = qids[i : i + LABEL_BATCH]
        rows = sparql(
            "SELECT ?x ?l ?lang WHERE { VALUES ?x { %s } ?x rdfs:label ?l "
            'FILTER(LANG(?l) = "en" || LANG(?l) = "mul") BIND(LANG(?l) AS ?lang) }' % " ".join(f"wd:{q}" for q in chunk)
        )
        for r in sorted(rows, key=lambda r: r["lang"] != "en"):  # prefer English over "mul"
            out.setdefault(_qid(r["x"]), r["l"])
        time.sleep(1)
    return out


def fetch(raw_dir: Path, refresh: bool = False) -> pd.DataFrame:
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    ids_file = raw_dir / "wikidata_film_ids.json"
    if refresh or not ids_file.exists():
        _log("Listing films on Wikidata ...")
        ids_file.write_text(json.dumps(film_ids()))
        for old in raw_dir.glob("wikidata_batch_*.csv"):
            old.unlink()
    ids = json.loads(ids_file.read_text())
    _log(f"{len(ids):,} film items")

    batches = [ids[i : i + BATCH] for i in range(0, len(ids), BATCH)]
    started, fetched = time.time(), 0
    for n, chunk in enumerate(batches):
        f = raw_dir / f"wikidata_batch_{n:04d}.csv"
        if f.exists():
            continue
        rows = _details(chunk)
        pd.DataFrame(rows).to_csv(f, index=False)
        fetched += 1
        eta = (time.time() - started) / fetched * (len(batches) - n - 1)
        _log(f"  batch {n + 1}/{len(batches)} ({len(rows):,} rows), ~{eta / 60:.0f} min left")
        time.sleep(1)
    return pd.concat(
        (pd.read_csv(f, dtype=str, keep_default_na=False) for f in sorted(raw_dir.glob("wikidata_batch_*.csv"))),
        ignore_index=True,
    )


def _pretty_genre(label: str) -> str:
    label = label.strip()
    for suffix in (" film", " movie", " films"):
        if label.lower().endswith(suffix) and len(label) > len(suffix):
            label = label[: -len(suffix)]
    return label[:1].upper() + label[1:]


def assemble(raw: pd.DataFrame, max_cast: int = 25, max_year: int | None = None) -> pd.DataFrame:
    df = raw.copy()
    df["qid"] = df["film"].map(_qid)
    df = df.drop_duplicates("qid")
    df["title"] = df["title"].where(df["title"] != "", df["otitle"])
    df = df[df["title"] != ""]
    df["year"] = pd.to_numeric(df["released"].str.extract(r"^(-?\d{4})", expand=False), errors="coerce").astype("Int64")
    max_year = max_year or date.today().year + 1
    df = df[df["year"].isna() | ((df["year"] >= 1870) & (df["year"] <= max_year))]

    _log("Looking up genre and director names ...")
    split = lambda s: [x for x in s.split("|") if x]  # noqa: E731
    genre_ids = {g for v in df["genres"] for g in split(v)}
    director_ids = {d for v in df["directors"] for d in split(v)}
    names = labels(genre_ids | director_ids)

    def named(v: str, pretty=None) -> str:
        out = [names.get(q) for q in split(v)]
        out = [pretty(x) if pretty else x for x in out if x]
        return "|".join(dict.fromkeys(out))

    out = pd.DataFrame(
        {
            "qid": df["qid"],
            "imdb_id": df["imdb_id"].replace("", pd.NA),
            "tmdb_id": pd.to_numeric(df["tmdb_id"], errors="coerce").astype("Int64"),
            "title": df["title"],
            "year": df["year"],
            "genres": df["genres"].map(lambda v: named(v, _pretty_genre)),
            "director": df["directors"].map(named),
            "director_ids": df["directors"],
            "cast_ids": df["cast"].map(lambda v: "|".join(split(v)[:max_cast])),
            "subject_ids": df["subjects"],
            "runtime": pd.to_numeric(df["runtime"], errors="coerce").round().astype("Int64"),
            "popularity": pd.to_numeric(df["links"], errors="coerce").fillna(0).astype(int),
        }
    )
    return out.sort_values("popularity", ascending=False, kind="stable").reset_index(drop=True)


def build(out: Path, raw_dir: Path, refresh: bool = False) -> Path:
    df = assemble(fetch(raw_dir, refresh=refresh))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    years = df["year"].dropna()
    _log(f"Wrote {len(df):,} films ({years.min()}-{years.max()}) to {out}")
    return out
