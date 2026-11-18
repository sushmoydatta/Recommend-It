"""Hugging Face Spaces entry point (Gradio SDK, free tier).

The Space runs `python app.py`. Instead of a Gradio UI, this serves the
Recommend-It FastAPI website directly on the port the Space exposes (7860).
"""

import os

# Must be set before importing the web app (it reads them at import time).
os.environ.setdefault("RECOMMEND_IT_DATA", "data/movies-wikidata.csv.gz")
os.environ.setdefault(
    "RECOMMEND_IT_DATA_URL",
    "https://github.com/sushmoydatta/Recommend-It/releases/download/dataset/movies-wikidata.csv.gz",
)

import uvicorn  # noqa: E402

from recommend_it.web.app import app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "7860")),
                proxy_headers=True, forwarded_allow_ips="*")
