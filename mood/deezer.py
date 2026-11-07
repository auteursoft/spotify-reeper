"""Deezer's public API: find a track's 30-second preview (no key or login needed)."""

from .cache import cached
from .net import Http, HttpError
from .text import clean_title, fold, same_song

API = "https://api.deezer.com"
NO_DATA = 800  # Deezer's "no data" error: a real "not found"


class Deezer:
    def __init__(self, cache, http=None):
        self.cache = cache
        self.http = http or Http(min_interval=0.12)  # Deezer allows 50 requests / 5 s

    def _get(self, path, params=None):
        """The reply, or None if Deezer genuinely has no such data (error code 800).
        Anything else that goes wrong (network, quota code 4, server busy) raises HttpError,
        so it is never cached as "no match"."""
        data = self.http.get_json(f"{API}/{path}", params)
        if not isinstance(data, dict):
            raise HttpError(f"Deezer: unexpected reply for {path}")
        error = data.get("error")
        if error:
            if isinstance(error, dict) and error.get("code") == NO_DATA:
                return None
            raise HttpError(f"Deezer: {error}")
        return data

    def find_id(self, artist, title, isrc=None):
        """Deezer track id for a song: exact ISRC match first, then a title/artist search."""
        def fetch():
            if isrc:
                hit = self._get(f"track/isrc:{isrc}")
                if hit and hit.get("id"):
                    return {"id": hit["id"], "match": "isrc"}
            q = f'artist:"{artist}" track:"{clean_title(title)}"'
            found = (self._get("search", {"q": q, "limit": 10}) or {}).get("data") or []
            for hit in found:
                if same_song(artist, title, (hit.get("artist") or {}).get("name"), hit.get("title")):
                    return {"id": hit["id"], "match": "search"}
            for hit in found:  # same artist, title differs only in version wording
                if fold((hit.get("artist") or {}).get("name")) == fold(artist) and \
                        fold(clean_title(title)) in fold(hit.get("title")):
                    return {"id": hit["id"], "match": "search-loose"}
            return {"id": None, "match": None}
        key = isrc or f"{fold(artist)}|{fold(clean_title(title))}"
        return cached(self.cache, "deezer:id", key, fetch)["id"]

    def preview_url(self, deezer_id):
        """Preview URLs are signed and expire, so they are fetched fresh, never cached.
        None if the track has no preview; HttpError if Deezer couldn't be asked."""
        track = self._get(f"track/{deezer_id}") or {}
        return track.get("preview") or None

    def download(self, url, path):
        return self.http.download(url, path)
