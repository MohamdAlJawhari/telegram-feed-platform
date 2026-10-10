import mimetypes
import os
import re
from contextlib import closing
from html import escape
from urllib.parse import quote

from feedgen.feed import FeedGenerator

from app.media.media_service import build_public_media_url
from app.database.database import get_connection
from app.processing.processor import extract_title
from app.translation.output import get_channel_outputs


def xml_text(text):
    # XML 1.0 cannot represent these characters, even as escaped entities.
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", "", text)


def generate_rss(channel_name):
    # Commit source decisions only after the entire feed serializes successfully.
    # This also makes output selection and late-result rejection one transaction.
    with closing(get_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        posts = get_channel_outputs(channel_name, limit=20, publish=True, connection=conn)
        return _render_rss(channel_name, posts)


def _render_rss(channel_name, posts):
    fg = FeedGenerator()
    fg.title(xml_text(channel_name))
    fg.description(f"RSS feed for Telegram channel '{xml_text(channel_name)}'")
    base_url = (os.getenv("BASE_URL") or "http://localhost:8000").rstrip("/")
    fg.link(href=f"{base_url}/rss/{quote(channel_name, safe='')}", rel="self")
    languages = {post["output_language"] for post in posts}
    if len(languages) == 1 and None not in languages:
        fg.language(next(iter(languages)))
    for post in reversed(posts):
        fe = fg.add_entry()
        title = xml_text(extract_title(post["body_text"]))
        fe.title(title[:80] + "..." if len(title) > 80 else title)
        # Keep source/model markup literal in readers that interpret HTML descriptions.
        fe.description(escape(xml_text(post["output_text"]), quote=False))
        fe.guid(f"{post['channel_id']}-{post['message_id']}", permalink=False)
        fe.pubDate(post["date"])
        if post["channel_username"]:
            fe.link(href=f"https://t.me/{post['channel_username']}/{post['message_id']}")
        if post["media_path"]:
            fe.enclosure(build_public_media_url(post["media_path"]), 0,
                         mimetypes.guess_type(post["media_path"])[0] or "application/octet-stream")
    return fg.rss_str(pretty=True)
