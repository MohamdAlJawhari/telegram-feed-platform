from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.database import database
from app.database.models import get_channel_by_username


class DatabaseIndexesTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "indexes.db"
        db_patch = patch.object(database, "DATABASE_NAME", str(self.path))
        db_patch.start()
        self.addCleanup(db_patch.stop)
        database.create_database()
        self.connection = sqlite3.connect(self.path)
        self.addCleanup(self.connection.close)
        self.connection.execute("INSERT INTO channels(channel_id, channel_title, channel_username) VALUES ('1', 'News', 'news')")
        self.connection.execute("INSERT INTO channel_settings(channel_id, prefix, translation_enabled) VALUES ('1', 'Keep this', 1)")
        self.connection.execute("""INSERT INTO posts(channel_id, channel_title, channel_username, message_id, text)
                                   VALUES ('1', 'News', 'news', 10, 'Keep this post')""")
        self.connection.commit()

    def plan(self, sql, parameters=()):
        return "\n".join(row[3] for row in self.connection.execute("EXPLAIN QUERY PLAN " + sql, parameters))

    def test_existing_database_upgrade_is_repeatable_and_preserves_rows(self):
        tables = [row[0] for row in self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        before = {table: self.connection.execute(f'SELECT * FROM "{table}"').fetchall() for table in tables}
        # Simulate the previous schema, retaining its original queue index.
        indexes = [row[0] for row in self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL AND name != 'translation_jobs_status_id'")]
        for name in indexes:
            self.connection.execute(f'DROP INDEX "{name}"')
        self.connection.commit()
        database.create_database()
        database.create_database()
        after = {table: self.connection.execute(f'SELECT * FROM "{table}"').fetchall() for table in tables}
        self.assertEqual(before, after)
        self.assertEqual(self.connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertIn('posts_username_nocase_id', self.plan(
            'SELECT * FROM posts WHERE channel_username=? COLLATE NOCASE ORDER BY id DESC', ('NEWS',)))

    def test_post_filter_and_history_use_indexes_without_sorting_all_rows(self):
        for sql, parameters, index in (
            ('SELECT * FROM posts WHERE channel_username=? COLLATE NOCASE ORDER BY id DESC', ('NEWS',), 'posts_username_nocase_id'),
            ('SELECT * FROM posts WHERE channel_id=? ORDER BY id DESC', ('1',), 'posts_channel_id_id'),
        ):
            with self.subTest(index=index):
                plan = self.plan(sql, parameters)
                self.assertIn(index, plan)
                self.assertNotIn('TEMP B-TREE', plan)

    def test_rss_lookup_uses_indexes_for_both_current_and_legacy_posts(self):
        plan = self.plan('''SELECT * FROM posts WHERE channel_id=? OR (
            lower(channel_username)=lower(?) AND (channel_id IS NULL OR ? IS NULL)
        ) ORDER BY id DESC LIMIT ?''', ('1', 'NEWS', '1', 20))
        self.assertIn('posts_channel_id_id', plan)
        self.assertIn('posts_username_lower_id', plan)
        self.assertNotIn('SCAN posts', plan)
        self.assertIn('channels_username_lower', self.plan(
            'SELECT * FROM channels WHERE lower(channel_username)=lower(?)', ('NEWS',)))
        self.assertEqual(get_channel_by_username('NEWS')['channel_id'], '1')

    def test_translation_queue_and_enabled_channel_queries_use_indexes(self):
        plan = self.plan("""SELECT j.* FROM translation_jobs j JOIN post_translations t
            ON t.current_job_id=j.id WHERE j.status='pending'""")
        self.assertIn('translation_jobs_status_id', plan)
        self.assertIn('post_translations_current_job_id', plan)
        self.assertIn('channel_settings_translation_enabled', self.plan('''
            SELECT c.channel_id FROM channels c JOIN channel_settings s ON s.channel_id=c.channel_id
            WHERE s.translation_enabled=1'''))
