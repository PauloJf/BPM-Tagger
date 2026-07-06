"""Spotify Web API client — Authorization Code flow (§ external-reality notes).

Development-mode apps can read the owning user's own playlists; Client
Credentials cannot return playlist items, so a one-time user connect + stored
refresh token is required. The refresh token is persisted to oauth_tokens and
rotated tokens are re-saved. invalid_grant (revoked/expired) raises
SpotifyAuthError so the sync loop can mark disconnected instead of crashing.
"""

import base64
import logging
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests

from .matching import normalize_artist, normalize_title

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
SCOPES = "playlist-read-private playlist-read-collaborative"
SERVICE = "spotify"


class SpotifyError(Exception):
    """Generic Spotify API failure (network / 5xx / unexpected)."""


class SpotifyAuthError(SpotifyError):
    """Auth is broken (invalid_grant, revoked token) — user must reconnect."""


def _now():
    return datetime.now(timezone.utc)


def _iso(dt) -> str:
    return dt.isoformat()


class SpotifyClient:
    def __init__(self, config: dict, db):
        self.db = db
        self.client_id = config.get("spotify_client_id", "")
        self.client_secret = config.get("spotify_client_secret", "")
        self.redirect_uri = config.get("spotify_redirect_uri", "")
        self._access_token = None
        self._access_expiry = 0.0  # epoch seconds

    # ── configuration / status ────────────────────────────────────────────────
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)

    def is_connected(self) -> bool:
        tok = self.db.get_oauth_token(SERVICE)
        return bool(tok and tok.get("refresh_token"))

    def status(self) -> dict:
        tok = self.db.get_oauth_token(SERVICE)
        return {
            "configured": self.is_configured(),
            "connected": bool(tok and tok.get("refresh_token")),
            "scope": (tok or {}).get("scope", ""),
            "redirect_uri": self.redirect_uri,
        }

    def disconnect(self):
        self.db.delete_oauth_token(SERVICE)
        self._access_token = None
        self._access_expiry = 0.0

    # ── OAuth ────────────────────────────────────────────────────────────────
    def authorize_url(self, state: str) -> str:
        if not self.is_configured():
            raise SpotifyError("Spotify client not configured (set SPOTIFY_CLIENT_ID/SECRET/REDIRECT_URI)")
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": SCOPES,
            "state": state,
            "show_dialog": "false",
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    def _basic_auth_header(self) -> dict:
        raw = f"{self.client_id}:{self.client_secret}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}

    def exchange_code(self, code: str) -> None:
        """Exchange an authorization code for tokens and persist them."""
        resp = requests.post(TOKEN_URL, data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
        }, headers=self._basic_auth_header(), timeout=15)
        if resp.status_code != 200:
            raise SpotifyAuthError(f"Token exchange failed: {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        self._store_tokens(data)

    def _store_tokens(self, data: dict) -> None:
        access = data.get("access_token", "")
        refresh = data.get("refresh_token")  # absent on refresh responses
        expires_in = int(data.get("expires_in", 3600))
        scope = data.get("scope", SCOPES)
        expires_at = _iso(_now() + timedelta(seconds=expires_in))
        self.db.save_oauth_token(SERVICE, access, refresh, expires_at, scope)
        self._access_token = access
        self._access_expiry = time.time() + expires_in - 60

    def _refresh(self) -> None:
        tok = self.db.get_oauth_token(SERVICE)
        if not tok or not tok.get("refresh_token"):
            raise SpotifyAuthError("Not connected to Spotify")
        resp = requests.post(TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": tok["refresh_token"],
        }, headers=self._basic_auth_header(), timeout=15)
        if resp.status_code == 400 and "invalid_grant" in resp.text:
            # Token revoked/expired — surface so the caller marks disconnected.
            raise SpotifyAuthError("Spotify refresh token is no longer valid (invalid_grant)")
        if resp.status_code != 200:
            raise SpotifyError(f"Token refresh failed: {resp.status_code} {resp.text[:200]}")
        self._store_tokens(resp.json())

    def _bearer(self) -> str:
        if not self._access_token or time.time() >= self._access_expiry:
            self._refresh()
        return self._access_token

    # ── API ──────────────────────────────────────────────────────────────────
    def _get(self, path: str, params: dict = None) -> dict:
        url = path if path.startswith("http") else f"{API_BASE}{path}"
        for attempt in range(2):
            headers = {"Authorization": f"Bearer {self._bearer()}"}
            resp = requests.get(url, headers=headers, params=params, timeout=20)
            if resp.status_code == 401 and attempt == 0:
                self._access_token = None  # force refresh + retry once
                continue
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "2"))
                time.sleep(min(wait, 10))
                continue
            if resp.status_code == 404:
                raise SpotifyError("Playlist not found (or not accessible to this account)")
            if resp.status_code != 200:
                raise SpotifyError(f"Spotify GET {path} → {resp.status_code} {resp.text[:200]}")
            return resp.json()
        raise SpotifyError(f"Spotify GET {path} failed after retry")

    def get_playlist_meta(self, playlist_id: str) -> dict:
        d = self._get(f"/playlists/{playlist_id}",
                      {"fields": "id,name,snapshot_id,images,tracks(total),owner(id,display_name)"})
        images = d.get("images") or []
        return {
            "spotify_id": d["id"],
            "name": d.get("name", ""),
            "snapshot_id": d.get("snapshot_id", ""),
            "image_url": images[0]["url"] if images else "",
            "track_count": (d.get("tracks") or {}).get("total", 0),
            "owner": (d.get("owner") or {}).get("display_name", ""),
        }

    @staticmethod
    def _parse_track(track: dict) -> dict:
        """Normalize a Spotify track object into our track-meta dict shape."""
        album = track.get("album") or {}
        images = album.get("images") or []
        artists = [a["name"] for a in track.get("artists", []) if a.get("name")]
        album_artists = [a["name"] for a in album.get("artists", []) if a.get("name")]
        artist_str = ", ".join(artists)
        title = track.get("name", "")
        rel = album.get("release_date", "") or ""
        return {
            "spotify_track_id": track["id"],
            "title": title,
            "artist": artist_str,
            "album": album.get("name", ""),
            "album_artist": album_artists[0] if album_artists else (artists[0] if artists else ""),
            "duration_ms": track.get("duration_ms"),
            "isrc": (track.get("external_ids") or {}).get("isrc", ""),
            "track_no": track.get("track_number"),
            "disc_no": track.get("disc_number"),
            "year": int(rel[:4]) if rel[:4].isdigit() else None,
            "cover_url": images[0]["url"] if images else "",
            "norm_title": normalize_title(title),
            "norm_artist": normalize_artist(artist_str),
        }

    def get_playlist_tracks(self, playlist_id: str) -> list[dict]:
        """Return normalized playlist_tracks dicts (paginated)."""
        out: list[dict] = []
        fields = ("items(added_at,track(id,name,duration_ms,track_number,disc_number,"
                  "external_ids(isrc),artists(name),album(name,release_date,images,artists(name)))),next")
        url = f"/playlists/{playlist_id}/tracks"
        params = {"fields": fields, "limit": 100}
        pos = 0
        while url:
            page = self._get(url, params)
            for item in page.get("items", []):
                track = item.get("track")
                if not track or not track.get("id"):
                    pos += 1
                    continue  # removed track / local file / episode
                row = self._parse_track(track)
                row.update({"position": pos, "added_at": item.get("added_at", ""),
                            "match_status": "unknown", "matched_file_path": None})
                out.append(row)
                pos += 1
            url = page.get("next")
            params = None  # `next` is a full URL already carrying params
        return out

    def search_tracks(self, query: str, limit: int = 20) -> list[dict]:
        """Search Spotify's catalog for tracks (manual search & grab)."""
        if not query.strip():
            return []
        data = self._get("/search", {"q": query, "type": "track", "limit": min(50, max(1, limit))})
        items = ((data.get("tracks") or {}).get("items")) or []
        return [self._parse_track(t) for t in items if t and t.get("id")]


def parse_playlist_id(value: str) -> str:
    """Accept a raw id, spotify:playlist:ID, or an open.spotify.com URL."""
    v = (value or "").strip()
    if not v:
        return ""
    if v.startswith("spotify:playlist:"):
        return v.split(":")[-1]
    if "open.spotify.com" in v:
        # https://open.spotify.com/playlist/<id>?si=...
        part = v.split("/playlist/", 1)[-1]
        return part.split("?")[0].split("/")[0]
    return v
