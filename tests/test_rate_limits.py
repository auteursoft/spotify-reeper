"""Spotify rate limits and the library cache that avoids them."""
import http.server
import json
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cleaner  # noqa: E402
from mood import cli, library  # noqa: E402
from mood.cache import Cache  # noqa: E402


# ======================= rate limits, through spotipy's real HTTP stack =======================

class Spotify429(http.server.BaseHTTPRequestHandler):
    """Answers like Spotify does when an app is rate-limited; counts requests."""
    retry_after = "80040"
    hits = 0
    ok_after = None  # answer 200 once this many 429s have been sent

    def do_GET(self):
        cls = type(self)
        cls.hits += 1
        if cls.ok_after is not None and cls.hits > cls.ok_after:
            body = json.dumps({"id": "me", "display_name": "Sean"}).encode()
            self.send_response(200)
        else:
            body = json.dumps({"error": {"status": 429, "message": "API rate limit exceeded"}}).encode()
            self.send_response(429)
            self.send_header("Retry-After", cls.retry_after)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_spotify_server():
    Spotify429.hits, Spotify429.ok_after, Spotify429.retry_after = 0, None, "80040"
    server = http.server.HTTPServer(("127.0.0.1", 0), Spotify429)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1/"
    server.shutdown()


def client(prefix, sleeps):
    sp = cleaner.make_spotify(sleep=sleeps.append, auth="token", requests_timeout=5)
    sp.prefix = prefix
    return sp


def test_long_rate_limit_stops_at_once_instead_of_sleeping(fake_spotify_server):
    sleeps = []
    sp = client(fake_spotify_server, sleeps)
    t0 = time.time()
    with pytest.raises(cleaner.RateLimited) as err:
        sp.current_user()
    assert time.time() - t0 < 5 and sleeps == []
    assert Spotify429.hits == 1                     # no hidden retries either
    assert err.value.seconds == 80040
    assert "about 22 hours" in str(err.value) and "Try again after" in str(err.value)


def test_short_rate_limit_is_waited_out(fake_spotify_server):
    Spotify429.retry_after, Spotify429.ok_after = "2", 2
    sleeps = []
    assert client(fake_spotify_server, sleeps).current_user()["id"] == "me"
    assert sleeps == [2, 2] and Spotify429.hits == 3


def test_persistent_short_rate_limit_eventually_stops(fake_spotify_server):
    Spotify429.retry_after = "1"
    sleeps = []
    with pytest.raises(cleaner.RateLimited):
        client(fake_spotify_server, sleeps).current_user()
    assert sleeps == [1, 1, 1]


def test_rate_limited_message_wording():
    assert "about 5 minutes" in str(cleaner.RateLimited(300))
    assert "about 1 hour " in str(cleaner.RateLimited(3600))
    assert "about 22 hours" in str(cleaner.RateLimited(80040))


# ======================= library cache =======================

def track(n):
    return {"type": "track", "uri": f"spotify:track:{n:022d}", "name": f"Song {n}",
            "artists": [{"name": f"Artist {n % 7}"}], "external_ids": {}}


class CountingSpotify:
    """Fake Spotify: 30 owned playlists with snapshot ids, Liked Songs, request counting."""

    def __init__(self, n_playlists=30, rate_limit_after=None):
        self.pls = [{"id": f"pl{i}", "name": f"List {i}", "owner": {"id": "me"},
                     "snapshot_id": "s1"} for i in range(n_playlists)]
        self.items = {f"pl{i}": [track(i * 10 + j) for j in range(3)] for i in range(n_playlists)}
        self.liked = [{"added_at": f"2026-01-{d:02d}", "track": track(900 + d)} for d in range(1, 8)]
        self.requests = 0
        self.item_reads = []
        self.rate_limit_after = rate_limit_after
        self.searches = 0

    def _req(self):
        self.requests += 1
        if self.rate_limit_after is not None and self.requests > self.rate_limit_after:
            raise cleaner.RateLimited(80040)

    def current_user_playlists(self, limit=50):
        self._req()
        return {"items": list(self.pls), "next": None}

    def playlist_items(self, pid, limit=50):
        self._req()
        self.item_reads.append(pid)
        return {"items": [{"item": t} for t in self.items[pid]], "next": None}

    def current_user_saved_tracks(self, limit=50):
        self._req()
        return {"items": list(self.liked), "total": len(self.liked), "next": None}

    def next(self, page):
        return None

    def playlist(self, pid, fields=None):
        self._req()
        return {"name": f"Linked {pid}", "snapshot_id": "s1"}

    def search(self, q, type="track", limit=10):
        self._req()
        self.searches += 1
        return {"tracks": {"items": [track(1)] if "Song 1" in q else []}}

    def track(self, tid):
        self._req()
        return track(int(tid))


@pytest.fixture
def cache(tmp_path):
    return Cache(str(tmp_path / "c.db"))


def read(sp, cache):
    return library.library_tracks(sp, "me", cache=cache, log=lambda m: None)


def test_second_run_reads_only_the_playlist_list(cache):
    sp = CountingSpotify()
    first = read(sp, cache)
    assert len(sp.item_reads) == 30 and len(first) == 30 * 3 + 7
    before = sp.requests
    second = read(sp, cache)
    assert sp.requests - before == 2                # the playlist list + one Liked Songs check
    assert sorted(t["uri"] for t in second) == sorted(t["uri"] for t in first)
    assert {tuple(t["playlists"]) for t in second} == {tuple(t["playlists"]) for t in first}


def test_only_changed_playlists_are_read_again(cache):
    sp = CountingSpotify()
    read(sp, cache)
    sp.item_reads.clear()
    sp.pls[4]["snapshot_id"] = "s2"
    sp.items["pl4"].append(track(555))
    lib = read(sp, cache)
    assert sp.item_reads == ["pl4"]
    assert "spotify:track:" + f"{555:022d}" in {t["uri"] for t in lib}


def test_liked_songs_reread_only_when_they_change(cache):
    sp = CountingSpotify()
    read(sp, cache)
    sp.liked.insert(0, {"added_at": "2026-02-01", "track": track(777)})
    lib = read(sp, cache)
    assert "spotify:track:" + f"{777:022d}" in {t["uri"] for t in lib}


def test_rate_limit_while_reading_stops_and_keeps_progress(cache):
    sp = CountingSpotify(rate_limit_after=11)          # list + 10 playlists, then 429
    with pytest.raises(cleaner.RateLimited):
        read(sp, cache)
    assert len(sp.item_reads) == 10                    # stopped at the 429, didn't plough on
    sp.rate_limit_after = None
    sp.item_reads.clear()
    read(sp, cache)
    assert len(sp.item_reads) == 20                    # the 10 already read came from the cache


def test_seed_lookups_and_searches_are_remembered(cache):
    sp = CountingSpotify()
    seed = {"artist": "Artist 1", "title": "Song 1"}
    assert library.resolve_seed(sp, seed, cache)["title"] == "Song 1"
    n = sp.requests
    assert library.resolve_seed(sp, seed, cache)["title"] == "Song 1"
    assert library.search_track_cached(sp, "Nobody", "Nothing", cache) is None
    m = sp.requests
    assert library.search_track_cached(sp, "Nobody", "Nothing", cache) is None
    assert sp.requests == m and m > n                  # "not found" is remembered too


def test_failed_search_is_not_remembered(cache):
    class Flaky(CountingSpotify):
        def search(self, q, type="track", limit=10):
            self.searches += 1
            raise RuntimeError("connection reset")
    sp = Flaky()
    assert library.search_track_cached(sp, "Artist 1", "Song 1", cache) is None
    first = sp.searches
    library.search_track_cached(sp, "Artist 1", "Song 1", cache)
    assert sp.searches == 2 * first                    # tried again


def test_rate_limit_during_search_propagates(cache):
    sp = CountingSpotify(rate_limit_after=0)
    with pytest.raises(cleaner.RateLimited):
        library.search_track_cached(sp, "Artist 1", "Song 1", cache)


def test_seed_playlists_use_the_cache(cache):
    sp = CountingSpotify()
    link = "https://open.spotify.com/playlist/" + "A" * 22
    sp.items["A" * 22] = [track(5), track(6)]
    tracks, problems = library.seed_playlist_tracks(sp, [link], cache)
    assert len(tracks) == 2 and problems == [] and tracks[0]["playlists"] == ["Linked " + "A" * 22]
    sp.item_reads.clear()
    library.seed_playlist_tracks(sp, [link], cache)
    assert sp.item_reads == []


def test_cli_reports_rate_limit_and_exits(monkeypatch, capsys):
    def boom(ctx):
        raise cleaner.RateLimited(80040)
    monkeypatch.setattr(cli, "cmd_analyze", boom)
    monkeypatch.setattr(cli.Context, "__init__", lambda self, args: None)
    parser = cli.build_parser()
    args = parser.parse_args(["analyze"])
    args.func = boom
    monkeypatch.setattr(cli, "build_parser", lambda: type("P", (), {"parse_args": lambda self, a: args})())
    assert cli.main(["analyze"]) == 1
    assert "about 22 hours" in capsys.readouterr().err
