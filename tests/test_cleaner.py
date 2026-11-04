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
        entries = [{"item": {"uri": u}, "is_local": u.startswith("spotify:local:")}
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
    assert sp.removed == ["a", "b"]


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
