import sqlite3

DATABASE_NAME = "telegram.db"


def get_connection():
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    return conn

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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT NOT NULL UNIQUE,
            channel_title TEXT NOT NULL,
            channel_username TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS channel_settings (
                channel_id TEXT PRIMARY KEY,
                prefix TEXT DEFAULT '',
                suffix TEXT DEFAULT '',
                remove_keywords TEXT DEFAULT '',
                replace_words TEXT DEFAULT '',
                translation_enabled INTEGER NOT NULL DEFAULT 0,
                source_language TEXT NOT NULL DEFAULT 'en',
                target_language TEXT NOT NULL DEFAULT 'fr',
                FOREIGN KEY(channel_id)
                    REFERENCES channels(channel_id)
                    ON DELETE CASCADE
            )
        """)

        settings_columns = {row[1] for row in cursor.execute("PRAGMA table_info(channel_settings)")}
        # Additive, repeatable migration: preserve all existing channel rules.
        for name, definition in (
            ("translation_enabled", "INTEGER NOT NULL DEFAULT 0"),
            ("source_language", "TEXT NOT NULL DEFAULT 'en'"),
            ("target_language", "TEXT NOT NULL DEFAULT 'fr'"),
        ):
            if name not in settings_columns:
                cursor.execute(f"ALTER TABLE channel_settings ADD COLUMN {name} {definition}")

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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS translation_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                fingerprint TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                processed_text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'running', 'completed', 'failed')),
                translated_body TEXT,
                output_text TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                claim_token TEXT,
                error_type TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at TEXT,
                completed_at TEXT,
                UNIQUE(post_id, fingerprint),
                FOREIGN KEY(post_id) REFERENCES posts(id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS post_translations (
                post_id INTEGER PRIMARY KEY,
                current_job_id INTEGER NOT NULL,
                FOREIGN KEY(post_id) REFERENCES posts(id),
                FOREIGN KEY(current_job_id) REFERENCES translation_jobs(id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS translation_jobs_status_id ON translation_jobs(status, id)")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS translation_requests (
                post_id INTEGER PRIMARY KEY,
                revision INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
