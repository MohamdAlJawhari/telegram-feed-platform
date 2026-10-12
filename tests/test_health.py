from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.health import router
from app.database import database


class HealthTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "health.db"
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TABLE posts (id INTEGER)")
        self.patch = patch.object(database, "DATABASE_NAME", str(self.path))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.app = FastAPI()
        self.app.include_router(router)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def test_healthy_telegram_and_readable_rss(self):
        self.app.state.telegram_supervisor = SimpleNamespace(
            status=lambda: {"status": "connected"})
        for endpoint in ("/health", "/health/telegram"):
            response = self.client.get(endpoint)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "ok")
            self.assertTrue(response.json()["rss_ready"])
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_telegram_failure_does_not_mark_saved_rss_unavailable(self):
        for state in ("connecting", "reconnecting", "authorization_required", "stopped"):
            self.app.state.telegram_supervisor = SimpleNamespace(
                status=lambda: {"status": state})
            response = self.client.get("/health")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "degraded")
            self.assertTrue(response.json()["rss_ready"])
            self.assertEqual(self.client.get("/health/telegram").status_code, 503)

    def test_missing_database_is_not_recreated(self):
        self.path.unlink()
        for endpoint in ("/health", "/health/telegram"):
            self.assertEqual(self.client.get(endpoint).status_code, 503)
        self.assertFalse(self.path.exists())

    def test_invalid_schema_or_corrupt_database_returns_safe_failure(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("DROP TABLE posts")
        self.assertEqual(self.client.get("/health").status_code, 503)
        self.path.write_bytes(b"not a database")
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(str(self.path), response.text)
