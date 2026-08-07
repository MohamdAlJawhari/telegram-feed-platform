import sqlite3

DATABASE_NAME = "telegram.db"


def get_connection():
    return sqlite3.connect(DATABASE_NAME)


def create_database():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        column_info = list(cursor.execute("PRAGMA table_info(posts)"))
        existing_columns = {row[1]: row for row in column_info}

        # Legacy rows have no reliable Telegram ID. Keep their channel_id NULL
        # until ingestion can reconcile them by username and message ID.
        channel_id_is_required = (
            "channel_id" in existing_columns
            and existing_columns["channel_id"][3] == 1
        )
        needs_migration = bool(existing_columns) and (
            "channel_id" not in existing_columns or channel_id_is_required
        )
        if needs_migration:
            cursor.execute("ALTER TABLE posts RENAME TO posts_legacy")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT,
            channel_title TEXT NOT NULL,
            channel_username TEXT NOT NULL,
            message_id INTEGER NOT NULL,
            text TEXT,
            message_type TEXT,
            media_path TEXT,
            date TEXT,
            UNIQUE(channel_id, message_id)
        )
        """)

        if needs_migration:
            if "channel_id" in existing_columns:
                channel_id_expression = """
                    CASE
                        WHEN channel_id LIKE 'username:%'
                          OR channel_id LIKE 'legacy:%' THEN NULL
                        ELSE channel_id
                    END
                """
            else:
                channel_id_expression = "NULL"

            cursor.execute(f"""
                INSERT INTO posts(
                    id, channel_id, channel_title, channel_username,
                    message_id, text, message_type, media_path, date
                )
                SELECT
                    id,
                    {channel_id_expression},
                    channel_title, channel_username, message_id, text,
                    message_type, media_path, date
                FROM posts_legacy
            """)
            cursor.execute("DROP TABLE posts_legacy")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
