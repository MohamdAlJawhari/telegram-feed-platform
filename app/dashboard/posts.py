"""Original and published post views without live Telegram/Ollama dependencies."""

from hashlib import sha256
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from app.media.media_service import build_local_media_url
from app.translation.output import get_channel_outputs


router = APIRouter()
templates = Jinja2Templates(directory="app/dashboard/templates")
_STATIC_DIRECTORY = Path(__file__).parent / "static"
_PAGE_ASSETS = ("css/common.css", "css/channel_posts.css", "js/channel_posts.js", "js/translation_activity.js")


@router.get("/dashboard/channel/{channel_username}")
def channel_posts(request: Request, channel_username: str):
    posts = get_channel_outputs(channel_username)
    for post in posts:
        post["media_url"] = build_local_media_url(post["media_path"]) if post["media_path"] else None
    # Keep the HTML and its scripts compatible after updates, even when a browser
    # still has the previous viewer cached. Hash contents rather than using a
    # manually maintained version number.
    asset_versions = {name: sha256((_STATIC_DIRECTORY / name).read_bytes()).hexdigest()[:16]
                      for name in _PAGE_ASSETS}
    return templates.TemplateResponse(
        request=request, name="channel_posts.html",
        context={"channel_username": channel_username, "posts": posts, "asset_versions": asset_versions},
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/dashboard/channel/{channel_username}/output")
def channel_output(channel_username: str):
    fields = ("id", "text", "output_text", "translation_status", "translation_label", "translation_message")
    posts = [{field: post[field] for field in fields} for post in get_channel_outputs(channel_username)]
    return JSONResponse({"posts": posts}, headers={"Cache-Control": "no-store"})
