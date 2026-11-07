"""A track's profile: Last.fm tags plus audio-model output, each fetched once and cached."""

import os
import tempfile

from .net import HttpError
from .text import track_key


class Profiler:
    def __init__(self, cache, lastfm, deezer=None, analyzer=None, log=print):
        self.cache = cache
        self.lastfm = lastfm
        self.deezer = deezer
        self.analyzer = analyzer  # None: tags only (e.g. --no-audio, or models not set up)
        self.log = log

    def profile(self, artist, title, isrc=None):
        tag_info = self.lastfm.track_tags(artist, title)
        audio, status = self.audio(artist, title, isrc)
        return {"key": track_key(artist, title), "artist": artist, "title": title,
                "tags": tag_info["tags"], "tag_source": tag_info["source"],
                "audio": audio, "audio_status": status}

    def audio(self, artist, title, isrc=None):
        """(audio features or None, status). Status is ok / no_match / no_preview / error / off."""
        if self.analyzer is None or self.deezer is None:
            return None, "off"
        deezer_id = self.deezer.find_id(artist, title, isrc)
        if not deezer_id:
            return None, "no_match"
        hit = self.cache.get("audio", str(deezer_id))
        if hit is not None:
            return (hit.get("features"), hit["status"])
        try:
            url = self.deezer.preview_url(deezer_id)
            if not url:
                self.cache.set("audio", str(deezer_id), {"status": "no_preview"})
                return None, "no_preview"
            fd, path = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)
            try:
                self.deezer.download(url, path)
                features = self.analyzer.analyze(path)
            finally:
                os.remove(path)
        except HttpError as err:
            # Network trouble is not cached, so the next run tries again.
            self.log(f"  audio download failed for {artist} - {title}: {err}")
            return None, "error"
        except Exception as err:
            self.cache.set("audio", str(deezer_id), {"status": "error", "error": str(err)})
            self.log(f"  audio analysis failed for {artist} - {title}: {err}")
            return None, "error"
        self.cache.set("audio", str(deezer_id), {"status": "ok", "features": features})
        return features, "ok"
