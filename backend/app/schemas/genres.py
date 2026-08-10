from pydantic import BaseModel


class TrendingGenre(BaseModel):
    country_code: str
    genre: str
    score: int
    previous_score: int
    delta: int


class TrendingGenresResponse(BaseModel):
    genres: list[TrendingGenre]
