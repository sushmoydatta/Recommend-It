"""
Core recommendation engine for Recommend-It.

Approach: content-based filtering.
- Combines genres, keywords, tagline, cast, and director into one text blob per movie.
- Vectorizes with TF-IDF.
- Ranks candidates by cosine similarity to the input movie's vector.
"""

from dataclasses import dataclass

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from difflib import get_close_matches


FEATURE_COLUMNS = ["genres", "keywords", "tagline", "cast", "director"]


@dataclass
class Recommendation:
    title: str
    score: float


class MovieRecommender:
    """Content-based movie recommender built on a TF-IDF + cosine similarity pipeline."""

    def __init__(self, csv_path: str):
        self.movies = pd.read_csv(csv_path)
        self._validate_columns()
        self._prepare_features()
        self._fit()

    def _validate_columns(self) -> None:
        required = set(FEATURE_COLUMNS) | {"title"}
        missing = required - set(self.movies.columns)
        if missing:
            raise ValueError(f"Dataset is missing required columns: {missing}")

    def _prepare_features(self) -> None:
        for col in FEATURE_COLUMNS:
            self.movies[col] = self.movies[col].fillna("")

        self.movies["combined_features"] = (
            self.movies["genres"]
            + " "
            + self.movies["keywords"]
            + " "
            + self.movies["tagline"]
            + " "
            + self.movies["cast"]
            + " "
            + self.movies["director"]
        )

        if "index" not in self.movies.columns:
            self.movies = self.movies.reset_index(drop=True)
            self.movies["index"] = self.movies.index

    def _fit(self) -> None:
        vectorizer = TfidfVectorizer()
        feature_vectors = vectorizer.fit_transform(self.movies["combined_features"])
        self.similarity_matrix = cosine_similarity(feature_vectors)

    def _resolve_title(self, query: str) -> str:
        titles = self.movies["title"].tolist()
        matches = get_close_matches(query, titles, n=1, cutoff=0.4)
        if not matches:
            raise ValueError(f"No close match found for '{query}'.")
        return matches[0]

    def recommend(self, movie_name: str, top_n: int = 5) -> list[Recommendation]:
        """Return the top_n movies most similar to the given movie title."""
        matched_title = self._resolve_title(movie_name)
        movie_index = self.movies[self.movies.title == matched_title]["index"].values[0]

        scores = list(enumerate(self.similarity_matrix[movie_index]))
        scores = sorted(scores, key=lambda x: x[1], reverse=True)

        results = []
        for idx, score in scores:
            title = self.movies[self.movies.index == idx]["title"].values[0]
            if title == matched_title:
                continue
            results.append(Recommendation(title=title, score=round(float(score), 4)))
            if len(results) >= top_n:
                break

        return results
