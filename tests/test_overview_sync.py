"""The Docker Hub overview sync check.

The sync step runs with continue-on-error, and GitHub then reports a failed step
as conclusion=success — so a 403 left the published overview several releases
stale with nothing in the run to show it. This check looks at what Docker Hub
actually serves instead of trusting the action's exit code.
"""

import importlib.util
import pathlib

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "check_overview_sync.py"
spec = importlib.util.spec_from_file_location("check_overview_sync", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

CRLF_README = b"# BPM Tagger\r\n\r\nhello\r\n\r\n\r\n"


@pytest.fixture
def readme(tmp_path, monkeypatch):
    """Point the checker at a throwaway overview file."""
    f = tmp_path / "DOCKERHUB_README.md"
    monkeypatch.setattr(mod, "README", f)
    return f


def _run(monkeypatch, live, capsys, github=False):
    monkeypatch.setattr(mod, "live_overview", lambda repo: live)
    argv = ["check_overview_sync.py"] + (["--github"] if github else [])
    monkeypatch.setattr(mod.sys, "argv", argv)
    code = mod.main()
    return code, capsys.readouterr()


def test_in_sync_passes(readme, monkeypatch, capsys):
    readme.write_text("# BPM Tagger\n\nhello\n", encoding="utf-8")
    code, out = _run(monkeypatch, "# BPM Tagger\n\nhello\n", capsys)
    assert code == 0
    assert "in sync" in out.out


def test_line_endings_and_trailing_blanks_do_not_count(readme, monkeypatch, capsys):
    # Raw bytes: write_text() re-translates newlines on Windows, which would
    # turn the CRLFs this test is about into CR CR LF.
    readme.write_bytes(CRLF_README)
    code, _ = _run(monkeypatch, "# BPM Tagger\n\nhello", capsys)
    assert code == 0


def test_stale_overview_fails(readme, monkeypatch, capsys):
    readme.write_text("new trimmed content\n", encoding="utf-8")
    code, out = _run(monkeypatch, "old stale content that is much longer\n", capsys)
    assert code == 1
    assert "does not match" in out.err


def test_truncated_overview_fails(readme, monkeypatch, capsys):
    """A sync that 'succeeds' but writes a clipped body is still a failure."""
    body = "x" * 5000
    readme.write_text(body, encoding="utf-8")
    code, _ = _run(monkeypatch, body[:3000], capsys)
    assert code == 1


def test_github_mode_emits_an_error_annotation(readme, monkeypatch, capsys, tmp_path):
    readme.write_text("new\n", encoding="utf-8")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code, out = _run(monkeypatch, "old\n", capsys, github=True)
    assert code == 1
    # ::error:: renders as a red annotation on the run and the commit; a
    # ::warning:: was judged too easy to scroll past, which is how this hid.
    assert out.out.startswith("::error title=Docker Hub overview out of sync::")
    assert "DOCKERHUB_DESCRIPTION_TOKEN" in summary.read_text(encoding="utf-8")


def test_unreachable_api_reports_rather_than_raising(readme, monkeypatch, capsys):
    readme.write_text("new\n", encoding="utf-8")

    def boom(repo):
        raise OSError("connection reset")

    monkeypatch.setattr(mod, "live_overview", boom)
    monkeypatch.setattr(mod.sys, "argv", ["check_overview_sync.py"])
    assert mod.main() == 1
    assert "could not read" in capsys.readouterr().err
