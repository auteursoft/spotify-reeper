"""Reading your Spotify library and matching songs to Spotify tracks."""

import json
import re

from cleaner import RateLimited, entry_item, paginate, playlist_owner_id

from .text import clean_title, fold, same_song

MONTH = 30 * 24 * 3600


class SearchFailed(Exception):
    """A Spotify search errored, so "not found" can't be trusted (or remembered)."""


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


def read_playlist(sp, playlist_id, snapshot_id=None, cache=None):
    """(tracks, from_cache): a playlist's tracks.

    Every Spotify playlist has a snapshot_id that changes whenever the playlist changes, so
    its tracks are kept in the cache with that snapshot and only read again from Spotify
    when it differs. Without a cache or snapshot, the playlist is always read."""
    if cache is not None and snapshot_id:
        hit = cache.get("spotify:playlist", playlist_id)
        if hit and hit.get("snapshot_id") == snapshot_id:
            return hit["tracks"], True
    tracks = [t for t in (to_track(entry_item(e or {}))
                          for e in paginate(sp, sp.playlist_items(playlist_id, limit=50))) if t]
    if cache is not None and snapshot_id:
        cache.set("spotify:playlist", playlist_id, {"snapshot_id": snapshot_id, "tracks": tracks})
    return tracks, False


def read_liked_songs(sp, cache=None, user_id=""):
    """(tracks, from_cache): Liked Songs. It has no snapshot_id, so the cached copy is used
    while the count and the most recently liked songs are unchanged (one request)."""
    first = sp.current_user_saved_tracks(limit=50)
    recent = [[e.get("added_at"), (entry_item(e or {}) or {}).get("uri")]
              for e in (first.get("items") or [])[:5]]
    signature = [first.get("total"), recent]
    if cache is not None:
        hit = cache.get("spotify:liked", user_id)
        if hit and hit.get("signature") == signature:
            return hit["tracks"], True
    tracks = [t for t in (to_track(entry_item(e or {})) for e in paginate(sp, first)) if t]
    if cache is not None:
        cache.set("spotify:liked", user_id, {"signature": signature, "tracks": tracks})
    return tracks, False


def library_tracks(sp, user_id, include_liked=True, log=print, cache=None):
    """Every track in playlists you own or collaborate on, plus Liked Songs, de-duplicated.

    Followed playlists owned by others are skipped: since February 2026 Spotify only lets
    apps read the tracks of playlists you own or collaborate on. With a cache, only
    playlists that changed since the last run are read again (see read_playlist).
    """
    from cleaner import fetch_all_playlists

    tracks = {}

    def add(found, source):
        for t in found:
            if t["uri"] in tracks:
                if source and source not in tracks[t["uri"]]["playlists"]:
                    tracks[t["uri"]]["playlists"].append(source)
            else:
                tracks[t["uri"]] = {**t, "playlists": [source] if source else []}

    playlists = [p for p in fetch_all_playlists(sp)
                 if playlist_owner_id(p) == user_id or p.get("collaborative")]
    reread = 0
    for i, pl in enumerate(playlists, 1):
        try:
            found, from_cache = read_playlist(sp, pl["id"], pl.get("snapshot_id"), cache)
            reread += not from_cache
            add(found, pl.get("name"))
        except RateLimited:
            raise  # stop: carrying on would only make the rate limit worse
        except Exception as err:
            log(f"  skipped playlist {pl.get('name')}: {err}")
        if i % 25 == 0 and reread:
            log(f"  read {i}/{len(playlists)} playlists ({reread} from Spotify, the rest unchanged)")
    if include_liked:
        try:
            found, from_cache = read_liked_songs(sp, cache, user_id)
            reread += not from_cache
            add(found, "Liked Songs")
        except RateLimited:
            raise
        except Exception as err:
            log(f"  skipped Liked Songs: {err}")
    log(f"  {len(playlists)} playlists: {reread} read from Spotify, "
        f"{len(playlists) + int(include_liked) - reread} unchanged since last time")
    return list(tracks.values())


_PLAYLIST_ID = re.compile(r"(?:spotify:playlist:|open\.spotify\.com/playlist/)([A-Za-z0-9]{22})")


def seed_playlist_tracks(sp, entries, cache=None):
    """Tracks from seed playlists. Each entry is a Spotify playlist link or URI, or the exact
    name of a playlist in your library (case-insensitive).

    Returns (tracks, problems). Spotify only lets apps read the tracks of playlists you own or
    collaborate on, so someone else's playlist is reported as a problem rather than read.
    """
    from cleaner import fetch_all_playlists

    tracks, problems, by_name = [], [], None
    for entry in entries:
        m = _PLAYLIST_ID.search(entry)
        if m:
            targets = [{"id": m.group(1), "name": entry}]
        else:
            if by_name is None:
                by_name = fetch_all_playlists(sp)
            targets = [p for p in by_name if fold(p.get("name")) == fold(entry)]
            if not targets:
                problems.append(f'no playlist named "{entry}" in your library')
                continue
        for pl in targets:
            name, snapshot = pl["name"], pl.get("snapshot_id")
            if m:  # a link: look up its name and snapshot, but don't fail over it
                try:
                    info = sp.playlist(pl["id"], fields="name,snapshot_id") or {}
                    name, snapshot = info.get("name") or entry, info.get("snapshot_id")
                except RateLimited:
                    raise
                except Exception:
                    pass
            try:
                found, _ = read_playlist(sp, pl["id"], snapshot, cache)
                found = [{**t, "playlists": [name]} for t in found]
            except RateLimited:
                raise
            except Exception as err:
                status = getattr(err, "http_status", None)
                why = (" Spotify only lets apps read playlists you own or collaborate on; "
                       "copy its songs into a playlist of your own and use that."
                       if status in (403, 404) else "")
                problems.append(f"could not read seed playlist {entry}: {err}.{why}")
                continue
            found = [t for t in found if t]
            if not found:
                problems.append(f"seed playlist {name} has no usable tracks")
            tracks += found
    return tracks, problems


_SPOTIFY_ID = re.compile(r"(?:spotify:track:|open\.spotify\.com/track/)([A-Za-z0-9]{22})")


def search_track(sp, artist, title):
    """Best Spotify match for artist + title, or None. Prefers the exact title (with its
    remaster/live wording), then the same song in any version."""
    queries = [f'track:"{title}" artist:"{artist}"',
               f'track:"{clean_title(title)}" artist:"{artist}"',
               f"{artist} {clean_title(title)}"]
    seen, failed = set(), False
    for q in queries:
        if q in seen:
            continue
        seen.add(q)
        try:
            items = (sp.search(q=q, type="track", limit=10).get("tracks") or {}).get("items") or []
        except RateLimited:
            raise
        except Exception:
            failed = True
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
    if failed:
        raise SearchFailed(f"Spotify search failed for {artist} - {title}")
    return None


def _artist_matches(artist, track):
    a = fold(artist)
    return a == fold(track["main_artist"]) or a in fold(track["artist"])


def cached(cache, ns, key, fetch):
    """fetch() once a month at most; None results are remembered too."""
    if cache is not None:
        hit = cache.get(ns, key, MONTH)
        if hit is not None:
            return hit.get("value")
    try:
        value = fetch()
    except SearchFailed:
        return None  # a network hiccup: try again next time rather than remember "not found"
    if cache is not None:
        cache.set(ns, key, {"value": value})
    return value


def search_track_cached(sp, artist, title, cache=None):
    """search_track, remembered for a month (each search is a Spotify request).
    Returns None when not found, or when the search failed (that isn't remembered)."""
    return cached(cache, "spotify:search", f"{fold(artist)}|{title}",
                  lambda: search_track(sp, artist, title))


def resolve_seed(sp, seed, cache=None):
    """resolve_seed_uncached, remembered for a month."""
    return cached(cache, "spotify:seed", json.dumps(seed, sort_keys=True),
                  lambda: resolve_seed_uncached(sp, seed))


def resolve_seed_uncached(sp, seed):
    """A seed is {"artist", "title"} (optionally with "uri"), or a Spotify track URI/URL.
    Returns a track dict or None."""
    if isinstance(seed, str):
        m = _SPOTIFY_ID.search(seed)
        if not m:
            return None
        return to_track(sp.track(m.group(1)))
    m = _SPOTIFY_ID.search(seed.get("uri") or "")
    if m:
        try:
            return to_track(sp.track(m.group(1)))
        except RateLimited:
            raise
        except Exception:
            pass  # fall back to searching by name
    return search_track(sp, seed.get("artist", ""), seed.get("title", ""))
