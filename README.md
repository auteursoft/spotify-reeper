# Spotify Reeper

## Configuration Setup
1.	Install Spotipy: Ensure you have Spotipy installed. You can install it using pip:
`pip install spotipy`
2.	Spotify Developer Account: You’ll need to register an application to obtain your client_id and client_secret. ￼
 - Visit the Spotify Developer Dashboard.
 - Log in with your Spotify account.
 - Click on “Create an App” and follow the prompts.
 - Once created, you’ll find your Client ID and Client Secret in the app’s settings. ￼ ￼
3.	Set Up Redirect URI: Although not used in this script, Spotify requires a redirect URI to be set.
 - In your app settings, set the redirect URI to http://localhost:7777/callback or any valid URI. ￼

## Using 
1.	Make sure your config.json file is in the same directory.
2.	Run from the terminal like this:
```
python screeper.py "https://open.spotify.com/playlist/2aOikiOH69uiC9VHIAODA6?si=d9c7ac2492bf43fa"
```
