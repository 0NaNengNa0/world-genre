from pydantic import BaseModel


class DomesticShare(BaseModel):
    """How much of a country's chart streaming is its own artists."""

    # Of the streams whose artist origin is known, the share that is domestic.
    domestic_percentage: float
    # How much of the country's streaming could be attributed at all. The
    # figure above is uninterpretable without it: 40 percent domestic means
    # something very different at 90 percent coverage than at 15, and artist
    # origins fill in gradually because MusicBrainz is rate-limited.
    coverage_percentage: float
    classified_entries: int
    total_entries: int


class CountrySummary(BaseModel):
    """One grid card. Deliberately shallow - the grid loads all 76 countries
    at once, so anything not rendered there is left to CountryDetail."""

    code: str
    name: str
    artist_count: int
    # What this country actually listens to, most-played first.
    top_genres: list[str]
    # The same data ranked by what sets this country apart from the others -
    # near-universal genres like pop/rock are excluded entirely. Can be empty
    # for a country whose taste has nothing unusual in it.
    distinctive_genres: list[str] = []
    top_artists: list[str]
    # Carried on the summary so the world map can colour by it without a
    # request per country. None until artist origins have been resolved.
    domestic_share: DomesticShare | None = None
    cover_image: str | None = None


class CountriesResponse(BaseModel):
    countries: list[CountrySummary]


class GenreScore(BaseModel):
    genre: str
    score: int
    # How much this genre sets the country apart vs. all others - 0 for
    # genres common to everywhere. See cleansing.score_distinctiveness.
    distinctiveness: float
    # Which extractors agreed on this genre: lastfm, musicbrainz, or both.
    sources: list[str] = []


class GenreShare(GenreScore):
    """A GenreScore plus its share of the country's total genre weight."""

    # 0-100, computed against EVERY qualifying genre rather than only the
    # returned ones, so these plus `other_percentage` add up to 100 instead of
    # each slice being inflated by the truncation.
    percentage: float


class GenreBreakdown(BaseModel):
    """One way of slicing a country's genres into shares of 100."""

    genres: list[GenreShare]
    # Everything outside `genres`, collapsed into one slice so the chart is
    # honest about what it isn't showing.
    other_percentage: float
    other_genre_count: int


class ChartTrack(BaseModel):
    """A charting track, straight from the fact table.

    Distinct from the artist rankings elsewhere in this response: those are
    derived from Last.fm listener counts, this is measured streaming from the
    chart itself.
    """

    position: int
    track: str | None = None
    artist: str
    # Nullable because kworb leaves the figure blank on some entries, and a
    # missing measure isn't zero plays.
    daily_streams: int | None = None
    days_on_chart: int | None = None


class HiddenGem(BaseModel):
    """An artist this country streams heavily that barely charts elsewhere."""

    artist: str
    streams: int
    best_position: int | None = None
    # How many countries chart this artist at all, out of the total with
    # chart data. Shown because it's what makes the claim checkable - "1 of
    # 76" is a hidden gem, "60 of 76" isn't, whatever the score says.
    country_count: int
    total_countries: int
    gem_score: float


class GlobalArtist(BaseModel):
    """An artist ranked by streams summed across every country's chart."""

    artist: str
    streams: int
    previous_streams: int | None = None
    # None, not 0, until a second snapshot exists: "unchanged" and "nothing
    # to compare against" are different statements.
    delta: int | None = None
    country_count: int
    origin_country: str | None = None


class GlobalArtistsResponse(BaseModel):
    artists: list[GlobalArtist]


class GenreArtist(BaseModel):
    artist: str
    # None when the artist is linked to this genre by tags but isn't on the
    # current chart - still a valid example, just not measurable today.
    streams: int | None = None
    best_position: int | None = None


class GenreDetail(BaseModel):
    """One genre as it exists in one country: what it is, and who plays it."""

    genre: str
    country_code: str
    country_name: str
    # From Last.fm's tag.getInfo. None for genres it has no wiki entry for,
    # which is common for narrower ones.
    summary: str | None = None
    url: str | None = None
    score: int | None = None
    distinctiveness: float | None = None
    artists: list[GenreArtist] = []


class CountryDetail(BaseModel):
    """One country in depth - the click-through from a grid card."""

    code: str
    name: str
    artist_count: int
    # Ranked; up to `artist_limit` depending on how many the pipeline resolved.
    artists: list[str]
    # Share of what the country actually plays. Looks much the same for every
    # country: pop and rock dominate nearly all of them.
    popularity: GenreBreakdown
    # Share of what sets this country apart from the others, by TF-IDF weight
    # (see cleansing.score_distinctiveness). Genres common to everywhere score
    # 0 and are excluded, so this can legitimately be empty for a country
    # whose taste has nothing unusual in it.
    distinctiveness: GenreBreakdown
    # None until enough artist origins are resolved to say anything - which
    # is an honest "not yet", not zero percent domestic.
    domestic_share: DomesticShare | None = None
    # The actual charting songs, with measured streams. Empty until run_load
    # has populated chart_entries.
    top_tracks: list[ChartTrack] = []
    # Artists big here and nowhere else. Empty when every artist this country
    # charts also charts everywhere - a real finding, not a gap.
    hidden_gems: list[HiddenGem] = []
    cover_image: str | None = None
