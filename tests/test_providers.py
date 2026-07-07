"""Provider tests — Monochrome (fake requests session) + yt-dlp (fake YoutubeDL)."""

import pytest

from bpm_tagger.grabber.providers import build_providers
from bpm_tagger.grabber.providers.base import ProviderCandidate, TrackMeta
from bpm_tagger.grabber.providers.deezer import DeezerProvider
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


# ── Deezer fake (async streamrip client) ──────────────────────────────────────
class FakeDeezerDownloadable:
    extension = "mp3"
    _size = 6

    async def download(self, path, cb):
        with open(path, "wb") as fh:
            for chunk in (b"abc", b"def"):
                fh.write(chunk)
                cb(len(chunk))


class FakeDeezerClient:
    def __init__(self, arl):
        self.arl = arl
        self.logged_in = False
        self.session = None  # → _close() is a no-op

    async def login(self):
        if not self.arl:
            raise RuntimeError("MissingCredentialsError")
        self.logged_in = True

    async def search(self, media_type, query, limit=200):
        return [{"total": 2, "data": [
            {"id": 3380574911, "title": "Voices In My Head",
             "artist": {"name": "Anyma"},
             "album": {"title": "Genesys", "cover_xl": "http://cover.xl"},
             "duration": 146, "isrc": "USUG12500914"},
            {"id": 2, "title": "Other", "artist": {"name": "X"},
             "album": {"title": "Y"}, "duration": 100},
        ]}]

    async def get_downloadable(self, item_id, quality=2):
        return FakeDeezerDownloadable()


def _deezer():
    p = DeezerProvider({"deezer_arl": "fake-arl", "deezer_quality": "MP3_128"})
    p._client_factory = FakeDeezerClient
    return p


def test_deezer_search_parses_candidates():
    p = _deezer()
    cands = p.search(TrackMeta(title="Voices In My Head", artist="Anyma"))
    assert len(cands) == 2
    c = cands[0]
    assert c.provider == "deezer" and c.provider_track_id == "3380574911"
    assert c.title == "Voices In My Head" and c.artist == "Anyma"
    assert c.album == "Genesys" and c.duration_ms == 146000
    assert c.isrc == "USUG12500914"  # ISRC feeds the matcher
    assert c.quality == "MP3_128" and c.cover_url == "http://cover.xl"


def test_deezer_download_streams_and_reports_progress(tmp_path):
    p = _deezer()
    seen = []
    cand = ProviderCandidate(provider="deezer", provider_track_id="3380574911")
    df = p.download(cand, str(tmp_path), progress_cb=lambda f: seen.append(f))
    assert df.ext == "mp3" and df.provider == "deezer" and df.quality == "MP3_128"
    with open(df.path, "rb") as fh:
        assert fh.read() == b"abcdef"
    assert seen and seen[-1] == 1.0


def test_deezer_search_empty_without_arl():
    p = DeezerProvider({"deezer_arl": ""})
    p._client_factory = FakeDeezerClient
    assert p.search(TrackMeta(title="x", artist="y")) == []


def test_deezer_healthcheck():
    assert _deezer().healthcheck() is True
    assert DeezerProvider({"deezer_arl": ""}).healthcheck() is False


# ── ordering ──────────────────────────────────────────────────────────────────
def test_build_providers_monochrome_on_hold():
    # Monochrome is on hold: skipped even when it's in the order AND configured.
    provs = build_providers({"provider_order": "monochrome,ytdlp"})
    assert [p.name for p in provs] == ["ytdlp"]
    provs = build_providers({"provider_order": "ytdlp,monochrome",
                             "monochrome_base_url": "http://m"})
    assert [p.name for p in provs] == ["ytdlp"]  # monochrome on hold


def test_build_providers_deezer_gated_on_arl():
    provs = build_providers({"provider_order": "deezer,ytdlp", "deezer_arl": "x"})
    assert [p.name for p in provs] == ["deezer", "ytdlp"]
    provs = build_providers({"provider_order": "deezer,ytdlp"})  # no arl
    assert [p.name for p in provs] == ["ytdlp"]  # deezer skipped (unconfigured)
