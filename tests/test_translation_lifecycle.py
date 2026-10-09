"""Verify the app owns and cleans up its background tasks without Telegram login."""

import asyncio
import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.database import database


class TranslationLifecycleTest(unittest.TestCase):
    def test_app_starts_and_stops_worker_and_telegram_tasks(self):
        disconnected = []

        async def telegram():
            try:
                await asyncio.Event().wait()
            finally:
                disconnected.append(True)

        fake_telegram = types.ModuleType("app.telegram.telegram_client")
        fake_telegram.start_telegram = telegram
        # Dashboard/channel imports also use this module for their shared client.
        fake_telegram.client = object()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(database, "DATABASE_NAME", str(Path(directory) / "test.db")):
                with patch.dict(sys.modules, {"app.telegram.telegram_client": fake_telegram}):
                    # Import with a stub so tests never load credentials or open a session.
                    previous = sys.modules.pop("main", None)
                    try:
                        module = importlib.import_module("main")
                        with patch.object(module, "TranslationWorker") as worker_class:
                            worker = worker_class.return_value
                            worker.start = AsyncMock()
                            worker.stop = AsyncMock()
                            with TestClient(module.app) as client:
                                self.assertEqual(client.get("/").status_code, 200)
                                worker.start.assert_awaited_once()
                                self.assertFalse(module.app.state.telegram_task.done())
                            worker.stop.assert_awaited_once()
                            self.assertTrue(module.app.state.telegram_task.cancelled())
                            self.assertEqual(disconnected, [True])
                    finally:
                        sys.modules.pop("main", None)
                        if previous is not None:
                            sys.modules["main"] = previous
