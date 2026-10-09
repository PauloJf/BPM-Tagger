"""docs/subsonic-api.md must list every Subsonic method the server implements —
so the reference can't quietly fall behind the code."""

import re
from pathlib import Path

from bpm_tagger.web.subsonic.handlers import METHODS

DOC = Path(__file__).resolve().parent.parent / "docs" / "subsonic-api.md"


def _documented() -> set[str]:
    """Method names in the first column of the Methods tables."""
    text = DOC.read_text(encoding="utf-8").split("## Methods", 1)[1].split("## Not implemented", 1)[0]
    names = set()
    for line in text.splitlines():
        if line.startswith("| `"):
            names |= set(re.findall(r"`([A-Za-z0-9]+)`", line.split("|")[1]))
    return names


def test_every_implemented_method_is_documented():
    missing = sorted(set(METHODS) - _documented())
    assert not missing, f"add these to docs/subsonic-api.md: {missing}"


def test_nothing_documented_that_isnt_implemented():
    extra = sorted(_documented() - set(METHODS))
    assert not extra, f"documented but not in METHODS: {extra}"
