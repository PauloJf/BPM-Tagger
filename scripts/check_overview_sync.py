#!/usr/bin/env python3
"""Verify that Docker Hub's repository overview matches DOCKERHUB_README.md.

The publish workflow syncs the overview with peter-evans/dockerhub-description,
which needs a token that can PATCH the repo — a different auth surface from the
image push. When it can't, it 403s. That step runs with `continue-on-error` so a
failed sync never fails a publish whose images already went out, but GitHub then
reports the step's *conclusion* as success, so the failure was invisible: the
overview silently sat several releases out of date.

This checks the published result rather than the action's exit code, so it also
catches a sync that "succeeded" but truncated or wrote something unexpected.

    python scripts/check_overview_sync.py                  # human-readable
    python scripts/check_overview_sync.py --github         # ::error:: annotation

Exit code is 0 when in sync, 1 when not. In CI the caller deliberately ignores
it and turns the annotation into the signal, so the publish stays green.
"""

import argparse
import json
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "DOCKERHUB_README.md"
DEFAULT_REPO = "gatoserio/bpm-tagger"


def live_overview(repo: str) -> str:
    """The repository's current overview text, from Docker Hub's public API."""
    url = f"https://hub.docker.com/v2/repositories/{repo}"
    with urllib.request.urlopen(urllib.request.Request(
            url, headers={"Accept": "application/json"}), timeout=20) as resp:
        return json.load(resp).get("full_description") or ""


def normalize(text: str) -> str:
    """Compare on content, not line endings or trailing blank lines."""
    return text.replace("\r\n", "\n").strip()


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
    ap.add_argument("--repo", default=DEFAULT_REPO, help=f"default: {DEFAULT_REPO}")
    ap.add_argument("--github", action="store_true",
                    help="emit a ::error:: workflow annotation on mismatch")
    args = ap.parse_args()

    want = normalize(README.read_text(encoding="utf-8"))
    try:
        got = normalize(live_overview(args.repo))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        msg = f"could not read the Docker Hub overview for {args.repo}: {exc}"
        print(f"::error title=Overview check failed::{msg}" if args.github else msg,
              file=sys.stderr)
        return 1

    if got == want:
        print(f"Docker Hub overview is in sync ({len(want)} chars).")
        return 0

    detail = (f"Docker Hub's overview for {args.repo} does not match "
              f"DOCKERHUB_README.md (live {len(got)} chars, file {len(want)}). "
              f"The sync step runs with continue-on-error and reports success even "
              f"when it 403s — check its log for 'Sending PATCH request'. Updating "
              f"the description needs a token that can write the repo: set the "
              f"DOCKERHUB_DESCRIPTION_TOKEN secret to a write-scoped Docker Hub PAT.")
    if args.github:
        print(f"::error title=Docker Hub overview out of sync::{detail}")
        summary = pathlib.Path(__import__("os").environ.get("GITHUB_STEP_SUMMARY", ""))
        if str(summary):
            with summary.open("a", encoding="utf-8") as fh:
                fh.write(f"### ⚠ Docker Hub overview out of sync\n\n{detail}\n")
    else:
        print(detail, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
