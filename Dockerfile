# Public website image. Works on Hugging Face Spaces (Docker), Render, Fly.io, Railway, Cloud Run...
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PORT=7860 \
    RECOMMEND_IT_DATA=/app/data/movies-wikidata.csv.gz \
    RECOMMEND_IT_DATA_URL=https://github.com/sushmoydatta/Recommend-It/releases/download/dataset/movies-wikidata.csv.gz

WORKDIR /app
COPY requirements.txt requirements-web.txt ./
RUN pip install -r requirements-web.txt

COPY recommend_it ./recommend_it
COPY cli.py pyproject.toml README.md LICENSE ./

# Bake the dataset and its TF-IDF cache into the image so containers start fast.
# Pass --build-arg FETCH_DATA=0 to download it at startup instead.
ARG FETCH_DATA=1
RUN mkdir -p data && if [ "$FETCH_DATA" = "1" ]; then \
      python -c "from pathlib import Path; from recommend_it.web.app import load_engine; load_engine(Path('$RECOMMEND_IT_DATA'))"; \
    fi

RUN useradd -m -u 1000 app && chown -R app /app
USER app
EXPOSE 7860
CMD ["sh", "-c", "uvicorn recommend_it.web.app:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
