"""Genre trend analytics - the one endpoint in this API backed by an
analytical (not simple-lookup) SQL query. The query itself lives in
sql/queries/trending_genres.sql, not as a Python string, so it's directly
runnable/reviewable on its own with psql. See that file for what it does
and why it can legitimately return an empty list (fewer than two days of
pipeline history so far).
"""
from pathlib import Path

from fastapi import APIRouter

from app.core.db import get_connection
from app.schemas.genres import TrendingGenre, TrendingGenresResponse

router = APIRouter(tags=["genres"])

_QUERY_PATH = Path(__file__).resolve().parents[3] / "sql" / "queries" / "trending_genres.sql"
_QUERY = _QUERY_PATH.read_text()


@router.get("/genres/trending", response_model=TrendingGenresResponse)
def get_trending_genres() -> TrendingGenresResponse:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_QUERY)
            rows = cur.fetchall()

    genres = [
        TrendingGenre(
            country_code=row[0],
            genre=row[1],
            score=row[2],
            previous_score=row[3],
            delta=row[4],
        )
        for row in rows
    ]
    return TrendingGenresResponse(genres=genres)
