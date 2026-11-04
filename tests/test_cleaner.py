import os
import sys

import pytest
import spotipy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cleaner  # noqa: E402

ME = "me123"


def pl(pid, name, owner=ME, total=0, new_fields=True):
    """A playlist object. new_fields uses the post-Feb-2026 'items' key, else legacy 'tracks'."""
    key = "items" if new_fields else "tracks"
    return {"id": pid, "name": name, "uri": f"spotify:playlist:{pid}",
            "owner": {"id": owner, "display_name": owner}, key: {"total": total}}


class FakeSpotify:
    """In-memory stand-in for spotipy.Spotify that records writes."""

    def __init__(self, playlists, contents=None, page_size=2, unfollow_status=None):
        self.playlists = list(playlists)
        self.contents = {k: list(v) for k, v in (contents or {}).items()}
        self.page_size = page_size
        self.unfollow_status = unfollow_status
        self.removed, self.library_deletes, self.created, self.renamed = [], [], [], []
        self.adds = []
        self.unreadable = set()

    def _page(self, items, offset):
        chunk = items[offset:offset + self.page_size]
        nxt = offset + self.page_size
        return {"items": chunk, "_all": items, "_offset": nxt,
                "next": "more" if nxt < len(items) else None}

    def current_user(self):
        return {"id": ME}

    def current_user_playlists(self, limit=50, offset=0):
        return self._page(self.playlists, offset)

    def next(self, page):
        return self._page(page["_all"], page["_offset"])

    def playlist_items(self, playlist_id, limit=50, offset=0):
        if playlist_id in self.unreadable:
            raise spotipy.SpotifyException(403, -1, "not your playlist")
        # None stands for an entry whose track is no longer available.
        entries = [{"item": {"uri": u} if u else None,
                    "is_local": bool(u) and u.startswith("spotify:local:")}
                   for u in self.contents.get(playlist_id, [])]
        return self._page(entries, offset)

    def current_user_unfollow_playlist(self, playlist_id):
        if self.unfollow_status:
            raise spotipy.SpotifyException(self.unfollow_status, -1, "nope")
        self.removed.append(playlist_id)

    def _delete(self, path, **kw):
        self.library_deletes.append((path, kw))

    def current_user_playlist_create(self, name, public=True, description=""):
        new = pl("new1", name)
        self.created.append(new)
        self.playlists.append(new)
        self.contents["new1"] = []
        return new

    def playlist_add_items(self, playlist_id, items):
        assert len(items) <= 100
        self.adds.append((playlist_id, list(items)))
        self.contents.setdefault(playlist_id, []).extend(items)

    def playlist_change_details(self, playlist_id, name=None):
        self.renamed.append((playlist_id, name))


def run(sp, *argv):
    args = cleaner.build_parser().parse_args(list(argv))
    return args.func(sp, args, ME)


LIBRARY = [
    pl("a", "Workout 2019"),
    pl("b", "workout chill"),
    pl("c", "Daily Mix 1", owner="spotify"),
    pl("d", "Untitled", total=3),
    pl("e", "Old - Jazz", new_fields=False, total=7),
]


# ---------------- pattern matching ----------------

@pytest.mark.parametrize("pattern,mode,case,expected", [
    ("workout", "substring", False, ["a", "b"]),
    ("Workout", "substring", True, ["a"]),
    ("*2019", "glob", False, ["a"]),
    ("work*", "glob", False, ["a", "b"]),
    ("2019", "glob", False, []),           # glob matches the whole name
    (r"^(daily|untitled)", "regex", False, ["c", "d"]),
    ("a.b", "substring", False, []),       # substring escapes regex characters
])
def test_filter_modes(pattern, mode, case, expected):
    got = cleaner.filter_playlists(LIBRARY, pattern, mode, case)
    assert [p["id"] for p in got] == expected


def test_exclude_and_mine():
    got = cleaner.filter_playlists(LIBRARY, "i", exclude="chill")
    assert "b" not in [p["id"] for p in got]
    got = cleaner.filter_playlists(LIBRARY, "daily", owner_id=ME)
    assert got == []


def test_track_total_old_and_new_fields():
    assert cleaner.playlist_track_total(LIBRARY[3]) == 3
    assert cleaner.playlist_track_total(LIBRARY[4]) == 7


def test_entry_item_old_and_new_fields():
    assert cleaner.entry_item({"item": {"uri": "x"}})["uri"] == "x"
    assert cleaner.entry_item({"track": {"uri": "y"}})["uri"] == "y"


# ---------------- list ----------------

def test_list_paginates_everything(capsys):
    sp = FakeSpotify(LIBRARY, page_size=2)
    assert run(sp, "list") == 0
    assert "5 playlist(s)" in capsys.readouterr().out


# ---------------- delete ----------------

def test_delete_dry_run_changes_nothing(capsys):
    sp = FakeSpotify(LIBRARY)
    run(sp, "delete", "workout")
    assert sp.removed == []
    assert "Dry run" in capsys.readouterr().out


def test_delete_apply(capsys):
    sp = FakeSpotify(LIBRARY)
    assert run(sp, "delete", "workout", "--apply", "--yes") == 0
    assert sp.removed == ["a", "b"]


def test_delete_apply_requires_confirmation(monkeypatch):
    sp = FakeSpotify(LIBRARY)
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert run(sp, "delete", "workout", "--apply") == 1
    assert sp.removed == []


def test_delete_falls_back_to_library_endpoint():
    sp = FakeSpotify(LIBRARY, unfollow_status=403)
    run(sp, "delete", "untitled", "--apply", "--yes")
    assert sp.library_deletes == [("me/library", {"uris": "spotify:playlist:d"})]


def test_delete_other_errors_are_reported_not_swallowed(capsys):
    sp = FakeSpotify(LIBRARY, unfollow_status=500)
    assert run(sp, "delete", "untitled", "--apply", "--yes") == 1
    assert sp.library_deletes == []
    assert "FAILED" in capsys.readouterr().err


# ---------------- move ----------------

def test_move_creates_destination_dedupes_and_removes_sources():
    contents = {"a": ["spotify:track:1", "spotify:track:2", "spotify:local:x"],
                "b": ["spotify:track:2", "spotify:track:3"]}
    sp = FakeSpotify(LIBRARY, contents)
    assert run(sp, "move", "workout", "--to", "Gym", "--apply", "--yes") == 0
    assert [c["name"] for c in sp.created] == ["Gym"]
    assert sp.contents["new1"] == ["spotify:track:1", "spotify:track:2", "spotify:track:3"]
    assert sp.removed == ["b"]  # "a" holds a local file, which can't be moved, so it is kept


def test_move_into_existing_playlist_skips_tracks_already_there():
    library = LIBRARY + [pl("g", "Gym")]
    contents = {"a": ["spotify:track:1", "spotify:track:2"], "g": ["spotify:track:1"]}
    sp = FakeSpotify(library, contents)
    run(sp, "move", "workout", "--to", "gym", "--apply", "--yes", "--keep-source")
    assert sp.created == []
    assert sp.contents["g"] == ["spotify:track:1", "spotify:track:2"]
    assert sp.removed == []  # --keep-source


def test_move_never_treats_destination_as_a_source():
    library = LIBRARY + [pl("g", "Workout All")]
    sp = FakeSpotify(library, {"a": ["spotify:track:1"], "g": []})
    run(sp, "move", "workout", "--to", "Workout All", "--apply", "--yes")
    assert "g" not in sp.removed


def test_move_dry_run_creates_nothing():
    sp = FakeSpotify(LIBRARY, {"a": ["spotify:track:1"]})
    run(sp, "move", "workout", "--to", "Gym")
    assert sp.created == [] and sp.adds == [] and sp.removed == []


def test_move_batches_large_playlists():
    big = [f"spotify:track:{i}" for i in range(250)]
    sp = FakeSpotify(LIBRARY, {"a": big}, page_size=50)
    run(sp, "move", "2019", "--to", "Big", "--apply", "--yes")
    assert [len(batch) for _, batch in sp.adds] == [100, 100, 50]


def test_move_keeps_source_if_adding_fails(capsys):
    sp = FakeSpotify(LIBRARY, {"a": ["spotify:track:1"]})

    def boom(*a, **k):
        raise RuntimeError("add failed")
    sp.playlist_add_items = boom
    assert run(sp, "move", "2019", "--to", "Gym", "--apply", "--yes") == 1
    assert sp.removed == []


def test_move_refuses_ambiguous_destination():
    library = LIBRARY + [pl("g1", "Gym"), pl("g2", "gym")]
    sp = FakeSpotify(library)
    with pytest.raises(SystemExit):
        run(sp, "move", "workout", "--to", "Gym")


# ---------------- rename ----------------

def test_rename_regex_only_touches_owned_playlists():
    library = LIBRARY + [pl("z", "Old - Someone else's", owner="other")]
    sp = FakeSpotify(library)
    run(sp, "rename", "^old - ", "--regex", "--replace", "", "--apply", "--yes")
    assert sp.renamed == [("e", "Jazz")]


def test_rename_skips_empty_result(capsys):
    sp = FakeSpotify(LIBRARY)
    assert run(sp, "rename", "untitled", "--replace", "", "--apply", "--yes") == 1
    assert sp.renamed == []


def test_rename_rejects_glob():
    sp = FakeSpotify(LIBRARY)
    with pytest.raises(SystemExit):
        run(sp, "rename", "*", "--glob", "--replace", "x")


# ---------------- review fixes: empty patterns ----------------

@pytest.mark.parametrize("mode", ["substring", "glob", "regex"])
@pytest.mark.parametrize("pattern", ["", "   "])
def test_empty_pattern_is_rejected_not_match_all(mode, pattern):
    with pytest.raises(ValueError):
        cleaner.filter_playlists(LIBRARY, pattern, mode)


def test_empty_exclude_is_rejected():
    with pytest.raises(ValueError):
        cleaner.filter_playlists(LIBRARY, "workout", exclude="")


def test_omitted_pattern_still_lists_everything():
    assert len(cleaner.filter_playlists(LIBRARY, None)) == len(LIBRARY)


@pytest.mark.parametrize("argv", [
    ["delete", "", "--glob", "--apply", "--yes"],
    ["delete", "", "--apply", "--yes"],
    ["move", "", "--to", "X", "--apply", "--yes"],
    ["rename", "", "--replace", "x", "--apply", "--yes"],
    ["delete", "workout", "--exclude", ""],
    ["delete", "(unclosed", "--regex"],
])
def test_cli_rejects_bad_patterns_before_logging_in(argv, monkeypatch):
    def no_login(*a, **k):
        raise AssertionError("should not reach login")
    monkeypatch.setattr(cleaner, "make_client", no_login)
    monkeypatch.setattr(cleaner, "load_config", lambda path: {})
    with pytest.raises(SystemExit) as exc:
        cleaner.main(argv)
    assert "Error" in str(exc.value)


# ---------------- review fixes: keep sources that can't be fully moved ----------------

def test_move_keeps_source_with_only_local_or_unavailable_entries():
    sp = FakeSpotify(LIBRARY, {"a": ["spotify:local:x", None], "b": ["spotify:track:1"]})
    run(sp, "move", "workout", "--to", "Gym", "--apply", "--yes")
    assert sp.contents["new1"] == ["spotify:track:1"]
    assert sp.removed == ["b"]


def test_move_keeps_source_with_some_unmovable_entries():
    sp = FakeSpotify(LIBRARY, {"a": ["spotify:track:1", "spotify:local:x"]})
    run(sp, "move", "2019", "--to", "Gym", "--apply", "--yes")
    assert sp.contents["new1"] == ["spotify:track:1"]  # what could be moved was copied
    assert sp.removed == []                            # but the local file's only copy stays


def test_move_keeps_empty_source():
    sp = FakeSpotify(LIBRARY, {"a": [], "b": ["spotify:track:1"]})
    run(sp, "move", "workout", "--to", "Gym", "--apply", "--yes")
    assert sp.removed == ["b"]


def test_move_removes_source_whose_tracks_were_all_duplicates():
    library = LIBRARY + [pl("g", "Gym")]
    sp = FakeSpotify(library, {"a": ["spotify:track:1"], "g": ["spotify:track:1"]})
    run(sp, "move", "2019", "--to", "Gym", "--apply", "--yes")
    assert sp.removed == ["a"]  # every track is already in the destination


# ---------------- review fixes: create the destination only when needed ----------------

def test_move_declined_creates_nothing(monkeypatch):
    sp = FakeSpotify(LIBRARY, {"a": ["spotify:track:1"]})
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert run(sp, "move", "workout", "--to", "Gym", "--apply") == 1
    assert sp.created == []


def test_move_with_no_matches_creates_nothing():
    sp = FakeSpotify(LIBRARY)
    run(sp, "move", "no such playlist", "--to", "Gym", "--apply", "--yes")
    assert sp.created == []


def test_move_with_nothing_addable_creates_nothing():
    sp = FakeSpotify(LIBRARY, {"a": ["spotify:local:x"], "b": []})
    run(sp, "move", "workout", "--to", "Gym", "--apply", "--yes")
    assert sp.created == [] and sp.removed == []


def test_move_unreadable_sources_create_nothing(capsys):
    sp = FakeSpotify(LIBRARY, {"a": ["spotify:track:1"], "b": ["spotify:track:2"]})
    sp.unreadable = {"a", "b"}
    assert run(sp, "move", "workout", "--to", "Gym", "--apply", "--yes") == 1
    assert sp.created == [] and sp.removed == []
    assert "could not read" in capsys.readouterr().err


def test_move_one_unreadable_source_does_not_stop_the_others():
    sp = FakeSpotify(LIBRARY, {"a": ["spotify:track:1"], "b": ["spotify:track:2"]})
    sp.unreadable = {"a"}
    assert run(sp, "move", "workout", "--to", "Gym", "--apply", "--yes") == 1
    assert sp.contents["new1"] == ["spotify:track:2"]
    assert sp.removed == ["b"]
