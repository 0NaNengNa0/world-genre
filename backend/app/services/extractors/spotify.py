"""Enrich artist names with genres via the Spotify Web API.

As of Feb 2026 Spotify no longer exposes editorial playlist contents to third-
party apps, so charts come from kworb instead. This module handles the
enrichment step: name -> artist object (which includes genres, id, images).

Auth: Client Credentials flow (app-only). Get credentials at
https://developer.spotify.com/dashboard.

Rate limits: dev mode uses a rolling 30-second window plus a daily quota.
_get_with_retry honours the Retry-After header. Callers should serialize
requests (single thread + small sleep) — concurrent workers just exhaust the
window faster.
"""
import base64
import time

import requests

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
MAX_RETRIES = 3
MAX_RETRY_WAIT = 90  # cap wait per 429 to avoid multi-minute stalls


class QuotaExceeded(Exception):
    """Raised when Spotify's daily dev-mode quota is exhausted. No retry can fix
    this — caller should abort the batch and try again later."""


def _get_with_retry(url: str, headers: dict, params: dict | None = None,
                    timeout: int = 15) -> requests.Response:
    for attempt in range(MAX_RETRIES):
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        if resp.status_code != 429:
            return resp

        body = resp.json() if resp.content else {}
        if body.get("error", {}).get("reason") == "QUOTA_EXCEEDED":
            raise QuotaExceeded("daily dev-mode quota exhausted")

        wait = min(int(resp.headers.get("Retry-After", 2 ** attempt)), MAX_RETRY_WAIT)
        print(f"  429 rate-limited, waiting {wait}s (attempt {attempt + 1}/{MAX_RETRIES})")
        time.sleep(wait)
    return resp


def get_access_token(client_id: str, client_secret: str, timeout: int = 15) -> str:
    auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        TOKEN_URL,
        headers={"Authorization": f"Basic {auth_header}"},
        data={"grant_type": "client_credentials"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def search_artist(token: str, artist_name: str, timeout: int = 15) -> dict | None:
    """Returns the full artist object (id, name, genres, images, ...) in one call.

    The response already contains genres, so callers don't need a follow-up
    /artists/{id} request.
    """
    resp = _get_with_retry(
        f"{API_BASE}/search",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": artist_name, "type": "artist", "limit": 1},
        timeout=timeout,
    )
    resp.raise_for_status()
    items = resp.json().get("artists", {}).get("items", [])
    return items[0] if items else None
