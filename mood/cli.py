"""python moods.py <command>: see README.md, "Mood playlists"."""

import argparse
import datetime
import json
import sys

from cleaner import add_uris, confirm, load_config

from . import audio as audio_mod
from .cache import DEFAULT_PATH as CACHE_PATH, Cache
from .text import fold, track_key

try:
    from tabulate import tabulate
except ImportError:
    tabulate = None


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def table(rows, headers):
    if tabulate:
        print(tabulate(rows, headers=headers))
    else:
        print("\t".join(headers))
        for r in rows:
            print("\t".join("" if v is None else str(v) for v in r))


# ---------------- moods.json ----------------

def load_moods(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        raise SystemExit(f"{path} not found. See README.md, \"Mood playlists\", for its format.")
    except json.JSONDecodeError as err:
        raise SystemExit(f"{path} is not valid JSON: {err}")


def get_mood(moods, name):
    for key, value in moods.items():
        if fold(key) == fold(name):
            return key, value
    raise SystemExit(f'No mood called "{name}" in moods file. Moods: ' +
                     ", ".join(f'"{k}"' for k in moods))


# ---------------- wiring ----------------

class Context:
    """Lazily-built clients, so commands only log in / load models when they need to."""

    def __init__(self, args):
        self.args = args
        self.cache = Cache(args.cache)
        self._config = self._sp = self._user = self._profiler = None

    @property
    def config(self):
        if self._config is None:
            self._config = load_config(self.args.config)
        return self._config

    @property
    def sp(self):
        if self._sp is None:
            from cleaner import make_client
            self._sp = make_client(self.config)
            self._user = self._sp.current_user()["id"]
        return self._sp

    @property
    def user_id(self):
        self.sp  # noqa: B018 - logs in
        return self._user

    @property
    def profiler(self):
        if self._profiler is None:
            from .deezer import Deezer
            from .lastfm import LastFM
            from .profile import Profiler
            lastfm = LastFM(self.config.get("lastfm_api_key"), self.cache)
            analyzer = None
            if not self.args.no_audio:
                try:
                    analyzer = audio_mod.AudioAnalyzer(self.args.models)
                except (FileNotFoundError, ImportError) as err:
                    raise SystemExit(f"{err}\nOr add --no-audio to use Last.fm tags only.")
            self._profiler = Profiler(self.cache, lastfm, Deezer(self.cache), analyzer, log=log)
        return self._profiler

    def profile_all(self, tracks, what="tracks"):
        out = []
        for i, t in enumerate(tracks, 1):
            out.append(self.profiler.profile(t["main_artist"], t["title"], t.get("isrc")))
            if i % 25 == 0 or i == len(tracks):
                log(f"  analyzed {i}/{len(tracks)} {what}")
        return out


def load_seeds(ctx, mood):
    """Resolve the mood's seed songs (and seed playlists) to tracks."""
    from .library import resolve_seed, seed_playlist_tracks

    seeds, problems = [], []
    for seed in mood.get("seeds") or []:
        t = resolve_seed(ctx.sp, seed)
        if t is None and isinstance(seed, dict) and seed.get("artist") and seed.get("title"):
            # Not found on Spotify: still usable for learning, via Last.fm and Deezer.
            problems.append(f'not found on Spotify, using name only: {seed["artist"]} - {seed["title"]}')
            t = {"uri": None, "artist": seed["artist"], "main_artist": seed["artist"],
                 "title": seed["title"], "isrc": None, "playlists": []}
        elif t is None:
            problems.append(f"could not use seed: {seed}")
            continue
        seeds.append(t)
    entries = mood.get("seed_playlists") or []
    if entries:
        found, playlist_problems = seed_playlist_tracks(ctx.sp, entries)
        problems += playlist_problems
        seeds += found
    unique = {}
    for t in seeds:
        unique.setdefault(track_key(t["main_artist"], t["title"]), t)
    return list(unique.values()), problems


def train(ctx, mood_name, mood, library):
    from .model import MoodModel, NotEnoughData

    seeds, problems = load_seeds(ctx, mood)
    for p in problems:
        log(f"  note: {p}")
    if not seeds:
        raise SystemExit(f'"{mood_name}" has no seed songs yet. Add some to the moods file.')
    seed_profiles = ctx.profile_all(seeds, "seed songs")
    seed_keys = {p["key"] for p in seed_profiles}
    lib_profiles = ctx.profile_all(library, "library tracks")
    negatives = [p for p in lib_profiles if p["key"] not in seed_keys]
    try:
        model = MoodModel(use_audio=not ctx.args.no_audio).fit(seed_profiles, negatives)
    except NotEnoughData as err:
        raise SystemExit(str(err))
    return model, seeds, seed_profiles, lib_profiles, negatives


def get_library(ctx):
    from .library import library_tracks
    log("Reading your library from Spotify...")
    lib = library_tracks(ctx.sp, ctx.user_id, include_liked=not ctx.args.no_liked, log=log)
    if ctx.args.limit:
        lib = lib[: ctx.args.limit]
    log(f"  {len(lib)} tracks")
    return lib


# ---------------- commands ----------------

def cmd_setup(ctx):
    from .net import Http
    log(f"Downloading Essentia audio models into {ctx.args.models}/...")
    failures = audio_mod.setup_models(ctx.args.models, Http(min_interval=0), log=log)
    if failures:
        log("\nSome downloads failed. Get them by hand from https://essentia.upf.edu/models.html "
            f"and put them in {ctx.args.models}/ with these names:")
        for name, url, _ in failures:
            log(f"  {name}  <-  {url}")
        return 1
    try:
        audio_mod.AudioAnalyzer(ctx.args.models)
    except Exception as err:
        log(f"Models downloaded but failed to load: {err}")
        return 1
    log("Models ready.")
    return 0


def cmd_seeds(ctx):
    mood_name, mood = get_mood(load_moods(ctx.args.moods), ctx.args.mood)
    seeds, problems = load_seeds(ctx, mood)
    rows = []
    for t in seeds:
        p = ctx.profiler.profile(t["main_artist"], t["title"], t.get("isrc"))
        tags = ", ".join(sorted(p["tags"], key=lambda k: -p["tags"][k])[:4])
        rows.append([t["title"], t["artist"], "yes" if t.get("uri") else "NO",
                     f"{tags} ({p['tag_source']})" if tags else "none", p["audio_status"]])
    print(f'Seeds for "{mood_name}":\n')
    table(rows, ["Title", "Artist", "On Spotify", "Last.fm tags", "Audio"])
    for p in problems:
        print(f"note: {p}")
    return 0


def cmd_analyze(ctx):
    lib = get_library(ctx)
    profiles = ctx.profile_all(lib, "library tracks")
    with_audio = sum(1 for p in profiles if p["audio"])
    with_tags = sum(1 for p in profiles if p["tags"])
    statuses = {}
    for p in profiles:
        statuses[p["audio_status"]] = statuses.get(p["audio_status"], 0) + 1
    print(f"{len(profiles)} tracks analyzed: {with_audio} with audio, {with_tags} with Last.fm tags.")
    print("Audio status: " + ", ".join(f"{k} {v}" for k, v in sorted(statuses.items())))
    return 0


def print_summary(model, mood_name):
    ups, downs, sound = model.summary()
    from .model import label
    print(f'What "{mood_name}" means, learned from {model.n_positives} seed songs '
          f"vs {model.n_negatives} other tracks in your library:\n")
    print("  More of:  " + ", ".join(label(n) for n, _ in ups))
    print("  Less of:  " + ", ".join(label(n) for n, _ in downs))
    if sound:
        print(f"  Overall sound (beyond the named moods) also counts, weight {sound:.2f}.")


def cmd_train(ctx):
    from .model import held_out_check
    mood_name, mood = get_mood(load_moods(ctx.args.moods), ctx.args.mood)
    lib = get_library(ctx)
    model, _, seed_profiles, _, negatives = train(ctx, mood_name, mood, lib)
    print_summary(model, mood_name)
    print("\nCheck: each seed left out in turn, then ranked against your library:")
    results = held_out_check(seed_profiles, negatives, use_audio=not ctx.args.no_audio)
    if results:
        rows = [[p["title"], p["artist"], f"top {100 - pct:.0f}%"] for p, pct in results]
        table(rows, ["Seed", "Artist", "Ranked in your library"])
        median = sorted(pct for _, pct in results)[len(results) // 2]
        verdict = ("good: the model generalizes" if median >= 80 else
                   "fair: add more seeds that share the feel" if median >= 60 else
                   "weak: the seeds may not share one mood, or need more of them")
        print(f"\nMedian: top {100 - median:.0f}% ({verdict}).")
        print("Songs in your library that fit the mood but aren't seeds also rank high, so if a "
              "lot of\nyour library fits this mood, even a perfect model can't put seeds in the top 5%.")
    return 0


def cmd_build(ctx):
    from .discover import choose, external_candidates
    from .library import search_track

    a = ctx.args
    if not 0 <= a.horizon <= 1:
        raise SystemExit("--horizon must be between 0 and 1.")
    mood_name, mood = get_mood(load_moods(a.moods), a.mood)
    lib = get_library(ctx)
    model, seeds, _, lib_profiles, _ = train(ctx, mood_name, mood, lib)
    print_summary(model, mood_name)

    lib_scores = model.score(lib_profiles)
    library = sorted(({**t, "fit": float(s), "via": "your library", "profile": p}
                      for t, p, s in zip(lib, lib_profiles, lib_scores)), key=lambda c: -c["fit"])

    external = []
    if a.horizon > 0:
        tags = model.top_tags(5)
        log(f"Looking beyond your library (Last.fm: similar tracks, similar artists, tags: "
            f"{', '.join(tags) or 'none'})...")
        exclude = {p["key"] for p in lib_profiles} | {track_key(s["main_artist"], s["title"])
                                                     for s in seeds}
        cands = external_candidates(ctx.profiler.lastfm, seeds, tags, exclude, limit=a.depth)
        log(f"  {len(cands)} candidates")
        profiles = ctx.profile_all(cands, "new candidates")
        scores = model.score(profiles)
        external = sorted(({**c, "fit": float(s), "profile": p}
                           for c, p, s in zip(cands, profiles, scores)), key=lambda c: -c["fit"])

    def resolve(c):
        t = search_track(ctx.sp, c["main_artist"], c["title"])
        return {k: t[k] for k in ("uri", "artist", "title", "isrc")} if t else None

    picks = choose(library, external, a.size, a.horizon, a.max_per_artist, a.min_fit, resolve)
    if not picks:
        print("\nNo tracks fit well enough. Try a lower --min-fit, or add more seeds.")
        return 1
    rows = [[i, p["title"], p["artist"], f"{p['fit'] * 100:.0f}",
             "NEW" if p["new"] else "", p["via"], "; ".join(model.explain(p["profile"]))]
            for i, p in enumerate(picks, 1)]
    print()
    table(rows, ["#", "Title", "Artist", "Fit", "", "Found via", "Why"])
    n_new = sum(p["new"] for p in picks)
    name = a.name or f"{mood_name} · mood mix {datetime.date.today():%Y-%m-%d}"
    print(f'\n{len(picks)} tracks ({n_new} new to you) for "{name}".')
    if len(picks) < a.size:
        print(f"Only {len(picks)} of {a.size} tracks reached the minimum fit of {a.min_fit:.2f}.")
    if not a.apply:
        print("Dry run: no playlist created. Re-run with --apply to create it.")
        return 0
    if not confirm(f'Create private playlist "{name}" with {len(picks)} tracks?', a.yes):
        print("Aborted.")
        return 1
    uris = [p["uri"] for p in picks if p.get("uri")]
    created = ctx.sp.current_user_playlist_create(
        name, public=False, description=f'Mood "{mood_name}", built by spotify-reeper')
    add_uris(ctx.sp, created["id"], uris)
    print(f"Created: {(created.get('external_urls') or {}).get('spotify', created['id'])}")
    return 0


# ---------------- CLI ----------------

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="config.json", help="path to config.json")
    common.add_argument("--moods", default="moods.json", help="path to the moods file")
    common.add_argument("--models", default=audio_mod.DEFAULT_MODEL_DIR,
                        help="folder holding the audio models")
    common.add_argument("--cache", default=CACHE_PATH, help="analysis cache file")
    common.add_argument("--no-audio", action="store_true",
                        help="use Last.fm tags only (skip previews and audio models)")
    common.add_argument("--no-liked", action="store_true",
                        help="leave Liked Songs out of your library")
    common.add_argument("--limit", type=int, metavar="N",
                        help="only use the first N library tracks (for a quick trial)")

    p = argparse.ArgumentParser(description="Build Spotify playlists by mood, learned from seed songs.")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("setup", parents=[common], help="download the audio models (once)")
    s.set_defaults(func=cmd_setup)

    s = sub.add_parser("seeds", parents=[common],
                       help="check a mood's seed songs are found and analyzed")
    s.add_argument("mood")
    s.set_defaults(func=cmd_seeds)

    s = sub.add_parser("analyze", parents=[common],
                       help="analyze your whole library ahead of time (resumable)")
    s.set_defaults(func=cmd_analyze)

    s = sub.add_parser("train", parents=[common],
                       help="show what a mood means to the model, and how well it learned it")
    s.add_argument("mood")
    s.set_defaults(func=cmd_train)

    s = sub.add_parser("build", parents=[common], help="build a playlist for a mood")
    s.add_argument("mood")
    s.add_argument("--size", type=int, default=40, help="number of tracks (default 40)")
    s.add_argument("--horizon", type=float, default=0.3,
                   help="share of tracks from outside your library, 0..1 (default 0.3)")
    s.add_argument("--max-per-artist", type=int, default=3, help="default 3")
    s.add_argument("--min-fit", type=float, default=0.5,
                   help="minimum fit score, 0..1 (default 0.5)")
    s.add_argument("--depth", type=int, default=300,
                   help="how many outside candidates to analyze (default 300)")
    s.add_argument("--name", help="playlist name (default: '<mood> · mood mix <date>')")
    s.add_argument("--apply", action="store_true", help="create the playlist (default: dry run)")
    s.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    s.set_defaults(func=cmd_build)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(Context(args))
    except KeyboardInterrupt:
        log("\nStopped. Everything analyzed so far is cached; re-run to continue.")
        return 130
