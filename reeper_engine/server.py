"""Line-delimited JSON protocol between the Mac app and the engine.

App -> engine, one object per line:
    {"id": "7", "method": "playlists.list", "params": {...}}
    {"id": "8", "method": "cancel", "params": {"id": "7"}}

Engine -> app, one object per line:
    {"type": "ready", "protocol": 1}
    {"type": "progress", "id": "7", "message": "...", "done": 3, "total": 10}
    {"type": "result", "id": "7", "result": {...}}
    {"type": "error", "id": "7", "code": "cancelled", "message": "..."}
    {"type": "event", "event": "tokens", "data": {...}}         (not tied to a request)

Requests run in two lanes so a long job never blocks the interface: "heavy" requests
(analysis, training, building, model downloads) run one at a time in one lane, and
everything else (browsing playlists, moods, search) runs in the other.

Error codes: user_error (show the message), invalid_pattern, auth (sign in again),
not_configured, cancelled, internal.
"""

import json
import queue
import sys
import threading
import traceback

from . import PROTOCOL_VERSION

HEAVY_PREFIXES = ("library.analyze", "mood.", "seeds.", "models.download")


class Cancelled(Exception):
    pass


class EngineError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Job:
    def __init__(self, server, rid, method, params):
        self.server, self.id, self.method, self.params = server, rid, method, params or {}
        self.lane = server.lane_for(method) if hasattr(server, "lane_for") else "light"
        self.cancelled = threading.Event()

    def progress(self, message, done=None, total=None):
        self.check_cancelled()
        msg = {"type": "progress", "id": self.id, "message": message}
        if done is not None:
            msg.update(done=done, total=total)
        self.server.send(msg)

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise Cancelled()


class Server:
    def __init__(self, handlers, inp=None, out=None):
        """handlers: {method: fn(job) -> result}. inp/out default to stdin/stdout."""
        self.handlers = handlers
        self.inp = inp or sys.stdin
        self.out = out or sys.stdout
        self._write_lock = threading.Lock()
        self.lanes = {"heavy": queue.Queue(), "light": queue.Queue()}
        self.jobs = {}  # id -> Job, queued or running
        self._jobs_lock = threading.Lock()

    def send(self, msg):
        line = json.dumps(msg, ensure_ascii=False, default=str)
        with self._write_lock:
            self.out.write(line + "\n")
            self.out.flush()

    def event(self, name, data):
        self.send({"type": "event", "event": name, "data": data})

    def lane_for(self, method):
        return "heavy" if method.startswith(HEAVY_PREFIXES) else "light"

    def _worker(self, lane):
        q = self.lanes[lane]
        while True:
            job = q.get()
            if job is None:
                return
            try:
                if job.cancelled.is_set():
                    raise Cancelled()
                handler = self.handlers.get(job.method)
                if handler is None:
                    raise EngineError("internal", f"Unknown method {job.method}")
                result = handler(job)
                self.send({"type": "result", "id": job.id, "result": result})
            except Cancelled:
                self.send({"type": "error", "id": job.id, "code": "cancelled", "message": "Cancelled."})
            except EngineError as err:
                self.send({"type": "error", "id": job.id, "code": err.code, "message": str(err)})
            except SystemExit as err:  # the CLI code reports user-facing problems this way
                self.send({"type": "error", "id": job.id, "code": "user_error", "message": str(err)})
            except Exception as err:
                traceback.print_exc(file=sys.stderr)
                code, message = classify(err)
                self.send({"type": "error", "id": job.id, "code": code, "message": message})
            finally:
                with self._jobs_lock:
                    self.jobs.pop(job.id, None)

    def handle_line(self, line):
        line = line.strip()
        if not line:
            return True
        try:
            msg = json.loads(line)
            rid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
        except (ValueError, KeyError, AttributeError) as err:
            self.send({"type": "error", "id": None, "code": "internal", "message": f"Bad request: {err}"})
            return True
        if method == "shutdown":
            return False
        if method == "cancel":
            with self._jobs_lock:
                target = self.jobs.get(params.get("id"))
            if target:
                target.cancelled.set()
            return True
        job = Job(self, rid, method, params)
        with self._jobs_lock:
            self.jobs[rid] = job
        self.lanes[job.lane].put(job)
        return True

    def run(self):
        workers = [threading.Thread(target=self._worker, args=(lane,), daemon=True)
                   for lane in self.lanes]
        for w in workers:
            w.start()
        self.send({"type": "ready", "protocol": PROTOCOL_VERSION})
        for line in self.inp:
            if not self.handle_line(line):
                break
        # Shutting down: quick requests already sent still finish; long jobs (analysis,
        # training, building) are cancelled. Their progress is cached, so they resume later.
        with self._jobs_lock:
            for job in self.jobs.values():
                if job.lane == "heavy":
                    job.cancelled.set()
        for q in self.lanes.values():
            q.put(None)
        for w in workers:
            w.join(timeout=10)
        return 0


def classify(err):
    """Map an unexpected exception to (code, message) the app can act on."""
    from cleaner import RateLimited
    if isinstance(err, RateLimited):
        return "user_error", str(err)
    status = getattr(err, "http_status", None)
    if status == 401:
        return "auth", "Spotify sign-in expired. Please sign in again."
    if status == 403:
        return "user_error", ("Spotify refused this request. Development-mode apps can only "
                              "read playlists you own or collaborate on.")
    if status == 429:
        return "user_error", "Spotify is rate-limiting requests. Please wait a minute and try again."
    msg = getattr(err, "msg", None) or str(err) or err.__class__.__name__
    return "internal", msg


def main(argv=None):
    from .handlers import build_handlers
    from .session import Session

    # The app reads stdout as protocol; keep stray prints from libraries off it.
    protocol_out = sys.stdout
    sys.stdout = sys.stderr
    server = Server({}, out=protocol_out)
    session = Session(server)
    server.handlers = build_handlers(session)
    return server.run()
