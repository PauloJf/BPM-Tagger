FROM python:3.12-slim

# System deps required by librosa / soundfile
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the deeprhythm model weights so the container works offline
RUN python -c "from deeprhythm import BPMPredictor; BPMPredictor()"

COPY bpm_tagger.py .

RUN mkdir -p /data

CMD ["python", "bpm_tagger.py"]
