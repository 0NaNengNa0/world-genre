"""
Shared list of countries used across all extraction scripts.

Each source names/identifies countries differently, so we keep one lookup
table instead of three inconsistent lists:
  - lastfm_name : full country name, as Last.fm's geo.* methods expect
  - kworb_code  : lowercase ISO 3166-1 alpha-2 code, as used in kworb.net URLs
  - spotify_q   : search string Spotify's official "Top 50" playlists use

NOTE: Spotify does not officially document its playlist naming, and Last.fm's
accepted country name spelling can be picky (must match their internal list).
Before running at scale, manually test 2-3 countries end-to-end and adjust
the strings below if a lookup comes back empty.
"""

COUNTRIES = [
    {"lastfm_name": "United States", "kworb_code": "us", "spotify_q": "Top 50 - USA"},
    {"lastfm_name": "United Kingdom", "kworb_code": "gb", "spotify_q": "Top 50 - United Kingdom"},
    {"lastfm_name": "Germany", "kworb_code": "de", "spotify_q": "Top 50 - Germany"},
    {"lastfm_name": "France", "kworb_code": "fr", "spotify_q": "Top 50 - France"},
    {"lastfm_name": "Brazil", "kworb_code": "br", "spotify_q": "Top 50 - Brazil"},
    {"lastfm_name": "Japan", "kworb_code": "jp", "spotify_q": "Top 50 - Japan"},
    {"lastfm_name": "South Korea", "kworb_code": "kr", "spotify_q": "Top 50 - South Korea"},
    {"lastfm_name": "Mexico", "kworb_code": "mx", "spotify_q": "Top 50 - Mexico"},
    {"lastfm_name": "India", "kworb_code": "in", "spotify_q": "Top 50 - India"},
    {"lastfm_name": "Canada", "kworb_code": "ca", "spotify_q": "Top 50 - Canada"},
    {"lastfm_name": "Australia", "kworb_code": "au", "spotify_q": "Top 50 - Australia"},
    {"lastfm_name": "Sweden", "kworb_code": "se", "spotify_q": "Top 50 - Sweden"},
    {"lastfm_name": "Italy", "kworb_code": "it", "spotify_q": "Top 50 - Italy"},
    {"lastfm_name": "Spain", "kworb_code": "es", "spotify_q": "Top 50 - Spain"},
    {"lastfm_name": "Netherlands", "kworb_code": "nl", "spotify_q": "Top 50 - Netherlands"},
    {"lastfm_name": "Philippines", "kworb_code": "ph", "spotify_q": "Top 50 - Philippines"},
    {"lastfm_name": "Indonesia", "kworb_code": "id", "spotify_q": "Top 50 - Indonesia"},
    {"lastfm_name": "Thailand", "kworb_code": "th", "spotify_q": "Top 50 - Thailand"},
    {"lastfm_name": "Poland", "kworb_code": "pl", "spotify_q": "Top 50 - Poland"},
    {"lastfm_name": "Turkey", "kworb_code": "tr", "spotify_q": "Top 50 - Turkey"},
]
