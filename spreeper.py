import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import pandas as pd
import json

# Load client_id and client_secret from config.json
with open('config.json') as f:
    config = json.load(f)
    client_id = config['client_id']
    client_secret = config['client_secret']

# Authenticate with Spotify
client_credentials_manager = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
sp = spotipy.Spotify(client_credentials_manager=client_credentials_manager)

def get_playlist_id(playlist_url):
    return playlist_url.split("/")[-1].split("?")[0]

def fetch_playlist_tracks(sp, playlist_id):
    tracks = []
    results = sp.playlist_tracks(playlist_id)
    tracks.extend(results['items'])
    while results['next']:
        results = sp.next(results)
        tracks.extend(results['items'])
    return tracks

def main():
    playlist_url = 'https://open.spotify.com/playlist/2aOikiOH69uiC9VHIAODA6?si=d9c7ac2492bf43fa'
    playlist_id = get_playlist_id(playlist_url)
    tracks = fetch_playlist_tracks(sp, playlist_id)

    songs_data = []
    for item in tracks:
        track = item['track']
        if track:
            song_name = track['name']
            artists = ', '.join([artist['name'] for artist in track['artists']])
            songs_data.append({'Song': song_name, 'Artists': artists})

    df = pd.DataFrame(songs_data)
    print(df)

if __name__ == '__main__':
    main()