from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import settings
from app.db.database import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("quarterly-results")


def _monitor_once():
    if settings.monitor_mode != "live" or not settings.alert_poll_enabled:
        return
    from app.services.live_pipeline import LivePipeline
    db = Database()
    pipeline = LivePipeline(db)
    try:
        result = pipeline.scan_alerts()
        logger.info("Alert cycle: %s", result)
    except Exception as exc:
        logger.exception("Alert monitor failed: %s", exc)


async def monitor_loop():
    while True:
        await asyncio.to_thread(_monitor_once)
        await asyncio.sleep(settings.monitor_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(monitor_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


for _folder in ("static", "generated", "data"):
    Path(_folder).mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Quarterly Results Alert Center", version="1.2.0", lifespan=lifespan)
app.include_router(router)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/generated", StaticFiles(directory="generated"), name="generated")


@app.get("/")
def home():
    return FileResponse("static/index.html")
