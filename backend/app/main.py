from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import countries, health

app = FastAPI(title="World Genre API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(countries.router, prefix="/api")
