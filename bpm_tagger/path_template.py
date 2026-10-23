"""Render a downloaded track's destination path from a template (§5).

Strictest-charset sanitization so paths are valid on both Windows and Linux,
POSIX-separated, ≤180 chars per segment, with AlbumArtist→Artist fallback and
on-disk collision suffixing (" (2)"). PurePosixPath keeps output '/'-separated
regardless of the dev OS.
"""

import os
import re
import string
from pathlib import PurePosixPath

# Illegal on Windows/most filesystems + control chars. '/' is illegal *inside*
# a field value (segments are split on the template's literal '/').
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WS = re.compile(r"\s+")
_RESERVED = {"CON", "PRN", "AUX", "NUL",
             *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def sanitize_segment(s: str, maxlen: int = 180) -> str:
    s = _ILLEGAL.sub(" ", s or "")
    s = _WS.sub(" ", s).strip()
    s = s.strip(". ")                       # Windows: no trailing dot/space
    if not s:
        return "_"
    if len(s) > maxlen:
        s = s[:maxlen].strip(". ") or "_"
    if s.upper() in _RESERVED or s.upper().split(".")[0] in _RESERVED:
        s = "_" + s
    return s


def render(template: str, meta: dict, ext: str) -> str:
    """Render `template` for `meta` producing a POSIX relative path ending in .ext."""
    values = {
        "AlbumArtist": meta.get("album_artist") or meta.get("artist") or "Unknown Artist",
        "Artist":      meta.get("artist") or meta.get("album_artist") or "Unknown Artist",
        "Album":       meta.get("album") or "Unknown Album",
        "Title":       meta.get("title") or "Unknown Title",
        "TrackNo":     int(meta.get("track_no") or 0),
        "DiscNo":      int(meta.get("disc_no") or 0),
        "Year":        meta.get("year") or "",
        "ext":         (ext or "").lstrip("."),
    }
    fmt = string.Formatter()
    out: list[str] = []
    try:
        pieces = list(fmt.parse(template))
    except ValueError:
        pieces = [(template, None, None, None)]
    for literal, field, spec, _conv in pieces:
        out.append(literal)                 # literal separators kept verbatim
        if field is not None:
            key = field or ""
            try:
                val = format(values[key], spec or "")
            except (KeyError, ValueError, TypeError):
                val = str(values.get(key, ""))
            out.append(_ILLEGAL.sub(" ", str(val)))  # no '/' allowed in a field
    raw = "".join(out).replace("\\", "/")
    parts = [sanitize_segment(p) for p in raw.split("/") if p.strip()]
    if not parts:
        parts = [sanitize_segment(f"{values['Title']}.{values['ext']}")]
    return str(PurePosixPath(*parts))


def unique_path(music_dir: str, rel_path: str) -> str:
    """Absolute destination path, suffixing ' (2)', ' (3)'… before the extension
    if the target already exists on disk. Returns an OS-native absolute path."""
    abs_path = os.path.join(music_dir, *PurePosixPath(rel_path).parts)
    if not os.path.exists(abs_path):
        return abs_path
    root, ext = os.path.splitext(abs_path)
    n = 2
    while os.path.exists(f"{root} ({n}){ext}"):
        n += 1
    return f"{root} ({n}){ext}"
