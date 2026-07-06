"""Backwards-compatibility shim.

The web UI now lives in the ``bpm_tagger.web`` package. This module preserves
the historical ``web_ui.start`` / ``web_ui.create_app`` import surface.
"""

from bpm_tagger.web.app import create_app, start  # noqa: F401
