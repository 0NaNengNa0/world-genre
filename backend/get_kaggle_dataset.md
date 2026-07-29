# Kaggle dataset: audio features (one-time download, not a scheduled pull)

Dataset: **"Spotify Tracks Dataset"** by maharshipandya
https://www.kaggle.com/datasets/maharshipandya/-spotify-tracks-dataset

Contains ~114k tracks with `track_id`, `artists`, `track_genre`, and audio
features (`danceability`, `energy`, `valence`, `tempo`, `acousticness`, etc.)
— this fills the gap left by Spotify's own `/audio-features` endpoint being
deprecated for new apps since Nov 2024.

## Option A — download manually (simplest)
1. Go to the dataset URL above, click "Download".
2. Unzip, you'll get a `dataset.csv`.
3. Move it into `raw/kaggle/spotify_tracks.csv`.

## Option B — Kaggle CLI (better if you want to script the refresh)
```bash
pip install kaggle
# Get an API token: Kaggle account settings -> "Create New Token" -> kaggle.json
mkdir -p ~/.kaggle && mv ~/Downloads/kaggle.json ~/.kaggle/
chmod 600 ~/.kaggle/kaggle.json

kaggle datasets download -d maharshipandya/-spotify-tracks-dataset -p raw/kaggle --unzip
```

Treat this as a reference table, not something you re-pull on a schedule —
refresh it every few months at most, not every pipeline run.
