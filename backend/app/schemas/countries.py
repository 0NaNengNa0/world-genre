from pydantic import BaseModel


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
    cover_image: str | None = None
