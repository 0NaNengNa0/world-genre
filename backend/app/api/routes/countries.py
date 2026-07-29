from fastapi import APIRouter

from app.schemas.countries import CountriesResponse, CountrySummary
from app.services.countries import get_country_summaries

router = APIRouter(tags=["countries"])


@router.get("/countries", response_model=CountriesResponse)
def list_countries() -> CountriesResponse:
    summaries = [CountrySummary(**s) for s in get_country_summaries()]
    return CountriesResponse(countries=summaries)
