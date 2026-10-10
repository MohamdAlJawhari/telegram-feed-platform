from fastapi import APIRouter, Request, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from hashlib import sha256
from pathlib import Path
from app.dashboard.settings import router as settings_router
from app.dashboard.translation_activity import router as translation_activity_router
from app.dashboard.posts import router as posts_router

from app.telegram.telegram_service import get_channel_information
from app.database.models import (
    upsert_channel, 
    get_all_channels, 
    set_channel_enabled, 
    delete_channel, 
    get_dashboard_statistics,
    get_channel_summaries,
    get_translation_channels,
)
from app.translation.languages import LANGUAGES

templates = Jinja2Templates(
    directory="app/dashboard/templates"
)

router = APIRouter()
router.include_router(settings_router)
router.include_router(translation_activity_router)
router.include_router(posts_router)


@router.get("/dashboard", include_in_schema=False)
def legacy_dashboard(request: Request):
    query = request.url.query
    return RedirectResponse("/telegram-feed" + (f"?{query}" if query else ""), status_code=308)


@router.api_route("/dashboard/{path:path}", methods=["GET", "POST"], include_in_schema=False)
def legacy_dashboard_path(request: Request, path: str):
    query = request.url.query
    # 308 preserves the method and body for old forms and preview requests.
    return RedirectResponse("/telegram-feed/" + path + (f"?{query}" if query else ""), status_code=308)


@router.get("/telegram-feed")
def dashboard(
    request: Request,
    success: int = 0,
    error: int = 0,
):
    channels = get_all_channels()
    statistics = get_dashboard_statistics()
    translation_channels = get_translation_channels()
    statistics["translation_channels"] = len(translation_channels)
    static = Path(__file__).parent / "static"
    assets = ("css/common.css", "css/dashboard.css", "js/dashboard.js", "js/translation_activity.js")
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "request": request,
            "channels": channels,
            "statistics": statistics,
            "translation_channels": translation_channels,
            "language_names": LANGUAGES,
            "success": success,
            "error": error,
            "asset_versions": {name: sha256((static / name).read_bytes()).hexdigest()[:16] for name in assets},
        },
        headers={"Cache-Control": "no-cache"},
    )

@router.post("/telegram-feed/add-channel")
async def add_channel_from_dashboard(
    username: str = Form(...)
):
    try:
        telegram_channel = await get_channel_information(
            username
        )
        upsert_channel(
            channel_id=telegram_channel["channel_id"],
            channel_title=telegram_channel["channel_title"],
            channel_username=telegram_channel["channel_username"],
        )
    except Exception:
        return RedirectResponse(
            url="/telegram-feed?error=1",
            status_code=303,
        )
    return RedirectResponse(
        url="/telegram-feed?success=1",
        status_code=303,
    )

@router.post("/telegram-feed/toggle/{channel_id}")
def toggle_channel(
    channel_id: str,
    enabled: str = Form(None),
):
    set_channel_enabled(
        channel_id=channel_id,
        enabled=enabled is not None,
    )
    return RedirectResponse(
        "/telegram-feed",
        status_code=303,
    )

@router.post("/telegram-feed/delete/{channel_id}")
def delete_channel_from_dashboard(channel_id: str):

    delete_channel(channel_id)

    return RedirectResponse(
        url="/telegram-feed",
        status_code=303,
    )

@router.get("/channel-summary", include_in_schema=False)
@router.get("/telegram-feed/channel-summary")
def channel_summary():

    return get_channel_summaries()
