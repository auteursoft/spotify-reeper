"""Engine state: settings from the app, Spotify clients, caches.

It offers the same attributes as mood.cli.Context (args, sp, user_id, cache, profiler,
profile_all), so the mood code is shared with the command-line tool rather than copied.
Clients and database connections are per thread, because each lane runs in its own thread.
"""

import os
import threading
import types

from mood import audio as audio_mod

from .auth import TokenManager
from .server import EngineError


class Session:
    def __init__(self, server):
        self.server = server
        self.config = {}
        self.tokens = None
        self._local = threading.local()
        self._lock = threading.Lock()
        self._library = None      # cached library tracks, shared by both lanes
        self._playlists = None    # cached playlist list
        self._generation = 0      # bumped by configure(), so threads rebuild their clients

    # ---------------- configuration ----------------

    def configure(self, params):
        with self._lock:
            old = dict(self.config)
            self.config.update({k: v for k, v in params.items() if k not in ("tokens",)})
            if "tokens" in params:
                t = params["tokens"] or {}
                if t.get("access_token"):
                    self.tokens = TokenManager(
                        self.config.get("client_id"), t.get("access_token"),
                        t.get("refresh_token"), t.get("expires_at"),
                        on_refresh=lambda new: self.server.event("tokens", new))
                else:
                    self.tokens = None
            account_changed = "tokens" in params or old.get("client_id") != self.config.get("client_id")
            if account_changed:
                self._library = self._playlists = None
            self._generation += 1
        if self.config.get("data_dir"):
            os.makedirs(self.config["data_dir"], exist_ok=True)

    @property
    def data_dir(self):
        d = self.config.get("data_dir")
        if not d:
            raise EngineError("not_configured", "The app has not told the engine where to keep data.")
        return d

    @property
    def models_dir(self):
        bundled = self.config.get("models_dir")
        if bundled and audio_mod.models_present(bundled):
            return bundled
        return os.path.join(self.data_dir, "models")

    @property
    def moods_path(self):
        return os.path.join(self.data_dir, "moods.json")

    @property
    def cache_path(self):
        return os.path.join(self.data_dir, "reeper-cache.db")

    @property
    def args(self):
        """What mood.cli reads from its command-line arguments."""
        return types.SimpleNamespace(no_audio=not self.config.get("use_audio", True),
                                     no_liked=not self.config.get("include_liked", True),
                                     limit=None)

    # ---------------- per-thread clients ----------------

    def _thread_state(self):
        st = self._local
        if getattr(st, "generation", None) != self._generation:
            st.generation = self._generation
            st.sp = st.user = st.profiler = None
            st.cache = None
        return st

    @property
    def sp(self):
        st = self._thread_state()
        if st.sp is None:
            if self.tokens is None:
                raise EngineError("auth", "Not signed in to Spotify.")
            import spotipy
            st.sp = spotipy.Spotify(auth_manager=self.tokens, retries=3, status_retries=3,
                                    requests_timeout=20)
        return st.sp

    @property
    def user(self):
        st = self._thread_state()
        if st.user is None:
            st.user = self.sp.current_user()
        return st.user

    @property
    def user_id(self):
        return self.user["id"]

    @property
    def cache(self):
        st = self._thread_state()
        if st.cache is None:
            from mood.cache import Cache
            st.cache = Cache(self.cache_path)
        return st.cache

    @property
    def profiler(self):
        st = self._thread_state()
        if st.profiler is None:
            from mood.deezer import Deezer
            from mood.lastfm import LastFM
            from mood.profile import Profiler
            key = self.config.get("lastfm_api_key")
            if not key:
                raise EngineError("not_configured", "Add your Last.fm API key in Settings.")
            analyzer = None
            if self.config.get("use_audio", True):
                try:
                    analyzer = audio_mod.AudioAnalyzer(self.models_dir)
                except FileNotFoundError:
                    raise EngineError("not_configured",
                                      "The audio models aren't installed yet. Download them in "
                                      "Settings, or turn off audio analysis.")
            import sys
            st.profiler = Profiler(self.cache, LastFM(key, self.cache), Deezer(self.cache),
                                   analyzer, log=lambda m: print(m, file=sys.stderr))
        return st.profiler

    # ---------------- shared caches ----------------

    def library(self, job=None, refresh=False):
        from mood.library import library_tracks
        with self._lock:
            lib = None if refresh else self._library
        if lib is None:
            if job:
                job.progress("Reading your library from Spotify…")
            lib = library_tracks(self.sp, self.user_id,
                                 include_liked=self.config.get("include_liked", True),
                                 log=lambda m: job.progress(m.strip()) if job else None)
            with self._lock:
                self._library = lib
        return lib

    def playlists(self, refresh=False):
        from cleaner import fetch_all_playlists
        with self._lock:
            pls = None if refresh else self._playlists
        if pls is None:
            pls = fetch_all_playlists(self.sp)
            with self._lock:
                self._playlists = pls
        return pls

    def playlists_changed(self):
        with self._lock:
            self._playlists = None
            self._library = None

    # ---------------- progress-reporting analysis ----------------

    @property
    def job(self):
        return getattr(self._local, "job", None)

    @job.setter
    def job(self, value):
        self._local.job = value

    def profile_all(self, tracks, what="tracks", each=False):
        profiler = self.profiler
        out, n = [], len(tracks)
        for i, t in enumerate(tracks, 1):
            if self.job:
                self.job.progress(f"Analyzing {what}: {t['artist']} – {t['title']}", i - 1, n)
            out.append(profiler.profile(t["main_artist"], t["title"], t.get("isrc")))
        if self.job and n:
            self.job.progress(f"Analyzed {n} {what}", n, n)
        return out
