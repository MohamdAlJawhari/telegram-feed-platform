import sqlite3
import tempfile
import unittest
from pathlib import Path

import app.database.database as database
from app.database.models import save_post


class DatabaseMigrationTest(unittest.TestCase):
    def test_legacy_post_is_reconciled_with_real_channel_id(self):
        original_database_name = database.DATABASE_NAME

        with tempfile.TemporaryDirectory() as temp_directory:
            database.DATABASE_NAME = str(Path(temp_directory) / "telegram.db")
            try:
                connection = sqlite3.connect(database.DATABASE_NAME)
                connection.execute("""
                    CREATE TABLE posts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        channel_title TEXT NOT NULL,
                        channel_username TEXT NOT NULL,
                        message_id INTEGER NOT NULL,
                        text TEXT,
                        message_type TEXT,
                        media_path TEXT,
                        date TEXT,
                        UNIQUE(channel_username, message_id)
                    )
                """)
                connection.execute("""
                    INSERT INTO posts(
                        channel_title, channel_username, message_id, text
                    ) VALUES ('News', 'news', 7, 'Legacy post')
                """)
                connection.commit()
                connection.close()

                database.create_database()
                save_post(
                    channel_id="12345",
                    channel_title="News",
                    channel_username="news",
                    message_id=7,
                    text="Legacy post",
                    message_type="text",
                    media_path=None,
                    date="2026-08-07",
                )

                connection = sqlite3.connect(database.DATABASE_NAME)
                rows = connection.execute("""
                    SELECT channel_id, channel_username, message_id
                    FROM posts
                """).fetchall()
                connection.close()

                self.assertEqual(rows, [("12345", "news", 7)])
            finally:
                database.DATABASE_NAME = original_database_name


if __name__ == "__main__":
    unittest.main()
