# syntax=docker/dockerfile:1.6
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    BMFOTE_REMOTE_DB=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY engine ./engine

RUN useradd --system --uid 1000 bmfote && chown -R bmfote /app
USER bmfote

EXPOSE 8000
CMD ["sh", "-c", "uvicorn engine.server:app --host 0.0.0.0 --port ${PORT:-8000}"]
