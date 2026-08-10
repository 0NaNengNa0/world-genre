from fastapi import APIRouter, HTTPException, Query

from app.schemas.countries import CountriesResponse, CountryDetail, CountrySummary
from app.services.countries import get_country_detail, get_country_summaries

router = APIRouter(tags=["countries"])


@router.get("/countries", response_model=CountriesResponse)
def list_countries() -> CountriesResponse:
    summaries = [CountrySummary(**s) for s in get_country_summaries()]
    return CountriesResponse(countries=summaries)


@router.get("/countries/{code}", response_model=CountryDetail)
def get_country(
    code: str,
    artist_limit: int = Query(100, ge=1, le=500),
    genre_limit: int = Query(10, ge=1, le=100),
) -> CountryDetail:
    """One country in depth - the click-through from a grid card."""
    detail = get_country_detail(code.lower(), artist_limit, genre_limit)
    if detail is None:
        # A code that isn't in the warehouse at all. Note this is also what
        # you get for a country that exists in seeds/countries.csv but whose
        # pipeline run produced nothing - the API can't distinguish "no such
        # country" from "never loaded", and both are genuinely absent here.
        raise HTTPException(status_code=404, detail=f"Unknown country code: {code}")
    return CountryDetail(**detail)
