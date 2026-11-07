"""Finding music outside your library, and choosing the final playlist."""

from itertools import zip_longest

import numpy as np

from .text import clean_title, fold, track_key


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


def fit_percentiles(scores, reference):
    """Each score as the share (0..1) of `reference` scores it beats, ties counting half.

    `reference` is your library's scores, so a fit of 0.97 means "fits this mood better than
    97% of your library", for library songs and new ones alike."""
    scores = np.asarray(scores, dtype=float)
    ref = np.sort(np.asarray(reference, dtype=float))
    ref = ref[np.isfinite(ref)]
    if not len(ref):
        return np.zeros(len(scores))
    below = np.searchsorted(ref, scores, side="left")
    at_or_below = np.searchsorted(ref, scores, side="right")
    return np.where(np.isfinite(scores), (below + at_or_below) / 2 / len(ref), 0.0)


def scored_pools(model, library, library_profiles, candidates=(), candidate_profiles=()):
    """(library, external): candidates with "fit" and "profile", best first."""
    lib_scores = model.decision(library_profiles)
    lib_fit = fit_percentiles(lib_scores, lib_scores)
    lib = sorted(({**t, "fit": float(f), "via": "your library", "profile": p}
                  for t, p, f in zip(library, library_profiles, lib_fit)), key=lambda c: -c["fit"])
    ext = []
    if candidates:
        ext_fit = fit_percentiles(model.decision(list(candidate_profiles)), lib_scores)
        ext = sorted(({**c, "fit": float(f), "profile": p}
                      for c, p, f in zip(candidates, candidate_profiles, ext_fit)), key=lambda c: -c["fit"])
    return lib, ext


def song_key(c):
    """Same song, any version: "September" on two albums, a 2017 remaster, a live take."""
    return track_key(c.get("main_artist") or c.get("artist", ""), c.get("title", ""))


def is_version(title):
    """True for "Abracadabra - Remastered 2017", "Hey Jude [Live]" and the like."""
    return clean_title(title) != (title or "").strip()


def one_per_song(pool):
    """Collapse versions of a song into one candidate: the original title if there is one
    (else the shortest), ranked by its best-fitting version."""
    groups = {}
    for c in pool:
        groups.setdefault(song_key(c), []).append(c)
    out = []
    for versions in groups.values():
        best = max(versions, key=lambda v: v["fit"])
        keep = min(versions, key=lambda v: (is_version(v["title"]), len(v["title"]), -v["fit"]))
        out.append({**keep, "fit": best["fit"], "profile": best.get("profile", keep.get("profile"))})
    out.sort(key=lambda c: -c["fit"])
    return out


def choose(library, external, size, horizon, max_per_artist=3, min_fit=0.5, resolve=None):
    """Pick the playlist from scored candidates.

    library / external: lists of dicts with "fit" (0..1), "main_artist", and track info.
    About `horizon` of the playlist (0..1) comes from outside your library; the rest from
    inside. If one side runs short, the other fills in, except at the ends: horizon 0 means
    library only, horizon 1 means new music only. Each song appears once, whatever versions
    of it exist. `resolve(candidate)` turns an outside candidate into a Spotify track, or
    None to skip it.
    """
    library, external = one_per_song(library), one_per_song(external)
    want_new = round(size * horizon)
    picks, per_artist, used, songs = [], {}, set(), set()

    def take(pool, n, is_new):
        taken = 0
        for c in pool:
            if taken >= n or len(picks) >= size:
                break
            key = song_key(c)
            if c["fit"] < min_fit or id(c) in used or key in songs:
                continue
            artist = fold(c["main_artist"])
            if per_artist.get(artist, 0) >= max_per_artist:
                continue
            used.add(id(c))
            track = resolve(c) if (is_new and resolve) else c
            if not track:
                continue
            found = song_key({"main_artist": c["main_artist"], "title": track.get("title", c["title"])})
            if found in songs:
                continue  # the search turned up a version of a song already picked
            songs.update({key, found})
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
