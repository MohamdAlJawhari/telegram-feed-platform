import sqlite3
from app.database.database import get_connection


def save_post(channel_id, channel_title, channel_username, message_id, text, message_type, media_path, date):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # A migrated legacy row has no trustworthy channel ID. Reconcile it
        # when Telegram supplies the real ID instead of inserting a duplicate.
        if channel_username:
            cursor.execute("""
                UPDATE posts
                SET
                    channel_id = ?,
                    channel_title = ?,
                    channel_username = ?,
                    text = ?,
                    message_type = ?,
                    media_path = COALESCE(?, media_path),
                    date = ?
                WHERE channel_id IS NULL
                  AND lower(channel_username) = lower(?)
                  AND message_id = ?
            """, (
                channel_id,
                channel_title,
                channel_username,
                text,
                message_type,
                media_path,
                str(date),
                channel_username,
                message_id,
            ))
            if cursor.rowcount:
                conn.commit()
                print(f"✅ Legacy post reconciled ({channel_id} / {message_id})")
                return True

        cursor.execute("""
            INSERT INTO posts(
                channel_id,
                channel_title,
                channel_username,
                message_id,
                text,
                message_type,
                media_path,
                date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            channel_id,
            channel_title,
            channel_username,
            message_id,
            text,
            message_type,
            media_path,
            str(date)
        ))

        conn.commit()
        print("✅ Post saved.")
        return True

    except sqlite3.IntegrityError as error:
        duplicate_constraint = (
            "UNIQUE constraint failed: posts.channel_id, posts.message_id"
        )
        if duplicate_constraint not in str(error):
            raise
        print(f"⚠️ Duplicate message ignored ({channel_id} / {message_id})")
        return False

    finally:
        conn.close()


def get_all_posts():
    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            channel_id,
            channel_title,
            channel_username,
            message_id,
            text,
            message_type,
            media_path,
            date
        FROM posts
        ORDER BY id DESC
    """)

    rows = cursor.fetchall()

    conn.close()

    return rows

def get_posts_by_channel(channel_username, limit=20):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            channel_id,
            channel_title,
            channel_username,
            message_id,
            text,
            message_type,
            media_path,
            date
        FROM posts
        WHERE channel_username = ?
        ORDER BY id DESC
        LIMIT ?
    """,
    (channel_username, limit))

    rows = cursor.fetchall()

    conn.close()

    return rows
