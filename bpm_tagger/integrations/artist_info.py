"""Best-effort artist description (keyless, no account).

Deezer's public catalog has no artist bio, so resolve one through the free,
keyless chain MusicBrainz → Wikidata → Wikipedia:

    1. MusicBrainz artist search   name → MBID (the right entity, not a name guess)
    2. MusicBrainz url-rels        MBID → Wikidata Q-id
    3. Wikidata sitelinks          Q-id → English Wikipedia page title
    4. Wikipedia REST summary      title → short plain-text extract

Every step is best-effort; any failure (or a disambiguation page) yields "".
Resolving through MusicBrainz's Wikidata relation keeps us on the correct
musician entity instead of blindly querying Wikipedia by name.
"""

import logging

import requests

log = logging.getLogger(__name__)

# MusicBrainz/Wikimedia ask for a descriptive User-Agent identifying the app.
_UA = "BPM-Tagger/2.5 (https://github.com/PauloJf/BPM-Tagger)"
_T = 10


def _mb_artist_mbid(name: str) -> str:
    resp = requests.get("https://musicbrainz.org/ws/2/artist",
                        params={"query": f'artist:"{name}"', "fmt": "json", "limit": 1},
                        headers={"User-Agent": _UA}, timeout=_T)
    resp.raise_for_status()
    artists = resp.json().get("artists") or []
    return artists[0].get("id", "") if artists else ""


def _mb_wikidata_qid(mbid: str) -> str:
    resp = requests.get(f"https://musicbrainz.org/ws/2/artist/{mbid}",
                        params={"inc": "url-rels", "fmt": "json"},
                        headers={"User-Agent": _UA}, timeout=_T)
    resp.raise_for_status()
    for rel in resp.json().get("relations") or []:
        if rel.get("type") == "wikidata":
            url = (rel.get("url") or {}).get("resource") or ""
            if "/wiki/Q" in url:
                return url.rsplit("/wiki/", 1)[-1]
    return ""


def _wikidata_enwiki_title(qid: str) -> str:
    resp = requests.get("https://www.wikidata.org/w/api.php",
                        params={"action": "wbgetentities", "ids": qid,
                                "props": "sitelinks", "format": "json"},
                        headers={"User-Agent": _UA}, timeout=_T)
    resp.raise_for_status()
    entity = (resp.json().get("entities") or {}).get(qid) or {}
    return ((entity.get("sitelinks") or {}).get("enwiki") or {}).get("title", "")


def _wikipedia_summary(title: str) -> str:
    quoted = requests.utils.quote(title, safe="")
    resp = requests.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{quoted}",
                        headers={"User-Agent": _UA}, timeout=_T)
    resp.raise_for_status()
    d = resp.json()
    if d.get("type") == "disambiguation":
        return ""
    return d.get("extract") or ""


def artist_bio(name: str) -> str:
    """A short plain-text artist description, or "" when none can be resolved."""
    if not name or not name.strip():
        return ""
    try:
        mbid = _mb_artist_mbid(name)
        if not mbid:
            return ""
        qid = _mb_wikidata_qid(mbid)
        if not qid:
            return ""
        title = _wikidata_enwiki_title(qid)
        if not title:
            return ""
        return _wikipedia_summary(title)
    except Exception as exc:
        log.debug("artist_bio failed for %r: %s", name, exc)
        return ""
