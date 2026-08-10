import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_error_handlers
from app.api.routes import countries, genres, health
from app.core.db import close_pool
from app.core.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    yield
    # Hand every pooled connection back on shutdown. Without this the pool's
    # sockets are only reclaimed when the process dies, which is untidy under
    # --reload (a new pool per reload) and leaks server-side connection slots
    # if the process is ever stopped less abruptly.
    close_pool()


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
