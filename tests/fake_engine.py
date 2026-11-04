"""The real engine (server + handlers) over fake Spotify, Last.fm, Deezer and audio models.

Used to test the Mac app's Swift engine client against real protocol traffic:
    python tests/fake_engine.py
Adds two test-only methods: "mood.slowtest" (a long job with progress, for cancellation)
and "debug.tokens_event" (emits a tokens event).
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import test_engine as TE  # noqa: E402
import test_mood as TM  # noqa: E402
from reeper_engine import handlers as H  # noqa: E402
from reeper_engine.server import Server  # noqa: E402
from reeper_engine.session import Session  # noqa: E402


def main():
    protocol_out = sys.stdout
    sys.stdout = sys.stderr
    sp = TE.AppSpotify()
    server = Server({}, out=protocol_out)
    session = Session(server)
    Session.sp = property(lambda self: sp)

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
    Session.profiler = property(fake_profiler)

    handlers = H.build_handlers(session)

    def slow(job):
        n = int(job.params.get("steps", 200))
        for i in range(n):
            job.progress(f"step {i}", i, n)
            time.sleep(0.02)
        return {"finished": True}

    def tokens_event(job):
        server.event("tokens", {"access_token": "fresh", "refresh_token": "r2",
                                "expires_at": 1893456000.0})
        return {"ok": True}

    handlers["mood.slowtest"] = slow
    handlers["debug.tokens_event"] = tokens_event
    server.handlers = handlers
    return server.run()


if __name__ == "__main__":
    sys.exit(main())
