"""Shared pytest fixtures.

The web fixtures obtain the Flask app through a single indirection so the same
characterization tests run unchanged against BOTH the pre-refactor monolith
(``web_ui.app`` configured directly) and the post-refactor package
(``bpm_tagger.web.app.create_app``). Only the app-construction helper differs;
every assertion stays byte-identical, which is what makes these a real
regression net across the M0 refactor.
"""

import os
import secrets
from pathlib import Path

import pytest


@pytest.fixture
def base_config(tmp_path):
    """A minimal config dict sufficient to boot the web app in tests."""
    db_path = tmp_path / "bpm_tagger.db"
    return {
        "db_path": str(db_path),
        "music_dir": str(tmp_path / "music"),
        "ui_password": "s3cret",
        "ui_secret_key": "unit-test-secret-key",
        "ui_max_login_attempts": 5,
        "ui_lockout_seconds": 300,
        "ui_session_hours": 24,
        "write_tags": False,
        "review_confidence_threshold": 0.4,
        "bpm_min": 60.0,
        "bpm_max": 200.0,
    }


def _make_app(config):
    """Build the configured Flask app for whichever code layout exists."""
    os.makedirs(config["music_dir"], exist_ok=True)
    try:
        from bpm_tagger.web.app import create_app  # post-refactor package
    except ModuleNotFoundError:
        create_app = None

    if create_app is not None:
        return create_app(config)

    # Pre-refactor monolith path: configure the module-global Flask app.
    import web_ui
    from bpm_tagger import BPMDatabase

    web_ui._db = BPMDatabase(config["db_path"])
    web_ui._music_dir = config["music_dir"]
    web_ui._config = config
    web_ui._settings_path = str(Path(config["db_path"]).parent / "settings.json")
    web_ui._max_login_attempts = int(config.get("ui_max_login_attempts", 5))
    web_ui._lockout_seconds = int(config.get("ui_lockout_seconds", 300))
    # Reset brute-force counters between tests sharing the process.
    web_ui._login_attempts.clear()
    web_ui._login_lockout_until.clear()
    app = web_ui.app
    app.secret_key = config.get("ui_secret_key") or secrets.token_hex(16)
    app.config["UI_PASSWORD"] = config["ui_password"]
    app.config["TESTING"] = True
    return app


@pytest.fixture
def app(base_config):
    return _make_app(base_config)


@pytest.fixture
def client(app):
    app.config["TESTING"] = True
    return app.test_client()
