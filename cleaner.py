"""Playlist cleaner: list, delete, move, or rename your Spotify playlists by name pattern.

Every write command is a dry run unless you pass --apply. With --apply you are
asked to confirm unless you also pass --yes.

Examples:
    python cleaner.py list
    python cleaner.py list "daily mix"
    python cleaner.py delete "^Untitled" --regex
    python cleaner.py delete "*2019*" --glob --apply
    python cleaner.py move "workout" --to "Gym (merged)" --apply
    python cleaner.py rename "^old - " --regex --replace "" --apply
"""

import argparse
import fnmatch
import json
import re
import sys

try:
    from tabulate import tabulate
except ImportError:  # tabulate is in requirements.txt; fall back to plain text
    tabulate = None

SCOPES = " ".join([
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-private",
    "playlist-modify-public",
    "user-library-modify",
    "user-library-read",
])
DEFAULT_REDIRECT_URI = "http://127.0.0.1:7777/callback"
ADD_BATCH_SIZE = 100  # Spotify's maximum number of items per add request


# --------------------------------------------------------------------------
# Pattern matching
# --------------------------------------------------------------------------

def compile_pattern(pattern, mode="substring", case_sensitive=False):
    """Turn a user pattern into a compiled regex.

    mode is "substring" (default), "glob" (shell wildcards: * ? [abc]), or "regex".
    """
    flags = 0 if case_sensitive else re.IGNORECASE
    if mode == "substring":
        return re.compile(re.escape(pattern), flags)
    if mode == "glob":
        return re.compile(fnmatch.translate(pattern), flags)
    if mode == "regex":
        return re.compile(pattern, flags)
    raise ValueError(f"Unknown match mode: {mode}")


def name_matches(regex, name, mode):
    """Glob patterns must match the whole name; the other modes match anywhere in it."""
    if mode == "glob":
        return regex.fullmatch(name) is not None
    return regex.search(name) is not None


def filter_playlists(playlists, pattern=None, mode="substring", case_sensitive=False,
                     exclude=None, owner_id=None):
    """Return the playlists whose names match pattern and do not match exclude.

    pattern=None means "no filter". An empty or blank pattern is rejected rather than
    treated as "no filter", so an empty shell variable can never select every playlist.
    If owner_id is given, only playlists owned by that user are kept.
    """
    for label, value in (("pattern", pattern), ("--exclude pattern", exclude)):
        if value is not None and not value.strip():
            raise ValueError(f"The {label} is empty. Pass a non-empty pattern.")
    include_re = compile_pattern(pattern, mode, case_sensitive) if pattern is not None else None
    exclude_re = compile_pattern(exclude, mode, case_sensitive) if exclude is not None else None
    result = []
    for pl in playlists:
        name = pl.get("name") or ""
        if owner_id and playlist_owner_id(pl) != owner_id:
            continue
        if include_re and not name_matches(include_re, name, mode):
            continue
        if exclude_re and name_matches(exclude_re, name, mode):
            continue
        result.append(pl)
    return result


# --------------------------------------------------------------------------
# Field helpers that tolerate Spotify's February 2026 renames
# ("tracks" -> "items" on playlists, "track" -> "item" on playlist entries)
# --------------------------------------------------------------------------

def playlist_owner_id(pl):
    return (pl.get("owner") or {}).get("id")


def playlist_track_total(pl):
    for key in ("items", "tracks"):
        value = pl.get(key)
        if isinstance(value, dict) and "total" in value:
            return value["total"]
    return None


def entry_item(entry):
    return entry.get("item") or entry.get("track")


# --------------------------------------------------------------------------
# Spotify reads
# --------------------------------------------------------------------------

def paginate(sp, first_page):
    page = first_page
    while page:
        for item in page.get("items") or []:
            yield item
        page = sp.next(page) if page.get("next") else None


def fetch_all_playlists(sp):
    """Every playlist in the current user's library (owned and followed)."""
    return [pl for pl in paginate(sp, sp.current_user_playlists(limit=50)) if pl]


def fetch_playlist_uris(sp, playlist_id):
    """Return (uris, skipped): the addable track/episode URIs in a playlist, in order, and
    how many entries were skipped because the API cannot add them to another playlist
    (local files, and entries whose track is no longer available)."""
    uris, skipped = [], 0
    for entry in paginate(sp, sp.playlist_items(playlist_id, limit=50)):
        entry = entry or {}
        item = entry_item(entry)
        uri = (item or {}).get("uri")
        if not uri or uri.startswith("spotify:local:") or entry.get("is_local"):
            skipped += 1
            continue
        uris.append(uri)
    return uris, skipped


# --------------------------------------------------------------------------
# Spotify writes
# --------------------------------------------------------------------------

def remove_playlist(sp, playlist):
    """Remove a playlist from the user's library.

    Spotify has no hard delete: removing a playlist you own is how you delete it, and
    you can restore it for 90 days at https://www.spotify.com/account/recover-playlists/.
    The followers endpoint is tried first; if Spotify rejects it, the newer
    /me/library endpoint is tried.
    """
    import spotipy  # local import so the pure helpers can be tested without spotipy

    try:
        sp.current_user_unfollow_playlist(playlist["id"])
    except spotipy.SpotifyException as err:
        if err.http_status not in (403, 404, 405, 410):
            raise
        sp._delete("me/library", uris=playlist.get("uri") or f"spotify:playlist:{playlist['id']}")


def find_destination(playlists, name, user_id):
    """The user's own playlist called `name` (case-insensitive), or None if there isn't one.
    Never creates anything: the caller creates it only once it knows tracks will be added."""
    owned = [pl for pl in playlists
             if playlist_owner_id(pl) == user_id and (pl.get("name") or "").lower() == name.lower()]
    if len(owned) > 1:
        raise SystemExit(f'You own {len(owned)} playlists named "{name}". '
                         "Rename one so the destination is unambiguous.")
    return owned[0] if owned else None


def create_destination(sp, name):
    return sp.current_user_playlist_create(
        name, public=False, description="Created by spotify-reeper cleaner")


def add_uris(sp, playlist_id, uris):
    for i in range(0, len(uris), ADD_BATCH_SIZE):
        sp.playlist_add_items(playlist_id, uris[i:i + ADD_BATCH_SIZE])


def dedupe(uris, existing=()):
    seen = set(existing)
    out = []
    for uri in uris:
        if uri not in seen:
            seen.add(uri)
            out.append(uri)
    return out


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def print_table(playlists, extra=None):
    if not playlists:
        print("No playlists matched.")
        return
    rows = []
    for i, pl in enumerate(playlists, 1):
        owner = pl.get("owner") or {}
        row = [i, pl.get("name"), owner.get("display_name") or owner.get("id"),
               playlist_track_total(pl), pl.get("id")]
        if extra:
            row.append(extra(pl))
        rows.append(row)
    headers = ["#", "Name", "Owner", "Tracks", "ID"] + (["New name"] if extra else [])
    if tabulate:
        print(tabulate(rows, headers=headers))
    else:
        print("\t".join(headers))
        for row in rows:
            print("\t".join("" if v is None else str(v) for v in row))
    print(f"\n{len(playlists)} playlist(s).")


def confirm(prompt, assume_yes):
    if assume_yes:
        return True
    try:
        return input(f"{prompt} Type 'yes' to continue: ").strip().lower() == "yes"
    except EOFError:
        return False


def cmd_list(sp, args, user_id):
    playlists = select(sp, args, user_id)
    print_table(playlists)
    return 0


def cmd_delete(sp, args, user_id):
    targets = select(sp, args, user_id)
    print_table(targets)
    if not targets:
        return 0
    if not args.apply:
        print("\nDry run: nothing deleted. Re-run with --apply to remove these playlists.")
        return 0
    if not confirm(f"Remove {len(targets)} playlist(s) from your library?", args.yes):
        print("Aborted.")
        return 1
    failures = 0
    for pl in targets:
        try:
            remove_playlist(sp, pl)
            print(f"  removed  {pl['name']}")
        except Exception as err:  # keep going; report at the end
            failures += 1
            print(f"  FAILED   {pl['name']}: {err}", file=sys.stderr)
    print("Owned playlists can be restored for 90 days at "
          "https://www.spotify.com/account/recover-playlists/")
    return 1 if failures else 0


def cmd_move(sp, args, user_id):
    all_playlists = fetch_all_playlists(sp)
    dest = find_destination(all_playlists, args.to, user_id)
    sources = [pl for pl in select(sp, args, user_id, all_playlists)
               if dest is None or pl.get("id") != dest["id"]]
    print_table(sources)
    if not sources:
        return 0
    action = "copied into" if args.keep_source else "moved into"
    if dest:
        print(f'\nTracks will be {action} your existing playlist "{dest["name"]}".')
    else:
        print(f'\nTracks will be {action} a new private playlist "{args.to}" '
              "(created only if there are tracks to add).")
    if not args.keep_source:
        print("Each source is then removed from your library, except sources that are empty or "
              "contain local files or unavailable tracks, which can't be moved and are kept.")
    if not args.apply:
        print("Dry run: nothing changed. Re-run with --apply to do it.")
        return 0
    if not confirm(f"Proceed with {len(sources)} playlist(s)?", args.yes):
        print("Aborted.")
        return 1

    # Read every source before writing anything, so a read failure can't leave a
    # half-built destination behind.
    failures = 0
    plan = []
    for pl in sources:
        try:
            uris, skipped = fetch_playlist_uris(sp, pl["id"])
        except Exception as err:
            failures += 1
            print(f"  FAILED   {pl['name']}: could not read tracks: {err}", file=sys.stderr)
            continue
        plan.append((pl, uris, skipped))

    if not any(uris for _, uris, _ in plan):
        print("No tracks to add, so nothing was changed.")
        return 1 if failures else 0

    if dest is None:
        dest = create_destination(sp, args.to)
        existing = set()
        print(f'  created  "{dest["name"]}"')
    else:
        existing = set(fetch_playlist_uris(sp, dest["id"])[0])

    for pl, uris, skipped in plan:
        try:
            new = dedupe(uris, existing) if args.dedupe else uris
            add_uris(sp, dest["id"], new)
            existing.update(new)
            msg = f"  {len(new):>4} added  from {pl['name']}"
            if len(new) != len(uris):
                msg += f" ({len(uris) - len(new)} duplicates skipped)"
            print(msg)
            if args.keep_source:
                continue
            # Only remove a source when every one of its entries is now in the destination.
            if skipped:
                print(f"        kept {pl['name']}: {skipped} local file(s) or unavailable "
                      "track(s) can't be moved through the API")
            elif not uris:
                print(f"        kept {pl['name']}: it has no tracks")
            else:
                remove_playlist(sp, pl)
                print(f"        removed {pl['name']}")
        except Exception as err:
            # A source is only removed after its tracks were added, so a failure never loses tracks.
            failures += 1
            print(f"  FAILED   {pl['name']}: {err}", file=sys.stderr)
    return 1 if failures else 0


def rename_preview(regex, replacement):
    return lambda pl: regex.sub(replacement, pl.get("name") or "")


def cmd_rename(sp, args, user_id):
    if args.match == "glob":
        raise SystemExit("rename supports substring or --regex patterns, not --glob.")
    regex = compile_pattern(args.pattern, args.match, args.case_sensitive)
    # Only playlists you own can be renamed.
    targets = [pl for pl in select(sp, args, user_id, owned_only=True)
               if regex.sub(args.replace, pl.get("name") or "") != pl.get("name")]
    preview = rename_preview(regex, args.replace)
    print_table(targets, extra=preview)
    if not targets:
        return 0
    if not args.apply:
        print("\nDry run: nothing renamed. Re-run with --apply to rename.")
        return 0
    if not confirm(f"Rename {len(targets)} playlist(s)?", args.yes):
        print("Aborted.")
        return 1
    failures = 0
    for pl in targets:
        new_name = preview(pl)
        if not new_name.strip():
            failures += 1
            print(f"  SKIPPED  {pl['name']}: new name would be empty", file=sys.stderr)
            continue
        try:
            sp.playlist_change_details(pl["id"], name=new_name)
            print(f"  renamed  {pl['name']}  ->  {new_name}")
        except Exception as err:
            failures += 1
            print(f"  FAILED   {pl['name']}: {err}", file=sys.stderr)
    return 1 if failures else 0


def select(sp, args, user_id, playlists=None, owned_only=False):
    playlists = playlists if playlists is not None else fetch_all_playlists(sp)
    return filter_playlists(
        playlists,
        pattern=getattr(args, "pattern", None),
        mode=args.match,
        case_sensitive=args.case_sensitive,
        exclude=args.exclude,
        owner_id=user_id if (owned_only or args.mine) else None,
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    mode = common.add_mutually_exclusive_group()
    mode.add_argument("--regex", dest="match", action="store_const", const="regex",
                      help="treat PATTERN as a regular expression")
    mode.add_argument("--glob", dest="match", action="store_const", const="glob",
                      help="treat PATTERN as a shell wildcard matched against the whole name")
    common.set_defaults(match="substring")
    common.add_argument("--case-sensitive", action="store_true",
                        help="match case exactly (default: ignore case)")
    common.add_argument("--exclude", metavar="PATTERN",
                        help="skip playlists whose names match this pattern (same mode)")
    common.add_argument("--mine", action="store_true",
                        help="only playlists you own (skip ones you merely follow)")
    common.add_argument("--config", default="config.json", help="path to config.json")

    writes = argparse.ArgumentParser(add_help=False)
    writes.add_argument("--apply", action="store_true",
                        help="actually make changes (default is a dry run)")
    writes.add_argument("-y", "--yes", action="store_true",
                        help="skip the confirmation prompt when using --apply")

    parser = argparse.ArgumentParser(
        description="List, delete, move, or rename Spotify playlists by name pattern.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", parents=[common], help="list playlists, optionally filtered")
    p.add_argument("pattern", nargs="?", help="name pattern (omit to list everything)")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("delete", parents=[common, writes],
                       help="remove matching playlists from your library")
    p.add_argument("pattern")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("move", parents=[common, writes],
                       help="move matching playlists' tracks into one destination playlist")
    p.add_argument("pattern")
    p.add_argument("--to", required=True, metavar="NAME",
                   help="destination playlist name (created if you don't own one by that name)")
    p.add_argument("--keep-source", action="store_true",
                   help="copy tracks but keep the source playlists")
    p.add_argument("--no-dedupe", dest="dedupe", action="store_false",
                   help="add tracks even if the destination already has them")
    p.set_defaults(func=cmd_move)

    p = sub.add_parser("rename", parents=[common, writes],
                       help="rename matching playlists you own by substitution")
    p.add_argument("pattern")
    p.add_argument("--replace", required=True, metavar="TEXT",
                   help=r"replacement text (with --regex, \1 etc. refer to groups)")
    p.set_defaults(func=cmd_rename)
    return parser


def validate_patterns(args):
    """Reject empty or invalid patterns before logging in or touching anything."""
    try:
        filter_playlists([], getattr(args, "pattern", None), args.match,
                         args.case_sensitive, args.exclude)
    except (ValueError, re.error) as err:
        raise SystemExit(f"Error: {err}")


def load_config(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as err:
        raise SystemExit(f"Error loading {path}: {err}")


def make_client(config):
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth

    redirect_uri = config.get("redirect_uri", DEFAULT_REDIRECT_URI)
    auth = SpotifyOAuth(
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        redirect_uri=redirect_uri,
        scope=SCOPES,
        open_browser=True,
    )
    if not auth.validate_token(auth.cache_handler.get_cached_token()):
        print(login_hint(redirect_uri), file=sys.stderr)
    return spotipy.Spotify(auth_manager=auth)


def login_hint(redirect_uri):
    return (
        "Opening Spotify in your browser so you can approve access to your playlists.\n"
        "This is needed once; the token is then saved in .cache.\n"
        'If Spotify says "redirect_uri: Not matching configuration", add exactly\n'
        f"    {redirect_uri}\n"
        "under Redirect URIs in your app's settings at https://developer.spotify.com/dashboard\n"
        "(then click Save), or set \"redirect_uri\" in config.json to one already registered.\n"
    )


def main(argv=None):
    args = build_parser().parse_args(argv)
    validate_patterns(args)
    sp = make_client(load_config(args.config))
    user_id = sp.current_user()["id"]
    return args.func(sp, args, user_id)


if __name__ == "__main__":
    sys.exit(main())
