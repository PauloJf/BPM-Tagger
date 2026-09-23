"""Which kind of Navidrome rescan each pass asks for.

Navidrome detects changes by mtime, and PRESERVE_MTIME deliberately restores
the mtime after every tag write. So after a pass that rewrites tags in place —
a forced scan, scan_review, retry_errors — a quick scan sees nothing changed
and skips every file, and the corrected BPMs never reach Navidrome. Those
passes have to ask for a full scan; incremental ones must not, because a full
scan of a large library is expensive and pointless when the mtimes really did
change.
"""

import pytest

import bpm_tagger.integrations.navidrome as nav

CONFIG = {
    "navidrome_url": "http://navidrome:4533",
    "navidrome_user": "admin",
    "navidrome_pass": "pw",
}


class _Resp:
    status_code = 200

    @staticmethod
    def json():
        return {"subsonic-response": {"status": "ok"}}


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake_get(url, params=None, timeout=None):
        seen.append({"url": url, "params": dict(params or {})})
        return _Resp()

    monkeypatch.setattr(nav.requests, "get", fake_get)
    return seen


def test_quick_by_default(calls):
    nav._trigger_navidrome_rescan(CONFIG)
    assert calls[0]["url"].endswith("/rest/startScan")
    assert "fullScan" not in calls[0]["params"]


def test_full_when_asked(calls):
    nav._trigger_navidrome_rescan(CONFIG, full=True)
    assert calls[0]["params"]["fullScan"] == "true"


def test_no_request_without_credentials(calls):
    nav._trigger_navidrome_rescan({"navidrome_url": "http://navidrome:4533"})
    assert calls == []


def test_a_failed_request_never_escapes(monkeypatch):
    """The rescan is a nice-to-have; it must not fail the scan that ran it."""
    def boom(*a, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(nav.requests, "get", boom)
    nav._trigger_navidrome_rescan(CONFIG, full=True)   # must not raise


# ── which pass asks for which ────────────────────────────────────────────────

@pytest.fixture
def tagger(monkeypatch):
    """A BPMTagger stub wired only for _finish_scan's rescan call."""
    from bpm_tagger.scan.scanner import BPMTagger

    t = BPMTagger.__new__(BPMTagger)
    t.config = CONFIG
    t.notifier = None
    asked = []
    monkeypatch.setattr("bpm_tagger.integrations.navidrome._trigger_navidrome_rescan",
                        lambda config, full=False: asked.append(full))
    return t, asked


COUNTS = {"tagged": 1, "needs_review": 0, "skipped": 0, "errors": 0}


def test_incremental_scan_asks_for_a_quick_rescan(tagger):
    t, asked = tagger
    t._finish_scan(dict(COUNTS), "Scan")
    assert asked == [False]


def test_forced_scan_asks_for_a_full_rescan(tagger):
    t, asked = tagger
    t._finish_scan(dict(COUNTS), "Scan", full_rescan=True)
    assert asked == [True]
