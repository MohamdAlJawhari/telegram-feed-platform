from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from scripts.backup import create_backup, verify_archive


class BackupTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.project = self.root / "project"
        self.output = self.root / "backups"
        self.caddy = self.root / "caddy"
        self.ollama = self.root / "ollama"
        self.dns = self.root / "dns"
        files = {
            self.project / ".env": "TEST_SECRET=private-placeholder",
            self.project / "main.py": "# test application",
            self.project / "requirements-production.txt": "# test dependencies",
            self.project / "Services/TelegramFeedService.xml": "<service/>",
            self.project / "app/example.py": "# test code",
            self.project / "data/media/image.jpg": "test media",
            self.project / ".venv/excluded.txt": "excluded",
            self.project / "app/__pycache__/excluded.pyc": "excluded",
            self.caddy / "Caddyfile": "test configuration",
            self.caddy / "CaddyService.xml": "<service/>",
            self.caddy / "storage/test/key.pem": "test certificate key",
            self.ollama / "OllamaService.xml": "<service/>",
            self.ollama / "models/excluded.bin": "excluded",
            self.dns / "update.ps1": "# test updater",
            self.dns / "token.txt": "private-placeholder",
        }
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        (self.project / "sessions").mkdir()
        for path in (self.project / "telegram.db",
                     self.project / "sessions/telegram_session.session"):
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("CREATE TABLE entries (value TEXT)")
                connection.execute("INSERT INTO entries VALUES ('initial')")

    def backup(self, keep=7):
        return create_backup(self.project, self.output, self.caddy,
                             self.ollama, self.dns, keep)

    def test_online_wal_snapshot_restores_committed_rows_and_session(self):
        with closing(sqlite3.connect(self.project / "telegram.db")) as live, live:
            live.execute("PRAGMA journal_mode=WAL")
            live.execute("INSERT INTO entries VALUES ('live committed row')")
            live.commit()
            path = self.backup()
            verify_archive(path)
            restore = self.root / "restore"
            with ZipFile(path) as archive:
                names = archive.namelist()
                self.assertIn("project/.env", names)
                self.assertIn("dns/token.txt", names)
                self.assertIn("project/data/media/image.jpg", names)
                self.assertIn("caddy/storage/test/key.pem", names)
                self.assertFalse(any("excluded" in name for name in names))
                archive.extractall(restore)
            with closing(sqlite3.connect(restore / "project/telegram.db")) as restored:
                self.assertEqual(restored.execute("SELECT value FROM entries").fetchall(),
                                 [("initial",), ("live committed row",)])
                self.assertEqual(restored.execute("PRAGMA quick_check").fetchone(), ("ok",))
            with closing(sqlite3.connect(restore / "project/sessions/telegram_session.session")) as session:
                self.assertEqual(session.execute("SELECT value FROM entries").fetchone(), ("initial",))

    def test_failed_backup_keeps_old_archive_and_removes_partial(self):
        completed = self.backup()
        (self.dns / "token.txt").unlink()
        with self.assertRaises(FileNotFoundError):
            self.backup(keep=1)
        self.assertEqual(list(self.output.glob("*.zip")), [completed])
        self.assertFalse(list(self.output.glob("*.partial")))
        self.assertFalse(list(self.output.glob(".snapshot-*")))
        verify_archive(completed)

    def test_missing_source_database_is_not_created(self):
        source = self.project / "telegram.db"
        source.unlink()
        with self.assertRaises(sqlite3.Error):
            self.backup()
        self.assertFalse(source.exists())
        self.assertFalse(list(self.output.glob("*.zip")))

    def test_retention_only_prunes_own_completed_archives_after_success(self):
        self.output.mkdir()
        unrelated = self.output / "other-backup.zip"
        unrelated.write_bytes(b"keep this unrelated file")
        old = self.backup(keep=2)
        second = self.backup(keep=2)
        third = self.backup(keep=2)
        self.assertFalse(old.exists())
        self.assertTrue(second.exists())
        self.assertTrue(third.exists())
        self.assertTrue(unrelated.exists())

    def test_corrupt_database_and_timeout_cannot_publish_backup(self):
        with patch("scripts.backup.time.monotonic", side_effect=[0, 121]):
            with self.assertRaises(TimeoutError):
                self.backup()
        (self.project / "telegram.db").write_bytes(b"corrupt SQLite database")
        with self.assertRaises(sqlite3.Error):
            self.backup()
        self.assertFalse(list(self.output.glob("*.zip")))
