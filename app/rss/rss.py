import mimetypes

from app.media.media_service import build_public_media_url
from feedgen.feed import FeedGenerator
from app.database.models import get_posts_by_channel, get_channel_settings
from app.processing.processor import process_text, extract_title

def generate_rss(channel_name):

    fg = FeedGenerator()

    fg.title(channel_name)
    fg.description(f"RSS feed for Telegram channel '{channel_name}'")
    fg.link(
        href=f"http://localhost:8000/rss/{channel_name}",
        rel="self"
    )
    fg.language("en")

    # posts = list(get_posts_for_rss())
    posts = list(get_posts_by_channel(channel_name))

    print("Requested channel:", channel_name)
    print("Posts found:", len(posts))
    print(posts)

    posts.reverse()

    for (
        channel_id,
        channel_title,
        channel_username,
        message_id,
        text,
        message_type,
        media_path,
        date,
    ) in posts:
        
        fe = fg.add_entry()
        settings = get_channel_settings(channel_id)

        processed_text = process_text(
            text=text or "",
            settings=settings,
        )

        # ---------- Title ----------
        title = extract_title(text)

        if len(title) > 80:
            title = title[:80] + "..."

        fe.title(title)

        # ---------- Description ----------
        fe.description(processed_text)

        # ---------- GUID ----------
        fe.guid(
            f"{channel_id}-{message_id}",
            permalink=False
        )

        # ---------- Publish Date ----------
        fe.pubDate(date)

        # ---------- Telegram Link ----------
        if channel_username:
            fe.link(
                href=f"https://t.me/{channel_username}/{message_id}"
            )

        if media_path:
            media_url = build_public_media_url(media_path)

            mime_type = (
                mimetypes.guess_type(media_path)[0]
                or "application/octet-stream"
            )

            fe.enclosure(
                media_url,
                0,
                mime_type,
            )

    return fg.rss_str(pretty=True)