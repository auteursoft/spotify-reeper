"""Build Spotify playlists by mood. See README.md, "Mood playlists".

    python moods.py setup                      # once: download the audio models
    python moods.py seeds "lets fucking go"    # check the seed songs are found
    python moods.py analyze                    # analyze your library (resumable)
    python moods.py train "lets fucking go"    # what did it learn?
    python moods.py build "lets fucking go"    # preview a playlist; add --apply to create it
"""

import sys

from mood.cli import main

if __name__ == "__main__":
    sys.exit(main())
