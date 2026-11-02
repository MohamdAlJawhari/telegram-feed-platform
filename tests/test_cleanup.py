import os
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.database.database import create_database
from scripts.cleanup import cleanup


class CleanupTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.database = self.root / "telegram.db"
        with patch("app.database.database.DATABASE_NAME", str(self.database)):
            create_database()
        self.media = self.root / "data/media"
        self.media.mkdir(parents=True)

    def post(self, age, path="news/photo.jpg"):
        date = (datetime.now(timezone.utc) - timedelta(days=age)).isoformat()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            cursor = connection.execute(
                "INSERT INTO posts(channel_title,channel_username,message_id,text,media_path,date) VALUES ('News','news',1,'Caption',?,?)",
                (path, date),
            )
            return cursor.lastrowid

    def file(self, name="news/photo.jpg"):
        target = self.media / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"media")
        return target

    def test_preview_does_not_modify_database_or_media(self):
        self.post(8)
        target = self.file()
        original = self.database.read_bytes()
        cleanup(self.root)
        self.assertTrue(target.exists())
        self.assertEqual(self.database.read_bytes(), original)

    def test_media_only_retains_text_and_recent_shared_file(self):
        self.post(8)
        old = self.file()
        recent = self.file("news/shared.jpg")
        self.post(9, "news/shared.jpg")
        self.post(1, "news/shared.jpg")
        cleanup(self.root, apply=True)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        with closing(sqlite3.connect(self.database)) as connection, connection:
            rows = connection.execute("SELECT text,media_path FROM posts ORDER BY id").fetchall()
        self.assertEqual(rows, [("Caption", None), ("Caption", "news/shared.jpg"), ("Caption", "news/shared.jpg")])

    def test_delete_posts_removes_dependents_and_retains_settings(self):
        old_id = self.post(8)
        self.post(1, None)
        self.file()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("INSERT INTO translation_requests(post_id) VALUES (?)", (old_id,))
            connection.execute("INSERT INTO translation_jobs(post_id,fingerprint,snapshot_json,processed_text) VALUES (?,'test','{}','Caption')", (old_id,))
            connection.execute("INSERT INTO post_translations(post_id,current_job_id) VALUES (?,1)", (old_id,))
            connection.execute("INSERT INTO channels(channel_id,channel_title) VALUES ('123','News')")
        cleanup(self.root, apply=True, delete_posts=True)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM posts").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM translation_jobs").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM translation_requests").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM channels").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_orphan_files_use_file_age_and_avatars_are_preserved(self):
        old = self.file("news/orphan.jpg")
        recent = self.file("news/recent.jpg")
        avatar = self.file("channel-profiles/avatar.jpg")
        stamp = (datetime.now(timezone.utc) - timedelta(days=9)).timestamp()
        for target in (old, avatar):
            os.utime(target, (stamp, stamp))
        cleanup(self.root, apply=True)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(avatar.exists())

    def test_unsafe_database_path_aborts_without_deleting(self):
        self.post(8, "../../outside.txt")
        target = self.file()
        with self.assertRaises(ValueError):
            cleanup(self.root, apply=True)
        self.assertTrue(target.exists())
