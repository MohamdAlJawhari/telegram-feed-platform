from app.database.database import get_connection


def save_post(channel_title, channel_username, message_id, text, message_type, date):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO posts(
                channel_title,
                channel_username,
                message_id,
                text,
                message_type,
                date
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            channel_title,
            channel_username,
            message_id,
            text,
            message_type,
            str(date)
        ))

        conn.commit()
        print("✅ Post saved.")

    except Exception:
        print("⚠️ Post already exists.")

    finally:
        conn.close()


def get_all_posts():
    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            channel_title,
            channel_username,
            message_id,
            text,
            message_type,
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
            channel_title,
            channel_username,
            message_id,
            text,
            message_type,
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