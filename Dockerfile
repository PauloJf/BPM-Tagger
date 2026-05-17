FROM python:3.12-slim

# System deps for librosa / soundfile / essentia
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
        libgomp1 \
        libsamplerate0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install essentia (pre-release); non-fatal — code falls back gracefully if unavailable
RUN pip install --no-cache-dir --pre essentia || echo "WARNING: essentia not available, falling back to two-detector mode"

# Pre-download the deeprhythm model weights so the container works offline
RUN python -c "from deeprhythm import DeepRhythmPredictor; DeepRhythmPredictor(quiet=True)"

COPY bpm_tagger.py web_ui.py ./
COPY templates/ templates/
COPY static/ static/

RUN mkdir -p /data

CMD ["python", "bpm_tagger.py"]
