"""Finding music outside your library, and choosing the final playlist."""

from itertools import zip_longest

from .text import fold, track_key


def external_candidates(lastfm, seeds, top_tags, exclude_keys, limit=300,
                        per_seed=25, similar_artists=8, per_artist=3, per_tag=25):
    """Songs from Last.fm related to the seeds, minus anything in exclude_keys.

    Three sources, interleaved so that no single one crowds out the others under `limit`:
      * tracks similar to each seed
      * top tracks of artists similar to the seed artists
      * top tracks for the Last.fm tags the mood model weights most
    Each candidate records why it was found ("via").
    """
    streams = []
    for s in seeds:
        streams.append([(t, f"similar to {s['title']}")
                        for t in lastfm.similar_tracks(s["main_artist"], s["title"], per_seed)])
    seed_artists = list(dict.fromkeys(s["main_artist"] for s in seeds))
    for artist in seed_artists:
        stream = []
        for other in lastfm.similar_artists(artist, similar_artists):
            stream += [(t, f"artist like {artist}")
                       for t in lastfm.artist_top_tracks(other, per_artist)]
        streams.append(stream)
    for tag in top_tags:
        streams.append([(t, f"tagged {tag}") for t in lastfm.tag_top_tracks(tag, per_tag)])

    out, seen = [], set(exclude_keys)
    for round_ in zip_longest(*streams):
        for entry in round_:
            if entry is None:
                continue
            t, via = entry
            if not t.get("artist") or not t.get("title"):
                continue
            key = track_key(t["artist"], t["title"])
            if key in seen:
                continue
            seen.add(key)
            out.append({"artist": t["artist"], "main_artist": t["artist"], "title": t["title"],
                        "key": key, "via": via})
            if len(out) >= limit:
                return out
    return out


def choose(library, external, size, horizon, max_per_artist=3, min_fit=0.5, resolve=None):
    """Pick the playlist from scored candidates.

    library / external: lists of dicts with "fit" (0..1), "main_artist", and track info,
    already sorted by fit, best first. About `horizon` of the playlist (0..1) comes from
    outside your library; the rest from inside. If one side runs short, the other fills in,
    except at the ends: horizon 0 means library only, horizon 1 means new music only.
    `resolve(candidate)` turns an outside candidate into a Spotify track, or None to skip it.
    """
    want_new = round(size * horizon)
    picks, per_artist, used = [], {}, set()

    def take(pool, n, is_new):
        taken = 0
        for c in pool:
            if taken >= n or len(picks) >= size:
                break
            if c["fit"] < min_fit or id(c) in used:
                continue
            artist = fold(c["main_artist"])
            if per_artist.get(artist, 0) >= max_per_artist:
                continue
            used.add(id(c))
            track = resolve(c) if (is_new and resolve) else c
            if not track:
                continue
            per_artist[artist] = per_artist.get(artist, 0) + 1
            picks.append({**c, **track, "fit": c["fit"], "new": is_new})
            taken += 1
        return taken

    take(external, want_new, True)
    if horizon < 1:
        take(library, size - len(picks), False)
    if 0 < horizon:
        take(external, size - len(picks), True)  # library ran short: more new music
    picks.sort(key=lambda p: -p["fit"])
    return picks
