import asyncio
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch, AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import database
from app.database.models import save_post, update_channel_settings, upsert_channel, set_channel_enabled
from app.dashboard.translation_activity import router
from app.llm.ollama_client import OllamaAPIError
from app.translation import activity
from app.translation.capacity import AutomaticCapacity, MemoryReading
from app.translation.check_workers import DemoClient
from app.translation.service import TranslationConfig
from app.translation.worker import TranslationWorker, WorkerConfig


class ClockCapacity(AutomaticCapacity):
    now = 0

    def update(self, *args, **kwargs):
        return super().update(*args, now=self.now)


class AutomaticTranslationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_NAME", str(Path(self.directory.name) / "test.db"))
        self.db_patch.start()
        database.create_database()
        self.client = DemoClient()
        self.client.gate.clear()
        self.memory = MemoryReading("healthy")
        self.policy = ClockCapacity()
        self.worker = TranslationWorker(
            client=self.client, translation_config=TranslationConfig(),
            worker_config=WorkerConfig(poll_seconds=0.02, automatic=True),
            memory_reader=lambda: self.memory,
        )
        self.worker.capacity = self.policy
        for channel in range(1, 11):
            upsert_channel(str(channel), str(channel), str(channel))
            update_channel_settings(str(channel), "", "", "", "", True, "en", "fr")
            save_post(str(channel), str(channel), str(channel), 1, "The meeting starts at ten.", "text", None, "2026-10-09T10:00:00+00:00")

    async def asyncTearDown(self):
        await self.worker.stop()
        self.db_patch.stop()
        self.directory.cleanup()

    async def until(self, predicate):
        async def poll():
            while not predicate():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(poll(), 5)

    async def ramp_to_four(self):
        await self.worker.start()
        await self.until(lambda: self.client.active == 1)
        for limit in (2, 3, 4):
            self.policy.now = (limit - 1) * 2
            await self.until(lambda: self.client.active == limit)

    async def test_channel_formula_drives_live_worker_and_shared_dashboard_status(self):
        await self.ramp_to_four()
        self.assertEqual(self.client.maximum, 4)
        await self.until(lambda: activity.get_activity().get("running") == 4)
        status = activity.get_activity()
        self.assertTrue(status["automatic"])
        self.assertEqual(status["limit"], 4)
        self.assertEqual(status["channels"], 10)
        self.assertEqual(status["waiting"], 6)
        self.client.gate.set()
        await self.until(lambda: activity.get_activity().get("waiting") == 0 and activity.get_activity().get("running") == 0)
        self.assertEqual(self.client.maximum, 4)

    async def test_channel_disable_lowers_limit_without_cancelling_current_jobs(self):
        await self.ramp_to_four()
        for channel in range(3, 11):
            set_channel_enabled(str(channel), False)
        await self.until(lambda: self.worker.effective_limit == 1)
        self.assertEqual(self.client.active, 4)
        self.client.gate.set()
        await self.until(lambda: self.client.active == 0)
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM translation_jobs WHERE status = 'pending'").fetchone()[0], 6)

    async def test_publishing_source_cancels_model_requests_without_requeueing(self):
        from app.rss.rss import generate_rss

        await self.ramp_to_four()
        with patch("app.translation.output.TranslationConfig.from_env", return_value=TranslationConfig()):
            for channel in range(1, 11):
                generate_rss(str(channel))
        await self.until(lambda: self.client.active == 0)
        await self.until(lambda: activity.get_activity().get("waiting") == 0 and activity.get_activity().get("running") == 0)
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM rss_source_fallbacks").fetchone()[0], 10)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM translation_jobs WHERE status IN ('pending', 'running')").fetchone()[0], 0)

    async def test_critical_memory_pauses_new_jobs_but_allows_running_jobs_to_finish(self):
        await self.ramp_to_four()
        self.memory = MemoryReading("critical", "Memory is nearly full.")
        await self.until(lambda: self.worker.effective_limit == 0)
        self.assertEqual(self.client.active, 4)
        self.client.gate.set()
        await self.until(lambda: self.client.active == 0)
        await asyncio.sleep(0.05)
        self.assertEqual(activity.get_activity()["waiting"], 6)
        self.memory = MemoryReading("healthy")
        self.policy.now = 100
        await self.until(lambda: activity.get_activity().get("waiting") == 0 and activity.get_activity().get("running") == 0)

    async def test_unknown_memory_and_probe_failure_are_conservative(self):
        self.worker.memory_reader = lambda: (_ for _ in ()).throw(OSError("Unavailable"))
        await self.worker.start()
        await self.until(lambda: self.client.active == 1)
        self.policy.now = 100
        await asyncio.sleep(0.05)
        self.assertEqual(self.client.active, 1)
        self.assertEqual(self.worker.effective_limit, 1)
        self.assertIn("unavailable", activity.get_activity()["memory_note"])

    async def test_model_memory_error_pauses_remaining_work(self):
        self.client.chat = AsyncMock(side_effect=OllamaAPIError("out of memory", 500))
        await self.worker.start()
        await self.until(lambda: activity.get_activity().get("failed") == 1)
        await self.until(lambda: self.worker.effective_limit == 0)
        await asyncio.sleep(0.05)
        self.assertEqual(self.client.chat.await_count, 1)
        self.assertEqual(activity.get_activity()["waiting"], 9)
        self.assertIn("memory-related failure", activity.get_activity()["memory_note"])

    def test_status_endpoint_and_stale_heartbeat(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        self.assertEqual(client.get("/dashboard/translation-activity").json()["state"], "unavailable")
        activity.publish_activity({"state": "ready", "message": "Translating 2 posts; 8 waiting.", "running": 2})
        response = client.get("/dashboard/translation-activity")
        self.assertEqual(response.json()["running"], 2)
        self.assertEqual(response.headers["cache-control"], "no-store")
        with closing(database.get_connection()) as conn, conn:
            conn.execute("UPDATE translation_activity SET updated_at = '2000-01-01 00:00:00'")
        self.assertEqual(client.get("/dashboard/translation-activity").json()["state"], "unavailable")
