"""Last.fm: crowd tags for tracks and artists, and similar tracks/artists for discovery.

Only public, read-only methods are used, so only the API key is needed (not the shared secret).
"""

from .cache import cached
from .net import Http, HttpError
from .text import clean_title, fold

API = "https://ws.audioscrobbler.com/2.0/"
MONTH = 30 * 24 * 3600

# Tags that say nothing about how a song feels.
STOP_TAGS = {
    "seen live", "favorites", "favourites", "favorite", "favourite", "my favorite", "love",
    "loved", "awesome", "best", "good", "great", "cool", "albums i own", "under 2000 listeners",
    "spotify", "beautiful", "amazing", "check out", "to listen", "all", "music", "songs",
}
NOT_FOUND = {6, 7}   # invalid parameters (no such track/artist) / invalid resource
BAD_KEY = {10, 26}   # invalid or suspended API key
MIN_TAG_COUNT = 5  # Last.fm tag counts run 0-100 relative to the item's top tag


def as_list(value):
    """Last.fm returns a bare object instead of a one-element list when there is one result."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def normalize_tags(raw, artist=None, scale=1.0):
    """[{name, count}] -> {tag: weight 0..1}, dropping noise and the artist's own name."""
    out = {}
    artist_folded = fold(artist) if artist else None
    for t in as_list(raw):
        name = (t.get("name") or "").strip().lower()
        try:
            count = float(t.get("count", 0))
        except (TypeError, ValueError):
            continue
        if not name or count < MIN_TAG_COUNT or name in STOP_TAGS or fold(name) == artist_folded:
            continue
        out[name] = round(min(count, 100) / 100 * scale, 3)
    return out


class LastFM:
    def __init__(self, api_key, cache, http=None):
        if not api_key:
            raise ValueError('Add "lastfm_api_key" to config.json (see README).')
        self.api_key = api_key
        self.cache = cache
        self.http = http or Http(min_interval=0.25)

    def _call(self, method, **params):
        """The reply; {} when Last.fm says the track/artist/tag doesn't exist (error 6 or 7).
        Other failures (network, outage, rate limit) raise HttpError, which callers don't
        cache, so the next run tries again. A rejected API key stops with a clear message."""
        params.update(method=method, api_key=self.api_key, format="json", autocorrect=1)
        data = self.http.get_json(API, params)
        if not isinstance(data, dict):
            raise HttpError(f"Last.fm {method}: unexpected reply")
        if "error" in data:
            code = data.get("error")
            if code in NOT_FOUND:
                return {}
            if code in BAD_KEY:
                raise SystemExit("Last.fm rejected your API key: " + str(data.get("message", ""))
                                 + "\nCheck lastfm_api_key in config.json (see README).")
            raise HttpError(f"Last.fm {method}: error {code}: {data.get('message', '')}")
        return data

    def track_tags(self, artist, title):
        """Tags for the track, falling back to the artist's tags (at half weight)."""
        title = clean_title(title)

        def fetch():
            data = self._call("track.getTopTags", artist=artist, track=title)
            tags = normalize_tags((data.get("toptags") or {}).get("tag"), artist)
            if tags:
                return {"tags": tags, "source": "track"}
            tags = self.artist_tags(artist)
            return {"tags": {k: round(v * 0.5, 3) for k, v in tags.items()}, "source": "artist"}
        return cached(self.cache, "lastfm:track_tags", f"{fold(artist)}|{fold(title)}", fetch, MONTH)

    def artist_tags(self, artist):
        def fetch():
            data = self._call("artist.getTopTags", artist=artist)
            return normalize_tags((data.get("toptags") or {}).get("tag"), artist)
        return cached(self.cache, "lastfm:artist_tags", fold(artist), fetch, MONTH)

    def similar_tracks(self, artist, title, limit=25):
        title = clean_title(title)

        def fetch():
            data = self._call("track.getSimilar", artist=artist, track=title, limit=limit)
            return [_track(t) for t in as_list((data.get("similartracks") or {}).get("track"))]
        return cached(self.cache, "lastfm:similar_tracks",
                      f"{fold(artist)}|{fold(title)}|{limit}", fetch, MONTH)

    def similar_artists(self, artist, limit=8):
        def fetch():
            data = self._call("artist.getSimilar", artist=artist, limit=limit)
            return [a.get("name") for a in as_list((data.get("similarartists") or {}).get("artist"))
                    if a.get("name")]
        return cached(self.cache, "lastfm:similar_artists", f"{fold(artist)}|{limit}", fetch, MONTH)

    def artist_top_tracks(self, artist, limit=3):
        def fetch():
            data = self._call("artist.getTopTracks", artist=artist, limit=limit)
            return [_track(t) for t in as_list((data.get("toptracks") or {}).get("track"))][:limit]
        return cached(self.cache, "lastfm:artist_top", f"{fold(artist)}|{limit}", fetch, MONTH)

    def tag_top_tracks(self, tag, limit=25):
        def fetch():
            data = self._call("tag.getTopTracks", tag=tag, limit=limit)
            return [_track(t) for t in as_list((data.get("tracks") or {}).get("track"))][:limit]
        return cached(self.cache, "lastfm:tag_top", f"{tag}|{limit}", fetch, MONTH)


def _track(t):
    artist = t.get("artist")
    artist = artist.get("name") if isinstance(artist, dict) else artist
    return {"artist": artist or "", "title": t.get("name") or ""}
