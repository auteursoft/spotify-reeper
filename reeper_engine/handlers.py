"""The engine's methods. Each takes a Job (job.params, job.progress) and returns JSON data."""

import json
import os
import re

import cleaner
from mood import audio as audio_mod
from mood import cli as mood_cli
from mood.model import held_out_check, label

from .server import EngineError

MOOD_NAME_MAX = 100


def build_handlers(session):
    s = session

    def with_job(fn):
        def run(job):
            s.job = job
            try:
                return fn(job, job.params)
            finally:
                s.job = None
        return run

    # ---------------- session ----------------

    def configure(job, p):
        s.configure(p)
        return {"ok": True}

    def account_me(job, p):
        u = s.user
        images = u.get("images") or []
        return {"id": u.get("id"), "name": u.get("display_name") or u.get("id"),
                "image": images[0]["url"] if images else None,
                "product": u.get("product")}

    # ---------------- playlists ----------------

    def playlists_list(job, p):
        pls = s.playlists(refresh=bool(p.get("refresh")))
        f = p.get("filter") or {}
        pattern = f.get("pattern") or None
        exclude = f.get("exclude") or None
        try:
            matched = cleaner.filter_playlists(
                pls, pattern=pattern, mode=f.get("mode", "substring"),
                case_sensitive=bool(f.get("case_sensitive")), exclude=exclude,
                owner_id=s.user_id if f.get("mine") else None,
                visibility=f.get("visibility") or None)
        except (ValueError, re.error) as err:
            raise EngineError("invalid_pattern", str(err))
        return {"playlists": [serialize_playlist(pl, s.user_id) for pl in matched],
                "total": len(pls)}

    def _by_ids(ids):
        wanted = set(ids or [])
        found = [pl for pl in s.playlists() if pl.get("id") in wanted]
        if not found:
            raise EngineError("user_error", "Those playlists are no longer in your library. Refresh and try again.")
        return found

    def playlists_delete(job, p):
        outcomes = []
        targets = _by_ids(p.get("ids"))
        for i, pl in enumerate(targets):
            job.progress(f"Removing {pl['name']}", i, len(targets))
            try:
                cleaner.remove_playlist(s.sp, pl)
                outcomes.append({"id": pl["id"], "name": pl["name"], "ok": True})
            except Exception as err:
                outcomes.append({"id": pl["id"], "name": pl["name"], "ok": False, "error": str(err)})
        s.playlists_changed()
        return {"outcomes": outcomes}

    def playlists_move_preview(job, p):
        name = (p.get("destination") or "").strip()
        if not name:
            raise EngineError("user_error", "Enter a destination playlist name.")
        dest = cleaner.find_destination(s.playlists(), name, s.user_id)
        return {"destination": serialize_playlist(dest, s.user_id) if dest else None,
                "creates_new": dest is None}

    def playlists_move(job, p):
        name = (p.get("destination") or "").strip()
        if not name:
            raise EngineError("user_error", "Enter a destination playlist name.")
        all_pls = s.playlists()
        dest = cleaner.find_destination(all_pls, name, s.user_id)
        sources = [pl for pl in _by_ids(p.get("ids")) if dest is None or pl["id"] != dest["id"]]
        job.progress(f"Merging {len(sources)} playlist(s) into {name}…")
        result = cleaner.execute_move(s.sp, sources, dest, name,
                                      keep_source=bool(p.get("keep_source")),
                                      do_dedupe=p.get("dedupe", True))
        s.playlists_changed()
        dest = result["destination"]
        return {
            "destination": serialize_playlist(dest, s.user_id) if dest else None,
            "created": result["created"],
            "error": result["error"],
            "outcomes": [{"id": o["playlist"]["id"], "name": o["playlist"]["name"],
                          "status": o["status"], "added": o["added"],
                          "duplicates": o["duplicates"], "reason": o["reason"]}
                         for o in result["outcomes"]],
        }

    def playlists_rename_preview(job, p):
        mode = "regex" if p.get("regex") else "substring"
        pattern = p.get("pattern") or ""
        if not pattern.strip():
            raise EngineError("invalid_pattern", "Enter text to find.")
        try:
            regex = cleaner.compile_pattern(pattern, mode, bool(p.get("case_sensitive")))
            preview = cleaner.rename_preview(regex, p.get("replace", ""), mode)
            preview({"name": ""})  # validates the replacement template
        except (ValueError, re.error) as err:
            raise EngineError("invalid_pattern", str(err))
        changes = []
        for pl in _by_ids(p.get("ids")):
            if cleaner.playlist_owner_id(pl) != s.user_id:
                continue  # only your own playlists can be renamed
            new = preview(pl)
            if new != pl.get("name"):
                changes.append({"id": pl["id"], "name": pl["name"], "new_name": new,
                                "valid": bool(new.strip())})
        return {"changes": changes}

    def playlists_rename(job, p):
        outcomes = []
        changes = p.get("changes") or []
        for i, c in enumerate(changes):
            job.progress(f"Renaming {c.get('name', '')}", i, len(changes))
            new = (c.get("new_name") or "").strip()
            if not new:
                outcomes.append({"id": c["id"], "ok": False, "error": "The new name is empty."})
                continue
            try:
                s.sp.playlist_change_details(c["id"], name=new)
                outcomes.append({"id": c["id"], "ok": True, "new_name": new})
            except Exception as err:
                outcomes.append({"id": c["id"], "ok": False, "error": str(err)})
        s.playlists_changed()
        return {"outcomes": outcomes}

    # ---------------- moods (stored in moods.json in the app's data folder) ----------------

    def read_moods():
        try:
            with open(s.moods_path) as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError as err:
            raise EngineError("user_error", f"The moods file is damaged: {err}")
        return data if isinstance(data, dict) else {}

    def write_moods(moods):
        tmp = s.moods_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(moods, f, indent=4, ensure_ascii=False)
        os.replace(tmp, s.moods_path)

    def moods_list(job, p):
        return {"moods": [{"name": k, **normalize_mood(v)} for k, v in read_moods().items()]}

    def moods_save(job, p):
        name = (p.get("name") or "").strip()
        if not name:
            raise EngineError("user_error", "A mood needs a name.")
        if len(name) > MOOD_NAME_MAX:
            raise EngineError("user_error", "That mood name is too long.")
        moods = read_moods()
        previous = p.get("previous_name")
        clash = [k for k in moods if k.lower() == name.lower() and k != previous]
        if clash:
            raise EngineError("user_error", f'There is already a mood called "{clash[0]}".')
        new = {}
        for k, v in moods.items():  # keep order; a rename stays in place
            new[name if k == previous else k] = normalize_mood(p.get("mood")) if k == previous else v
        if previous not in moods:
            new[name] = normalize_mood(p.get("mood"))
        write_moods(new)
        return {"name": name}

    def moods_delete(job, p):
        moods = read_moods()
        moods.pop(p.get("name"), None)
        write_moods(moods)
        return {"ok": True}

    def moods_import(job, p):
        """Merge moods from the JSON text of a moods file (the app reads the file)."""
        try:
            incoming = json.loads(p.get("content") or "")
        except json.JSONDecodeError as err:
            raise EngineError("user_error", f"That isn't a moods file: {err}")
        if not isinstance(incoming, dict):
            raise EngineError("user_error", "That isn't a moods file.")
        moods = read_moods()
        added = []
        for k, v in incoming.items():
            name = k
            n = 2
            while name in moods:
                name = f"{k} {n}"
                n += 1
            moods[name] = normalize_mood(v)
            added.append(name)
        write_moods(moods)
        return {"added": added}

    def moods_export(job, p):
        return {"content": json.dumps(read_moods(), indent=4, ensure_ascii=False)}

    def get_mood(name):
        moods = read_moods()
        for k, v in moods.items():
            if k.lower() == (name or "").lower():
                return k, normalize_mood(v)
        raise EngineError("user_error", f'There is no mood called "{name}".')

    # ---------------- Spotify search ----------------

    def tracks_search(job, p):
        q = (p.get("query") or "").strip()
        if not q:
            return {"tracks": []}
        m = re.search(r"(?:spotify:track:|open\.spotify\.com/track/)([A-Za-z0-9]{22})", q)
        items = [s.sp.track(m.group(1))] if m else \
            (s.sp.search(q=q, type="track", limit=min(int(p.get("limit", 10)), 10))
             .get("tracks") or {}).get("items") or []
        return {"tracks": [serialize_track(t) for t in items if t]}

    def playlists_lookup(job, p):
        """Resolve a playlist link to its name, for adding seed playlists."""
        m = re.search(r"(?:spotify:playlist:|open\.spotify\.com/playlist/)([A-Za-z0-9]{22})",
                      p.get("link") or "")
        if not m:
            raise EngineError("user_error", "That doesn't look like a Spotify playlist link.")
        pl = s.sp.playlist(m.group(1), fields="id,name,owner(id,display_name)")
        return {"id": pl["id"], "name": pl["name"],
                "mine": (pl.get("owner") or {}).get("id") == s.user_id,
                "link": f"https://open.spotify.com/playlist/{pl['id']}"}

    # ---------------- analysis ----------------

    def seeds_check(job, p):
        name, mood = get_mood(p.get("mood"))
        seeds, problems = mood_cli.load_seeds(s, mood)
        profiles = s.profile_all(seeds, "seed songs")
        rows = []
        for t, prof in zip(seeds, profiles):
            tags = sorted(prof["tags"], key=lambda k: -prof["tags"][k])[:5]
            rows.append({"title": t["title"], "artist": t["artist"], "uri": t.get("uri"),
                         "on_spotify": bool(t.get("uri")), "tags": tags,
                         "tag_source": prof["tag_source"], "audio_status": prof["audio_status"]})
        return {"mood": name, "seeds": rows, "problems": problems}

    def library_status(job, p):
        from mood.cache import Cache
        cache = Cache(s.cache_path)
        try:
            return {"audio_analyzed": cache.count("audio"),
                    "tagged": cache.count("lastfm:track_tags"),
                    "models_present": audio_mod.models_present(s.models_dir),
                    "audio_supported": audio_mod.audio_supported()}
        finally:
            cache.db.close()

    def library_analyze(job, p):
        lib = s.library(job, refresh=bool(p.get("refresh")))
        profiles = s.profile_all(lib, "your library")
        statuses = {}
        for prof in profiles:
            statuses[prof["audio_status"]] = statuses.get(prof["audio_status"], 0) + 1
        return {"tracks": len(profiles), "with_audio": sum(1 for x in profiles if x["audio"]),
                "with_tags": sum(1 for x in profiles if x["tags"]), "audio_status": statuses}

    def summary_of(model):
        ups, downs, sound = model.summary()
        return {"more_of": [label(n) for n, _ in ups], "less_of": [label(n) for n, _ in downs],
                "sound_share": round(sound, 3), "seeds_used": model.n_positives,
                "compared_with": model.n_negatives}

    def mood_train(job, p):
        name, mood = get_mood(p.get("mood"))
        lib = s.library(job)
        model, _, seed_profiles, _, negatives = mood_cli.train(s, name, mood, lib)
        job.progress("Checking what the model learned…")
        results = held_out_check(seed_profiles, negatives, use_audio=not s.args.no_audio)
        check = [{"title": prof["title"], "artist": prof["artist"], "percentile": round(pct, 1)}
                 for prof, pct in results]
        median = sorted(pct for _, pct in results)[len(results) // 2] if results else None
        level, text = mood_cli.check_verdict(median) if median is not None else (None, None)
        return {"mood": name, "summary": summary_of(model), "check": check,
                "median_percentile": median, "verdict": level, "verdict_text": text}

    def mood_build(job, p):
        name, mood = get_mood(p.get("mood"))
        lib = s.library(job)
        model, picks = mood_cli.plan_build(
            s, name, mood, size=int(p.get("size", 40)), horizon=float(p.get("horizon", 0.3)),
            max_per_artist=int(p.get("max_per_artist", 3)), min_fit=float(p.get("min_fit", 0.5)),
            depth=int(p.get("depth", 300)), library=lib)
        return {"mood": name, "summary": summary_of(model),
                "default_name": mood_cli.default_playlist_name(name),
                "picks": [{"uri": x.get("uri"), "title": x["title"], "artist": x["artist"],
                           "fit": round(x["fit"], 4), "new": bool(x["new"]), "via": x.get("via"),
                           "why": model.explain(x["profile"])} for x in picks]}

    def playlist_create(job, p):
        name = (p.get("name") or "").strip()
        uris = [u for u in p.get("uris") or [] if u]
        if not name:
            raise EngineError("user_error", "The playlist needs a name.")
        if not uris:
            raise EngineError("user_error", "There are no tracks to add.")
        created = mood_cli.create_playlist(s.sp, name, uris, p.get("mood") or "")
        s.playlists_changed()
        return {"id": created["id"],
                "url": (created.get("external_urls") or {}).get("spotify")
                or f"https://open.spotify.com/playlist/{created['id']}"}

    # ---------------- audio models ----------------

    def models_status(job, p):
        return {"present": audio_mod.models_present(s.models_dir), "dir": s.models_dir,
                "audio_supported": audio_mod.audio_supported()}

    def models_download(job, p):
        from mood.net import Http
        if not audio_mod.audio_supported():
            raise EngineError("user_error", "This edition of Reeper judges songs by tags only, "
                              "so it has no audio models to download.")
        target = os.path.join(s.data_dir, "models")
        files = audio_mod.model_files()
        count = {"i": 0}

        def log(msg):
            count["i"] += 1
            job.progress(msg.strip(), min(count["i"], len(files)), len(files))
        failures = audio_mod.setup_models(target, Http(min_interval=0), log=log)
        if failures:
            raise EngineError("user_error", "Couldn't download: " + ", ".join(f[0] for f in failures))
        return {"present": True, "dir": target}

    table = {
        "session.configure": configure,
        "account.me": account_me,
        "playlists.list": playlists_list,
        "playlists.delete": playlists_delete,
        "playlists.move_preview": playlists_move_preview,
        "playlists.move": playlists_move,
        "playlists.rename_preview": playlists_rename_preview,
        "playlists.rename": playlists_rename,
        "playlists.lookup": playlists_lookup,
        "moods.list": moods_list,
        "moods.save": moods_save,
        "moods.delete": moods_delete,
        "moods.import": moods_import,
        "moods.export": moods_export,
        "tracks.search": tracks_search,
        "seeds.check": seeds_check,
        "library.status": library_status,
        "library.analyze": library_analyze,
        "mood.train": mood_train,
        "mood.build": mood_build,
        "playlist.create": playlist_create,
        "models.status": models_status,
        "models.download": models_download,
    }
    return {name: with_job(fn) for name, fn in table.items()}


def serialize_playlist(pl, user_id):
    owner = pl.get("owner") or {}
    images = pl.get("images") or []
    return {
        "id": pl.get("id"), "name": pl.get("name") or "",
        "owner_id": owner.get("id"), "owner_name": owner.get("display_name") or owner.get("id"),
        "mine": owner.get("id") == user_id,
        "visibility": cleaner.playlist_visibility(pl),
        "tracks": cleaner.playlist_track_total(pl),
        "url": (pl.get("external_urls") or {}).get("spotify")
        or (f"https://open.spotify.com/playlist/{pl['id']}" if pl.get("id") else None),
        "image": images[0].get("url") if images else None,
    }


def serialize_track(t):
    album = t.get("album") or {}
    images = album.get("images") or []
    return {"uri": t.get("uri"), "title": t.get("name") or "",
            "artist": ", ".join(a.get("name", "") for a in t.get("artists") or []),
            "album": album.get("name"), "image": images[-1]["url"] if images else None}


def normalize_mood(m):
    m = m if isinstance(m, dict) else {}
    seeds = []
    for x in m.get("seeds") or []:
        if isinstance(x, str) and x.strip():
            seeds.append(x.strip())
        elif isinstance(x, dict) and (x.get("uri") or (x.get("artist") and x.get("title"))):
            seeds.append({k: x[k] for k in ("artist", "title", "uri") if x.get(k)})
    playlists = [x.strip() for x in m.get("seed_playlists") or [] if isinstance(x, str) and x.strip()]
    return {"seeds": seeds, "seed_playlists": playlists}
