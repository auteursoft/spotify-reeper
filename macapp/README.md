# Reeper for Mac

A native SwiftUI app for everything the command-line tools do: browse, filter, remove, merge and rename playlists, and build mood playlists from seed songs. The Python engine (`reeper_engine`, sharing code with `cleaner.py` and `mood/`) runs inside the app; nothing needs to be installed separately.

```
macapp/
  project.yml              XcodeGen project definition (generates Reeper.xcodeproj)
  Reeper/Core/             engine client, models, Spotify PKCE (Foundation only, tested on Linux too)
  Reeper/App/              SwiftUI app, Keychain, sign-in
  Reeper/Resources/        Info.plist, entitlements, privacy manifest, icon
  scripts/build_python.sh  builds the bundled Python runtime + engine
  scripts/embed_engine.sh  Xcode build phase: copies it into the app and signs it
  scripts/package_*.sh     App Store (.pkg) and direct-download (.dmg) packaging
  Tests/run_core_tests.sh  core tests: PKCE, engine protocol
```

## Two editions

| | App Store edition | Direct-download edition |
|---|---|---|
| How songs are judged | Last.fm tags | Last.fm tags **and** sound (Essentia models on Deezer previews) |
| Build | `build_python.sh appstore` | `build_python.sh direct` |
| Ship with | `package_appstore.sh` → Transporter | `package_direct.sh` → notarized `.dmg` |
| Why | Essentia is AGPL-3.0, which conflicts with App Store terms; Deezer's API and the models are non-commercial only | Distribute under AGPL terms: this repository is the source |

Same app and same code; the engine tells the app whether audio analysis is available, and the interface adapts.

## Requirements

- An Apple Silicon Mac with macOS 15 or later, and Xcode 16 or later.
- [XcodeGen](https://github.com/yonaskolb/XcodeGen): `brew install xcodegen`
- An Apple Developer Program membership to sign, notarize or submit.

## Build and run

```
cd macapp
scripts/build_python.sh appstore     # or: direct. Takes a few minutes; re-run when Python code changes.
xcodegen                             # creates Reeper.xcodeproj from project.yml
open Reeper.xcodeproj
```

In Xcode, set your team under **Signing & Capabilities** (or put your Team ID in `DEVELOPMENT_TEAM` in `project.yml` and re-run `xcodegen`), then **Run** (⌘R).

### Faster iteration without rebuilding Python

Debug builds aren't sandboxed, so they can run the engine straight from this checkout:

1. Create the repository's virtual environment as in the main README, and `pip install -r requirements-mood.txt`.
2. In Xcode: **Product → Scheme → Edit Scheme → Run → Arguments**, tick `REEPER_PYTHON` and `REEPER_SOURCE`. They point at `../.venv/bin/python` and the repository root.
3. Delete `macapp/build/python` (or never build it) so the Debug build uses them.

Python changes then take effect on the next launch.

### Tests

```
python -m pytest tests                    # engine, cleaner, mood code
macapp/Tests/run_core_tests.sh python3    # Swift core: PKCE, engine protocol
```

## First launch

The setup guide walks through it:

1. Create a free Spotify developer app at https://developer.spotify.com/dashboard, add the redirect URI `reeper-auth://callback`, and tick **Web API**.
2. Paste its **Client ID** (no secret: Reeper signs in with PKCE).
3. **Sign In with Spotify** in the secure sign-in sheet.
4. Paste a free Last.fm API key.

Each person uses their own Spotify app because Spotify only lets an individual's app serve its owner and a few people added under **User Management**.

Data stays on the Mac: tokens and the Last.fm key in the Keychain; moods, the analysis cache and downloaded models in `~/Library/Containers/com.auteursoft.reeper/Data/Library/Application Support/Reeper/` (unsandboxed Debug builds use `~/Library/Application Support/Reeper/`). **Settings → Analysis → Show in Finder** opens it. To bring in moods from the command-line tool, use **File → Import Moods…** with your `moods.json`.

## Using the app

- **Playlists** (⌘1): type in the search field to filter; choose Contains, Wildcard or Regex at the left of the toolbar; the filter button adds owner, visibility, exclusion and case options. Select playlists, then **Merge**, **Rename** or **Remove** (⌫) from the toolbar or the right-click menu. Every change shows what will happen first. Double-click opens a playlist in Spotify.
- **Moods** (sidebar, ⌘N for a new one):
  - **Seeds**: add songs by search, paste Spotify links (⌘V), or drag songs and playlists in from the Spotify app. **Check Seeds** confirms each one is found and analyzed.
  - **What It Learned**: what sets the mood apart, and a check of how well it learned it.
  - **Build Playlist**: choose the length and the mix of familiar and new music, **Preview** (⌘↩), untick anything you don't want, name it, and **Create in Spotify**.
- **Library Analysis** (⌘2): analyze your whole library ahead of time. Long jobs show progress in the toolbar with a Stop button; stopping keeps everything done so far.

## Packaging

### Mac App Store

1. `scripts/build_python.sh appstore`, then in Xcode **Product → Archive**.
2. In the Organizer, right-click the archive → **Show in Finder** → right-click → **Show Package Contents** → `Products/Applications/Reeper.app`.
3. In App Store Connect, create the app record (bundle ID `com.auteursoft.reeper`) and a **Mac App Store** provisioning profile for it in the developer portal.
4. Sign and package:
   ```
   APP_IDENTITY="Apple Distribution: Your Name (TEAMID)" \
   INSTALLER_IDENTITY="3rd Party Mac Developer Installer: Your Name (TEAMID)" \
   scripts/package_appstore.sh path/to/Reeper.app path/to/Reeper.provisionprofile
   ```
5. Upload `build/dist-appstore/Reeper.pkg` with Apple's **Transporter** app, then follow [APP_STORE.md](APP_STORE.md).

Why not Xcode's own export: it doesn't re-sign the Python runtime inside `Contents/Resources`, which App Store validation then rejects. The script signs everything inside out.

### Direct download (notarized)

```
scripts/build_python.sh direct
# Archive in Xcode as above, then:
DEVELOPER_ID="Developer ID Application: Your Name (TEAMID)" NOTARY_PROFILE=reeper-notary \
scripts/package_direct.sh path/to/Reeper.app
```

Create the notary profile once with `xcrun notarytool store-credentials reeper-notary --apple-id you@example.com --team-id TEAMID`.

## Troubleshooting

- **"Reeper's engine isn't running"**: the app shows the engine's last error lines. In Debug, check `REEPER_PYTHON`/`REEPER_SOURCE`, or run `scripts/build_python.sh` again. The Xcode console shows the engine's log in Debug builds.
- **Spotify says "redirect_uri: Not matching configuration"**: the dashboard's Redirect URIs must include `reeper-auth://callback` exactly, and you must click **Save**.
- **Spotify says "INVALID_CLIENT"**: the Client ID in Settings doesn't match your Spotify app.
- **A 403 error reading a playlist**: since February 2026 Spotify only lets apps read playlists you own or collaborate on.
- **First build fails in the Embed Python engine phase**: run `scripts/build_python.sh` first.
