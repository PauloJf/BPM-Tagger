"""Provider tests — Monochrome (fake requests session) + yt-dlp (fake YoutubeDL)."""

import pytest

from bpm_tagger.grabber.providers import build_providers
from bpm_tagger.grabber.providers.base import ProviderCandidate, TrackMeta
from bpm_tagger.grabber.providers.monochrome import MonochromeProvider
from bpm_tagger.grabber.providers.ytdlp import YtDlpProvider


# ── Fake requests plumbing for Monochrome ─────────────────────────────────────
class FakeResp:
    def __init__(self, status=200, json_data=None, headers=None, chunks=None):
        self.status_code = status
        self._json = json_data or {}
        self.headers = headers or {}
        self._chunks = chunks or []

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=65536):
        yield from self._chunks


class FakeSession:
    def __init__(self, handler):
        self.handler = handler
        self.calls = 0

    def get(self, url, params=None, headers=None, stream=False, timeout=None):
        self.calls += 1
        return self.handler(url, params, stream, self.calls)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("bpm_tagger.grabber.providers.monochrome.time.sleep", lambda *_: None)


def _mono():
    return MonochromeProvider({"monochrome_base_url": "http://mono.local", "monochrome_api_key": "k"})


SEARCH_JSON = {"items": [
    {"id": 111, "title": "Blinding Lights", "artists": [{"name": "The Weeknd"}],
     "album": {"title": "After Hours"}, "duration": 200, "isrc": "USUG11904206",
     "audioQuality": "LOSSLESS"},
    {"id": 222, "title": "Other", "artists": [{"name": "Nobody"}], "duration": 180},
]}


def test_monochrome_search_parses_candidates():
    p = _mono()
    p.session = FakeSession(lambda url, params, stream, n: FakeResp(json_data=SEARCH_JSON))
    cands = p.search(TrackMeta(title="Blinding Lights", artist="The Weeknd"))
    assert len(cands) == 2
    c = cands[0]
    assert c.provider == "monochrome" and c.provider_track_id == "111"
    assert c.title == "Blinding Lights" and c.artist == "The Weeknd"
    assert c.duration_ms == 200000 and c.isrc == "USUG11904206"


def test_monochrome_download_streams_and_reports_progress(tmp_path):
    p = _mono()
    p.session = FakeSession(lambda url, params, stream, n: FakeResp(
        headers={"Content-Length": "6", "Content-Type": "audio/flac"},
        chunks=[b"abc", b"def"]))
    seen = []
    cand = ProviderCandidate(provider="monochrome", provider_track_id="111")
    df = p.download(cand, str(tmp_path), progress_cb=lambda f: seen.append(f))
    assert df.ext == "flac"
    with open(df.path, "rb") as fh:
        assert fh.read() == b"abcdef"
    assert seen and seen[-1] == 1.0


def test_monochrome_429_then_success():
    p = _mono()

    def handler(url, params, stream, n):
        if n == 1:
            return FakeResp(status=429, headers={"Retry-After": "1"})
        return FakeResp(json_data=SEARCH_JSON)

    p.session = FakeSession(handler)
    cands = p.search(TrackMeta(title="x", artist="y"))
    assert len(cands) == 2  # retried after the 429


def test_monochrome_circuit_breaker_opens_after_failures():
    p = _mono()

    def boom(url, params, stream, n):
        import requests
        raise requests.ConnectionError("down")

    p.session = FakeSession(boom)
    for _ in range(5):
        assert p.search(TrackMeta(title="x", artist="y")) == []
    assert p._breaker_open()
    calls_before = p.session.calls
    assert p.search(TrackMeta(title="x", artist="y")) == []  # short-circuited
    assert p.session.calls == calls_before  # breaker skipped the network entirely


# ── yt-dlp fake ───────────────────────────────────────────────────────────────
class FakeYDL:
    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, target, download=False):
        if not download:
            return {"entries": [
                {"id": "v1", "title": "Song", "uploader": "The Artist - Topic", "duration": 200},
                {"id": "v2", "title": "Song (Live)", "uploader": "Some Rando", "duration": 210},
            ]}
        for h in self.opts.get("progress_hooks", []):
            h({"status": "finished", "filename": self.opts["outtmpl"].replace("%(ext)s", "webm")})
        return {"id": "v1", "ext": "webm"}

    def prepare_filename(self, info):
        return self.opts["outtmpl"].replace("%(ext)s", info.get("ext", "webm"))


def test_ytdlp_search_marks_topic_channel():
    p = YtDlpProvider({})
    p._ydl_cls = FakeYDL
    cands = p.search(TrackMeta(title="Song", artist="The Artist"))
    assert len(cands) == 2
    assert cands[0].is_topic is True and cands[0].artist == "The Artist"
    assert cands[1].is_topic is False


def test_ytdlp_download_returns_file(tmp_path):
    p = YtDlpProvider({})
    p._ydl_cls = FakeYDL
    cand = ProviderCandidate(provider="ytdlp", provider_track_id="v1", url="http://x")
    seen = []
    df = p.download(cand, str(tmp_path), progress_cb=lambda f: seen.append(f))
    assert df.ext == "webm" and df.provider == "ytdlp"
    assert seen[-1] == 1.0


# ── ordering ──────────────────────────────────────────────────────────────────
def test_build_providers_order_and_skip_unconfigured():
    provs = build_providers({"provider_order": "monochrome,ytdlp"})  # no monochrome url
    assert [p.name for p in provs] == ["ytdlp"]  # monochrome skipped (unconfigured)
    provs = build_providers({"provider_order": "ytdlp,monochrome",
                             "monochrome_base_url": "http://m"})
    assert [p.name for p in provs] == ["ytdlp", "monochrome"]
