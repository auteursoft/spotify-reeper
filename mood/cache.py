"""A small on-disk JSON cache so every track is only looked up and analyzed once."""

import json
import sqlite3
import time

DEFAULT_PATH = "reeper-cache.db"


class Cache:
    def __init__(self, path=DEFAULT_PATH):
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS kv (ns TEXT, key TEXT, value TEXT, updated REAL, "
            "PRIMARY KEY (ns, key))")

    def get(self, ns, key, max_age=None):
        row = self.db.execute("SELECT value, updated FROM kv WHERE ns=? AND key=?",
                              (ns, key)).fetchone()
        if row is None:
            return None
        if max_age is not None and time.time() - row[1] > max_age:
            return None
        return json.loads(row[0])

    def set(self, ns, key, value):
        self.db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?, ?, ?)",
                        (ns, key, json.dumps(value), time.time()))
        self.db.commit()

    def has(self, ns, key):
        return self.db.execute("SELECT 1 FROM kv WHERE ns=? AND key=?", (ns, key)).fetchone() is not None

    def count(self, ns):
        return self.db.execute("SELECT COUNT(*) FROM kv WHERE ns=?", (ns,)).fetchone()[0]


def cached(cache, ns, key, fetch, max_age=None):
    """Return cache[ns][key], computing and storing it with fetch() on a miss."""
    value = cache.get(ns, key, max_age)
    if value is None:
        value = fetch()
        cache.set(ns, key, value)
    return value
