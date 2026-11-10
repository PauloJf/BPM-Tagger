"""docs/contracts/requests-v1.json is the contract for requesting a missing track from a
connected companion app. The client here is held to it against a loopback server that only
speaks the contract: it rejects any request body the contract forbids and answers with the
contract's own examples. No real network."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import contract_schema as cs
import pytest

from bpm_tagger.db import BPMDatabase
from bpm_tagger.integrations import music_grabber as mg

CONTRACT_PATH = Path(__file__).resolve().parent.parent / "docs" / "contracts" / "requests-v1.json"
# Pinned: the companion keeps the same file under the same digest. Changing the contract means
# changing both sides, additively, and both pins.
CONTRACT_DIGEST = "2c4a41c73fb3be90352643cb08474b812d332b359cd8b36db487f3ebe1e1f3aa"
KEY = "mgk_contract_test_key"


@pytest.fixture(scope="module")
def contract():
    return cs.load(CONTRACT_PATH)


class ContractPeer:
    """A loopback HTTP server that answers /api/v1/requests the way the contract says."""

    def __init__(self, contract):
        self.contract, self.seen, self.violations = contract, [], []
        self.answers = list(contract["examples"]["RequestAnswer"])
        self.views = {v["id"]: v for v in contract["examples"]["RequestView"]}
        peer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, body):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _authorized(self):
                if self.headers.get("Authorization") == f"Bearer {KEY}":
                    return True
                self._send(401, {"error": "unauthorized", "message": "bad key"})
                return False

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                peer.seen.append(("POST", self.path, body))
                if not self._authorized():
                    return
                peer.violations += cs.errors(body, {"$ref": "RequestBody"}, contract)
                answer = peer.answers.pop(0) if peer.answers else {"ok": True, "result": "in_library"}
                self._send(201 if answer["result"] == "queued" else 200, answer)

            def do_GET(self):
                url = urlparse(self.path)
                peer.seen.append(("GET", self.path, None))
                if not self._authorized():
                    return
                ids = parse_qs(url.query).get("ids", [""])[0]
                if not ids:
                    return self._send(400, contract["examples"]["Error"][0])
                want = [int(x) for x in ids.split(",")]
                self._send(200, {"requests": [peer.views[i] for i in want if i in peer.views]})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def peer(contract):
    p = ContractPeer(contract)
    yield p
    p.close()
    assert not p.violations, f"sent a request the contract forbids: {p.violations}"


@pytest.fixture
def linked(tmp_path, peer):
    db = BPMDatabase(str(tmp_path / "bpm.db"))
    return db, {"music_grabber_url": peer.url, "music_grabber_api_key": KEY, "music_dir": str(tmp_path / "music")}


def test_the_contract_file_is_the_pinned_one_and_its_examples_conform(contract):
    assert cs.digest(CONTRACT_PATH) == CONTRACT_DIGEST
    for name, examples in contract["examples"].items():
        for example in examples:
            cs.check(example, name, contract)


def test_the_public_contract_names_no_provider():
    import re
    text = CONTRACT_PATH.read_text(encoding="utf-8").lower()
    for word in ("deezer", "spotify", "yt-dlp", "ytdlp", "youtube", "arl"):
        assert not re.search(rf"\b{re.escape(word)}\b", text), word


def test_every_request_body_we_build_is_allowed_by_the_contract(linked, peer, contract):
    db, config = linked
    rows = [{"title": "Levitating", "artist": "Dua Lipa", "album": "Future Nostalgia",
             "duration_ms": 203000, "isrc": "GBAHT2000001"},
            {"title": "Only A Title", "artist": "Someone", "album": None, "duration_ms": None, "isrc": None}]
    for i, row in enumerate(rows):
        mg.request_track(db, config, f"pt:{i}", mg.payload_for_playlist_track(row), who="ana")
    mg.request_track(db, config, "dz:101", {"dz_id": "101"}, who="admin", title="T", artist="A")
    sent = [body for method, _, body in peer.seen if method == "POST"]
    assert len(sent) == 3
    assert sent[0]["source_ref"] == "bpm-tagger:pt:0" and sent[0]["requested_for"] == "ana"
    for body in sent:
        cs.check(body, "RequestBody", contract)


def test_each_contract_answer_becomes_a_stored_state(linked, contract):
    db, config = linked
    got = [mg.request_track(db, config, f"ref:{i}", {"title": "t", "artist": "a"}, who="ana")
           for i in range(len(contract["examples"]["RequestAnswer"]))]
    assert [g["status"] for g in got] == ["pending", "in_library", "downloading", "done"]
    assert [g["request_id"] for g in got] == [7, None, 7, 7]
    assert got[1]["active"] is False and got[0]["active"] is True


def test_contract_views_update_the_stored_request(linked, contract):
    db, config = linked
    mg.request_track(db, config, "ref:a", {"title": "t", "artist": "a"}, who="ana")      # queued as id 7
    states = mg.refresh(db, config, ["ref:a"], force=True)
    done = contract["examples"]["RequestView"][0]
    assert states["ref:a"]["status"] == "done" and states["ref:a"]["path"] == done["path"]
    assert states["ref:a"]["needs_choice"] is False and states["ref:a"]["active"] is False


def test_the_connection_check_relies_on_the_contracts_error(linked, peer):
    _, config = linked
    assert mg.Client(config).check() == {"ok": True}
    assert peer.seen[-1][:2] == ("GET", "/api/v1/requests")
    with pytest.raises(mg.GrabberError) as e:
        mg.Client({**config, "music_grabber_api_key": "mgk_wrong"}).check()
    assert e.value.code == "bad_key"
