from feedgen.feed import FeedGenerator
from app.database.models import get_posts_by_channel


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

    for channel_id, channel_title, channel_username, message_id, text, message_type, media_path, date in posts:

        fe = fg.add_entry()

        # ---------- Title ----------
        title = (text or "(No text)").strip()

        if len(title) > 80:
            title = title[:80] + "..."

        fe.title(title)

        # ---------- Description ----------
        fe.description(text or "")

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

    return fg.rss_str(pretty=True)
