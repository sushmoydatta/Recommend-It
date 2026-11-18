"""Content-based recommendation engine.

Each film becomes a bag of weighted tokens:

* genres, directors and cast become single tokens (``g_scifi``,
  ``p_christophernolan``), so "Tom Hanks" never matches "Tom Cruise".
* overview, tagline and keywords (when the dataset has them) are free text.

The documents are TF-IDF vectorised once (and cached on disk next to the
dataset). A query computes one row of cosine similarity on demand, so memory
stays small even for hundreds of thousands of films. Scores can be blended
with a Bayesian-weighted rating so well-liked films rank ahead of obscure ones.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

FEATURE_VERSION = "2"
TOKEN_FIELDS = {"genres": ("g", 3), "director": ("p", 2), "cast": ("p", 1)}
TEXT_FIELDS = {"keywords": 2, "overview": 1, "tagline": 1}
# Columns of the classic Kaggle "TMDB 5000" CSV, mapped to ours.
ALIASES = {"vote_average": "rating", "vote_count": "votes", "id": "tmdb_id"}

_YEAR_RE = re.compile(r"^(.*?)\s*\((\d{4})\)\s*$")


def normalize(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", str(title).lower())).strip()


def _split(value: str) -> list[str]:
    if not value:
        return []
    if "|" in value or "," in value:
        return [v for v in re.split(r"[|,]", value) if v.strip()]
    return [value]


def _tokens(value: str, prefix: str, as_names: bool) -> str:
    items = _split(value) if as_names else value.split()
    return " ".join(f"{prefix}_{re.sub(r'[^0-9a-z]', '', i.lower())}" for i in items if i.strip())


@dataclass
class Movie:
    title: str
    year: int | None = None
    score: float | None = None
    genres: str = ""
    director: str = ""
    rating: float | None = None
    votes: int | None = None
    imdb_id: str | None = None
    tmdb_id: int | None = None

    @property
    def label(self) -> str:
        return f"{self.title} ({self.year})" if self.year else self.title

    def to_dict(self) -> dict:
        return {**asdict(self), "label": self.label}


class MovieRecommender:
    def __init__(self, csv_path: str | Path, quality_weight: float = 0.15, use_cache: bool = True):
        self.path = Path(csv_path)
        if not self.path.exists():
            raise FileNotFoundError(
                f"Dataset not found: {self.path}. Build it with `python cli.py build-data` (see README)."
            )
        self.quality_weight = quality_weight
        self.movies = self._load(self.path)
        self._norm_titles = self.movies["title"].map(normalize)
        self._X = self._vectors(use_cache)
        self._quality = self._quality_scores()

    # ------------------------------------------------------------------ loading
    @staticmethod
    def _load(path: Path) -> pd.DataFrame:
        df = pd.read_csv(path, low_memory=False)
        df = df.rename(columns={k: v for k, v in ALIASES.items() if k in df.columns and v not in df.columns})
        if "title" not in df.columns:
            raise ValueError("Dataset must have a 'title' column.")
        features = [c for c in [*TOKEN_FIELDS, *TEXT_FIELDS] if c in df.columns]
        if not features:
            raise ValueError(f"Dataset needs at least one feature column out of {[*TOKEN_FIELDS, *TEXT_FIELDS]}.")
        if "year" not in df.columns and "release_date" in df.columns:
            df["year"] = pd.to_datetime(df["release_date"], errors="coerce").dt.year
        for col in [*TOKEN_FIELDS, *TEXT_FIELDS]:
            df[col] = df[col].fillna("").astype(str) if col in df.columns else ""
        df = df.dropna(subset=["title"]).reset_index(drop=True)
        df["title"] = df["title"].astype(str)
        return df

    def _documents(self) -> list[str]:
        cols = {}
        for col, (prefix, weight) in TOKEN_FIELDS.items():
            # The old TMDB-5000 CSV separates genres and cast with plain spaces, so
            # names can't be recovered there; fall back to single words. A director
            # field without separators is always one name.
            as_names = col == "director" or self.movies[col].str.contains(r"[|,]", regex=True).any()
            toks = self.movies[col].map(lambda v, p=prefix, a=as_names: _tokens(v, p, a))
            cols[col] = (toks + " ") * weight
        for col, weight in TEXT_FIELDS.items():
            cols[col] = (self.movies[col] + " ") * weight
        return pd.DataFrame(cols).agg(" ".join, axis=1).tolist()

    def _vectors(self, use_cache: bool) -> sparse.csr_matrix:
        stat = self.path.stat()
        key = hashlib.sha1(f"{self.path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{FEATURE_VERSION}".encode()).hexdigest()[:16]
        cache = self.path.parent / ".cache" / f"{self.path.name}.{key}.npz"
        if use_cache and cache.exists():
            return sparse.load_npz(cache).tocsr()
        vec = TfidfVectorizer(stop_words="english", sublinear_tf=True, min_df=1, dtype=np.float32)
        X = vec.fit_transform(self._documents()).tocsr()
        if use_cache:
            try:
                cache.parent.mkdir(exist_ok=True)
                for old in cache.parent.glob(f"{self.path.name}.*.npz"):
                    old.unlink()
                sparse.save_npz(cache, X)
            except OSError:
                pass
        return X

    def _quality_scores(self) -> np.ndarray | None:
        if not {"rating", "votes"} <= set(self.movies.columns):
            return None
        R = pd.to_numeric(self.movies["rating"], errors="coerce")
        v = pd.to_numeric(self.movies["votes"], errors="coerce").fillna(0)
        C = R.mean()
        m = max(float(v.quantile(0.5)), 1.0)
        wr = (v / (v + m)) * R.fillna(C) + (m / (v + m)) * C
        return (wr / 10.0).clip(0, 1).to_numpy(dtype=np.float32)

    # ------------------------------------------------------------------ lookup
    def _movie(self, pos: int, score: float | None = None) -> Movie:
        r = self.movies.iloc[pos]

        def val(col, cast):
            x = r.get(col)
            return None if x is None or pd.isna(x) else cast(x)

        return Movie(
            title=r["title"],
            year=val("year", int),
            score=None if score is None else round(float(score), 4),
            genres=str(r.get("genres", "")).replace("|", ", "),
            director=str(r.get("director", "")).replace("|", ", "),
            rating=val("rating", float),
            votes=val("votes", int),
            imdb_id=val("imdb_id", str),
            tmdb_id=val("tmdb_id", int),
        )

    def _by_popularity(self, positions) -> list[int]:
        positions = list(positions)
        if "votes" in self.movies.columns:
            votes = pd.to_numeric(self.movies["votes"].iloc[positions], errors="coerce").fillna(0)
            return list(votes.sort_values(ascending=False, kind="stable").index)
        return positions

    def search(self, query: str, limit: int = 10) -> list[Movie]:
        """Find films whose title matches ``query`` (optionally "Title (Year)")."""
        return [self._movie(p) for p in self._search_positions(query)[:limit]]

    def _search_positions(self, query: str) -> list[int]:
        year = None
        m = _YEAR_RE.match(query.strip())
        if m:
            query, year = m.group(1), int(m.group(2))
        q = normalize(query)
        if not q:
            return []
        titles = self._norm_titles
        year_ok = (
            pd.to_numeric(self.movies["year"], errors="coerce").eq(year) if year and "year" in self.movies else True
        )

        exact = titles.index[(titles == q) & year_ok]
        if len(exact):
            return self._by_popularity(exact)

        words = titles.index[titles.str.contains(rf"\b{re.escape(q)}\b", regex=True) & year_ok]
        if len(words):
            return self._by_popularity(words)

        close = difflib.get_close_matches(q, titles.drop_duplicates().tolist(), n=5, cutoff=0.6)
        hits = titles.index[titles.isin(close) & year_ok]
        order = {t: i for i, t in enumerate(close)}
        return sorted(self._by_popularity(hits), key=lambda p: order[titles.iat[p]])

    def resolve(self, query: str) -> tuple[Movie, list[Movie]]:
        """Return the best match for ``query`` plus up to 4 alternatives."""
        positions = self._search_positions(query)
        if not positions:
            raise ValueError(f"No movie found matching '{query}'. Try `search` to browse titles.")
        return self._movie(positions[0]), [self._movie(p) for p in positions[1:5]]

    # ------------------------------------------------------------------ recommend
    def recommend(self, query: str, top_n: int = 10) -> tuple[Movie, list[Movie]]:
        """Return (matched movie, top_n most similar movies)."""
        if top_n < 1:
            raise ValueError("top_n must be at least 1.")
        positions = self._search_positions(query)
        if not positions:
            raise ValueError(f"No movie found matching '{query}'. Try `search` to browse titles.")
        pos = positions[0]

        sims = linear_kernel(self._X[pos], self._X).ravel()
        ranked = sims.copy()
        if self._quality is not None and self.quality_weight:
            ranked *= (1 - self.quality_weight) + self.quality_weight * self._quality
        ranked[pos] = -np.inf

        k = min(top_n, len(ranked) - 1)
        if k <= 0:
            return self._movie(pos), []
        idx = np.argpartition(-ranked, k - 1)[:k]
        idx = idx[np.argsort(-ranked[idx], kind="stable")]
        return self._movie(pos), [self._movie(i, sims[i]) for i in idx if ranked[i] > 0]
