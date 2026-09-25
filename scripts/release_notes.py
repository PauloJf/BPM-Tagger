#!/usr/bin/env python3
"""Print the CHANGELOG.md section for one version, for a GitHub release body.

Used by .github/workflows/docker-publish.yml to turn the release commit's
CHANGELOG entry into the release notes; also handy locally:

    python scripts/release_notes.py 2.17.0
    python scripts/release_notes.py --title 2.17.0
    python scripts/release_notes.py --unreleased   # beta pre-release notes

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

# "## v2.17.0 — 2026-09-09" (the date suffix is optional), or "## Unreleased"
HEADING = re.compile(r"^## (?:v(\d+\.\d+\.\d+)\s*(?:—.*)?|(Unreleased))$")


def section(version: str) -> str | None:
    """The body of this version's changelog section, or None if absent.
    ``version="Unreleased"`` returns the not-yet-released section, which the
    beta pre-release notes use (.github/workflows/docker-beta.yml)."""
    body: list[str] | None = None
    for line in CHANGELOG.read_text(encoding="utf-8").splitlines():
        m = HEADING.match(line.strip())
        if m:
            if body is not None:          # next heading ends the one we wanted
                break
            body = [] if (m.group(1) or m.group(2)) == version else None
        elif body is not None:
            body.append(line)
    return "\n".join(body).strip() if body else None


def title(version: str, body: str) -> str:
    """'vX.Y.Z — headline' when the section opens with a bold headline line."""
    first = next((line for line in body.splitlines() if line.strip()), "")
    m = re.match(r"^\*\*(.+?)\*\*$", first.strip())
    return f"v{version} — {m.group(1).rstrip('.')}" if m else f"v{version}"


def _utf8_stdio() -> None:
    """Don't die on a non-UTF-8 console.

    CI is UTF-8, but a Windows terminal defaults to cp1252 and the changelog is
    full of arrows and em dashes — printing one there raises UnicodeEncodeError
    and takes the whole script with it.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def main() -> int:
    _utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("version", nargs="?", help="version without the leading v, e.g. 2.17.0")
    ap.add_argument("--title", action="store_true",
                    help="print the release title instead of the notes")
    ap.add_argument("--unreleased", action="store_true",
                    help="print the '## Unreleased' section (beta pre-release notes)")
    args = ap.parse_args()
    if not args.unreleased and not args.version:
        ap.error("give a version, or --unreleased")

    body = section("Unreleased" if args.unreleased else args.version)
    if body is None:
        what = "'## Unreleased'" if args.unreleased else f"'## v{args.version}'"
        print(f"CHANGELOG.md has no {what} section — add one before releasing.",
              file=sys.stderr)
        return 1
    if args.unreleased:
        print(body)
        return 0
    print(title(args.version, body) if args.title else body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
