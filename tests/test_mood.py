import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mood import audio, cli, discover, library  # noqa: E402
from mood.cache import Cache, cached  # noqa: E402
from mood.deezer import Deezer  # noqa: E402
from mood.lastfm import LastFM, normalize_tags  # noqa: E402
from mood.model import MoodModel, NotEnoughData, held_out_check  # noqa: E402
from mood.net import Http, HttpError  # noqa: E402
from mood.profile import Profiler  # noqa: E402
from mood.text import clean_title, fold, same_song, track_key  # noqa: E402


@pytest.fixture
def cache(tmp_path):
    return Cache(str(tmp_path / "cache.db"))


# ======================= text =======================

@pytest.mark.parametrize("raw,clean", [
    ("Alex Chilton - 2008 Remaster", "Alex Chilton"),
    ("Staring Down The Barrel Of The Middle Distance - Live At The Olympia Theatre, Dublin, "
     "Ireland / 2007", "Staring Down The Barrel Of The Middle Distance"),
    ("Pop Song 89 - Remastered 2013", "Pop Song 89"),
    ("Mrs. Robinson - 2022 Remastered Edition", "Mrs. Robinson"),
    ("Song (Remastered 2009)", "Song"),
    ("Hey Jude [Live]", "Hey Jude"),
    ("Rock - Paper", "Rock - Paper"),      # a dash that is part of the name stays
    ("Live Forever", "Live Forever"),
    ("1999", "1999"),
])
def test_clean_title(raw, clean):
    assert clean_title(raw) == clean


def test_track_key_ignores_versions_case_accents_and_leading_the():
    assert track_key("The Replacements", "Alex Chilton - 2008 Remaster") == \
        track_key("replacements", "Alex Chilton")
    assert fold("Beyoncé & Jay-Z") == "beyonce and jay z"
    assert same_song("R.E.M.", "Superman", "R.E.M.", "Superman - 2012 Remaster")
    assert not same_song("R.E.M.", "Superman", "The Clique", "Superman")


# ======================= cache / http =======================

def test_cache_roundtrip_and_cached(cache):
    calls = []
    assert cached(cache, "ns", "k", lambda: calls.append(1) or {"a": 1}) == {"a": 1}
    assert cached(cache, "ns", "k", lambda: calls.append(1) or {"a": 2}) == {"a": 1}
    assert calls == [1]
    assert cache.count("ns") == 1 and cache.get("ns", "missing") is None


class Resp:
    def __init__(self, status, payload=None, content=b"", headers=None):
        self.status_code, self._payload, self.content = status, payload, content
        self.headers = headers or {}

    def json(self):
        return self._payload


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_http_retries_429_and_5xx_then_succeeds():
    sleeps = []
    s = Session([Resp(429, headers={"Retry-After": "3"}), Resp(503), Resp(200, {"ok": 1})])
    http = Http(min_interval=0, session=s, sleep=sleeps.append)
    assert http.get_json("u") == {"ok": 1}
    assert 3.0 in sleeps and len(s.calls) == 3


def test_http_gives_up_and_does_not_retry_404():
    http = Http(min_interval=0, retries=1, session=Session([Resp(500), Resp(500)]), sleep=lambda _: 0)
    with pytest.raises(HttpError):
        http.get_json("u")
    s = Session([Resp(404)])
    with pytest.raises(HttpError):
        Http(min_interval=0, session=s, sleep=lambda _: 0).get_json("u")
    assert len(s.calls) == 1


# ======================= Last.fm =======================

class FakeHttp:
    """Answers by a function of (url, params); records calls."""

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def get_json(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        result = self.answer(url, params or {})
        if isinstance(result, Exception):
            raise result
        return result

    def download(self, url, path):
        self.calls.append((url, path))
        with open(path, "w") as f:
            f.write(url)
        return path


def test_normalize_tags_drops_noise_and_artist_name():
    raw = [{"name": "Power Pop", "count": 100}, {"name": "seen live", "count": 90},
           {"name": "The Knack", "count": 50}, {"name": "jangle", "count": 2},
           {"name": "new wave", "count": "40"}]
    assert normalize_tags(raw, artist="The Knack") == {"power pop": 1.0, "new wave": 0.4}


def test_lastfm_track_tags_cached_and_single_object_quirk(cache):
    http = FakeHttp(lambda u, p: {"toptags": {"tag": {"name": "college rock", "count": 100}}})
    lf = LastFM("key", cache, http)
    assert lf.track_tags("R.E.M.", "Superman - 2012 Remaster") == \
        {"tags": {"college rock": 1.0}, "source": "track"}
    lf.track_tags("R.E.M.", "Superman")  # same song: served from cache
    assert len(http.calls) == 1
    assert http.calls[0][1]["track"] == "Superman" and http.calls[0][1]["api_key"] == "key"


def test_lastfm_falls_back_to_artist_tags_at_half_weight(cache):
    def answer(url, p):
        if p["method"] == "track.getTopTags":
            return {"error": 6, "message": "Track not found"}
        return {"toptags": {"tag": [{"name": "power pop", "count": 100}]}}
    lf = LastFM("key", cache, FakeHttp(answer))
    assert lf.track_tags("The Knack", "Obscure B-side") == \
        {"tags": {"power pop": 0.5}, "source": "artist"}


def test_lastfm_network_failure_means_no_data(cache):
    lf = LastFM("key", cache, FakeHttp(lambda u, p: HttpError("down")))
    assert lf.similar_tracks("A", "B") == []


def test_lastfm_requires_key(cache):
    with pytest.raises(ValueError, match="lastfm_api_key"):
        LastFM(None, cache)


def test_lastfm_similar_and_tag_tracks(cache):
    def answer(url, p):
        return {
            "track.getSimilar": {"similartracks": {"track": [
                {"name": "Kiss Me on the Bus", "artist": {"name": "The Replacements"}}]}},
            "artist.getSimilar": {"similarartists": {"artist": [{"name": "Hüsker Dü"}]}},
            "artist.getTopTracks": {"toptracks": {"track": [
                {"name": "Makes No Sense at All", "artist": {"name": "Hüsker Dü"}}]}},
            "tag.getTopTracks": {"tracks": {"track": [
                {"name": "Shake Some Action", "artist": {"name": "Flamin' Groovies"}}]}},
        }[p["method"]]
    lf = LastFM("key", cache, FakeHttp(answer))
    assert lf.similar_tracks("R.E.M.", "Superman")[0]["title"] == "Kiss Me on the Bus"
    assert lf.similar_artists("Replacements") == ["Hüsker Dü"]
    assert lf.artist_top_tracks("Hüsker Dü")[0]["artist"] == "Hüsker Dü"
    assert lf.tag_top_tracks("power pop")[0]["title"] == "Shake Some Action"


# ======================= Deezer =======================

def test_deezer_isrc_first(cache):
    http = FakeHttp(lambda u, p: {"id": 42} if "isrc:" in u else {"data": []})
    assert Deezer(cache, http).find_id("R.E.M.", "Superman", "USIR1000") == 42
    assert "track/isrc:USIR1000" in http.calls[0][0]


def test_deezer_search_fallback_matches_same_song_only(cache):
    def answer(url, p):
        if "isrc:" in url:
            return {"error": {"code": 800}}
        return {"data": [
            {"id": 1, "title": "Superman", "artist": {"name": "The Clique"}},
            {"id": 2, "title": "Superman (2012 Remaster)", "artist": {"name": "R.E.M."}}]}
    assert Deezer(cache, FakeHttp(answer)).find_id("R.E.M.", "Superman", "X") == 2


def test_deezer_no_match_is_cached(cache):
    http = FakeHttp(lambda u, p: {"data": [{"id": 9, "title": "Other", "artist": {"name": "Else"}}]})
    dz = Deezer(cache, http)
    assert dz.find_id("A", "B") is None
    dz.find_id("A", "B")
    assert len(http.calls) == 1


# ======================= profile =======================

class FakeLastFMTags:
    def __init__(self, tags=None):
        self.tags = tags or {}

    def track_tags(self, artist, title):
        return {"tags": self.tags.get(title, {}), "source": "track"}


class FakeDeezer:
    def __init__(self, ids=None, previews=None, fail_download=False):
        self.ids, self.previews, self.fail_download = ids or {}, previews or {}, fail_download
        self.downloads = 0

    def find_id(self, artist, title, isrc=None):
        return self.ids.get(title)

    def preview_url(self, deezer_id):
        return self.previews.get(deezer_id)

    def download(self, url, path):
        self.downloads += 1
        if self.fail_download:
            raise HttpError("timeout")
        with open(path, "w") as f:
            f.write(url)


class FakeAnalyzer:
    """'Analyzes' a file whose content names its vibe: loud or soft."""

    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    def analyze(self, path):
        self.calls += 1
        if self.fail:
            raise ValueError("cannot decode")
        loud = "loud" in open(path).read()
        rng = np.random.default_rng(len(path))
        emb = (np.ones(32) if loud else -np.ones(32)) + rng.normal(0, 0.3, 32)
        return {"moods": {"audio:aggressive": 0.9 if loud else 0.1,
                          "theme:romantic": 0.1 if loud else 0.8},
                "embedding": emb.round(3).tolist()}


def test_profile_ok_then_cached(cache):
    an = FakeAnalyzer()
    p = Profiler(cache, FakeLastFMTags({"S": {"rock": 1.0}}), FakeDeezer({"S": 7}, {7: "loud.mp3"}), an)
    first = p.profile("A", "S")
    assert first["audio_status"] == "ok" and first["tags"] == {"rock": 1.0}
    assert first["audio"]["moods"]["audio:aggressive"] == 0.9
    assert p.profile("A", "S")["audio"] == first["audio"] and an.calls == 1


@pytest.mark.parametrize("deezer,status", [
    (FakeDeezer({}), "no_match"),
    (FakeDeezer({"S": 7}, {}), "no_preview"),
])
def test_profile_missing_audio_statuses(cache, deezer, status):
    prof = Profiler(cache, FakeLastFMTags(), deezer, FakeAnalyzer()).profile("A", "S")
    assert prof["audio"] is None and prof["audio_status"] == status


def test_profile_network_errors_are_retried_but_decode_errors_are_cached(cache):
    dz = FakeDeezer({"S": 7}, {7: "x"}, fail_download=True)
    p = Profiler(cache, FakeLastFMTags(), dz, FakeAnalyzer(), log=lambda m: None)
    assert p.profile("A", "S")["audio_status"] == "error"
    p.profile("A", "S")
    assert dz.downloads == 2  # not cached: tried again

    an = FakeAnalyzer(fail=True)
    p = Profiler(cache, FakeLastFMTags(), FakeDeezer({"T": 8}, {8: "x"}), an, log=lambda m: None)
    p.profile("A", "T")
    p.profile("A", "T")
    assert an.calls == 1  # cached: a file that can't be decoded won't decode next time


def test_profile_without_analyzer_is_tags_only(cache):
    prof = Profiler(cache, FakeLastFMTags({"S": {"pop": 0.5}}), None, None).profile("A", "S")
    assert prof["audio_status"] == "off" and prof["tags"] == {"pop": 0.5}


def test_preview_temp_file_is_removed(cache, tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    Profiler(cache, FakeLastFMTags(), FakeDeezer({"S": 7}, {7: "loud"}), FakeAnalyzer()).profile("A", "S")
    assert not list(tmp_path.glob("*.mp3"))


# ======================= audio helpers =======================

def test_model_files_cover_embedding_and_all_heads():
    names = [n for n, _ in audio.model_files()]
    assert "discogs-effnet-bs64-1.pb" in names and "discogs-effnet-bs64-1.json" in names
    assert "mtg_jamendo_moodtheme-discogs-effnet-1.pb" in names
    assert len(names) == 2 * (1 + len(audio.HEADS))
    assert all(u.startswith("https://essentia.upf.edu/models/") for _, u in audio.model_files())


def test_positive_class_and_node_lookup():
    assert audio.positive_class(["non_aggressive", "aggressive"]) == 1
    assert audio.positive_class(["danceable", "not_danceable"]) == 0
    meta = {"schema": {"inputs": [{"name": "in"}], "outputs": [
        {"name": "PartitionedCall:0", "output_purpose": "predictions"},
        {"name": "PartitionedCall:1", "output_purpose": "embeddings"}]}}
    assert audio._node(meta, "outputs", "embeddings", "x") == "PartitionedCall:1"
    assert audio._node(meta, "inputs", None, "x") == "in"
    assert audio._node({}, "outputs", "predictions", "model/Softmax") == "model/Softmax"


def test_setup_models_downloads_missing_and_reports_failures(tmp_path):
    class H:
        def download(self, url, path):
            if "mood_sad" in url:
                raise HttpError("404")
            open(path, "w").write("x")
    (tmp_path / "discogs-effnet-bs64-1.pb").write_text("already here")
    failures = audio.setup_models(str(tmp_path), H(), log=lambda m: None)
    assert [f[0] for f in failures] == ["mood_sad-discogs-effnet-1.pb", "mood_sad-discogs-effnet-1.json"]
    assert (tmp_path / "discogs-effnet-bs64-1.pb").read_text() == "already here"
    assert not list(tmp_path.glob("*.part"))
    assert not audio.models_present(str(tmp_path))


def test_analyzer_explains_missing_models(tmp_path):
    with pytest.raises(FileNotFoundError, match="moods.py setup"):
        audio.AudioAnalyzer(str(tmp_path))


# ======================= model =======================

def synth_profile(i, loud, audio_on=True, tags=True):
    rng = np.random.default_rng(i)
    p = {"key": f"k{i}", "artist": f"A{i}", "title": f"T{i}", "tags": {}, "audio": None}
    if tags:
        p["tags"] = {"power pop": 0.9, "rock": 0.6} if loud else {"ballad": 0.8, "rock": 0.3}
        p["tags"]["noise%d" % (i % 7)] = 0.5
    if audio_on:
        emb = (np.ones(32) if loud else -np.ones(32)) + rng.normal(0, 0.5, 32)
        p["audio"] = {"moods": {"audio:aggressive": float(np.clip((0.8 if loud else 0.2) + rng.normal(0, 0.1), 0, 1)),
                                "theme:romantic": float(np.clip((0.2 if loud else 0.7) + rng.normal(0, 0.1), 0, 1))},
                      "embedding": emb.tolist()}
    return p


def library_split(n_pos=8, n_loud_neg=10, n_soft=60):
    pos = [synth_profile(i, True) for i in range(n_pos)]
    neg = [synth_profile(100 + i, True) for i in range(n_loud_neg)] + \
          [synth_profile(200 + i, False) for i in range(n_soft)]
    return pos, neg


def test_model_ranks_matching_tracks_above_others():
    pos, neg = library_split()
    m = MoodModel().fit(pos, neg)
    loud_new = [synth_profile(500 + i, True) for i in range(5)]
    soft_new = [synth_profile(600 + i, False) for i in range(5)]
    assert m.score(loud_new).min() > m.score(soft_new).max()


def test_model_summary_and_explanations_are_readable():
    pos, neg = library_split()
    m = MoodModel().fit(pos, neg)
    ups, downs, sound = m.summary()
    up_names = [n for n, _ in ups]
    assert "tag:power pop" in up_names and "audio:aggressive" in up_names
    assert "tag:ballad" in [n for n, _ in downs]
    assert sound > 0
    why = m.explain(synth_profile(700, True))
    assert why and all(isinstance(w, str) for w in why)
    assert any(w in ("tagged power pop", "sounds aggressive", "overall sound") for w in why)
    assert "power pop" in m.top_tags()


def test_model_handles_tracks_missing_audio_or_tags():
    pos, neg = library_split()
    neg += [synth_profile(300 + i, False, audio_on=False) for i in range(10)]
    m = MoodModel().fit(pos, neg)
    no_audio_loud = synth_profile(800, True, audio_on=False)
    no_audio_soft = synth_profile(801, False, audio_on=False)
    s = m.score([no_audio_loud, no_audio_soft, synth_profile(802, True, audio_on=False, tags=False)])
    assert s[0] > s[1]
    assert s[2] == 0.0  # nothing known about it: never picked


def test_model_tags_only_mode():
    pos, neg = library_split()
    m = MoodModel(use_audio=False).fit(pos, neg)
    assert not any(n.startswith(("audio:", "theme:", "sound:")) for n in m.feature_names)
    assert m.score([synth_profile(900, True)])[0] > m.score([synth_profile(901, False)])[0]


def test_model_refuses_too_little_data():
    pos, neg = library_split()
    with pytest.raises(NotEnoughData, match="seed"):
        MoodModel().fit(pos[:3], neg)
    with pytest.raises(NotEnoughData, match="analyze"):
        MoodModel().fit(pos, neg[:5])


def test_held_out_check_scores_consistent_seeds_high():
    pos, neg = library_split()
    results = held_out_check(pos, neg)
    assert len(results) == len(pos)
    assert np.median([pct for _, pct in results]) >= 80


# ======================= discovery / choosing =======================

class FakeLastFMDiscover:
    def similar_tracks(self, artist, title, limit=25):
        return [{"artist": "Sim", "title": f"{title} sim {i}"} for i in range(3)]

    def similar_artists(self, artist, limit=8):
        return ["Other Band"]

    def artist_top_tracks(self, artist, limit=3):
        return [{"artist": artist, "title": "Hit"}]

    def tag_top_tracks(self, tag, limit=25):
        return [{"artist": "Tagger", "title": f"{tag} anthem"}, {"artist": "", "title": "bad"}]


def test_external_candidates_interleaves_sources_and_excludes_known():
    seeds = [{"main_artist": "R.E.M.", "title": "Superman"},
             {"main_artist": "The Knack", "title": "My Sharona"}]
    exclude = {track_key("Sim", "Superman sim 0")}
    out = discover.external_candidates(FakeLastFMDiscover(), seeds, ["power pop"], exclude, limit=5)
    titles = [c["title"] for c in out]
    assert "Superman sim 0" not in titles
    # Round-robin: each source gets one turn per round. Round 1: Superman's turn is the
    # excluded track, both artist streams offer the same "Hit" (kept once), the tag stream
    # offers its anthem, and its nameless entry is dropped. Round 2 continues.
    assert titles == ["My Sharona sim 0", "Hit", "power pop anthem", "Superman sim 1",
                      "My Sharona sim 1"]
    assert {c["via"] for c in out} >= {"similar to Superman", "tagged power pop"}
    assert "bad" not in titles


def cand(title, artist, fit):
    return {"title": title, "main_artist": artist, "artist": artist, "fit": fit,
            "uri": f"spotify:track:{title}"}


def test_choose_respects_horizon_artist_cap_and_min_fit():
    lib = [cand(f"L{i}", "Same" if i < 5 else f"LA{i}", 0.9 - i * 0.01) for i in range(20)]
    ext = [cand(f"E{i}", f"EA{i}", 0.95 - i * 0.01) for i in range(20)] + [cand("weak", "W", 0.2)]
    picks = discover.choose(lib, ext, size=10, horizon=0.3, max_per_artist=2, min_fit=0.5)
    assert len(picks) == 10
    assert sum(p["new"] for p in picks) == 3
    assert sum(p["main_artist"] == "Same" for p in picks) == 2
    assert all(p["fit"] >= 0.5 for p in picks)
    assert [p["fit"] for p in picks] == sorted([p["fit"] for p in picks], reverse=True)


def test_choose_fills_from_the_other_side_and_skips_unresolvable():
    lib = [cand("L0", "a", 0.9)]
    ext = [cand(f"E{i}", f"e{i}", 0.8) for i in range(6)]
    resolve = lambda c: None if c["title"] == "E0" else {"uri": "spotify:track:" + c["title"]}  # noqa: E731
    picks = discover.choose(lib, ext, size=4, horizon=0.25, resolve=resolve)
    assert len(picks) == 4 and "E0" not in [p["title"] for p in picks]
    assert sum(p["new"] for p in picks) == 3  # library had only one track


def test_choose_horizon_ends_are_strict():
    lib, ext = [cand("L", "a", 0.9)], [cand("E", "b", 0.99), cand("E2", "c", 0.98)]
    assert [p["title"] for p in discover.choose(lib, ext, size=5, horizon=0)] == ["L"]
    assert [p["title"] for p in discover.choose(lib, ext, size=5, horizon=1)] == ["E", "E2"]
    # in between, a short side is filled from the other
    assert len(discover.choose(lib, ext, size=3, horizon=0.5)) == 3


# ======================= Spotify matching =======================

def sp_track(title, artist, tid=None, isrc=None):
    tid = tid or (title + artist).replace(" ", "")[:22].ljust(22, "x")
    return {"type": "track", "uri": f"spotify:track:{tid}", "name": title,
            "artists": [{"name": artist}], "external_ids": {"isrc": isrc} if isrc else {}}


class FakeSearch:
    def __init__(self, catalog):
        self.catalog, self.queries = catalog, []

    def search(self, q, type="track", limit=10):
        self.queries.append(q)
        words = fold(q.replace("track:", " ").replace("artist:", " ")).split()
        hits = [t for t in self.catalog
                if all(w in fold(t["name"] + " " + t["artists"][0]["name"]) for w in words)]
        return {"tracks": {"items": hits[:limit]}}

    def track(self, tid):
        return next(t for t in self.catalog if t["uri"].endswith(tid))


def test_search_prefers_exact_version_then_any_version():
    catalog = [sp_track("Superman", "The Clique"), sp_track("Superman", "R.E.M."),
               sp_track("Superman - 2012 Remaster", "R.E.M.")]
    sp = FakeSearch(catalog)
    assert library.search_track(sp, "R.E.M.", "Superman - 2012 Remaster")["title"] == \
        "Superman - 2012 Remaster"
    assert library.search_track(sp, "R.E.M.", "Superman")["artist"] == "R.E.M."
    assert library.search_track(sp, "R.E.M.", "Losing My Religion") is None


def test_resolve_seed_accepts_uri_and_url():
    t = sp_track("My Sharona", "The Knack", tid="6Ff1sm8EUMhAXbPBnFDqYk")
    sp = FakeSearch([t])
    assert library.resolve_seed(sp, "spotify:track:6Ff1sm8EUMhAXbPBnFDqYk")["title"] == "My Sharona"
    assert library.resolve_seed(sp, "https://open.spotify.com/track/6Ff1sm8EUMhAXbPBnFDqYk?si=x")
    assert library.resolve_seed(sp, "not a link") is None


def test_to_track_skips_local_files_and_episodes():
    assert library.to_track({"type": "episode", "uri": "spotify:episode:x"}) is None
    assert library.to_track({"type": "track", "uri": "spotify:local:a:b:c", "is_local": True}) is None
    t = library.to_track(sp_track("A", "B", isrc="US123"), "My list")
    assert t["isrc"] == "US123" and t["playlists"] == ["My list"]


# ======================= end to end (dry run and --apply) =======================

LOUD = [("R.E.M.", "Superman"), ("R.E.M.", "Begin The Begin"), ("The Knack", "My Sharona"),
        ("The Replacements", "Alex Chilton"), ("10,000 Maniacs", "These Are Days"),
        ("The Lemonheads", "Mrs. Robinson")]
LOUD_LIB = [(f"Loud Band {i}", f"Banger {i}") for i in range(8)]
SOFT_LIB = [(f"Soft Act {i}", f"Lullaby {i}") for i in range(40)]
LOUD_NEW = [(f"New Loud {i}", f"Fresh Banger {i}") for i in range(6)]
SOFT_NEW = [(f"New Soft {i}", f"Fresh Lullaby {i}") for i in range(6)]
VIBE = {t: "loud" for _, t in LOUD + LOUD_LIB + LOUD_NEW}


class E2ESpotify(FakeSearch):
    def __init__(self):
        lib = [sp_track(t, a) for a, t in LOUD + LOUD_LIB + SOFT_LIB]
        super().__init__(lib + [sp_track(t, a) for a, t in LOUD_NEW + SOFT_NEW])
        self.library = lib
        self.created, self.added = [], []

    def current_user(self):
        return {"id": "me"}

    def current_user_playlists(self, limit=50):
        return {"items": [{"id": "pl1", "name": "Everything", "owner": {"id": "me"}}], "next": None}

    def playlist_items(self, pid, limit=50):
        return {"items": [{"item": t} for t in self.library], "next": None}

    def current_user_saved_tracks(self, limit=50):
        return {"items": [], "next": None}

    def next(self, page):
        return None

    def current_user_playlist_create(self, name, public=True, description=""):
        self.created.append((name, public))
        return {"id": "newpl", "external_urls": {"spotify": "https://open.spotify.com/playlist/newpl"}}

    def playlist_add_items(self, pid, uris):
        self.added.append((pid, list(uris)))


class E2ELastFM:
    def track_tags(self, artist, title):
        loud = VIBE.get(title) == "loud"
        return {"tags": {"power pop": 0.9, "rock": 0.5} if loud else {"ballad": 0.9}, "source": "track"}

    def similar_tracks(self, artist, title, limit=25):
        return [{"artist": a, "title": t} for a, t in LOUD_NEW[:3] + SOFT_NEW[:3]]

    def similar_artists(self, artist, limit=8):
        return []

    def artist_top_tracks(self, artist, limit=3):
        return []

    def tag_top_tracks(self, tag, limit=25):
        return [{"artist": a, "title": t} for a, t in LOUD_NEW[3:] + SOFT_NEW[3:]]


class E2EDeezer(FakeDeezer):
    def find_id(self, artist, title, isrc=None):
        return abs(hash(title)) % 10**9

    def preview_url(self, deezer_id):
        return "preview"

    def download(self, url, path):
        raise AssertionError("replaced per test")


@pytest.fixture
def e2e(tmp_path, monkeypatch):
    moods = tmp_path / "moods.json"
    moods.write_text(json.dumps({"Lets go": {"seeds": [{"artist": a, "title": t} for a, t in LOUD]},
                                 "empty": {"seeds": []}}))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"client_id": "x", "client_secret": "y", "lastfm_api_key": "k"}))
    sp = E2ESpotify()

    class Dz(E2EDeezer):
        def find_id(self, artist, title, isrc=None):
            return title  # use the title as the id so download knows the vibe

        def preview_url(self, deezer_id):
            return VIBE.get(deezer_id, "soft")

        def download(self, url, path):
            open(path, "w").write(url)

    def fake_profiler(self):
        if self._profiler is None:
            self._profiler = Profiler(self.cache, E2ELastFM(), Dz(), FakeAnalyzer(), log=lambda m: None)
        return self._profiler

    monkeypatch.setattr(cli.Context, "profiler", property(fake_profiler))
    monkeypatch.setattr(cli.Context, "sp", property(lambda self: sp))
    monkeypatch.setattr(cli.Context, "user_id", property(lambda self: "me"))
    base = ["--moods", str(moods), "--config", str(config), "--cache", str(tmp_path / "c.db")]
    return sp, base


def test_e2e_build_dry_run_picks_the_mood_and_creates_nothing(e2e, capsys):
    sp, base = e2e
    assert cli.main(["build", "lets go", "--size", "10", "--horizon", "0.3"] + base) == 0
    out = capsys.readouterr().out
    assert "Dry run" in out and sp.created == []
    rows = [line for line in out.splitlines() if "Banger" in line or "Lullaby" in line]
    assert rows and not any("Lullaby" in r for r in rows)
    assert any("NEW" in r and "Fresh Banger" in r for r in rows)
    assert "More of:" in out and "tagged power pop" in out


def test_e2e_build_apply_creates_private_playlist(e2e, capsys):
    sp, base = e2e
    assert cli.main(["build", "lets go", "--size", "8", "--name", "LFG", "--apply", "--yes"] + base) == 0
    assert sp.created == [("LFG", False)]
    (pid, uris), = sp.added
    assert pid == "newpl" and len(uris) == 8 and all(u.startswith("spotify:track:") for u in uris)


def test_e2e_train_reports_learning_and_check(e2e, capsys):
    _, base = e2e
    assert cli.main(["train", "LETS GO"] + base) == 0
    out = capsys.readouterr().out
    assert "learned from 6 seed songs" in out and "Median: top" in out


def test_e2e_seeds_table(e2e, capsys):
    _, base = e2e
    assert cli.main(["seeds", "lets go"] + base) == 0
    out = capsys.readouterr().out
    assert "My Sharona" in out and "power pop" in out and "ok" in out


def test_e2e_errors_are_clear(e2e):
    _, base = e2e
    with pytest.raises(SystemExit, match="No mood called"):
        cli.main(["build", "sexy"] + base)
    with pytest.raises(SystemExit, match="no seed songs"):
        cli.main(["build", "empty"] + base)
    with pytest.raises(SystemExit, match="horizon"):
        cli.main(["build", "lets go", "--horizon", "2"] + base)


def test_shipped_moods_file_is_valid():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "moods.json")
    moods = json.load(open(path))
    assert {"lets fucking go", "romantic", "chill and work in the garage"} <= set(moods)
    assert len(moods["lets fucking go"]["seeds"]) == 9


# ======================= seed playlists =======================

class PlaylistSpotify:
    def __init__(self, contents, names, forbidden=(), name_lookup_fails=False):
        self.contents, self.names = contents, names
        self.forbidden, self.name_lookup_fails = set(forbidden), name_lookup_fails
        self.read = []

    def current_user_playlists(self, limit=50):
        return {"items": [{"id": i, "name": n, "owner": {"id": "me"}} for i, n in self.names.items()],
                "next": None}

    def next(self, page):
        return None

    def playlist(self, pid, fields=None):
        if self.name_lookup_fails:
            raise RuntimeError("nope")
        return {"name": self.names.get(pid, "Linked list")}

    def playlist_items(self, pid, limit=50):
        if pid in self.forbidden:
            import spotipy
            raise spotipy.SpotifyException(403, -1, "Forbidden")
        self.read.append(pid)
        return {"items": [{"item": t} for t in self.contents.get(pid, [])], "next": None}


LFG_ID = "4KIAT34SSNCFOFnA7yFlhI"


@pytest.mark.parametrize("entry", [
    f"https://open.spotify.com/playlist/{LFG_ID}?si=cc3e9ea86d074325",
    f"spotify:playlist:{LFG_ID}",
])
def test_seed_playlist_by_link(entry):
    sp = PlaylistSpotify({LFG_ID: [sp_track("Kids in America", "Kim Wilde")]}, {LFG_ID: "LFG"})
    tracks, problems = library.seed_playlist_tracks(sp, [entry])
    assert [t["title"] for t in tracks] == ["Kids in America"] and problems == []
    assert tracks[0]["playlists"] == ["LFG"]


def test_seed_playlist_by_name_and_missing_name():
    sp = PlaylistSpotify({"p1": [sp_track("A", "B")]}, {"p1": "zb- Gym"})
    tracks, problems = library.seed_playlist_tracks(sp, ["ZB- gym", "no such list"])
    assert len(tracks) == 1
    assert problems == ['no playlist named "no such list" in your library']


def test_seed_playlist_someone_elses_is_explained_not_fatal():
    sp = PlaylistSpotify({"ok": [sp_track("A", "B")]}, {"ok": "Mine"}, forbidden={LFG_ID})
    tracks, problems = library.seed_playlist_tracks(
        sp, [f"spotify:playlist:{LFG_ID}", "Mine"])
    assert len(tracks) == 1
    assert len(problems) == 1 and "own or collaborate on" in problems[0]


def test_seed_playlist_name_lookup_failure_still_reads_tracks():
    sp = PlaylistSpotify({LFG_ID: [sp_track("A", "B")]}, {}, name_lookup_fails=True)
    tracks, problems = library.seed_playlist_tracks(sp, [f"spotify:playlist:{LFG_ID}"])
    assert len(tracks) == 1 and problems == []


def test_shipped_lfg_mood_uses_the_playlist():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "moods.json")
    entries = json.load(open(path))["lets fucking go"]["seed_playlists"]
    assert any(LFG_ID in e for e in entries)
