# ── Frontend build stage ────────────────────────────────────────────────────
# Builds the React SPA (frontend/dist) that Flask serves at runtime.
FROM node:22-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── Application image ─────────────────────────────────────────────────────────
FROM python:3.12-slim

# slim (default): essentia + librosa only, no PyTorch  ~400 MB
# full:           adds PyTorch CPU + deeprhythm CNN    ~1.8 GB
#   docker build --build-arg WITH_DEEPRHYTHM=true -t gatoserio/bpm-tagger:full .
ARG WITH_DEEPRHYTHM=false
# Bake the build-time flag into the image so the app can read it at runtime
ENV WITH_DEEPRHYTHM=${WITH_DEEPRHYTHM}

# System deps for librosa / soundfile / essentia
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
        libgomp1 \
        libsamplerate0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# PyTorch + deeprhythm — only in the full build
RUN if [ "$WITH_DEEPRHYTHM" = "true" ]; then \
        pip install --no-cache-dir \
            torch torchaudio \
            --index-url https://download.pytorch.org/whl/cpu \
        && pip install --no-cache-dir deeprhythm \
        && python -c "from deeprhythm import DeepRhythmPredictor; DeepRhythmPredictor(quiet=True)"; \
    fi

COPY requirements.txt requirements-core.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Install essentia (pre-release); non-fatal — code falls back gracefully if unavailable
RUN pip install --no-cache-dir --pre essentia || echo "WARNING: essentia not available, falling back to two-detector mode"

# Application code, ordered least- to most-frequently changed so a release
# invalidates as few layers as possible. VERSION and CHANGELOG.md change on
# every single release, so they go last — copied earlier they would invalidate
# the SPA bundle and package layers behind them for nothing.
COPY web_ui.py ./
COPY static/ static/
COPY bpm_tagger/ bpm_tagger/
# React SPA bundle from the frontend build stage (served by Flask)
COPY --from=frontend /fe/dist ./frontend/dist
COPY VERSION CHANGELOG.md ./

RUN mkdir -p /data

CMD ["python", "-m", "bpm_tagger"]
