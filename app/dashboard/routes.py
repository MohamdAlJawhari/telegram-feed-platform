from fastapi import APIRouter, Request, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
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
)

templates = Jinja2Templates(
    directory="app/dashboard/templates"
)

router = APIRouter()
router.include_router(settings_router)
router.include_router(translation_activity_router)
router.include_router(posts_router)


@router.get("/dashboard")
def dashboard(
    request: Request,
    success: int = 0,
    error: int = 0,
):
    channels = get_all_channels()
    statistics = get_dashboard_statistics()
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "request": request,
            "channels": channels,
            "statistics": statistics,
            "success": success,
            "error": error,
        }
    )

@router.post("/dashboard/add-channel")
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
            url="/dashboard?error=1",
            status_code=303,
        )
    return RedirectResponse(
        url="/dashboard?success=1",
        status_code=303,
    )

@router.post("/dashboard/toggle/{channel_id}")
def toggle_channel(
    channel_id: str,
    enabled: str = Form(None),
):
    set_channel_enabled(
        channel_id=channel_id,
        enabled=enabled is not None,
    )
    return RedirectResponse(
        "/dashboard",
        status_code=303,
    )

@router.post("/dashboard/delete/{channel_id}")
def delete_channel_from_dashboard(channel_id: str):

    delete_channel(channel_id)

    return RedirectResponse(
        url="/dashboard",
        status_code=303,
    )

@router.get("/channel-summary")
def channel_summary():

    return get_channel_summaries()
