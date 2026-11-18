import gzip
import json

import pandas as pd
import pytest

from recommend_it import MovieRecommender, watch
from recommend_it.cli import main
from recommend_it.dataset import build_from_imdb

MOVIES = [
    # imdb_id, title, year, genres, director, cast, rating, votes
    ("tt1375666", "Inception", 2010, "Action|Sci-Fi|Thriller", "Christopher Nolan", "Leonardo DiCaprio|Tom Hardy|Elliot Page", 8.8, 2_500_000),
    ("tt0816692", "Interstellar", 2014, "Adventure|Drama|Sci-Fi", "Christopher Nolan", "Matthew McConaughey|Anne Hathaway", 8.7, 2_200_000),
    ("tt0482571", "The Prestige", 2006, "Drama|Mystery|Sci-Fi", "Christopher Nolan", "Christian Bale|Hugh Jackman", 8.5, 1_400_000),
    ("tt0468569", "The Dark Knight", 2008, "Action|Crime|Drama", "Christopher Nolan", "Christian Bale|Heath Ledger|Tom Hardy", 9.0, 2_900_000),
    ("tt1345836", "The Dark Knight Rises", 2012, "Action|Drama", "Christopher Nolan", "Christian Bale|Tom Hardy", 8.4, 1_800_000),
    ("tt0109830", "Forrest Gump", 1994, "Drama|Romance", "Robert Zemeckis", "Tom Hanks|Robin Wright", 8.8, 2_200_000),
    ("tt0120338", "Titanic", 1997, "Drama|Romance", "James Cameron", "Leonardo DiCaprio|Kate Winslet", 7.9, 1_300_000),
    ("tt0012349", "The Kid", 1921, "Comedy|Drama|Family", "Charles Chaplin", "Charles Chaplin|Jackie Coogan", 8.2, 130_000),
    ("tt0000001", "Titanic", 1953, "Drama|History", "Jean Negulesco", "Clifton Webb|Barbara Stanwyck", 6.9, 9_000),
]


@pytest.fixture
def csv_path(tmp_path):
    df = pd.DataFrame(MOVIES, columns=["imdb_id", "title", "year", "genres", "director", "cast", "rating", "votes"])
    # Shuffle and give a misleading index column to make sure positions, not labels, are used.
    df = df.sample(frac=1, random_state=3).reset_index(drop=True)
    df.insert(0, "index", range(100, 100 + len(df)))
    p = tmp_path / "movies.csv.gz"
    df.to_csv(p, index=False)
    return p


@pytest.fixture
def engine(csv_path):
    return MovieRecommender(csv_path)


def test_recommend_nolan(engine):
    matched, recs = engine.recommend("inception", top_n=4)
    assert matched.title == "Inception"
    titles = [r.title for r in recs]
    assert "Inception" not in titles
    assert set(titles[:3]) <= {"Interstellar", "The Prestige", "The Dark Knight", "The Dark Knight Rises"}
    assert all(0 < r.score <= 1 for r in recs)


def test_duplicate_titles_not_dropped(engine):
    matched, recs = engine.recommend("Titanic (1997)", top_n=8)
    assert (matched.title, matched.year) == ("Titanic", 1997)
    assert ("Titanic", 1953) in [(r.title, r.year) for r in recs]


def test_most_popular_wins_ambiguous_title(engine):
    m, alts = engine.resolve("titanic")
    assert m.year == 1997 and alts[0].year == 1953


def test_partial_and_typo_matching(engine):
    assert engine.resolve("dark knight")[0].title == "The Dark Knight"
    assert engine.resolve("Intersteller")[0].title == "Interstellar"
    with pytest.raises(ValueError):
        engine.resolve("zzzzzz qqqq")


def test_people_are_single_tokens(engine):
    # "Tom Hanks" must not be similar to "Tom Hardy" just because of "Tom".
    _, recs = engine.recommend("Forrest Gump", top_n=8)
    scores = {(r.title, r.year): r.score for r in recs}
    assert scores.get(("Titanic", 1997), 0) > scores.get(("The Dark Knight Rises", 2012), 0)


def test_top_n_bounds(engine):
    with pytest.raises(ValueError):
        engine.recommend("Inception", top_n=0)
    _, recs = engine.recommend("Inception", top_n=1000)
    assert len(recs) <= len(MOVIES) - 1


def test_cache_reused(csv_path):
    MovieRecommender(csv_path)
    assert list((csv_path.parent / ".cache").glob("*.npz"))
    _, recs = MovieRecommender(csv_path).recommend("Inception", top_n=2)
    assert recs


def test_legacy_tmdb5000_columns(tmp_path):
    df = pd.DataFrame(
        {
            "index": [0, 1, 2],
            "id": [19995, 285, 206647],
            "title": ["Avatar", "Pirates of the Caribbean: At World's End", "Spectre"],
            "genres": ["Action Adventure Fantasy", "Adventure Fantasy Action", "Action Adventure Crime"],
            "keywords": ["alien future space", "ocean pirate", "spy secret agent"],
            "tagline": ["Enter the World of Pandora.", None, "A Plan No One Escapes"],
            "cast": ["Sam Worthington Zoe Saldana", "Johnny Depp Orlando Bloom", "Daniel Craig Christoph Waltz"],
            "director": ["James Cameron", "Gore Verbinski", "Sam Mendes"],
            "release_date": ["2009-12-10", "2007-05-19", "2015-10-26"],
            "vote_average": [7.2, 6.9, 6.3],
            "vote_count": [11800, 4500, 4466],
        }
    )
    p = tmp_path / "movies.csv"
    df.to_csv(p, index=False)
    m, recs = MovieRecommender(p).recommend("avatar", top_n=2)
    assert m.year == 2009 and m.tmdb_id == 19995 and len(recs) == 2


# ---------------------------------------------------------------- dataset build
def _tsv(path, header, rows):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write("\t".join(header) + "\n")
        for r in rows:
            f.write("\t".join(r) + "\n")


def test_build_from_imdb(tmp_path):
    _tsv(tmp_path / "title.basics.tsv.gz",
         ["tconst", "titleType", "primaryTitle", "originalTitle", "isAdult", "startYear", "endYear", "runtimeMinutes", "genres"],
         [["tt1", "movie", "Old Film", "Old Film", "0", "1903", "\\N", "12", "Short,Western"],
          ["tt2", "movie", "New Film", "New Film", "0", "2026", "\\N", "\\N", "Drama"],
          ["tt3", "tvSeries", "A Show", "A Show", "0", "2020", "\\N", "50", "Drama"],
          ["tt4", "movie", "Obscure", "Obscure", "0", "2001", "\\N", "90", "Drama"],
          ["tt5", "movie", "Adult", "Adult", "1", "2001", "\\N", "90", "Adult"]])
    _tsv(tmp_path / "title.ratings.tsv.gz", ["tconst", "averageRating", "numVotes"],
         [["tt1", "7.3", "30000"], ["tt2", "8.1", "900"], ["tt3", "9.0", "5000"], ["tt4", "6.0", "3"], ["tt5", "5", "999"]])
    _tsv(tmp_path / "title.crew.tsv.gz", ["tconst", "directors", "writers"],
         [["tt1", "nm1", "\\N"], ["tt2", "nm2,nm3,nm4", "\\N"], ["tt3", "\\N", "\\N"]])
    _tsv(tmp_path / "title.principals.tsv.gz", ["tconst", "ordering", "nconst", "category", "job", "characters"],
         [["tt1", "2", "nm5", "actress", "\\N", "\\N"], ["tt1", "1", "nm6", "actor", "\\N", "\\N"],
          ["tt1", "3", "nm1", "director", "\\N", "\\N"], ["tt2", "1", "nm7", "actor", "\\N", "\\N"]])
    _tsv(tmp_path / "name.basics.tsv.gz", ["nconst", "primaryName", "birthYear"],
         [["nm1", "Edwin S. Porter", "1870"], ["nm2", "Dir Two", "\\N"], ["nm3", "Dir Three", "\\N"],
          ["nm4", "Dir Four", "\\N"], ["nm5", "Marie Murray", "\\N"], ["nm6", "Justus Barnes", "\\N"], ["nm7", "Star", "\\N"]])

    df = build_from_imdb(tmp_path, min_votes=50)
    assert list(df["imdb_id"]) == ["tt1", "tt2"]  # sorted by votes; series/adult/low-vote removed
    old = df.iloc[0]
    assert old.year == 1903 and old.genres == "Short|Western"
    assert old.cast == "Justus Barnes|Marie Murray" and old.director == "Edwin S. Porter"
    assert df.iloc[1].director == "Dir Two|Dir Three" and df.iloc[1].year == 2026

    p = tmp_path / "out.csv.gz"
    df.to_csv(p, index=False)
    assert MovieRecommender(p).resolve("old film")[0].imdb_id == "tt1"


# ---------------------------------------------------------------- where to watch
class FakeTMDB:
    def resolve_id(self, title, year=None, imdb_id=None):
        return 27205

    def watch_providers(self, tmdb_id):
        return {
            "IN": {
                "link": "https://www.themoviedb.org/movie/27205/watch?locale=IN",
                "flatrate": [{"provider_name": "Netflix", "display_priority": 1}],
                "ads": [{"provider_name": "Amazon MX Player", "display_priority": 5}],
                "rent": [{"provider_name": "Apple TV", "display_priority": 2}],
            }
        }


def test_where_to_watch_free_first(monkeypatch):
    monkeypatch.setattr(watch, "archive_offers", lambda *a, **k: [])
    w = watch.where_to_watch("Inception", 2010, imdb_id="tt1375666", region="in", tmdb=FakeTMDB())
    assert w.region == "IN"
    assert [(o.provider, o.kind) for o in w.offers] == [
        ("Amazon MX Player", "ads"), ("Netflix", "flatrate"), ("Apple TV", "rent")]
    assert [o.provider for o in w.free_offers] == ["Amazon MX Player"]
    assert w.tmdb_link.endswith("locale=IN")


def test_where_to_watch_without_key_gives_search_links(monkeypatch):
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    monkeypatch.delenv("TMDB_READ_TOKEN", raising=False)
    monkeypatch.setattr(watch, "archive_offers", lambda *a, **k: [])
    w = watch.where_to_watch("The Kid", 1921, region="US")
    names = [n for n, _ in w.search_links]
    assert names[0] == "JustWatch" and "Tubi" in names
    assert "TMDB_API_KEY" in w.notes[0]
    assert "Tubi" not in [n for n, _ in watch.search_links("x", None, "IN")]


def test_archive_year_filter(monkeypatch):
    docs = {"response": {"docs": [
        {"identifier": "billy", "title": "Billy The Kid 1930"},
        {"identifier": "wrong-year", "title": "The Kid", "year": "1960"},
        {"identifier": "no-year", "title": "The Kid"},
        {"identifier": "TheKid1921", "title": "The Kid (1921) [Restored, 1080p]"},
    ]}}
    monkeypatch.setattr(watch, "get_json", lambda *a, **k: docs)
    offers = watch.archive_offers("The Kid", 1921)
    assert offers[0].url == "https://archive.org/details/TheKid1921" and offers[0].is_free
    assert watch.archive_offers("Billy the Kid", 1941) == []


# ---------------------------------------------------------------- CLI
def test_cli_json_with_watch(csv_path, capsys, monkeypatch):
    monkeypatch.setattr(watch, "TMDB", type("T", (), {"from_env": staticmethod(lambda: FakeTMDB())}))
    monkeypatch.setattr(watch, "archive_offers", lambda *a, **k: [])
    assert main(["Inception", "--top", "3", "--watch", "--region", "IN", "--json", "--data", str(csv_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["matched"]["title"] == "Inception"
    assert len(out["recommendations"]) == 3
    assert out["recommendations"][0]["watch"]["offers"][0]["kind"] == "ads"


def test_cli_errors(csv_path, capsys):
    assert main(["recommend", "Inception", "--data", "nope.csv"]) == 1
    assert "build-data" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["recommend", "Inception", "--top", "0", "--data", str(csv_path)])


def test_cli_search(csv_path, capsys):
    assert main(["search", "dark knight", "--data", str(csv_path)]) == 0
    out = capsys.readouterr().out
    assert out.index("The Dark Knight (2008)") < out.index("The Dark Knight Rises")
