"""Reading your Spotify library and matching songs to Spotify tracks."""

import re

from cleaner import entry_item, paginate, playlist_owner_id

from .text import clean_title, fold, same_song


def to_track(item, playlist=None):
    """Spotify track object -> the small dict used throughout the mood code."""
    if not item or item.get("type", "track") != "track" or item.get("is_local"):
        return None
    uri = item.get("uri") or ""
    if not uri.startswith("spotify:track:"):
        return None
    return {
        "uri": uri,
        "artist": ", ".join(a.get("name", "") for a in item.get("artists") or []) or "",
        "main_artist": ((item.get("artists") or [{}])[0]).get("name", ""),
        "title": item.get("name") or "",
        "isrc": (item.get("external_ids") or {}).get("isrc"),
        "playlists": [playlist] if playlist else [],
    }


def library_tracks(sp, user_id, include_liked=True, log=print):
    """Every track in playlists you own or collaborate on, plus Liked Songs, de-duplicated.

    Followed playlists owned by others are skipped: since February 2026 Spotify only lets
    apps read the tracks of playlists you own or collaborate on.
    """
    from cleaner import fetch_all_playlists

    tracks = {}

    def add(item, source):
        t = to_track(item, source)
        if not t:
            return
        if t["uri"] in tracks:
            if source and source not in tracks[t["uri"]]["playlists"]:
                tracks[t["uri"]]["playlists"].append(source)
        else:
            tracks[t["uri"]] = t

    playlists = [p for p in fetch_all_playlists(sp)
                 if playlist_owner_id(p) == user_id or p.get("collaborative")]
    for i, pl in enumerate(playlists, 1):
        try:
            for entry in paginate(sp, sp.playlist_items(pl["id"], limit=50)):
                add(entry_item(entry or {}), pl.get("name"))
        except Exception as err:
            log(f"  skipped playlist {pl.get('name')}: {err}")
        if i % 25 == 0:
            log(f"  read {i}/{len(playlists)} playlists")
    if include_liked:
        try:
            for entry in paginate(sp, sp.current_user_saved_tracks(limit=50)):
                add(entry_item(entry or {}), "Liked Songs")
        except Exception as err:
            log(f"  skipped Liked Songs: {err}")
    return list(tracks.values())


def playlist_tracks_by_name(sp, user_id, names):
    """Tracks of your playlists whose names match any of `names` (case-insensitive, exact)."""
    from cleaner import fetch_all_playlists

    wanted = {fold(n) for n in names}
    out = []
    for pl in fetch_all_playlists(sp):
        if fold(pl.get("name")) in wanted:
            for entry in paginate(sp, sp.playlist_items(pl["id"], limit=50)):
                t = to_track(entry_item(entry or {}), pl.get("name"))
                if t:
                    out.append(t)
    return out


_SPOTIFY_ID = re.compile(r"(?:spotify:track:|open\.spotify\.com/track/)([A-Za-z0-9]{22})")


def search_track(sp, artist, title):
    """Best Spotify match for artist + title, or None. Prefers the exact title (with its
    remaster/live wording), then the same song in any version."""
    queries = [f'track:"{title}" artist:"{artist}"',
               f'track:"{clean_title(title)}" artist:"{artist}"',
               f"{artist} {clean_title(title)}"]
    seen = set()
    for q in queries:
        if q in seen:
            continue
        seen.add(q)
        try:
            items = (sp.search(q=q, type="track", limit=10).get("tracks") or {}).get("items") or []
        except Exception:
            continue
        candidates = [to_track(i) for i in items]
        candidates = [c for c in candidates if c]
        for c in candidates:
            if fold(c["title"]) == fold(title) and _artist_matches(artist, c):
                return c
        for c in candidates:
            if same_song(c["main_artist"], c["title"], artist, title) or (
                    _artist_matches(artist, c) and fold(clean_title(c["title"])) == fold(clean_title(title))):
                return c
    return None


def _artist_matches(artist, track):
    a = fold(artist)
    return a == fold(track["main_artist"]) or a in fold(track["artist"])


def resolve_seed(sp, seed):
    """A seed is {"artist", "title"} or a Spotify track URI/URL. Returns a track dict or None."""
    if isinstance(seed, str):
        m = _SPOTIFY_ID.search(seed)
        if not m:
            return None
        return to_track(sp.track(m.group(1)))
    return search_track(sp, seed.get("artist", ""), seed.get("title", ""))
