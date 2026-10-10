"""Activity polling has no dependency on a live Telegram or Ollama connection."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.translation.activity import get_activity
from app.dashboard.post_status import present_activity


router = APIRouter()


@router.get("/dashboard/translation-activity")
def translation_activity():
    return JSONResponse(present_activity(get_activity()), headers={"Cache-Control": "no-store"})
