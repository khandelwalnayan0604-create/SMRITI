import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.seed import seed_database
from app.routers import auth, patients, surveys, games, reminders, memories, facts, alerts, sathi, digest, upload

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("smriti.main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Smriti backend database...")
    await init_db()
    try:
        await seed_database()
        logger.info("Database initialized and demo seed verified.")
    except Exception as e:
        logger.error(f"Error during seeding: {e}")
    yield
    logger.info("Smriti backend shutting down...")

app = FastAPI(
    title=settings.APP_NAME,
    description="Voice-first, mobile-responsive AI dementia care web app for elderly patients in NER India",
    version="1.0.0",
    lifespan=lifespan
)

# CORS configuration locked to frontend origin
allowed_origins = [
    settings.FRONTEND_URL,
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://*.onrender.com",
    "https://*.vercel.app"
]

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https:\/\/.*\.onrender\.com|https:\/\/.*\.vercel\.app|http:\/\/localhost:\d+",
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure upload directory exists and mount static files
os.makedirs(settings.EMERGENT_STORAGE_PATH, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.EMERGENT_STORAGE_PATH), name="uploads")

# Mount API Routers under /api prefix
app.include_router(auth.router, prefix=settings.API_PREFIX)
app.include_router(patients.router, prefix=settings.API_PREFIX)
app.include_router(surveys.router, prefix=settings.API_PREFIX)
app.include_router(games.router, prefix=settings.API_PREFIX)
app.include_router(reminders.router, prefix=settings.API_PREFIX)
app.include_router(memories.router, prefix=settings.API_PREFIX)
app.include_router(facts.router, prefix=settings.API_PREFIX)
app.include_router(alerts.router, prefix=settings.API_PREFIX)
app.include_router(sathi.router, prefix=settings.API_PREFIX)
app.include_router(digest.router, prefix=settings.API_PREFIX)
app.include_router(upload.router, prefix=settings.API_PREFIX)

@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "region": "North Eastern Region (NER) India",
        "compliance": "SIH26003 / MDoNER"
    }

# Mount built frontend static files if present (for single-service unified cloud deployment)
frontend_dist = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../frontend/dist"))
if os.path.isdir(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    @app.exception_handler(404)
    async def spa_fallback(request, exc):
        if request.url.path.startswith("/api"):
            return JSONResponse(status_code=404, content={"detail": "API route not found"})
        index_file = os.path.join(frontend_dist, "index.html")
        if os.path.isfile(index_file):
            return FileResponse(index_file)
        return JSONResponse(status_code=404, content={"detail": "Not found"})
