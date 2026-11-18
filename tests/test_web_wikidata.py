import pandas as pd
import pytest
from fastapi.testclient import TestClient

from recommend_it import MovieRecommender, wikidata
from recommend_it.web import app as webapp


# ---------------------------------------------------------------- wikidata
def test_group_and_assemble(monkeypatch):
    E = "http://www.wikidata.org/entity/"
    rows = [
        {"film": E + "Q25188", "p": "links", "v": "80"},
        {"film": E + "Q25188", "p": "label_en", "v": "Inception"},
        {"film": E + "Q25188", "p": "date", "v": "2010-07-16T00:00:00Z"},
        {"film": E + "Q25188", "p": "date", "v": "2010-07-08T00:00:00Z"},
        {"film": E + "Q25188", "p": "imdb", "v": "tt1375666"},
        {"film": E + "Q25188", "p": "tmdb", "v": "27205"},
        {"film": E + "Q25188", "p": "genre", "v": E + "Q471839"},
        {"film": E + "Q25188", "p": "director", "v": E + "Q25191"},
        {"film": E + "Q25188", "p": "cast", "v": E + "Q38111"},
        {"film": E + "Q25188", "p": "cast", "v": E + "Q38111"},
        {"film": E + "Q1", "p": "links", "v": "1"},
        {"film": E + "Q1", "p": "label_mul", "v": "Mul Only"},
        {"film": E + "Q2", "p": "links", "v": "1"},  # no title at all -> dropped
        {"film": E + "Q3", "p": "links", "v": "2"},
        {"film": E + "Q3", "p": "otitle", "v": "Les Félines"},
        {"film": E + "Q3", "p": "date", "v": "2999-01-01T00:00:00Z"},  # implausible -> dropped
    ]
    grouped = wikidata._group(rows)
    inc = next(g for g in grouped if g["film"] == "Q25188")
    assert inc["released"].startswith("2010-07-08") and inc["cast"] == "Q38111"

    monkeypatch.setattr(wikidata, "labels", lambda ids: {"Q471839": "science fiction film", "Q25191": "Christopher Nolan"})
    df = wikidata.assemble(pd.DataFrame(grouped))
    assert list(df["qid"]) == ["Q25188", "Q1"]  # sorted by popularity
    row = df.iloc[0]
    assert row.title == "Inception" and row.year == 2010 and row.genres == "Science fiction"
    assert row.director == "Christopher Nolan" and row.tmdb_id == 27205 and row.popularity == 80
    assert df.iloc[1].title == "Mul Only"


def test_sparql_retries_on_rate_limit(monkeypatch):
    import io
    import urllib.error

    calls = []

    def fake_urlopen(req, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {"Retry-After": "1"}, None)
        return io.BytesIO(b"film\nhttp://www.wikidata.org/entity/Q1\n")

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(wikidata.urllib.request, "urlopen", lambda r, timeout: Resp(fake_urlopen(r, timeout).getvalue()))
    monkeypatch.setattr(wikidata.time, "sleep", lambda s: None)
    assert wikidata.sparql("SELECT") == [{"film": "http://www.wikidata.org/entity/Q1"}]
    assert len(calls) == 2


# ---------------------------------------------------------------- web
@pytest.fixture
def client(tmp_path, monkeypatch):
    df = pd.DataFrame(
        {
            "qid": ["Q25188", "Q13417189", "Q104123", "Q47703", "Q1"],
            "imdb_id": ["tt1375666", "tt0816692", "tt0482571", "tt0068646", None],
            "title": ["Inception", "Interstellar", "The Prestige", "The Godfather", "Spirited Away"],
            "year": [2010, 2014, 2006, 1972, 2001],
            "genres": ["Science fiction|Thriller", "Science fiction|Drama", "Drama|Mystery", "Crime|Drama", "Animated|Fantasy"],
            "director": ["Christopher Nolan"] * 3 + ["Francis Ford Coppola", "Hayao Miyazaki"],
            "director_ids": ["Q25191"] * 3 + ["Q56093", "Q55400"],
            "cast_ids": ["Q38111|Q4573", "Q193048", "Q45772|Q42869", "Q34012", ""],
            "subject_ids": ["Q1|Q2", "Q3", "Q4", "Q5", "Q6"],
            "popularity": [80, 75, 60, 90, 70],
        }
    )
    p = tmp_path / "wd.csv.gz"
    df.to_csv(p, index=False)
    webapp.S.engine = MovieRecommender(p)
    webapp.S.hits.clear()
    monkeypatch.setattr(webapp, "where_to_watch", lambda *a, **k: type("W", (), {"to_dict": lambda self: {"region": k["region"], "offers": []}})())
    with TestClient(webapp.app) as c:
        yield c
    webapp.S.engine = None


def test_api_flow(client):
    assert client.get("/api/health").json()["films"] == 5
    sug = client.get("/api/suggest", params={"q": "inc"}).json()
    assert sug[0]["id"] == "Q25188"
    rec = client.get("/api/recommend", params={"id": "Q25188", "top": 2}).json()
    assert rec["matched"]["title"] == "Inception"
    assert {r["title"] for r in rec["results"]} == {"Interstellar", "The Prestige"}
    assert client.get("/api/recommend", params={"q": "godfather"}).json()["matched"]["id"] == "Q47703"
    assert client.get("/api/watch", params={"id": "Q25188", "region": "in"}).json()["region"] == "IN"


def test_api_errors_and_limits(client, monkeypatch):
    assert client.get("/api/recommend", params={"id": "Q999"}).status_code == 404
    assert client.get("/api/recommend").status_code == 400
    assert client.get("/api/watch", params={"id": "Q25188", "region": "india"}).status_code == 422
    assert client.get("/api/recommend", params={"q": "x", "top": 500}).status_code == 422
    monkeypatch.setattr(webapp, "RATE", 3)
    codes = [client.get("/api/suggest", params={"q": "in"}).status_code for _ in range(5)]
    assert codes[-1] == 429


def test_pages_and_security_headers(client):
    for path in ["/", "/film/Q25188"]:
        r = client.get(path)
        assert r.status_code == 200 and "Recommend-It" in r.text
        assert "default-src 'self'" in r.headers["content-security-policy"]
    assert client.get("/static/app.js").status_code == 200
