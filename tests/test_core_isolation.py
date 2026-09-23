"""The core (BPM detection + tagging) must work without any optional layer.

Two guards (docs/plans/core-decoupling.md, step B6):

* **Isolation** — in a fresh interpreter where every optional-layer package is
  unimportable, the core modules import and a real scan detects a BPM and
  writes the tag.
* **Layering** — core modules never import an optional layer at module level.
  Function-level (lazy) imports behind a config check are the allowed pattern.

Both run in the ``core-only`` CI job, which installs only requirements-core.txt.
"""

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.core

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "bpm_tagger"

# Packages only the optional layers need. None may be imported by the core.
OPTIONAL_PACKAGES = ("flask", "waitress", "requests", "rapidfuzz", "streamrip", "yt_dlp", "PIL")

# Optional layers inside the package.
OPTIONAL_MODULES = ("web", "grabber", "integrations", "notify", "install_ping")

# The core: detection, tagging, scanning, the DB record, config and dispatch.
CORE_PATHS = ("bpm", "scan", "db", "config.py", "main.py", "hooks.py", "text.py", "__init__.py")


_ISOLATED_SCAN = textwrap.dedent("""
    import importlib.abc, sys

    BLOCKED = set(sys.argv[1].split(","))

    class _Block(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in BLOCKED:
                raise ImportError(f"{name} is blocked (core isolation test)")
            return None

    sys.meta_path.insert(0, _Block())

    import numpy as np
    import soundfile as sf
    from pathlib import Path

    music, db_path = Path(sys.argv[2]), sys.argv[3]
    music.mkdir()
    # 20 s click track at 120 BPM: short decaying noise bursts on each beat.
    sr, bpm, secs = 22050, 120, 20
    y = np.zeros(sr * secs, dtype=np.float32)
    burst = (np.random.default_rng(0).standard_normal(int(0.02 * sr))
             * np.exp(-np.linspace(0, 8, int(0.02 * sr)))).astype(np.float32)
    step = int(sr * 60 / bpm)
    for i in range(0, len(y) - len(burst), step):
        y[i:i + len(burst)] += burst
    track = music / "click.flac"
    sf.write(str(track), y, sr, format="FLAC")

    import bpm_tagger.bpm.pipeline, bpm_tagger.db, bpm_tagger.main  # noqa: F401
    from bpm_tagger.config import build_config
    from bpm_tagger.scan.scanner import BPMTagger
    from mutagen.flac import FLAC

    tagger = BPMTagger(build_config())
    tagger.scan_directory(force=False)

    row = tagger.db.get_track(str(track))
    assert row and row["status"] == "done", row
    assert row["bpm"], row
    tag = FLAC(str(track)).get("bpm") or FLAC(str(track)).get("BPM")
    assert tag, "BPM tag not written"

    leaked = sorted(m for m in sys.modules if m.split(".")[0] in BLOCKED)
    assert not leaked, leaked
    print("OK", row["bpm"])
""")


def test_core_scans_with_every_optional_package_blocked(tmp_path):
    env = {
        **os.environ,
        "MUSIC_DIR": str(tmp_path / "music"),
        "DB_PATH": str(tmp_path / "bpm.db"),
        "ENABLE_UI": "false",
        "GRABBER_ENABLED": "false",
        "USE_DEEPRHYTHM": "false",
        "WRITE_TAGS": "true",
        "PYTHONPATH": str(ROOT),
    }
    for k in ("NTFY_URL", "NTFY_TOPIC", "NAVIDROME_URL", "INSTALL_PING"):
        env.pop(k, None)
    proc = subprocess.run(
        [sys.executable, "-c", _ISOLATED_SCAN, ",".join(OPTIONAL_PACKAGES),
         str(tmp_path / "music"), str(tmp_path / "bpm.db")],
        env=env, cwd=str(tmp_path), capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout


def _core_files():
    for rel in CORE_PATHS:
        p = PKG / rel
        yield from (sorted(p.rglob("*.py")) if p.is_dir() else [p])


def _module_level_imports(tree: ast.Module):
    """Imports that execute at import time: top level, plus the bodies of
    top-level if/try blocks — but not ``if TYPE_CHECKING:``."""
    stack = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
        elif isinstance(node, ast.If):
            if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
                continue
            stack += node.body + node.orelse
        elif isinstance(node, ast.Try):
            stack += node.body + node.orelse + node.finalbody
            for h in node.handlers:
                stack += h.body


def _resolve(file: Path, node) -> list[str]:
    """Absolute dotted names an import statement refers to."""
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if node.level == 0:
        return [node.module or ""]
    pkg_parts = list(file.relative_to(ROOT).with_suffix("").parts)[:-1]
    base = pkg_parts[: len(pkg_parts) - (node.level - 1)]
    mod = ".".join(base + ([node.module] if node.module else []))
    # `from .. import hooks` names submodules, not attributes
    return [mod] if node.module else [f"{mod}.{a.name}" for a in node.names]


def test_core_never_imports_an_optional_layer_at_module_level():
    bad = []
    optional = {f"bpm_tagger.{m}" for m in OPTIONAL_MODULES}
    for f in _core_files():
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in _module_level_imports(tree):
            for name in _resolve(f, node):
                top = name.split(".")[0]
                hit = (top in OPTIONAL_PACKAGES
                       or any(name == o or name.startswith(o + ".") for o in optional))
                if hit:
                    bad.append(f"{f.relative_to(ROOT)}:{node.lineno} imports {name}")
    assert not bad, "core → optional-layer imports at module level:\n" + "\n".join(bad)
