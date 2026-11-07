"""Polite HTTP: per-service rate limiting and retries on 429 / 5xx."""

import time

USER_AGENT = "spotify-reeper/1.0 (+https://github.com/auteursoft/spotify-reeper)"


class HttpError(Exception):
    pass


class Http:
    def __init__(self, min_interval=0.25, retries=3, timeout=20, session=None, sleep=time.sleep):
        if session is None:
            import requests
            session = requests.Session()
            session.headers["User-Agent"] = USER_AGENT
        self.session = session
        self.min_interval = min_interval
        self.retries = retries
        self.timeout = timeout
        self.sleep = sleep
        self._last = 0.0

    def _request(self, url, params=None):
        for attempt in range(self.retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                self.sleep(wait)
            self._last = time.monotonic()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except Exception as err:  # network hiccup
                if attempt == self.retries:
                    raise HttpError(f"{url}: {err}") from err
                self.sleep(2 ** attempt)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == self.retries:
                    raise HttpError(f"{url}: HTTP {resp.status_code}")
                retry_after = resp.headers.get("Retry-After")
                self.sleep(float(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt)
                continue
            if resp.status_code >= 400:
                raise HttpError(f"{url}: HTTP {resp.status_code}")
            return resp
        raise HttpError(url)  # unreachable

    def get_json(self, url, params=None):
        return self._request(url, params).json()

    def download(self, url, path):
        with open(path, "wb") as f:
            f.write(self._request(url).content)
        return path
