import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.errors import register_error_handlers
from app.api.routes import countries, genres, health
from app.core.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    yield
    # Nothing to close. The API reads published JSON and holds no connections;
    # the pool teardown that used to live here went with the database.


app = FastAPI(title="World Genre API", version="0.1.0", lifespan=lifespan)

# Comma-separated, so a deployment can allow its real frontend origin without
# a code change. Defaults to the Vite dev server for local work.
_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)

app.include_router(health.router, prefix="/api")
app.include_router(countries.router, prefix="/api")
app.include_router(genres.router, prefix="/api")


# --- Frontend ---
#
# The built SPA is served from this same service rather than separate static
# hosting. That makes it same-origin with the API, which removes the CORS
# configuration entirely - the browser never makes a cross-origin request, so
# there is no allow-list to keep in sync with a deploy, and the frontend's
# relative /api paths work in production exactly as they do behind the Vite
# dev proxy.
#
# The directory is absent during local development (the Vite dev server serves
# the frontend instead) and present in the image, so its existence is checked
# rather than assumed.
_FRONTEND_DIR = Path(os.environ.get("FRONTEND_DIR", "/app/static"))

if (_FRONTEND_DIR / "index.html").exists():
    # Hashed asset filenames, safe to cache hard. Mounted before the catch-all
    # so a missing asset 404s honestly instead of being handed index.html -
    # which would otherwise surface as a confusing "unexpected token <" when
    # the browser tries to parse HTML as JavaScript.
    assets = _FRONTEND_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str):
        """Any unmatched path returns index.html, for client-side routing.

        Registered last, so the API routers above take precedence. The /api
        guard is still needed: without it an unknown API path would fall
        through to here and return an HTML page with status 200, which is a
        far more confusing failure for a client than a 404.
        """
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")

        requested = _FRONTEND_DIR / full_path
        if full_path and requested.is_file():
            return FileResponse(requested)
        return FileResponse(_FRONTEND_DIR / "index.html")
