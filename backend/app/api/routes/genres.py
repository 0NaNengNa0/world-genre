"""Genre trend analytics, served from published JSON.

The underlying query (sql/bigquery/queries/trending_genres.sql) compares each
country's two most recent snapshots with LAG, and can legitimately return an
empty list when there is fewer than two days of pipeline history.
"""
from fastapi import APIRouter, HTTPException

from app.schemas.genres import TrendingGenresResponse
from app.services import published

router = APIRouter(tags=["genres"])


@router.get("/genres/trending", response_model=TrendingGenresResponse)
def get_trending_genres() -> TrendingGenresResponse:
    genres = published.get_trending_genres()
    if genres is None:
        raise HTTPException(
            status_code=503,
            detail="No published data yet. Run the pipeline's publish step.",
        )
    return TrendingGenresResponse(genres=genres)
