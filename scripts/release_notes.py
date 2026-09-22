#!/usr/bin/env python3
"""Print the CHANGELOG.md section for one version, for a GitHub release body.

Used by .github/workflows/docker-publish.yml to turn the release commit's
CHANGELOG entry into the release notes; also handy locally:

    python scripts/release_notes.py 2.17.0
    python scripts/release_notes.py --title 2.17.0

Exits 1 with a clear message when the version has no section, so a release that
was shipped without a changelog entry fails visibly instead of publishing empty
notes (v2.11.3 and v2.13.0 both slipped out that way).
"""

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"

# "## v2.17.0 — 2026-09-09" (the date suffix is optional)
HEADING = re.compile(r"^## v(\d+\.\d+\.\d+)\s*(?:—.*)?$")


def section(version: str) -> str | None:
    """The body of this version's changelog section, or None if absent."""
    body: list[str] | None = None
    for line in CHANGELOG.read_text(encoding="utf-8").splitlines():
        m = HEADING.match(line.strip())
        if m:
            if body is not None:          # next heading ends the one we wanted
                break
            body = [] if m.group(1) == version else None
        elif body is not None:
            body.append(line)
    return "\n".join(body).strip() if body else None


def title(version: str, body: str) -> str:
    """'vX.Y.Z — headline' when the section opens with a bold headline line."""
    first = next((line for line in body.splitlines() if line.strip()), "")
    m = re.match(r"^\*\*(.+?)\*\*$", first.strip())
    return f"v{version} — {m.group(1).rstrip('.')}" if m else f"v{version}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("version", help="version without the leading v, e.g. 2.17.0")
    ap.add_argument("--title", action="store_true",
                    help="print the release title instead of the notes")
    args = ap.parse_args()

    body = section(args.version)
    if body is None:
        print(f"CHANGELOG.md has no '## v{args.version}' section — "
              f"add one before releasing.", file=sys.stderr)
        return 1
    print(title(args.version, body) if args.title else body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
