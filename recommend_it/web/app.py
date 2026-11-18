"""Public web app: FastAPI JSON API + a single-page frontend.

Run locally:   uvicorn recommend_it.web.app:app --reload
Environment:
    RECOMMEND_IT_DATA      dataset path (default data/movies.csv.gz)
    RECOMMEND_IT_DATA_URL  if the dataset file is missing, download it from here at startup
    TMDB_READ_TOKEN / TMDB_API_KEY   enables per-service availability and posters
    RATE_LIMIT_PER_MIN     requests per minute per client IP (default 90)
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict, defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..engine import MovieRecommender
from ..http import HTTPError, download
from ..tmdb import TMDB
from ..watch import where_to_watch

STATIC = Path(__file__).parent / "static"
DATA = Path(os.environ.get("RECOMMEND_IT_DATA", "data/movies.csv.gz"))
DATA_URL = os.environ.get("RECOMMEND_IT_DATA_URL")
RATE = int(os.environ.get("RATE_LIMIT_PER_MIN", "90"))
WATCH_TTL = 12 * 3600
IMG = "https://image.tmdb.org/t/p/w342"


class TTLCache:
    def __init__(self, maxsize: int = 20_000, ttl: float = WATCH_TTL):
        self.maxsize, self.ttl = maxsize, ttl
        self._d: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            hit = self._d.get(key)
            if not hit or hit[0] < time.time():
                return None
            self._d.move_to_end(key)
            return hit[1]

    def set(self, key, value):
        with self._lock:
            self._d[key] = (time.time() + self.ttl, value)
            self._d.move_to_end(key)
            while len(self._d) > self.maxsize:
                self._d.popitem(last=False)


class State:
    engine: MovieRecommender | None = None
    tmdb: TMDB | None = None
    watch_cache = TTLCache()
    detail_cache = TTLCache(ttl=7 * 24 * 3600)
    hits: dict[str, deque] = defaultdict(deque)


S = State()


def load_engine(path: Path = DATA) -> MovieRecommender:
    if not path.exists() and DATA_URL:
        download(DATA_URL, path)
    return MovieRecommender(path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if S.engine is None:
        S.engine = load_engine()
    S.tmdb = TMDB.from_env()
    yield


app = FastAPI(title="Recommend-It", docs_url="/api/docs", redoc_url=None, lifespan=lifespan)


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.url.path != "/api/health":
        # Behind a hosting proxy the last X-Forwarded-For hop is the one the proxy added.
        fwd = request.headers.get("x-forwarded-for")
        ip = fwd.split(",")[-1].strip() if fwd else (request.client.host if request.client else "?")
        now = time.time()
        if len(S.hits) > 50_000:
            for k in [k for k, v in S.hits.items() if not v or v[-1] < now - 60]:
                del S.hits[k]
        q = S.hits[ip]
        while q and q[0] < now - 60:
            q.popleft()
        if len(q) >= RATE:
            return JSONResponse({"detail": "Too many requests, slow down a little."}, status_code=429)
        q.append(now)
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' https://image.tmdb.org data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'self' https://huggingface.co https://*.hf.space"
    )
    return resp


def _engine() -> MovieRecommender:
    if S.engine is None:
        raise HTTPException(503, "Still loading the film catalogue, try again in a few seconds.")
    return S.engine


def _pos(key: str) -> int:
    try:
        return _engine().position_of(key)
    except KeyError:
        raise HTTPException(404, "Unknown film id.") from None


@app.get("/api/health")
def health():
    return {"ok": S.engine is not None, "films": S.engine.size if S.engine else 0, "tmdb": S.tmdb is not None}


@app.get("/api/suggest")
def suggest(q: str = Query(..., max_length=100), limit: int = Query(8, ge=1, le=20)):
    return [m.to_dict() for m in _engine().suggest(q, limit)]


@app.get("/api/search")
def search(q: str = Query(..., max_length=100), limit: int = Query(10, ge=1, le=30)):
    return [m.to_dict() for m in _engine().search(q, limit)]


@app.get("/api/recommend")
def recommend(
    id: str | None = Query(None, max_length=40),
    q: str | None = Query(None, max_length=100),
    top: int = Query(12, ge=1, le=40),
):
    eng = _engine()
    if id:
        matched, recs = eng.recommend_position(_pos(id), top)
    elif q:
        try:
            matched, recs = eng.recommend(q, top)
        except ValueError as e:
            raise HTTPException(404, str(e)) from None
    else:
        raise HTTPException(400, "Pass a film id or a title query.")
    return {"matched": matched.to_dict(), "results": [m.to_dict() for m in recs]}


@app.get("/api/watch")
def watch(id: str = Query(..., max_length=40), region: str = Query("US", pattern="^[A-Za-z]{2}$")):
    region = region.upper()
    key = (id, region)
    if (hit := S.watch_cache.get(key)) is not None:
        return hit
    m = _engine().movie(_pos(id))
    info = where_to_watch(m.title, m.year, m.imdb_id, m.tmdb_id, region=region, tmdb=S.tmdb).to_dict()
    S.watch_cache.set(key, info)
    return info


@app.get("/api/details")
def details(id: str = Query(..., max_length=40)):
    """Poster + plot from TMDB (only when a TMDB key is configured)."""
    if (hit := S.detail_cache.get(id)) is not None:
        return hit
    m = _engine().movie(_pos(id))
    out = {"poster": None, "overview": None}
    if S.tmdb:
        try:
            tid = m.tmdb_id or S.tmdb.resolve_id(m.title, m.year, m.imdb_id)
            if tid:
                d = S.tmdb.get(f"/movie/{tid}")
                out = {"poster": f"{IMG}{d['poster_path']}" if d.get("poster_path") else None, "overview": d.get("overview")}
        except HTTPError:
            return out  # don't cache failures
    S.detail_cache.set(id, out)
    return out


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
@app.get("/film/{rest:path}", include_in_schema=False)
def index(rest: str = ""):
    return FileResponse(STATIC / "index.html")
