from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import database
from main import posts


class PostsApiTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "posts.db"
        db_patch = patch.object(database, "DATABASE_NAME", str(path))
        db_patch.start()
        self.addCleanup(db_patch.stop)
        database.create_database()
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executemany(
                """INSERT INTO posts
                   (channel_id, channel_title, channel_username, message_id, text, message_type, date)
                   VALUES (?, ?, ?, ?, ?, 'text', '2026-11-03 12:00:00+00:00')""",
                [("1", "Mayadeen", "almayadeen", 10, "First post"),
                 ("2", "Other news", "othernews", 20, "Other channel's post"),
                 ("1", "Mayadeen", "almayadeen", 11, "Latest post")],
            )
        app = FastAPI()
        app.get("/posts")(posts)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_channel_filter_returns_only_matching_posts_newest_first(self):
        response = self.client.get("/posts", params={"channel": "almayadeen"})
        self.assertEqual(response.status_code, 200)
        rows = response.json()
        self.assertEqual([row["message_id"] for row in rows], [11, 10])
        self.assertTrue(all(row["channel_username"] == "almayadeen" for row in rows))
        self.assertEqual(set(rows[0]), {"channel_id", "channel_title", "channel_username",
                                        "message_id", "text", "message_type", "media_path", "date"})

    def test_no_filter_still_returns_all_channels(self):
        rows = self.client.get("/posts").json()
        self.assertEqual([row["message_id"] for row in rows], [11, 20, 10])

    def test_username_case_at_prefix_and_whitespace(self):
        rows = self.client.get("/posts", params={"channel": " @AlMayadeen "}).json()
        self.assertEqual([row["message_id"] for row in rows], [11, 10])

    def test_unknown_empty_or_sql_text_never_returns_all_channels(self):
        for channel in ("unknown", "", " ", "@", "almayadeen' OR 1=1 --"):
            with self.subTest(channel=channel):
                response = self.client.get("/posts", params={"channel": channel})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), [])
