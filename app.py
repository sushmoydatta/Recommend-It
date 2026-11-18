"""Hugging Face Spaces entry point: a standard Gradio app (`demo.launch()`).

It uses the same recommendation engine and where-to-watch lookup as the CLI
and the FastAPI site. On first start it downloads the film dataset (Wikidata,
CC0) from this repository's `dataset` GitHub release.

Space settings (optional):
    SITE_AUTHOR, SITE_AUTHOR_URL   shown as "Built by ..."
    TMDB_READ_TOKEN                exact per-service streaming availability
"""

from __future__ import annotations

import html
import os
from functools import lru_cache
from pathlib import Path

import gradio as gr

from recommend_it.engine import MovieRecommender
from recommend_it.http import download
from recommend_it.watch import where_to_watch_many

DATA = Path(os.environ.get("RECOMMEND_IT_DATA", "data/movies-wikidata.csv.gz"))
DATA_URL = os.environ.get(
    "RECOMMEND_IT_DATA_URL",
    "https://github.com/sushmoydatta/Recommend-It/releases/download/dataset/movies-wikidata.csv.gz",
)
GITHUB = os.environ.get("SITE_GITHUB_URL", "https://github.com/sushmoydatta/Recommend-It")
AUTHOR = os.environ.get("SITE_AUTHOR", "")
AUTHOR_URL = os.environ.get("SITE_AUTHOR_URL", "")

REGIONS = {
    "Australia": "AU", "Bangladesh": "BD", "Brazil": "BR", "Canada": "CA", "France": "FR", "Germany": "DE",
    "India": "IN", "Indonesia": "ID", "Ireland": "IE", "Italy": "IT", "Japan": "JP", "Malaysia": "MY",
    "Mexico": "MX", "Netherlands": "NL", "New Zealand": "NZ", "Nigeria": "NG", "Pakistan": "PK",
    "Philippines": "PH", "Singapore": "SG", "South Africa": "ZA", "South Korea": "KR", "Spain": "ES",
    "Sri Lanka": "LK", "Sweden": "SE", "United Arab Emirates": "AE", "United Kingdom": "GB",
    "United States": "US",
}

# Zero-GPU Spaces require at least one @spaces.GPU function to exist; this app
# runs entirely on CPU and never calls it. On other hardware it's a no-op.
try:
    import spaces

    @spaces.GPU
    def _unused_gpu_hook() -> None:
        return None
except Exception:  # not running on a Space / package missing
    pass


def load_engine() -> MovieRecommender:
    if not DATA.exists():
        print(f"Downloading dataset from {DATA_URL} ...", flush=True)
        download(DATA_URL, DATA)
    print("Building search index (first start takes about a minute) ...", flush=True)
    return MovieRecommender(DATA)


ENGINE = load_engine()
print(f"Ready: {ENGINE.size:,} films", flush=True)


@lru_cache(maxsize=4096)
def _watch_cached(key: str, region: str):
    m = ENGINE.movie(ENGINE.position_of(key))
    return where_to_watch_many([m], region=region)[0]


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def _watch_html(w) -> str:
    free = [o for o in w.offers if o.is_free]
    paid = [o for o in w.offers if not o.is_free]

    def badge(o, cls):
        label = _e(o.provider + (" (ads)" if o.kind == "ads" else ""))
        if o.url:
            return f'<a class="ri-badge {cls}" href="{_e(o.url)}" target="_blank" rel="noopener">{label}</a>'
        return f'<span class="ri-badge {cls}">{label}</span>'

    parts = []
    if free:
        parts.append("<div><b>🆓 Free &amp; legal:</b> " + " ".join(badge(o, "free") for o in free) + "</div>")
    for kind, label in (("flatrate", "Subscription"), ("rent", "Rent"), ("buy", "Buy")):
        items = [o for o in paid if o.kind == kind][:6]
        if items:
            parts.append(f"<div><b>{label}:</b> " + " ".join(badge(o, "paid") for o in items) + "</div>")
    if w.tmdb_link:
        parts.append(f'<div><a href="{_e(w.tmdb_link)}" target="_blank" rel="noopener">All options (TMDB / JustWatch) →</a></div>')
    elif not w.offers:
        links = " ".join(
            f'<a class="ri-badge paid" href="{_e(u)}" target="_blank" rel="noopener">{_e(n)}</a>' for n, u in w.search_links
        )
        parts.append(f"<div>Check where it's streaming: {links}</div>")
    return '<div class="ri-watch">' + "".join(parts) + "</div>"


def _card(m, watch=None) -> str:
    facts = " · ".join(str(x) for x in [m.year, m.genres, f"{m.runtime} min" if m.runtime else None] if x)
    director = f'<div class="ri-muted">Dir. {_e(m.director)}</div>' if m.director else ""
    score = f'<div class="ri-muted">{round(m.score * 100)}% match</div>' if m.score is not None else ""
    return (
        f'<div class="ri-card"><div class="ri-title">{_e(m.title)}</div>'
        f'<div class="ri-muted">{_e(facts)}</div>{director}{score}'
        f'{_watch_html(watch) if watch else ""}</div>'
    )


def recommend(title: str, region_name: str, count: int, show_watch: bool, free_only: bool):
    title = (title or "").strip()
    if not title:
        return "Type a film you like, for example **Inception** or **3 Idiots**.", ""
    try:
        matched, alternatives = ENGINE.resolve(title)
    except ValueError as e:
        return f"⚠️ {e}", ""
    pos = ENGINE.position_of(matched.id)
    # Ask for more when filtering to free films, since many won't be free.
    _, recs = ENGINE.recommend_position(pos, top_n=int(count) * (3 if free_only else 1))
    region = REGIONS.get(region_name, "US")

    watches = [None] * len(recs)
    if show_watch or free_only:
        watches = [_watch_cached(m.id, region) for m in recs]
        if free_only:
            pairs = [(m, w) for m, w in zip(recs, watches) if w.free_offers][: int(count)]
            recs, watches = [p[0] for p in pairs], [p[1] for p in pairs]
    recs, watches = recs[: int(count)], watches[: int(count)]

    head = f"### Because you liked **{matched.label}**"
    if matched.director:
        head += f"\nDirected by {matched.director}"
    if alternatives:
        alts = ", ".join(a.label for a in alternatives[:3])
        head += f"\n\n<sub>Not the one you meant? Other matches: {html.escape(alts)}. Type the title with its year, e.g. *Dune (2021)*.</sub>"
    if not recs:
        note = f"No similar films stream free in {region_name} right now. Untick *Free to watch only* to see paid options."
        return head, f'<p class="ri-muted">{_e(note)}</p>'
    grid = '<div class="ri-grid">' + "".join(_card(m, w) for m, w in zip(recs, watches)) + "</div>"
    return head, grid


CSS = """
.ri-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
.ri-card { border: 1px solid var(--border-color-primary); border-radius: 12px; padding: 12px; background: var(--block-background-fill); }
.ri-title { font-weight: 700; margin-bottom: 4px; }
.ri-muted { color: var(--body-text-color-subdued); font-size: .88em; }
.ri-watch { margin-top: 8px; font-size: .85em; display: flex; flex-direction: column; gap: 4px; }
.ri-badge { display: inline-block; border-radius: 6px; padding: 1px 7px; margin: 1px 2px; text-decoration: none; font-size: .9em; }
.ri-badge.free { background: #dcfce7; color: #166534; font-weight: 600; }
.ri-badge.paid { background: #e2e8f0; color: #334155; }
"""

built_by = ""
if AUTHOR:
    who = f"[{AUTHOR}]({AUTHOR_URL})" if AUTHOR_URL else AUTHOR
    built_by = f"Built by {who} · "

with gr.Blocks(title="Recommend-It") as demo:
    gr.Markdown(
        "# 🎬 Recommend-It\n"
        f"Find films like the ones you love, out of **{ENGINE.size:,} films** from 1890s silents to this year's releases. "
        "See where each one streams, with **free and legal** options first."
    )
    with gr.Row():
        title = gr.Textbox(label="A film you like", placeholder="e.g. Inception, 3 Idiots, Spirited Away, Dune (2021)", scale=3)
        region = gr.Dropdown(choices=list(REGIONS), value="India", label="Watching from", scale=1)
    with gr.Row():
        count = gr.Slider(4, 24, value=12, step=1, label="How many recommendations")
        show_watch = gr.Checkbox(value=False, label="Show where to watch (slower)")
        free_only = gr.Checkbox(value=False, label="Free to watch only")
    go = gr.Button("Recommend", variant="primary")
    header = gr.Markdown()
    results = gr.HTML()

    inputs = [title, region, count, show_watch, free_only]
    go.click(recommend, inputs, [header, results])
    title.submit(recommend, inputs, [header, results])
    gr.Examples(
        examples=[["Inception"], ["3 Idiots"], ["Spirited Away"], ["The Godfather"], ["Parasite"], ["Nosferatu (1922)"]],
        inputs=[title],
    )

    with gr.Accordion("About this project", open=False):
        gr.Markdown(
            "**How it works**\n"
            "- **Data:** films from Wikidata (CC0), rebuilt weekly by a GitHub Actions job through batched, rate-limit-aware SPARQL queries.\n"
            "- **Model:** genres, people and subjects become weighted TF-IDF tokens. People are identified by Wikidata ID, "
            "so two actors with the same name never get mixed up.\n"
            "- **Ranking:** cosine similarity for the selected film only (one sparse row, about 50 ms over 300k films), "
            "blended with a popularity prior.\n"
            "- **Search:** exact, then word match, then typo-tolerant fuzzy matching.\n"
            "- **Where to watch:** TMDB / JustWatch availability per country, plus public-domain films on the Internet Archive. "
            "Pirate sites are never linked.\n\n"
            "**Stack:** Python · pandas · scikit-learn · Gradio / FastAPI · Docker · GitHub Actions\n\n"
            f"[Source code on GitHub]({GITHUB})"
        )

    gr.Markdown(
        f"<sub>{built_by}Film data: [Wikidata](https://www.wikidata.org) (CC0). Streaming availability: JustWatch via "
        "[TMDB](https://www.themoviedb.org). This product uses the TMDB API but is not endorsed or certified by TMDB. "
        "Public-domain films: [Internet Archive](https://archive.org/details/feature_films). "
        "Only legal sources are linked.</sub>"
    )

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=8).launch(css=CSS)
