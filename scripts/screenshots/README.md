# Screenshot tooling

Helper scripts that regenerate the UI screenshots in [`docs/screenshots/`](../../docs/screenshots)
(the ones embedded in the README). They drive the real built UI with Playwright
against a local instance seeded from `demo_music/`.

## Prerequisites

- The backend deps installed, and a **built frontend**:
  ```bash
  npm --prefix frontend ci && npm --prefix frontend run build
  ```
- Playwright (in the project venv):
  ```bash
  pip install playwright
  playwright install chromium
  ```
- A `demo_music/` folder at the repo root with a spread of audio files. It's
  git-ignored (audio isn't committed); point `MUSIC_DIR` elsewhere if you keep
  your demo library somewhere else.

## Usage

1. **Start the screenshot backend** (serves the built UI on `:5088`, analyses
   `demo_music/` into a throwaway `screenshots-data/` DB, keeps the UI alive):
   ```powershell
   pwsh scripts/screenshots/start-backend.ps1
   ```
   Wait for `Serving on http://0.0.0.0:5088`. On first run it analyses the
   library, so give it a moment.

2. **Capture** (in another terminal, using the venv's Python):
   ```bash
   python scripts/screenshots/capture_all.py     # 01-login … 10-player-login
   python scripts/screenshots/capture_player.py  # 11-player-desktop, 12-player-mobile
   ```
   Images are written straight into `docs/screenshots/`. Review `git diff` and
   commit the ones that changed.

`capture_player.py` is separate because those shots need a *populated, running*
queue (it stars a few mid-tempo tracks, enters player mode, and starts a run).

## Config (env vars, defaults match `start-backend.ps1`)

| Var | Default | Meaning |
|---|---|---|
| `SHOT_BASE` | `http://localhost:5088` | UI base URL |
| `SHOT_ADMIN_PW` | `screenshot123` | admin UI password |
| `SHOT_RUN_PW` | `runmode123` | player/run password |
| `SHOT_OUT` | `docs/screenshots` | output directory |
| `MUSIC_DIR` | `./demo_music` | library the backend serves |

The passwords are throwaway values for this local, disposable instance only —
not real credentials.
