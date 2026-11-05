import json
import os
import queue
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reeper_engine import handlers as H  # noqa: E402
from reeper_engine.auth import AuthExpired, TokenManager  # noqa: E402
from reeper_engine.server import Cancelled, EngineError, Server  # noqa: E402
from reeper_engine.session import Session  # noqa: E402

import test_mood as TM  # noqa: E402  (fake Spotify / Last.fm / audio)


# ======================= protocol harness =======================

class Lines:
    """Blocking stdin stand-in: feed() lines, close() to end input."""

    def __init__(self):
        self.q = queue.Queue()

    def feed(self, obj):
        self.q.put(json.dumps(obj) + "\n")

    def close(self):
        self.q.put(None)

    def __iter__(self):
        while True:
            line = self.q.get()
            if line is None:
                return
            yield line


class Out:
    def __init__(self):
        self.msgs, self.cv = [], threading.Condition()

    def write(self, s):
        with self.cv:
            for line in s.splitlines():
                if line.strip():
                    self.msgs.append(json.loads(line))
            self.cv.notify_all()

    def flush(self):
        pass

    def wait(self, pred, timeout=10):
        deadline = time.time() + timeout
        with self.cv:
            while True:
                hit = [m for m in self.msgs if pred(m)]
                if hit:
                    return hit[0]
                left = deadline - time.time()
                if left <= 0:
                    raise AssertionError(f"timed out; got {self.msgs}")
                self.cv.wait(left)

    def final(self, rid, timeout=10):
        return self.wait(lambda m: m.get("id") == rid and m["type"] in ("result", "error"), timeout)


@pytest.fixture
def harness():
    started = []

    def make(handlers):
        inp, out = Lines(), Out()
        server = Server(handlers, inp=inp, out=out)
        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        started.append((inp, t))
        out.wait(lambda m: m["type"] == "ready")
        return server, inp, out
    yield make
    for inp, t in started:
        inp.close()
        t.join(timeout=5)


def test_ready_result_and_unknown_method(harness):
    _, inp, out = harness({"echo": lambda job: {"you_said": job.params["x"]}})
    assert out.msgs[0] == {"type": "ready", "protocol": 1}
    inp.feed({"id": "1", "method": "echo", "params": {"x": 5}})
    assert out.final("1") == {"type": "result", "id": "1", "result": {"you_said": 5}}
    inp.feed({"id": "2", "method": "nope"})
    assert out.final("2")["code"] == "internal"


def test_error_codes(harness):
    def user(job):
        raise SystemExit("No seeds yet.")

    def engine(job):
        raise EngineError("invalid_pattern", "bad regex")

    def spotify401(job):
        import spotipy
        raise spotipy.SpotifyException(401, -1, "expired")
    _, inp, out = harness({"u": user, "e": engine, "a": spotify401})
    for rid, m in (("1", "u"), ("2", "e"), ("3", "a")):
        inp.feed({"id": rid, "method": m})
    assert (out.final("1")["code"], out.final("1")["message"]) == ("user_error", "No seeds yet.")
    assert out.final("2")["code"] == "invalid_pattern"
    assert out.final("3")["code"] == "auth"


def test_bad_json_line_is_reported_not_fatal(harness):
    server, inp, out = harness({"ok": lambda job: 1})
    inp.q.put("this is not json\n")
    inp.feed({"id": "9", "method": "ok"})
    assert out.final("9")["result"] == 1
    assert any(m.get("code") == "internal" and m.get("id") is None for m in out.msgs)


def test_progress_and_cancel_of_running_job(harness):
    gate = threading.Event()

    def slow(job):
        for i in range(1000):
            job.progress("working", i, 1000)
            gate.wait(0.01)
        return "finished"
    _, inp, out = harness({"mood.build": slow})
    inp.feed({"id": "1", "method": "mood.build"})
    out.wait(lambda m: m.get("id") == "1" and m["type"] == "progress")
    inp.feed({"id": "c", "method": "cancel", "params": {"id": "1"}})
    assert out.final("1")["code"] == "cancelled"


def test_light_requests_are_not_blocked_by_heavy_ones(harness):
    release = threading.Event()

    def heavy(job):
        release.wait(5)
        return "heavy done"
    _, inp, out = harness({"library.analyze": heavy, "playlists.list": lambda job: "light done"})
    inp.feed({"id": "h", "method": "library.analyze"})
    inp.feed({"id": "l", "method": "playlists.list"})
    assert out.final("l")["result"] == "light done"
    assert not any(m.get("id") == "h" and m["type"] == "result" for m in out.msgs)
    release.set()
    assert out.final("h")["result"] == "heavy done"


def test_queued_job_cancelled_before_it_starts(harness):
    release = threading.Event()
    ran = []

    def first(job):
        release.wait(5)
        return 1

    def second(job):
        ran.append(1)
        return 2
    _, inp, out = harness({"mood.train": first, "mood.build": second})
    inp.feed({"id": "1", "method": "mood.train"})
    inp.feed({"id": "2", "method": "mood.build"})
    inp.feed({"id": "c", "method": "cancel", "params": {"id": "2"}})
    time.sleep(0.1)
    release.set()
    assert out.final("2")["code"] == "cancelled" and ran == []


def test_events_go_out_untied_to_requests():
    out = Out()
    Server({}, inp=[], out=out).event("tokens", {"access_token": "a"})
    assert out.msgs == [{"type": "event", "event": "tokens", "data": {"access_token": "a"}}]


def test_main_keeps_library_prints_off_the_protocol(monkeypatch):
    """Anything a library prints must not corrupt the JSON on stdout."""
    import io
    import reeper_engine.server as srv
    fake_out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", fake_out)
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"id": "1", "method": "moods.list"}\n'))
    captured = {}

    def fake_run(self):
        print("noise from a library")  # goes to stderr now
        captured["out"] = self.out
        return 0
    monkeypatch.setattr(srv.Server, "run", fake_run)
    assert srv.main() == 0
    assert captured["out"] is fake_out and fake_out.getvalue() == ""


# ======================= tokens =======================

class Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


def test_token_manager_refreshes_near_expiry_and_reports_new_tokens():
    now = [1000.0]
    posted, reported = [], []

    def post(url, data, timeout):
        posted.append(data)
        return Resp(200, {"access_token": "new", "expires_in": 3600})
    tm = TokenManager("cid", "old", "refresh1", expires_at=1030, on_refresh=reported.append,
                      post=post, clock=lambda: now[0])
    assert tm.get_access_token() == "new"  # 30 s left -> refreshed
    assert posted[0] == {"grant_type": "refresh_token", "refresh_token": "refresh1", "client_id": "cid"}
    assert reported[0]["refresh_token"] == "refresh1"  # not rotated: kept
    assert reported[0]["expires_at"] == 4600
    assert tm.get_access_token() == "new" and len(posted) == 1  # still fresh


def test_token_manager_rotation_and_failure():
    tm = TokenManager("cid", "a", "r1", 0, post=lambda *a, **k: Resp(200, {
        "access_token": "b", "refresh_token": "r2", "expires_in": 60}), clock=lambda: 0)
    tm.get_access_token()
    assert tm.refresh_token == "r2"
    bad = TokenManager("cid", "a", "r1", 0, post=lambda *a, **k: Resp(400, {}), clock=lambda: 0)
    with pytest.raises(AuthExpired):
        bad.get_access_token()
    with pytest.raises(AuthExpired):
        TokenManager("cid", "a", None, 0, clock=lambda: 0).get_access_token()


# ======================= handlers =======================

class Job:
    def __init__(self, params=None):
        self.params, self.updates = params or {}, []

    def progress(self, message, done=None, total=None):
        self.updates.append((message, done, total))

    def check_cancelled(self):
        pass


class AppSpotify(TM.E2ESpotify):
    """E2E fake plus the playlist-management calls."""

    def __init__(self):
        super().__init__()
        self.pls = [
            {"id": "pl1", "name": "Everything", "owner": {"id": "me", "display_name": "Me"},
             "public": True, "items": {"total": 54}, "uri": "spotify:playlist:pl1"},
            {"id": "pl2", "name": "zb- old stuff", "owner": {"id": "me"}, "public": False,
             "items": {"total": 2}, "uri": "spotify:playlist:pl2"},
            {"id": "pl3", "name": "Their Mix", "owner": {"id": "other"}, "public": True,
             "items": {"total": 9}, "uri": "spotify:playlist:pl3"},
        ]
        self.removed, self.renamed = [], []

    def current_user(self):
        return {"id": "me", "display_name": "Sean", "images": [], "product": "premium"}

    def current_user_playlists(self, limit=50):
        return {"items": list(self.pls), "next": None}

    def playlist_items(self, pid, limit=50):
        if pid == "pl2":
            return {"items": [{"item": self.catalog[0]}], "next": None}
        return super().playlist_items(pid, limit)

    def current_user_unfollow_playlist(self, pid):
        self.removed.append(pid)
        self.pls = [p for p in self.pls if p["id"] != pid]

    def playlist_change_details(self, pid, name=None):
        self.renamed.append((pid, name))

    def playlist(self, pid, fields=None):
        return {"id": pid, "name": "Linked", "owner": {"id": "me"}}


@pytest.fixture
def engine(tmp_path, monkeypatch):
    sp = AppSpotify()
    sent = []

    class FakeServer:
        def event(self, name, data):
            sent.append((name, data))

    session = Session(FakeServer())
    session.configure({"data_dir": str(tmp_path), "lastfm_api_key": "k", "use_audio": True,
                       "include_liked": True})
    monkeypatch.setattr(Session, "sp", property(lambda self: sp))

    def fake_profiler(self):
        st = self._thread_state()
        if st.profiler is None:
            class Dz(TM.E2EDeezer):
                def find_id(self, artist, title, isrc=None):
                    return title

                def preview_url(self, deezer_id):
                    return TM.VIBE.get(deezer_id, "soft")

                def download(self, url, path):
                    open(path, "w").write(url)
            st.profiler = TM.Profiler(self.cache, TM.E2ELastFM(), Dz(), TM.FakeAnalyzer(),
                                      log=lambda m: None)
        return st.profiler
    monkeypatch.setattr(Session, "profiler", property(fake_profiler))
    handlers = H.build_handlers(session)

    def call(method, **params):
        job = Job(params)
        return handlers[method](job), job
    return call, sp, session


def test_account_and_playlist_listing_with_filters(engine):
    call, sp, _ = engine
    assert call("account.me")[0]["name"] == "Sean"
    res, _ = call("playlists.list")
    assert res["total"] == 3 and [p["name"] for p in res["playlists"]][0] == "Everything"
    first = res["playlists"][0]
    assert first["mine"] and first["visibility"] == "public" and first["tracks"] == 54
    assert first["url"].endswith("/pl1")
    res, _ = call("playlists.list", filter={"pattern": "zb-", "mine": True, "visibility": "private"})
    assert [p["id"] for p in res["playlists"]] == ["pl2"]
    res, _ = call("playlists.list", filter={"pattern": "*mix", "mode": "glob"})
    assert [p["id"] for p in res["playlists"]] == ["pl3"]


def test_invalid_pattern_is_its_own_error(engine):
    call, _, _ = engine
    with pytest.raises(EngineError) as e:
        call("playlists.list", filter={"pattern": "(", "mode": "regex"})
    assert e.value.code == "invalid_pattern"


def test_delete_and_cache_refresh(engine):
    call, sp, _ = engine
    res, job = call("playlists.delete", ids=["pl2"])
    assert res["outcomes"] == [{"id": "pl2", "name": "zb- old stuff", "ok": True}]
    assert sp.removed == ["pl2"] and job.updates
    assert "pl2" not in [p["id"] for p in call("playlists.list")[0]["playlists"]]
    with pytest.raises(EngineError):
        call("playlists.delete", ids=["gone"])


def test_move_preview_and_move(engine):
    call, sp, _ = engine
    prev, _ = call("playlists.move_preview", destination="Gym")
    assert prev == {"destination": None, "creates_new": True}
    res, _ = call("playlists.move", ids=["pl2"], destination="Gym")
    assert res["created"] is True and res["destination"]["id"] == "newpl" and res["error"] is None
    assert res["outcomes"][0]["status"] == "moved" and res["outcomes"][0]["added"] == 1
    assert sp.removed == ["pl2"]


def test_rename_preview_only_own_playlists_and_apply(engine):
    call, sp, _ = engine
    res, _ = call("playlists.rename_preview", ids=["pl2", "pl3"], pattern="^zb- ", regex=True,
                  replace="Archive: ")
    assert res["changes"] == [{"id": "pl2", "name": "zb- old stuff",
                               "new_name": "Archive: old stuff", "valid": True}]
    with pytest.raises(EngineError) as e:
        call("playlists.rename_preview", ids=["pl2"], pattern="zb", regex=True, replace=r"\1")
    assert e.value.code == "invalid_pattern"
    out, _ = call("playlists.rename", changes=res["changes"])
    assert out["outcomes"][0]["ok"] and sp.renamed == [("pl2", "Archive: old stuff")]


def test_moods_crud_rename_import_export(engine, tmp_path):
    call, _, _ = engine
    assert call("moods.list")[0] == {"moods": []}
    call("moods.save", name="Lets go", mood={"seeds": [{"artist": "R.E.M.", "title": "Superman"},
                                                      {"bad": 1}, "  "]})
    call("moods.save", name="Romantic", mood={})
    with pytest.raises(EngineError):
        call("moods.save", name="romantic", mood={})  # clashes, ignoring case
    call("moods.save", name="LFG", previous_name="Lets go",
         mood={"seeds": [{"artist": "R.E.M.", "title": "Superman"}]})
    moods = call("moods.list")[0]["moods"]
    assert [m["name"] for m in moods] == ["LFG", "Romantic"]  # renamed in place
    assert moods[0]["seeds"] == [{"artist": "R.E.M.", "title": "Superman"}]
    exported = call("moods.export")[0]["content"]
    added = call("moods.import", content=exported)[0]["added"]
    assert added == ["LFG 2", "Romantic 2"]
    call("moods.delete", name="LFG 2")
    assert "LFG 2" not in [m["name"] for m in call("moods.list")[0]["moods"]]
    with pytest.raises(EngineError):
        call("moods.import", content="not json")
    assert json.load(open(tmp_path / "moods.json"))  # written atomically, valid


def test_search_by_text_and_link(engine):
    call, sp, _ = engine
    res, _ = call("tracks.search", query="Sharona")
    assert res["tracks"][0]["title"] == "My Sharona" and res["tracks"][0]["uri"]
    sharona = next(t for t in sp.catalog if t["name"] == "My Sharona")  # alphanumeric id
    tid = sharona["uri"].split(":")[-1]
    res, _ = call("tracks.search", query=f"https://open.spotify.com/track/{tid}?si=abc")
    assert res["tracks"][0]["uri"] == sharona["uri"]
    assert call("tracks.search", query="  ")[0] == {"tracks": []}


def seed_lfg(call):
    call("moods.save", name="Lets go",
         mood={"seeds": [{"artist": a, "title": t} for a, t in TM.LOUD]})


def test_seeds_check_train_and_build(engine):
    call, sp, _ = engine
    seed_lfg(call)
    seeds, job = call("seeds.check", mood="lets go")
    assert len(seeds["seeds"]) == 6 and all(x["audio_status"] == "ok" for x in seeds["seeds"])
    assert any(u[1] is not None for u in job.updates)  # numeric progress for a progress bar

    trained, _ = call("mood.train", mood="Lets go")
    assert "tagged power pop" in trained["summary"]["more_of"]
    assert len(trained["check"]) == 6 and trained["verdict"] in ("good", "fair", "weak")

    built, _ = call("mood.build", mood="Lets go", size=8, horizon=0.25)
    assert len(built["picks"]) == 8 and built["default_name"].startswith("Lets go · mood mix")
    assert all("Lullaby" not in p["title"] for p in built["picks"])
    assert any(p["new"] for p in built["picks"]) and all(p["why"] for p in built["picks"])

    created, _ = call("playlist.create", name="LFG", uris=[p["uri"] for p in built["picks"]],
                      mood="Lets go")
    assert created["url"].endswith("newpl") and sp.created == [("LFG", False)]


def test_library_analyze_and_status(engine):
    call, _, _ = engine
    res, job = call("library.analyze")
    assert res["tracks"] == 54 and res["with_audio"] == 54
    status, _ = call("library.status")
    assert status["audio_analyzed"] >= 54 and status["models_present"] is False


def test_unknown_mood_and_missing_setup(engine, monkeypatch):
    call, _, session = engine
    with pytest.raises(EngineError):
        call("mood.train", mood="nope")
    call("moods.save", name="Empty", mood={})
    with pytest.raises(SystemExit, match="no seed songs"):
        call("mood.train", mood="Empty")


def test_profiler_explains_missing_lastfm_key_and_models(tmp_path):
    s = Session(server=None)
    s.configure({"data_dir": str(tmp_path), "use_audio": True})
    with pytest.raises(EngineError, match="Last.fm"):
        s.profiler  # noqa: B018
    s.configure({"lastfm_api_key": "k"})
    with pytest.raises(EngineError, match="audio models"):
        s.profiler  # noqa: B018
    s.configure({"use_audio": False})
    assert s.profiler.analyzer is None


def test_not_signed_in(tmp_path):
    s = Session(server=None)
    s.configure({"data_dir": str(tmp_path)})
    with pytest.raises(EngineError) as e:
        s.sp  # noqa: B018
    assert e.value.code == "auth"


def test_configure_tokens_builds_refreshing_manager(tmp_path):
    sent = []

    class Srv:
        def event(self, name, data):
            sent.append(name)
    s = Session(Srv())
    s.configure({"data_dir": str(tmp_path), "client_id": "cid",
                 "tokens": {"access_token": "a", "refresh_token": "r", "expires_at": 0}})
    assert s.tokens.client_id == "cid" and s.tokens.refresh_token == "r"
    s.tokens._post = lambda *a, **k: Resp(200, {"access_token": "b", "expires_in": 3600})
    assert s.tokens.get_access_token() == "b" and sent == ["tokens"]
    s.configure({"tokens": None})
    assert s.tokens is None


def test_cancelled_is_raised_from_progress():
    from reeper_engine.server import Job as RealJob

    class Srv:
        def send(self, m):
            pass
    j = RealJob(Srv(), "1", "x", {})
    j.cancelled.set()
    with pytest.raises(Cancelled):
        j.progress("x")


def test_shutdown_finishes_quick_requests_and_cancels_long_ones():
    out = Out()
    lines = [json.dumps(m) + "\n" for m in (
        {"id": "h", "method": "library.analyze"},
        {"id": "1", "method": "moods.list"},
        {"id": "2", "method": "moods.list"},
        {"method": "shutdown"})]

    def heavy(job):
        for _ in range(500):
            job.progress("working")
            time.sleep(0.01)
        return "done"
    Server({"moods.list": lambda job: "ok", "library.analyze": heavy}, inp=lines, out=out).run()
    assert out.final("1")["result"] == "ok" and out.final("2")["result"] == "ok"
    assert out.final("h")["code"] == "cancelled"


def test_tags_only_edition_without_essentia(engine, monkeypatch):
    """The App Store edition ships without Essentia: no audio, but everything else works."""
    call, _, session = engine
    from mood import audio as audio_mod
    monkeypatch.setattr(audio_mod, "audio_supported", lambda: False)
    status, _ = call("library.status")
    assert status["audio_supported"] is False
    assert session.args.no_audio is True
    with pytest.raises(EngineError, match="tags only"):
        call("models.download")
    seed_lfg(call)
    built, _ = call("mood.build", mood="Lets go", size=6, horizon=0)
    assert len(built["picks"]) == 6
    assert not any(w.startswith("sounds") for p in built["picks"] for w in p["why"])


def test_audio_supported_detects_essentia():
    from mood import audio as audio_mod
    import importlib.util
    assert audio_mod.audio_supported() == (importlib.util.find_spec("essentia") is not None)


def test_rate_limit_reaches_the_app_as_a_readable_message(harness):
    import cleaner

    def limited(job):
        raise cleaner.RateLimited(80040)
    _, inp, out = harness({"library.analyze": limited})
    inp.feed({"id": "1", "method": "library.analyze"})
    reply = out.final("1")
    assert reply["code"] == "user_error" and "about 22 hours" in reply["message"]


def test_engine_spotify_client_does_not_sleep_on_rate_limits(tmp_path):
    s = Session(server=None)
    s.configure({"data_dir": str(tmp_path), "client_id": "cid",
                 "tokens": {"access_token": "a", "refresh_token": "r", "expires_at": 9e9}})
    retry = s.sp._session.get_adapter("https://api.spotify.com").max_retries
    assert retry.respect_retry_after_header is False
    assert 429 not in retry.status_forcelist
