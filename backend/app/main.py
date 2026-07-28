from fastapi import FastAPI

from app.api.routes import cleansing, health

app = FastAPI(title="World Genre API", version="0.1.0")

app.include_router(health.router)
app.include_router(cleansing.router, prefix="/api/v1")
