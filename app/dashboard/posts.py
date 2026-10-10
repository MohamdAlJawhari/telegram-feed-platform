"""Original and published post views without live Telegram/Ollama dependencies."""

from hashlib import sha256
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from app.media.media_service import build_local_media_url
from app.translation.output import get_channel_outputs
from app.dashboard.post_status import present_posts, STATUS_GUIDE, STATUS_APPEARANCE
from app.dashboard.demo_posts import get_demo_posts
from app.database.models import get_channel_by_username
from app.telegram.channel_photos import cached_channel_photo_url


router = APIRouter()
templates = Jinja2Templates(directory="app/dashboard/templates")
_STATIC_DIRECTORY = Path(__file__).parent / "static"
_PAGE_ASSETS = ("css/common.css", "css/channel_posts.css", "js/channel_avatars.js", "js/channel_posts.js", "js/translation_activity.js")


@router.get("/telegram-feed/channel/{channel_username}")
def channel_posts(request: Request, channel_username: str):
    posts = present_posts(channel_username, get_channel_outputs(channel_username))
    for post in posts:
        post["media_url"] = build_local_media_url(post["media_path"]) if post["media_path"] else None
    channel = get_channel_by_username(channel_username)
    return render_posts(request, channel_username, posts, channel=channel)


def render_posts(request, channel_username, posts, *, demo=False, channel=None):
    header_avatar = dict(channel) if channel else dict(channel_id="", channel_title=channel_username)
    header_avatar["avatar_url"] = cached_channel_photo_url(channel["channel_id"]) if channel else None
    # Keep the HTML and its scripts compatible after updates, even when a browser
    # still has the previous viewer cached. Hash contents rather than using a
    # manually maintained version number.
    asset_versions = {name: sha256((_STATIC_DIRECTORY / name).read_bytes()).hexdigest()[:16]
                      for name in _PAGE_ASSETS}
    return templates.TemplateResponse(
        request=request, name="channel_posts.html",
        context={"channel_username": channel_username, "posts": posts, "asset_versions": asset_versions,
                 "demo": demo, "status_guide": STATUS_GUIDE, "status_appearance": STATUS_APPEARANCE,
                 "header_avatar": header_avatar},
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/telegram-feed/channel/{channel_username}/output")
def channel_output(channel_username: str):
    fields = ("id", "text", "output_text", "translation_status", "translation_label", "translation_message",
              "translation_tone", "translation_icon", "rss_label")
    posts = [{field: post[field] for field in fields} for post in present_posts(channel_username, get_channel_outputs(channel_username))]
    return JSONResponse({"posts": posts}, headers={"Cache-Control": "no-store"})


@router.get("/telegram-feed/demo/posts")
def demo_posts(request: Request):
    return render_posts(request, "demo_news", get_demo_posts(), demo=True)
