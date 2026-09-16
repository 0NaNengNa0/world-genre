from pydantic import BaseModel


class TrendingGenre(BaseModel):
    """One genre's movement in one country, week over week.

    `delta` is the change in SHARE, in percentage points - not the difference
    between score and previous_score, which are carried for context only. The
    ranking is by share because a big genre in a big market moves more raw
    points for the same underlying nothing.

    THE OPTIONAL FIELDS ARE DELIBERATE. This service reads JSON written by a
    different process on a different schedule: the API deploys on a merge to
    main, the payload is rewritten when the pipeline next publishes, and
    between those two moments the running code is reading a file the previous
    release wrote. Requiring `share` would turn that window into 500s on a
    live endpoint. A serving layer that reads files it did not write has to
    tolerate both shapes - which is the same forward-compatibility problem as
    adding a column to a table other jobs already query.
    """

    country_code: str
    genre: str
    score: int
    previous_score: int
    delta: float
    share: float | None = None
    previous_share: float | None = None
    snapshot_date: str | None = None
    previous_date: str | None = None


class TrendingGenresResponse(BaseModel):
    genres: list[TrendingGenre]
