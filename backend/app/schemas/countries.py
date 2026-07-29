from pydantic import BaseModel


class CountrySummary(BaseModel):
    code: str
    name: str
    artist_count: int
    top_genres: list[str]
    top_artists: list[str]
    cover_image: str | None = None


class CountriesResponse(BaseModel):
    countries: list[CountrySummary]
