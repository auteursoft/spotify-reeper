# Spotify Reeper

## Setup
1. Make sure you have Python 3 loaded on your computer. This code was implemented and tested using Python 3.13.12 on an Apple Silicon Macbook Pro (circa 2023). Full Hardware ID: 
```
Model Name:	MacBook Pro
Model Identifier:	Mac14,6
Model Number:	MNXA3JA/A
Chip:	Apple M2 Max
Total Number of Cores:	12 (8 performance and 4 efficiency)
Memory:	64 GB
System Firmware Version:	11881.101.1
OS Loader Version:	11881.101.1
Serial Number (system):	H460JG96WY
Hardware UUID:	AF186476-E8CF-5BF8-A2C2-6CC968E02DAE
Provisioning UDID:	00006021-000230DA3A43C01E
Activation Lock Status:	Enabled
```

2. Clone this repository. Fork it first if you want to make contributions. 
3. Create a python virtual environment: `python3 -m venv .venv`
4. Activate the Python virtual environment: `source .venv/bin/activate`
5. Add the necessary external libraries to your Python virtual environment: `pip install -r requirements.txt`
6.	Spotify Developer Account: You’ll need to register an application to obtain your client_id and client_secret. ￼
 - Visit the Spotify Developer Dashboard.
 - Log in with your Spotify account.
 - Click on “Create an App” and follow the prompts.
 - Once created, you’ll find your Client ID and Client Secret in the app’s settings. ￼ ￼
7.	Set Up Redirect URI: **Spotify requires a redirect URI to be set**, and the playlist cleaner uses it to log you in. It must match character for character.
 - In your app settings, set the redirect URI to `http://127.0.0.1:7777/callback`. (Spotify no longer accepts `localhost` redirect URIs; use the loopback IP.) The playlist cleaner below uses this URI to log you in.
8. Using the `sample.config.json` file as a template, put your Client ID and Client Secret in the designated location. Then, rename the file to `config.json`. This file is part of the `.gitignore` for your repository, so you won't accidentally push it up to the repo. 

## Using 
1.	Make sure your config.json file is in the same directory.
2.	Run from the terminal like this:
```
python spreeper.py "https://open.spotify.com/playlist/2aOikiOH69uiC9VHIAODA6?si=d9c7ac2492bf43fa"
```

![alt text](image.png)

## Playlist cleaner (`cleaner.py`)

Lists the playlists in your library and lets you delete, move, or rename the ones whose names match a pattern. It logs in as you, so the first run opens a browser to authorize the app; the token is cached in `.cache` (already git-ignored). Your app key alone isn't enough: changing your playlists requires your approval as a Spotify user.

If Spotify shows **"redirect_uri: Not matching configuration"**, the redirect URI in your app's settings on the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) doesn't exactly match the one the cleaner sends (`http://127.0.0.1:7777/callback` unless you set `redirect_uri` in `config.json`). Add it under **Settings → Edit → Redirect URIs** and click **Save**. Spotify no longer accepts `localhost`. In Development Mode the app owner needs Premium, and any other account you log in with must be added under **User Management**.

**Every write command is a dry run unless you add `--apply`.** With `--apply` it asks you to type `yes` first, unless you also add `--yes`.

```
python cleaner.py list                                   # everything in your library
python cleaner.py list "workout" --mine                  # only playlists you own
python cleaner.py list --private --mine                  # your private playlists
python cleaner.py delete --private --mine --apply        # remove all of your private playlists
python cleaner.py delete "zb-" --public --apply          # remove public playlists whose names contain "zb-"
python cleaner.py delete "^Untitled" --regex             # preview what would be removed
python cleaner.py delete "^Untitled" --regex --apply     # actually remove them
python cleaner.py move "workout" --to "Gym" --apply      # merge into one playlist, remove the sources
python cleaner.py move "2019" --to "2019 archive" --keep-source --apply   # copy, keep the sources
python cleaner.py rename "^Old - " --regex --replace "Archive: " --apply
```

Pattern options (all commands). An empty pattern is rejected, so an unset shell variable can't select everything:

| Option | Meaning |
|---|---|
| *(default)* | case-insensitive substring: `work` matches "Workout 2019" |
| `--glob` | shell wildcards matched against the whole name: `*2019*`, `Daily Mix ?` |
| `--regex` | Python regular expression, matched anywhere in the name |
| `--case-sensitive` | match case exactly |
| `--exclude PATTERN` | skip names that match this too |
| `--mine` | only playlists you own, not ones you follow |
| `--public` / `--private` | only public, or only private, playlists. Collaborative playlists count as private |

The table shows each playlist's visibility as reported by Spotify: `public`, `private`, `collaborative`, or `unknown` if Spotify doesn't say (those match neither `--public` nor `--private`). `delete` needs a name pattern, `--public`, or `--private`, so it can never select your whole library by accident. Check the dry run against what the Spotify app shows before adding `--apply`.

What each command does on Spotify:

- **delete** removes the playlist from your library. Spotify has no hard delete; a playlist you own can be restored for 90 days at <https://www.spotify.com/account/recover-playlists/>. Removing one you only follow just unfollows it.
- **move** copies the tracks from every matching playlist into the `--to` playlist, skipping tracks already there (`--no-dedupe` turns that off), then removes each source. If you don't own a playlist with that name, a private one is created, but only after you confirm and only if there are tracks to add. A source is removed only once all of its tracks are in the destination: a source that is empty, or that contains local files or tracks no longer on Spotify (which the API can't add), is kept so you don't lose them. The Spotify Web API has no access to playlist *folders*, so "move into a folder" isn't possible; this merges into a playlist instead.
- **rename** substitutes text in the names of playlists you own. With `--regex`, `\1` etc. refer to capture groups; without it, the replacement is used as literal text. An invalid replacement is reported before anything is changed.

Since Spotify's February 2026 API changes, track listings are only available for playlists you own or collaborate on, so `move` will report an error for a followed playlist you don't own and leave it alone. Your Spotify developer app's owner also needs Premium.

## Mood playlists (`moods.py`)

Builds playlists for a mood, learned from songs you pick. You give a handful of seed songs that nail the mood; it learns what they have in common compared with the rest of your library, then finds tracks that fit, both in your library and outside it.

It judges two things about every track:

- **What it sounds like.** Free audio models from [Essentia](https://essentia.upf.edu/models.html) listen to the track's 30-second preview from Deezer and estimate moods such as aggressive, happy, relaxed, danceable, energetic, romantic or sexy, plus an overall fingerprint of the sound. This is how it tells songs that *actually* rock from songs that are only called rock.
- **What people say about it.** Crowd tags from Last.fm, such as "power pop", "jangle pop" or "sultry".

Your seed songs are the core of each mood. Songs from outside your library are found through Last.fm (tracks similar to your seeds, top tracks by similar artists, and top tracks for the tags the model cares about most), and they are scored by the same model as everything else.

### Setup (once)

1. Python 3.13 on macOS 15 or later (Apple Silicon or Intel), or Linux.
2. Install the extra libraries (a few hundred MB, mostly TensorFlow):
   ```
   pip install -r requirements-mood.txt
   ```
3. Get a free Last.fm API key at https://www.last.fm/api/account/create (only the API key, not the shared secret) and add it to `config.json` as `"lastfm_api_key"`.
4. Download the audio models (a few tens of MB, into `models/`, which is git-ignored):
   ```
   python moods.py setup
   ```

### Defining moods

Moods live in `moods.json`. Each mood has seed songs (artist and title as they appear on Spotify, or a Spotify track link) and optionally whole playlists whose songs all count as seeds. A seed playlist can be a Spotify playlist link or the exact name of a playlist in your library. Spotify only lets apps read playlists you own or collaborate on, so to train on someone else's playlist, copy its songs into one of your own first.

```json
{
    "lets fucking go": {
        "seeds": [
            {"artist": "The Knack", "title": "My Sharona"},
            "https://open.spotify.com/track/..."
        ],
        "seed_playlists": ["https://open.spotify.com/playlist/...", "zb- gym"]
    }
}
```

Use at least 5 seeds; 10 to 30 that really share the feel work best. Mood names are matched ignoring case.

### Using it

```
python moods.py seeds "lets fucking go"     # check each seed was found, tagged and analyzed
python moods.py analyze                     # analyze your whole library (first time: long)
python moods.py train "lets fucking go"     # what did it learn, and how well?
python moods.py build "lets fucking go"     # preview a 40-track playlist
python moods.py build "lets fucking go" --size 30 --horizon 0.5 --apply   # create it
```

- **analyze** reads your playlists (the ones you own or collaborate on) and Liked Songs, then fetches tags and analyzes previews for every track. The first run takes roughly a second per track, so allow an hour for a few thousand tracks. Everything is cached in `reeper-cache.db` (git-ignored); you can stop it with Ctrl-C and re-run to continue. `build` and `train` analyze anything missing themselves, but running `analyze` first is easier to watch.
- **train** shows the features that most raise and lower a track's score, then checks the model. It leaves each seed out in turn, retrains, and reports where that seed ranks in your library. Seeds that rank high mean the model learned the mood rather than memorizing your list.
- **build** prints the playlist with each song's fit, whether it's new to you, how it was found, and why it fits. Fit is how much better a song fits the mood than the rest of your library: 99.5 means better than 99.5% of it. Each song appears once, even if your library has several versions of it (album and compilation copies, remasters, live recordings); the original is preferred. Nothing is created without `--apply`; with `--apply`, it creates a private playlist named "<mood> · mood mix <date>" (or `--name`).

`build` options:

| Option | Meaning |
|---|---|
| `--size N` | number of tracks (default 40) |
| `--horizon X` | share from outside your library, 0 to 1 (default 0.3). 0 = only your library, 1 = only new music |
| `--max-per-artist N` | default 3 |
| `--min-fit X` | leave out songs that fit the mood worse than this share of your library, 0 to 1 (default 0.5: the better-fitting half) |
| `--depth N` | how many outside candidates to analyze (default 300; more = wider search, slower first run) |

Options for every command: `--no-audio` (Last.fm tags only; no models needed), `--no-liked` (leave Liked Songs out), `--limit N` (only the first N library tracks, for a quick trial), `--moods`, `--config`, `--models`, `--cache` (file locations).

Notes:

- Tracks with no Deezer preview are scored on their tags, and tracks with no tags on their audio. Tracks with neither are never picked.
- The Essentia models are licensed CC BY-NC-SA 4.0: fine for personal use, not for commercial use.
- Spotify stopped offering its own audio features and previews to new apps in 2024, which is why this uses Deezer previews and Essentia instead.

## Running the tests

```
pip install -r requirements-dev.txt
python -m pytest tests
```

The tests use fake Spotify, Last.fm and Deezer clients and fake audio models, so they need no credentials or network access and change nothing in your account.

© 2026, Professor Doctor Sean P. Goggins, the Dork Union, and ["Mr. Hand's Songs of Spicolli"](https://open.spotify.com/playlist/2aOikiOH69uiC9VHIAODA6?si=d9c7ac2492bf43fa), which is also listed in the [`playlist.md`](playlist.md) file. Playlist generated using [*spotify-reeper*](https://github.com/auteursoft/spotify-reeper.git)