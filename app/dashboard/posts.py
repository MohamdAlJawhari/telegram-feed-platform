"""Original and published post views without live Telegram/Ollama dependencies."""

from hashlib import sha256
from pathlib import Path

from typing import Annotated
from fastapi import APIRouter, Request, Query, Body
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
PAGE_SIZE = 50


@router.get("/telegram-feed/channel/{channel_username}")
def channel_posts(request: Request, channel_username: str,
                  before: Annotated[int | None, Query(gt=0)] = None):
    outputs = get_channel_outputs(channel_username, limit=PAGE_SIZE + 1, before_id=before)
    has_more = len(outputs) > PAGE_SIZE
    posts = present_posts(channel_username, outputs[:PAGE_SIZE])
    for post in posts:
        post["media_url"] = build_local_media_url(post["media_path"]) if post["media_path"] else None
    channel = get_channel_by_username(channel_username)
    next_url = (str(request.url_for('channel_posts', channel_username=channel_username).path)
                + f"?before={posts[-1]['id']}") if has_more else None
    return render_posts(request, channel_username, posts, channel=channel, next_url=next_url)


def render_posts(request, channel_username, posts, *, demo=False, channel=None, next_url=None):
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
                 "header_avatar": header_avatar, "next_url": next_url},
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/telegram-feed/channel/{channel_username}/output")
def channel_output(channel_username: str,
                   ids: Annotated[list[int] | None, Query(max_length=700)] = None):
    fields = ("id", "text", "output_text", "translation_status", "translation_label", "translation_message",
              "translation_tone", "translation_icon", "rss_label")
    outputs = get_channel_outputs(channel_username, limit=700 if ids is not None else PAGE_SIZE, post_ids=ids)
    posts = [{field: post[field] for field in fields} for post in present_posts(channel_username, outputs)]
    return JSONResponse({"posts": posts}, headers={"Cache-Control": "no-store"})


@router.post("/telegram-feed/channel/{channel_username}/output")
def loaded_channel_output(channel_username: str,
                          ids: Annotated[list[int], Body(max_length=700)]):
    return channel_output(channel_username, ids)


@router.get("/telegram-feed/demo/posts")
def demo_posts(request: Request):
    return render_posts(request, "demo_news", get_demo_posts(), demo=True)
