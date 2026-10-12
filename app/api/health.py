"""Small read-only health checks; no network or translation requests."""

from contextlib import closing
from pathlib import Path
import sqlite3

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.database import database


router = APIRouter()


def health_snapshot(request):
    try:
        # Read-only mode never creates a missing database during a health check.
        uri = Path(database.DATABASE_NAME).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as connection:
            connection.execute("SELECT 1 FROM posts LIMIT 1").fetchone()
        rss_ready = True
    except (sqlite3.Error, OSError, ValueError):
        rss_ready = False

    supervisor = getattr(request.app.state, "telegram_supervisor", None)
    telegram = supervisor.status() if supervisor else {
        "status": "stopped",
        "message": "Telegram listener has not started.",
        "restart_count": 0,
        "last_connected_at": None,
    }
    return {
        "status": "ok" if rss_ready and telegram["status"] == "connected" else "degraded",
        "rss_ready": rss_ready,
        "database": "readable" if rss_ready else "unavailable",
        "telegram": telegram,
    }


@router.get("/health", include_in_schema=False)
def health(request: Request):
    snapshot = health_snapshot(request)
    # Telegram/Ollama outages must not falsely report saved RSS as unavailable.
    return JSONResponse(snapshot, status_code=200 if snapshot["rss_ready"] else 503,
                        headers={"Cache-Control": "no-store"})


@router.get("/health/telegram", include_in_schema=False)
def telegram_health(request: Request):
    snapshot = health_snapshot(request)
    healthy = snapshot["rss_ready"] and snapshot["telegram"]["status"] == "connected"
    return JSONResponse(snapshot, status_code=200 if healthy else 503,
                        headers={"Cache-Control": "no-store"})
