"""Where to watch: legal streaming options, with free ones listed first.

Sources:

1. **TMDB watch providers** (data by JustWatch), needs a free TMDB API key.
   Lists, per country, which services carry a film and how: free,
   free-with-ads (Tubi, Pluto TV, Plex, Roku Channel, Amazon MX Player,
   YouTube...), subscription, rent or buy.
2. **Internet Archive "Feature Films" collection**, no key needed. Older
   films whose copyright has expired can be watched there for free, legally.
3. Search links to JustWatch and free ad-supported services, so you can check
   by hand when no API key is configured.

Pirate sites are deliberately not included. They host films without a
licence, and they often carry malware and scam ads.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

from .http import HTTPError, get_json
from .tmdb import TMDB

# TMDB monetisation keys -> label, in display priority order (free first).
KINDS = {"free": "Free", "ads": "Free with ads", "flatrate": "Subscription", "rent": "Rent", "buy": "Buy"}
FREE_KINDS = {"free", "ads"}

# Free, legal, ad-supported services with a stable search URL, and the
# countries they operate in ("*" = worldwide).
FREE_SEARCH = [
    ("Tubi", "https://tubitv.com/search/{q}", {"US", "CA", "MX", "AU", "NZ", "GB", "EC", "SV", "GT", "PA", "CR"}),
    ("Internet Archive", "https://archive.org/search?query={q}&and%5B%5D=mediatype%3A%22movies%22", {"*"}),
]


def default_region() -> str:
    return os.environ.get("RECOMMEND_IT_REGION", "US").upper()


@dataclass
class Offer:
    provider: str
    kind: str  # one of KINDS
    url: str | None = None

    @property
    def is_free(self) -> bool:
        return self.kind in FREE_KINDS

    @property
    def kind_label(self) -> str:
        return KINDS.get(self.kind, self.kind)


@dataclass
class WatchInfo:
    title: str
    year: int | None
    region: str
    offers: list[Offer] = field(default_factory=list)
    search_links: list[tuple[str, str]] = field(default_factory=list)
    tmdb_link: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def free_offers(self) -> list[Offer]:
        return [o for o in self.offers if o.is_free]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["search_links"] = [{"name": n, "url": u} for n, u in self.search_links]
        return d


def tmdb_offers(tmdb: TMDB, tmdb_id: int, region: str) -> tuple[list[Offer], str | None]:
    data = tmdb.watch_providers(tmdb_id).get(region) or {}
    link = data.get("link")
    offers, seen = [], set()
    for kind in KINDS:
        for p in sorted(data.get(kind) or [], key=lambda p: p.get("display_priority", 999)):
            key = (p["provider_name"], kind)
            if key not in seen:
                seen.add(key)
                offers.append(Offer(provider=p["provider_name"], kind=kind, url=link))
    return offers, link


_NOISE = re.compile(
    r"\b(1080p|720p|480p|4k|60fps|hd|blu-?ray|restored|remastered|colou?ri[sz]ed|full movie|public domain)\b", re.I
)


def _clean_title(t: str) -> tuple[str, int | None]:
    """'Night Of The Living Dead 720p 1968' -> ('night of the living dead', 1968)."""
    t = str(t)
    years = re.findall(r"\b(18[89]\d|19\d\d|20\d\d)\b", t)
    t = re.sub(r"[\(\[].*?[\)\]]", " ", t)
    t = _NOISE.sub(" ", t)
    t = re.sub(r"\b(18[89]\d|19\d\d|20\d\d)\b", " ", t)
    t = re.sub(r"[^\w\s]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip(), int(years[-1]) if years else None


def _doc_year(doc: dict, title_year: int | None) -> int | None:
    for v in (doc.get("year"), str(doc.get("date") or "")[:4], title_year):
        if v is not None and str(v)[:4].isdigit():
            return int(str(v)[:4])
    return None


def archive_offers(title: str, year: int | None, timeout: float = 10) -> list[Offer]:
    """Public-domain copies in the Internet Archive's curated Feature Films collection.

    The archive's title search is fuzzy ("The Kid" also hits "Billy the Kid"), so
    results are kept only if the cleaned title matches exactly and, when we know
    the film's year, the item's year is within one year of it.
    """
    want, _ = _clean_title(title)
    q = f'title:("{title.replace(chr(34), "")}") AND mediatype:(movies) AND collection:(feature_films)'
    data = get_json(
        "https://archive.org/advancedsearch.php",
        params={"q": q, "fl[]": ["identifier", "title", "year", "date"], "sort[]": "downloads desc",
                "rows": 50, "output": "json"},
        timeout=timeout,
    )
    for doc in data.get("response", {}).get("docs", []):
        got, title_year = _clean_title(doc.get("title", ""))
        if got != want:
            continue
        y = _doc_year(doc, title_year)
        if year and (y is None or abs(y - year) > 1):
            continue
        return [Offer("Internet Archive (public domain)", "free", f"https://archive.org/details/{doc['identifier']}")]
    return []


def search_links(title: str, year: int | None, region: str) -> list[tuple[str, str]]:
    q = urllib.parse.quote(title)
    links = [("JustWatch", f"https://www.justwatch.com/{region.lower()}/search?q={q}")]
    links += [(n, url.format(q=q)) for n, url, regions in FREE_SEARCH if "*" in regions or region in regions]
    return links


def where_to_watch(
    title: str,
    year: int | None = None,
    imdb_id: str | None = None,
    tmdb_id: int | None = None,
    region: str | None = None,
    tmdb: TMDB | None = None,
    check_archive: bool = True,
) -> WatchInfo:
    region = (region or default_region()).upper()
    info = WatchInfo(title=title, year=year, region=region, search_links=search_links(title, year, region))
    tmdb = tmdb or TMDB.from_env()

    if tmdb:
        try:
            tmdb_id = tmdb_id or tmdb.resolve_id(title, year, imdb_id)
            if tmdb_id:
                info.offers, info.tmdb_link = tmdb_offers(tmdb, tmdb_id, region)
                info.notes.append("Streaming availability data by JustWatch (via TMDB).")
            else:
                info.notes.append("Film not found on TMDB.")
        except HTTPError as e:
            info.notes.append(f"TMDB lookup failed: {e}")
    else:
        info.notes.append("Set TMDB_API_KEY (or TMDB_READ_TOKEN) for exact availability per service (free key: themoviedb.org).")

    if check_archive:
        try:
            info.offers += archive_offers(title, year)
        except HTTPError as e:
            info.notes.append(f"Internet Archive lookup failed: {e}")

    info.offers.sort(key=lambda o: list(KINDS).index(o.kind) if o.kind in KINDS else 99)
    return info


def where_to_watch_many(movies, region: str | None = None, workers: int = 6, **kw) -> list[WatchInfo]:
    tmdb = TMDB.from_env()

    def one(m):
        return where_to_watch(m.title, m.year, m.imdb_id, m.tmdb_id, region=region, tmdb=tmdb, **kw)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, movies))
