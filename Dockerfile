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

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install essentia (pre-release); non-fatal — code falls back gracefully if unavailable
RUN pip install --no-cache-dir --pre essentia || echo "WARNING: essentia not available, falling back to two-detector mode"

COPY VERSION web_ui.py ./
COPY bpm_tagger/ bpm_tagger/
COPY templates/ templates/
COPY static/ static/

RUN mkdir -p /data

CMD ["python", "-m", "bpm_tagger"]
