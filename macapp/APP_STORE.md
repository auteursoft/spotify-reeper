# Submitting Reeper to the Mac App Store

This is the checklist, the answers App Store Connect asks for, the notes for App Review, and the risks to understand before submitting.

## What ships in the App Store edition

The App Store edition (`build_python.sh appstore`) judges songs by Last.fm tags only. It leaves out:

- **Essentia** (the audio-analysis library). It is licensed AGPL-3.0, and Apple's App Store terms add restrictions that the AGPL/GPL family forbids; GPL apps such as VLC have been removed from the App Store over this. Including it would need a commercial license from the Music Technology Group, Universitat Pompeu Fabra (https://essentia.upf.edu).
- **The Essentia models** (CC BY-NC-SA 4.0: non-commercial) and **Deezer previews** (Deezer's API terms: non-commercial use, no audio files). Without Essentia, the app never contacts Deezer.

`package_appstore.sh` refuses to package an app that contains the audio edition.

## Risks to understand first

1. **Spotify access for other people.** A Spotify app in Development mode serves only its owner and up to a few people added under User Management; Spotify grants wider ("extended") access only to established organizations' products. Reeper therefore asks each person to create their own free Spotify developer app and paste its Client ID, which the setup guide walks through. This is unusual, and App Review may question it (see the notes below). People also need Spotify Premium to own a developer app.
2. **Spotify's Developer Policy** bars using Spotify content to train machine-learning models. Reeper's position, stated in the review notes below and designed into the app:
   - a person's playlists are **their own curation**: which songs they chose to group together. The mood model learns from those choices.
   - the model's features come from **Last.fm tags**, not from Spotify audio, Spotify audio features, or Spotify's catalog metadata.
   - each model is trained **on the person's own Mac, for that person only**. Nothing is uploaded, pooled across users, or used to train any shared model.
   - Spotify still decides how its policy applies and can revoke a Client ID. Because each person uses their own Client ID, any revocation affects only that person.
3. **Spotify branding.** Don't use "Spotify" in the app's name, subtitle or icon. Saying the app works *with* Spotify in the description is normal; the app states it isn't affiliated.
4. **Last.fm API terms** allow non-commercial use. Keep the app free.

## Checklist

- [ ] Apple Developer Program membership; certificates **Apple Distribution** and **Mac Installer Distribution** in your keychain.
- [ ] Explicit App ID `com.auteursoft.reeper` (no extra capabilities needed) and a **Mac App Store** provisioning profile for it.
- [ ] App Store Connect app record: name (e.g. "Reeper: Playlist Moods"), primary category **Music**, price **Free**.
- [ ] `scripts/build_python.sh appstore`, archive in Xcode, `scripts/package_appstore.sh`, upload with Transporter.
- [ ] Screenshots (at least one, 2880×1800 recommended): Playlists with a filter applied, a mood's Seeds tab, What It Learned, and Build Playlist with a preview.
- [ ] Privacy policy URL (required). A short page is enough; text below.
- [ ] Review notes and a demo account (below).

## App Store Connect answers

- **Data collection (App Privacy):** "Data Not Collected." Reeper has no servers and no analytics. Sign-in tokens and keys stay in the user's Keychain; moods and the analysis cache stay in the app's container. Requests go directly from the Mac to Spotify and Last.fm.
- **Tracking:** No.
- **Encryption (export compliance):** uses only Apple's and standard HTTPS encryption. `ITSAppUsesNonExemptEncryption` is `false` in Info.plist.
- **Content rights:** the app displays song and playlist names from the user's own Spotify account and tags from Last.fm; it doesn't stream or store audio.
- **Age rating:** no objectionable content of its own (4+), though song and playlist names come from the user's library.

## Privacy policy text

> Reeper keeps your data on your Mac. It signs in to your own Spotify developer app with your permission, reads and changes only the playlists you ask it to, and looks up public song tags on Last.fm. Your Spotify sign-in and Last.fm key are stored in your Mac's Keychain; your moods and song analysis are stored in Reeper's folder on your Mac. Reeper has no servers, collects no data, and shares nothing with anyone. Spotify's and Last.fm's own privacy policies apply to your use of those services.

## Notes for App Review

> Reeper helps people organize their own Spotify playlists (filter, merge, rename, remove) and build new playlists for a mood from songs they choose as examples.
>
> **Why the setup asks for a Spotify Client ID:** Spotify only allows an individual developer's app to serve a handful of users, so Reeper can't offer a shared Spotify login. Each person creates a free app on Spotify's developer site (about two minutes; the in-app guide walks through it) and pastes its Client ID. Sign-in uses OAuth with PKCE in ASWebAuthenticationSession; no secret is stored.
>
> **To review without creating accounts:** we've added your review account to our Spotify app. In Settings → Accounts, enter Client ID `<your Client ID>`, choose Sign In with Spotify, and sign in with Spotify account `<review account email>` / `<password>`. Then enter Last.fm API key `<a key you created for review>`. The account has sample playlists and two moods ready.
>
> **About the mood feature:** the model learns from the songs the user picked as examples (their own curation) using public Last.fm tags, runs entirely on the Mac, and is used only for that user. Reeper doesn't use Spotify audio or audio features, and uploads nothing.
>
> **The bundled Python runtime** runs only Reeper's own bundled code, inside the app's sandbox (it inherits the sandbox); it doesn't download or execute other code.

Before submitting, create a dedicated Spotify account for App Review, add it under **User Management** in your Spotify app, import a couple of moods (File → Import Moods…), and fill in the placeholders above.

## If App Review rejects the bring-your-own-Client-ID setup

The fallback is the direct-download edition (`package_direct.sh`): a notarized `.dmg` you share with people you add under User Management. It also includes audio analysis.
