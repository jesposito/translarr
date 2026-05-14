# --- UI build stage ------------------------------------------------------
# Builds the SvelteKit static bundle and emits it to /ui/dist for the
# runtime stage to copy in. Pinned to node:20-slim — bumping major node
# requires a re-test against adapter-static.
FROM node:20-slim AS ui-builder

WORKDIR /ui
COPY ui/package.json ui/package-lock.json* ./
# `npm ci` if a lockfile is present, fall back to `npm install` for dev
# clones that haven't committed one yet.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY ui/ ./
RUN npm run build


# --- Runtime stage -------------------------------------------------------
FROM python:3.12-slim AS base

# ffmpeg + mkvtoolnix for sub track extraction
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    mkvtoolnix \
    ca-certificates \
    curl \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

COPY server/ ./server/
COPY --from=ui-builder /ui/dist/ ./ui/dist/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 9000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:9000/health || exit 1

CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "9000"]
