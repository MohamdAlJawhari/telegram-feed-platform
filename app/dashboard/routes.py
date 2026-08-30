from fastapi import APIRouter, Request, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse

from app.telegram.telegram_service import get_channel_information
from app.media.media_service import build_public_media_url, build_local_media_url
from app.database.models import (
    upsert_channel, 
    get_all_channels, 
    set_channel_enabled, 
    delete_channel, 
    get_posts_by_channel_username, 
    get_posts_by_channel_username,
    get_dashboard_statistics,
    get_channel_summaries,
    get_channel_settings,
    update_channel_settings,
    get_channel
)

templates = Jinja2Templates(
    directory="app/dashboard/templates"
)

router = APIRouter()


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

@router.get("/dashboard/channel/{channel_username}")
def channel_posts(
    request: Request,
    channel_username: str,
):
    posts = [
        dict(post)
        for post in get_posts_by_channel_username(channel_username)
    ]

    for post in posts:

        if post["media_path"]:
            post["media_url"] = build_local_media_url(
                post["media_path"]
            )
            print(post["media_url"])
        else:
            post["media_url"] = None
            
    return templates.TemplateResponse(
        request=request,
        name="channel_posts.html",
        context={
            "channel_username": channel_username,
            "posts": posts,
        },
    )

@router.get("/dashboard/settings/{channel_id}")
def channel_settings_page(
    request: Request,
    channel_id: str,
):

    settings = get_channel_settings(channel_id)
    channel = get_channel(channel_id)

    return templates.TemplateResponse(
        request=request,
        name="channel_settings.html",
        context={
            "channel_id": channel_id,
            "channel": channel,
            "settings": settings,
        },
    )

@router.post("/dashboard/settings/{channel_id}")
def save_channel_settings(

    channel_id: str,

    prefix: str = Form(""),
    suffix: str = Form(""),
    remove_keywords: str = Form(""),
    replace_words: str = Form(""),

):

    update_channel_settings(
        channel_id,
        prefix,
        suffix,
        remove_keywords,
        replace_words,
    )

    return RedirectResponse(
        url=f"/dashboard/settings/{channel_id}",
        status_code=303,
    )

@router.get("/channel-summary")
def channel_summary():

    return get_channel_summaries()