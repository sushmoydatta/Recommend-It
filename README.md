# Recommend-It
# Recommend-It

A content-based movie recommendation engine. Give it a movie you like, and it returns titles with similar genres, cast, director, and plot themes — ranked by cosine similarity over TF-IDF vectors.

## How it works

1. **Feature combination** — each movie's genres, keywords, tagline, cast, and director are concatenated into a single text blob.
2. **Vectorization** — the blobs are converted to TF-IDF vectors using `scikit-learn`.
3. **Similarity ranking** — cosine similarity between vectors scores every movie against every other movie.
4. **Fuzzy title matching** — `difflib` resolves imprecise user input (typos, partial titles) to the closest known title.

## Usage

```bash
pip install -r requirements.txt
python cli.py "Inception"
```

```
Because you liked 'Inception', you might enjoy:

  1. The Prestige       (similarity: 0.2841)
  2. Interstellar        (similarity: 0.2603)
  3. Shutter Island       (similarity: 0.2417)
  4. Memento             (similarity: 0.2109)
  5. The Matrix          (similarity: 0.1988)
```

Optional flags:

```bash
python cli.py "The Dark Knight" --top 10
python cli.py "Titanic" --data path/to/other_movies.csv
```

## Dataset

Expects a CSV with at minimum these columns: `title`, `genres`, `keywords`, `tagline`, `cast`, `director`. A sample dataset is included in `data/movies.csv`.

## Project structure

```
recommend-it/
├── recommender.py     # Core recommendation engine (MovieRecommender class)
├── cli.py              # Command-line interface
├── data/
│   └── movies.csv      # Sample dataset
├── requirements.txt
└── README.md
```

## Possible extensions

- Swap TF-IDF for embeddings (e.g. sentence-transformers) to capture semantic similarity beyond keyword overlap
- Add a simple Flask/Streamlit front end
- Incorporate collaborative filtering using user ratings, if available, as a hybrid approach

## License

MIT recommend you upto 30 movies related to your search. 
