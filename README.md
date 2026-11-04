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
7.	Set Up Redirect URI: Although not used in this script, **Spotify requires a redirect URI to be set.**
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

Lists the playlists in your library and lets you delete, move, or rename the ones whose names match a pattern. It logs in as you, so the first run opens a browser to authorize the app; the token is cached in `.cache` (already git-ignored).

**Every write command is a dry run unless you add `--apply`.** With `--apply` it asks you to type `yes` first, unless you also add `--yes`.

```
python cleaner.py list                                   # everything in your library
python cleaner.py list "workout" --mine                  # only playlists you own
python cleaner.py delete "^Untitled" --regex             # preview what would be removed
python cleaner.py delete "^Untitled" --regex --apply     # actually remove them
python cleaner.py move "workout" --to "Gym" --apply      # merge into one playlist, remove the sources
python cleaner.py move "2019" --to "2019 archive" --keep-source --apply   # copy, keep the sources
python cleaner.py rename "^Old - " --regex --replace "Archive: " --apply
```

Pattern options (all commands):

| Option | Meaning |
|---|---|
| *(default)* | case-insensitive substring: `work` matches "Workout 2019" |
| `--glob` | shell wildcards matched against the whole name: `*2019*`, `Daily Mix ?` |
| `--regex` | Python regular expression, matched anywhere in the name |
| `--case-sensitive` | match case exactly |
| `--exclude PATTERN` | skip names that match this too |
| `--mine` | only playlists you own, not ones you follow |

What each command does on Spotify:

- **delete** removes the playlist from your library. Spotify has no hard delete; a playlist you own can be restored for 90 days at <https://www.spotify.com/account/recover-playlists/>. Removing one you only follow just unfollows it.
- **move** copies the tracks from every matching playlist into the `--to` playlist (created as a private playlist if you don't own one with that name), skipping tracks already there (`--no-dedupe` turns that off), then removes each source. A source is only removed after its tracks were added. Local files can't be added through the API and are skipped. The Spotify Web API has no access to playlist *folders*, so "move into a folder" isn't possible; this merges into a playlist instead.
- **rename** substitutes text in the names of playlists you own. With `--regex`, `\1` etc. refer to capture groups.

Since Spotify's February 2026 API changes, track listings are only available for playlists you own or collaborate on, so `move` will report an error for a followed playlist you don't own and leave it alone. Your Spotify developer app's owner also needs Premium.

### Running the tests

```
pip install -r requirements-dev.txt
python -m pytest tests
```

The tests use a fake Spotify client, so they need no credentials and change nothing in your account.

© 2026, Professor Doctor Sean P. Goggins, the Dork Union, and ["Mr. Hand's Songs of Spicolli"](https://open.spotify.com/playlist/2aOikiOH69uiC9VHIAODA6?si=d9c7ac2492bf43fa), which is also listed in the [`playlist.md`](playlist.md) file. Playlist generated using [*spotify-reeper*](https://github.com/auteursoft/spotify-reeper.git)