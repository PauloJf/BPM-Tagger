# BPM Tagger

Automatically detects the BPM of every song in your [Navidrome](https://www.navidrome.org/) music library, writes the result back to the file's metadata tag, tracks everything in a SQLite database, and sends batched notifications via [ntfy](https://ntfy.sh/).

## Features

- **High-accuracy BPM detection** — uses [deeprhythm](https://github.com/Auto-Janus/DeepRhythm) (deep learning) as the primary detector with [librosa](https://librosa.org/) as an automatic fallback
- **Tag writing** — writes the `BPM` tag to MP3 (ID3 `TBPM`), FLAC, OGG/Opus (Vorbis comment), M4A/AAC (MP4 `tmpo`), and other formats via mutagen
- **SQLite tracking** — records every file's path, hash, BPM, confidence, detector used, and analysis timestamp; re-analyzes only files that are new or have changed
- **Three operating modes** — full re-scan, incremental scan, or filesystem watcher
- **ntfy notifications** — batched and rate-limited to avoid channel spam
- **Fully Docker-native** — all settings via environment variables in `docker-compose.yml`

## Quick Start

1. Clone this repository and edit `docker-compose.yml`:
   - Set `volumes` → point `/music` at your Navidrome music directory
   - Set `NTFY_TOPIC` to your ntfy topic (or leave blank to disable notifications)
   - Set `MODE` to your preferred operating mode (see below)
   - Uncomment `user:` and set it to match your Navidrome user/group if you see permission errors

2. Build and run:
   ```bash
   docker compose up -d --build
   ```

3. Follow the logs:
   ```bash
   docker compose logs -f
   ```

## Operating Modes

| `MODE` | Behavior |
|---|---|
| `scan_all` | Re-analyze every audio file, overwriting existing results |
| `scan_unscanned` | Only analyze files not yet in the database or whose content has changed |
| `watch` | Run `scan_unscanned` on start (if `SCAN_ON_START=true`), then continuously watch for new or modified files |

## Configuration Reference

All settings are environment variables in `docker-compose.yml`.

| Variable | Default | Description |
|---|---|---|
| `MODE` | `watch` | Operating mode: `scan_all`, `scan_unscanned`, `watch` |
| `MUSIC_DIR` | `/music` | Path to the music directory inside the container |
| `DB_PATH` | `/data/bpm_tagger.db` | Path to the SQLite database inside the container |
| `WRITE_TAGS` | `true` | Write BPM to audio file metadata tags |
| `SCAN_ON_START` | `true` | Run `scan_unscanned` before watching (watch mode only) |
| `AUDIO_EXTENSIONS` | `.mp3,.flac,.ogg,.m4a,.aac,.wav,.opus,.wv` | Comma-separated list of extensions to process |
| `LOG_LEVEL` | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `NTFY_URL` | `https://ntfy.sh` | ntfy server URL (leave blank to disable notifications) |
| `NTFY_TOPIC` | _(empty)_ | ntfy topic (leave blank to disable notifications) |
| `NTFY_BATCH_SIZE` | `10` | Max tracks per notification message |
| `NTFY_MIN_INTERVAL` | `300` | Minimum seconds between notifications |

## SQLite Database

The database is stored at the path configured by `DB_PATH` (default: `/data/bpm_tagger.db`). You can query it directly with any SQLite client:

```sql
-- Tracks analyzed by the fallback detector (candidates for re-analysis)
SELECT file_path, bpm, bpm_confidence FROM tracks WHERE detector = 'librosa';

-- Tracks with errors
SELECT file_path, error_message FROM tracks WHERE status = 'error';

-- Summary stats
SELECT detector, COUNT(*) as count, AVG(bpm) as avg_bpm FROM tracks
WHERE status = 'done' GROUP BY detector;
```

| Column | Description |
|---|---|
| `file_path` | Absolute path to the audio file |
| `file_hash` | `size:mtime` fingerprint for change detection |
| `bpm` | Detected BPM (1 decimal place) |
| `bpm_confidence` | Confidence score 0–1 (beat consistency; deeprhythm always reports 1.0) |
| `detector` | `deeprhythm` or `librosa` |
| `analyzed_at` | ISO-8601 UTC timestamp |
| `status` | `done`, `error`, or `pending` |
| `error_message` | Error detail when `status = 'error'` |

## BPM Detection Details

Detection is attempted in this order:

1. **deeprhythm** — a CNN-based deep learning model pre-downloaded during `docker build`. Handles a wide range of genres accurately, including electronic, hip-hop, and complex rhythms. The model is baked into the image so no internet access is needed at runtime.
2. **librosa (fallback)** — if deeprhythm raises an exception. Uses onset strength envelope + local tempo estimation via autocorrelation. Less accurate for complex rhythms but reliable for simple patterns.

The `detector` column in the database lets you identify which method was used for each track.

## Notification Anti-Spam

Notifications are batched to avoid flooding your ntfy channel:

- A message is sent when `NTFY_BATCH_SIZE` tracks accumulate **or** `NTFY_MIN_INTERVAL` seconds have passed since the last send
- At the end of a scan, a single summary notification is sent (e.g. "Scan complete — 142 tagged, 0 errors")
- In watch mode, the buffer is flushed every 60 seconds in the background

## Navidrome Integration Tips

- **Shared volume**: mount the same music directory used by Navidrome so BPM-Tagger can read and tag files in place
- **User/group**: set `user: "UID:GID"` in `docker-compose.yml` to match Navidrome's user so file permissions are consistent
- **Tag refresh**: after BPM-Tagger writes tags, trigger a Navidrome library rescan to pick up the new BPM values (Navidrome reads the `BPM` tag from file metadata)

## Example: Sharing a Volume with Navidrome

```yaml
services:
  navidrome:
    image: deluan/navidrome:latest
    volumes:
      - /srv/music:/music:ro

  bpm-tagger:
    build: ./BPM-Tagger
    environment:
      MODE: watch
      MUSIC_DIR: /music
      NTFY_TOPIC: my-music-alerts
    volumes:
      - /srv/music:/music   # same host path, read-write for tag writing
      - bpm_tagger_data:/data
    user: "1000:1000"

volumes:
  bpm_tagger_data:
```
