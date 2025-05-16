import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import pandas as pd
import json
import argparse
import sys

def load_config():
    try:
        with open('config.json') as f:
            config = json.load(f)
            return config['client_id'], config['client_secret']
    except Exception as e:
        print(f"Error loading config.json: {e}")
        sys.exit(1)

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
    parser = argparse.ArgumentParser(description="Extract song titles and artists from a Spotify playlist.")
    parser.add_argument("playlist_url", help="The Spotify playlist URL")
    args = parser.parse_args()

    client_id, client_secret = load_config()

    # Authenticate with Spotify
    client_credentials_manager = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
    sp = spotipy.Spotify(client_credentials_manager=client_credentials_manager)

    playlist_id = get_playlist_id(args.playlist_url)
    tracks = fetch_playlist_tracks(sp, playlist_id)

    songs_data = []
    for item in tracks:
        track = item['track']
        if track:
            song_name = track['name']
            artists = ', '.join([artist['name'] for artist in track['artists']])
            songs_data.append({'Song': song_name, 'Artists': artists})

    df = pd.DataFrame(songs_data)
    # Output as Markdown table
    print(df.to_markdown(index=False))

if __name__ == '__main__':
    main()