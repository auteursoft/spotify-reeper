"""Spotify tokens for the app.

The app signs in with Authorization Code + PKCE (no client secret is shipped) and gives the
engine the tokens. The engine refreshes them itself when they are about to expire, so a
long analysis never fails halfway, and reports the new tokens back to the app, which keeps
them in the Keychain.
"""

import threading
import time

TOKEN_URL = "https://accounts.spotify.com/api/token"


class AuthExpired(Exception):
    http_status = 401


class TokenManager:
    """A spotipy auth_manager: spotipy calls get_access_token() before every request."""

    def __init__(self, client_id, access_token, refresh_token, expires_at, on_refresh=None,
                 post=None, clock=time.time):
        self.client_id = client_id
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_at = float(expires_at or 0)
        self.on_refresh = on_refresh
        self._post = post
        self._clock = clock
        self._lock = threading.Lock()

    def get_access_token(self, as_dict=False):
        with self._lock:
            if self._clock() > self.expires_at - 60:
                self._refresh()
            if as_dict:
                return {"access_token": self.access_token, "expires_at": self.expires_at}
            return self.access_token

    def _refresh(self):
        if not self.refresh_token:
            raise AuthExpired("Spotify sign-in expired.")
        post = self._post
        if post is None:
            import requests
            post = requests.post
        resp = post(TOKEN_URL, data={"grant_type": "refresh_token",
                                     "refresh_token": self.refresh_token,
                                     "client_id": self.client_id}, timeout=20)
        if getattr(resp, "status_code", 200) >= 400:
            raise AuthExpired(f"Spotify sign-in expired (refresh failed: HTTP {resp.status_code}).")
        data = resp.json()
        self.access_token = data["access_token"]
        # Spotify may rotate the refresh token; keep the old one if it doesn't.
        self.refresh_token = data.get("refresh_token") or self.refresh_token
        self.expires_at = self._clock() + float(data.get("expires_in", 3600))
        if self.on_refresh:
            self.on_refresh({"access_token": self.access_token, "refresh_token": self.refresh_token,
                             "expires_at": self.expires_at})
