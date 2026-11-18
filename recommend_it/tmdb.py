"""Minimal TMDB (themoviedb.org) client.

Get a free API key at https://www.themoviedb.org/settings/api and export either
``TMDB_API_KEY`` (v3 key) or ``TMDB_READ_TOKEN`` (v4 read access token).
"""

from __future__ import annotations

import os

from .http import get_json

API = "https://api.themoviedb.org/3"


class TMDB:
    def __init__(self, api_key: str | None = None, read_token: str | None = None, timeout: float = 15):
        self.api_key = api_key or os.environ.get("TMDB_API_KEY")
        self.read_token = read_token or os.environ.get("TMDB_READ_TOKEN")
        self.timeout = timeout
        if not (self.api_key or self.read_token):
            raise RuntimeError("No TMDB credentials: set TMDB_API_KEY or TMDB_READ_TOKEN.")

    @classmethod
    def from_env(cls) -> "TMDB | None":
        try:
            return cls()
        except RuntimeError:
            return None

    def get(self, path: str, **params) -> dict:
        headers = {}
        if self.read_token:
            headers["Authorization"] = f"Bearer {self.read_token}"
        else:
            params["api_key"] = self.api_key
        return get_json(f"{API}{path}", params=params, headers=headers, timeout=self.timeout)

    def find_by_imdb(self, imdb_id: str) -> dict | None:
        res = self.get(f"/find/{imdb_id}", external_source="imdb_id").get("movie_results") or []
        return res[0] if res else None

    def search_movie(self, title: str, year: int | None = None) -> dict | None:
        params = {"query": title}
        if year:
            params["year"] = year
        res = self.get("/search/movie", **params).get("results") or []
        return res[0] if res else None

    def resolve_id(self, title: str, year: int | None = None, imdb_id: str | None = None) -> int | None:
        hit = self.find_by_imdb(imdb_id) if imdb_id else None
        hit = hit or self.search_movie(title, year)
        return int(hit["id"]) if hit else None

    def details(self, tmdb_id: int) -> dict:
        return self.get(f"/movie/{tmdb_id}", append_to_response="keywords")

    def watch_providers(self, tmdb_id: int) -> dict:
        return self.get(f"/movie/{tmdb_id}/watch/providers").get("results") or {}
