import sqlite3
from app.database.database import get_connection

# This file defines the database models and provides functions to interact with the SQLite database.
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
        if media_path:
            cursor.execute("""
                UPDATE posts
                SET media_path = ?
                WHERE channel_id = ? AND message_id = ?
            """, (media_path, channel_id, message_id))
            conn.commit()
        print(f"⚠️ Duplicate message ignored ({channel_id} / {message_id})")
        return False

    finally:
        conn.close()


# Function to add a new channel to the database
def upsert_channel(
    channel_id,
    channel_title,
    channel_username,
):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO channels (
            channel_id,
            channel_title,
            channel_username
        )
        VALUES (?, ?, ?)

        ON CONFLICT(channel_id)
        DO UPDATE SET
            channel_title = excluded.channel_title,
            channel_username = excluded.channel_username;
    """,
    (
        channel_id,
        channel_title,
        channel_username,
    ))

    conn.commit()
    conn.close()

# Function to check if a channel is enabled in the database
def is_channel_enabled(channel_id):
    """Check if a channel is enabled."""

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT 1
        FROM channels
        WHERE channel_id = ?
          AND enabled = 1
    """, (str(channel_id),))

    row = cursor.fetchone()

    conn.close()

    return row is not None

# get all posts from the database
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

# get posts by channel username
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

# get all enabled channels from the database
def get_enabled_channels():

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            channel_id,
            channel_title,
            channel_username
        FROM channels
        WHERE enabled = 1
    """)

    rows = cursor.fetchall()

    conn.close()

    return rows

def set_channel_enabled(
    channel_id: str,
    enabled: bool,
):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE channels
        SET enabled = ?
        WHERE channel_id = ?
    """,
    (
        int(enabled),
        channel_id,
    ))

    conn.commit()
    conn.close()

def delete_channel(channel_id: str):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM channels
        WHERE channel_id = ?
    """, (channel_id,))

    conn.commit()
    conn.close()