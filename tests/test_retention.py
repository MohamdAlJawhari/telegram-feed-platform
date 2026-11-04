import tempfile
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.database import database
from app.database.models import save_post, upsert_channel
from app.database.retention import enforce_retention, cleanup_retired_media
from app.translation import store
from app.translation.queue import finalized_jobs
from app.translation.service import TranslationConfig


class RetentionTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for replacement in (
            patch.object(database, 'DATABASE_NAME', str(self.root / 'test.db')),
            patch('app.database.retention.get_media_root', return_value=self.root / 'media'),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)
        database.create_database()
        upsert_channel('1', 'News', 'news')

    def seed(self, count=701, channel_id='1', username='news'):
        with closing(database.get_connection()) as conn, conn:
            conn.executemany("""
                INSERT INTO posts(channel_id, channel_title, channel_username, message_id, text)
                VALUES (?, 'News', ?, ?, 'News')
            """, [(channel_id, username, number) for number in range(1, count + 1)])

    def file(self, relative):
        target = self.root / 'media' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'media')
        return target

    def test_startup_prunes_each_channel_and_is_repeatable(self):
        self.seed(705)
        self.seed(702, '2', 'other')
        self.assertEqual(enforce_retention(), 7)
        self.assertEqual(enforce_retention(), 0)
        with closing(database.get_connection()) as conn:
            self.assertEqual([tuple(row) for row in conn.execute(
                'SELECT channel_id, count(*), min(message_id) FROM posts GROUP BY channel_id'
            )], [('1', 700, 6), ('2', 700, 3)])
            self.assertEqual(conn.execute('SELECT count(*) FROM channels').fetchone()[0], 1)

    def test_ingestion_keeps_newest_message_not_last_inserted(self):
        self.seed(700)
        save_post('1', 'News', 'news', 701, 'Fresh', 'text', None, '2026-11-04')
        # Receiving old history out of order must not evict newer news.
        save_post('1', 'News', 'news', 0, 'Old', 'text', None, '2020-01-01')
        with closing(database.get_connection()) as conn:
            self.assertEqual(tuple(conn.execute(
                'SELECT count(*), min(message_id), max(message_id) FROM posts'
            ).fetchone()), (700, 2, 701))
            self.assertEqual(conn.execute('SELECT count(*) FROM translation_requests').fetchone()[0], 1)

    def test_legacy_and_renamed_rows_share_stable_channel_limit(self):
        self.seed(699, '1', 'old_name')
        with closing(database.get_connection()) as conn, conn:
            conn.executemany("""
                INSERT INTO posts(channel_title,channel_username,message_id,text)
                VALUES ('News', ?, ?, 'Legacy')
            """, [('NEWS', 700), ('news', 701)])
        self.assertEqual(enforce_retention(), 1)

    def test_dependents_deleted_and_running_callback_cannot_restore_post(self):
        self.seed()
        with closing(database.get_connection()) as conn, conn:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute("INSERT INTO translation_jobs(post_id,fingerprint,snapshot_json,processed_text,status,claim_token) VALUES (1,'f','{}','News','running','token')")
            conn.execute('INSERT INTO post_translations VALUES (1,1)')
            conn.execute('INSERT INTO translation_requests(post_id) VALUES (1)')
            conn.execute("""INSERT INTO rss_source_fallbacks(post_id,output_text,body_text,translation_status,translation_label,translation_message)
                         VALUES (1,'News','News','source_final','Final','Published')""")
        enforce_retention()
        self.assertEqual(finalized_jobs([1]), {1})
        self.assertFalse(store.complete_translation(1, 'token', 'Bonjour', TranslationConfig(), 'digest'))
        with closing(database.get_connection()) as conn:
            for table in ('post_translations', 'translation_requests', 'rss_source_fallbacks', 'translation_jobs'):
                self.assertEqual(conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_media_cleanup_preserves_shared_files_avatars_and_outside_paths(self):
        self.seed(704)
        retired = self.file('news/old.jpg')
        shared = self.file('news/shared.jpg')
        avatar = self.file('channel-profiles/photo.jpg')
        outside = self.root / 'outside.txt'
        outside.write_text('keep')
        with closing(database.get_connection()) as conn, conn:
            for post_id, relative in [(1, 'news/old.jpg'), (2, 'news/shared.jpg'),
                                      (3, 'channel-profiles/photo.jpg'), (4, '../outside.txt'),
                                      (5, 'news/../news/shared.jpg')]:
                conn.execute('UPDATE posts SET media_path=? WHERE id=?', (relative, post_id))
        enforce_retention()
        self.assertFalse(retired.exists())
        for target in (shared, avatar, outside):
            self.assertTrue(target.exists())

    def test_failed_unlink_is_queued_for_retry(self):
        self.seed()
        target = self.file('news/old.jpg')
        with closing(database.get_connection()) as conn, conn:
            conn.execute("UPDATE posts SET media_path='news/old.jpg' WHERE id=1")
        with patch.object(Path, 'unlink', side_effect=PermissionError('busy')):
            enforce_retention()
        self.assertTrue(target.exists())
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM retired_media').fetchone()[0], 1)
        cleanup_retired_media()
        self.assertFalse(target.exists())

    def test_failed_post_deletion_rolls_back_dependents_and_media_queue(self):
        self.seed()
        target = self.file('news/old.jpg')
        with closing(database.get_connection()) as conn, conn:
            conn.execute("UPDATE posts SET media_path='news/old.jpg' WHERE id=1")
            conn.execute('INSERT INTO translation_requests(post_id) VALUES (1)')
            conn.execute("""CREATE TRIGGER prevent_delete BEFORE DELETE ON posts
                            BEGIN SELECT RAISE(ABORT, 'simulated failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            enforce_retention()
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM posts').fetchone()[0], 701)
            self.assertEqual(conn.execute('SELECT count(*) FROM translation_requests').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT count(*) FROM retired_media').fetchone()[0], 0)
        self.assertTrue(target.exists())
