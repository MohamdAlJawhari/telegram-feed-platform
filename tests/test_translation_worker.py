import asyncio
import tempfile
import subprocess
import sys
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.database import database
from app.database.models import save_post, upsert_channel, update_channel_settings
from app.llm.ollama_client import OllamaConnectionError, OllamaModelNotFoundError
from app.translation import queue, store
from app.translation.service import TranslationConfig, TranslationConfigurationError
from app.translation.worker import TranslationWorker, WorkerConfig, WorkerLock


class FakeOllama:
    def __init__(self):
        self.digest = "test-model-v1"
        self.unavailable = None
        self.active = 0
        self.maximum = 0
        self.calls = 0
        self.started = asyncio.Event()
        self.gate = asyncio.Event()
        self.gate.set()
        self.fail_first = False

    async def require_model(self, model):
        if self.unavailable:
            raise self.unavailable
        return {"name": model, "digest": self.digest}

    async def chat(self, model, messages, **kwargs):
        self.calls += 1
        call = self.calls
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        self.started.set()
        try:
            await self.gate.wait()
            await asyncio.sleep(0.03)
            if self.fail_first and call == 1:
                raise OllamaConnectionError("Disconnected")
            return {"message": {"content": "Texte traduit"}, "done": True}
        finally:
            self.active -= 1


class TranslationWorkerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_NAME", str(Path(self.directory.name) / "test.db"))
        self.db_patch.start()
        database.create_database()
        self.client = FakeOllama()
        self.config = TranslationConfig()
        self.settings = dict(prefix="Latest", suffix="Source", remove_keywords="", replace_words="",
                             translation_enabled=True, source_language="en", target_language="fr")
        self.workers = []
        self.add_channel("1")
        self.add_channel("2")

    async def asyncTearDown(self):
        for worker in self.workers:
            await worker.stop()
        self.db_patch.stop()
        self.directory.cleanup()

    def add_channel(self, channel):
        upsert_channel(channel, channel, channel)
        update_channel_settings(channel, **self.settings)

    def add_post(self, channel="1", number=1, text="The meeting starts at ten."):
        save_post(channel, channel, channel, number, text, "text", None, "2026-10-09")
        with closing(database.get_connection()) as conn:
            return conn.execute("SELECT id FROM posts WHERE channel_id = ? AND message_id = ?", (channel, number)).fetchone()[0]

    def jobs(self):
        with closing(database.get_connection()) as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM translation_jobs ORDER BY id")]

    async def wait_for(self, predicate):
        async def poll():
            while not predicate():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(poll(), 5)

    async def start_worker(self, **kwargs):
        worker = TranslationWorker(client=self.client, translation_config=self.config,
                                   worker_config=WorkerConfig(poll_seconds=0.02, **kwargs))
        self.workers.append(worker)
        await worker.start()
        return worker

    async def test_two_channels_overlap_and_concurrency_is_bounded(self):
        self.client.gate.clear()
        for number in range(1, 5):
            self.add_post("1", number)
        second = self.add_post("2")
        await self.start_worker()
        await self.wait_for(lambda: self.client.active == 2)
        running = [job for job in self.jobs() if job["status"] == "running"]
        self.assertIn(second, [job["post_id"] for job in running])
        self.assertEqual(self.client.maximum, 2)
        self.client.gate.set()
        await self.wait_for(lambda: len(self.jobs()) == 5 and all(job["status"] == "completed" for job in self.jobs()))
        self.assertEqual(self.client.maximum, 2)
        self.assertEqual(self.jobs()[0]["output_text"], "Latest\n\nTexte traduit\n\nSource")

    async def test_offline_and_missing_model_preserve_intents_then_resume(self):
        post = self.add_post()
        self.client.unavailable = OllamaConnectionError("Stopped")
        worker = await self.start_worker()
        await self.wait_for(lambda: worker.error_message is not None)
        self.assertEqual(len(queue.pending_requests()), 1)
        self.assertEqual(self.jobs(), [])
        self.client.unavailable = OllamaModelNotFoundError("Missing")
        await self.wait_for(lambda: "installed" in worker.error_message)
        self.assertEqual(len(queue.pending_requests()), 1)
        self.client.unavailable = None
        await self.wait_for(lambda: self.jobs() and self.jobs()[0]["status"] == "completed")
        self.assertEqual(store.get_current_translation(post, self.config, self.client.digest)["status"], "completed")

    async def test_failure_does_not_block_another_job_and_is_not_retried(self):
        self.add_post("1")
        self.add_post("2")
        self.client.fail_first = True
        await self.start_worker()
        await self.wait_for(lambda: len(self.jobs()) == 2 and all(job["status"] in ("failed", "completed") for job in self.jobs()))
        self.assertEqual({job["status"] for job in self.jobs()}, {"failed", "completed"})
        queue.seed_requests()
        await self.wait_for(lambda: not queue.pending_requests())
        self.assertEqual(self.client.calls, 2)

    async def test_shutdown_and_restart_requeue_running_attempts(self):
        post = self.add_post()
        self.client.gate.clear()
        worker = await self.start_worker()
        await self.wait_for(lambda: self.client.active == 1)
        old = self.jobs()[0]
        await worker.stop()
        self.assertEqual(self.jobs()[0]["status"], "pending")
        self.assertIsNone(self.jobs()[0]["claim_token"])
        self.client.gate.set()
        await self.start_worker()
        await self.wait_for(lambda: self.jobs()[0]["status"] == "completed")
        self.assertEqual(self.jobs()[0]["attempts"], 2)
        self.assertFalse(store.complete_translation(old["id"], old["claim_token"], "stale", self.config, self.client.digest))
        self.assertEqual(store.get_current_translation(post, self.config, self.client.digest)["translated_body"], "Texte traduit")

    async def test_startup_recovers_abandoned_running_job(self):
        post = self.add_post()
        job = store.enqueue_translation(post, self.config, self.client.digest)
        store.claim_translation(self.config, self.client.digest)
        await self.start_worker()
        await self.wait_for(lambda: store.get_translation_job(job["id"])["status"] == "completed")
        self.assertEqual(self.jobs()[0]["attempts"], 2)

    async def test_settings_change_during_generation_rejects_old_output(self):
        self.add_post()
        self.client.gate.clear()
        await self.start_worker()
        await self.wait_for(lambda: self.client.active == 1)
        old_id = self.jobs()[0]["id"]
        update_channel_settings("1", **dict(self.settings, target_language="de"))
        await self.wait_for(lambda: len(self.jobs()) == 2)
        self.client.gate.set()
        await self.wait_for(lambda: self.jobs()[-1]["status"] == "completed")
        self.assertEqual(store.get_translation_job(old_id)["status"], "failed")
        self.assertIsNone(store.get_translation_job(old_id)["output_text"])

    async def test_model_digest_change_requeues_cached_output(self):
        self.add_post()
        await self.start_worker()
        await self.wait_for(lambda: self.jobs() and self.jobs()[0]["status"] == "completed")
        self.client.digest = "test-model-v2"
        await self.wait_for(lambda: len(self.jobs()) == 2 and self.jobs()[-1]["status"] == "completed")
        self.assertEqual(self.client.calls, 2)

    async def test_second_manager_does_not_steal_running_attempt(self):
        self.add_post()
        self.client.gate.clear()
        await self.start_worker()
        await self.wait_for(lambda: self.client.active == 1)
        old_token = self.jobs()[0]["claim_token"]
        second = await self.start_worker()
        await asyncio.sleep(0.05)
        self.assertIsNone(second.lock.file)
        await second.stop()
        self.assertEqual(self.jobs()[0]["claim_token"], old_token)
        self.assertEqual(self.client.calls, 1)

    async def test_whole_job_timeout_is_recorded_and_slot_freed(self):
        self.add_post()
        self.client.gate.clear()
        await self.start_worker(job_timeout=0.05)
        await self.wait_for(lambda: self.jobs() and self.jobs()[0]["status"] == "failed")
        self.assertIn(self.jobs()[0]["error_type"], ("TimeoutError", "CancelledError"))
        self.assertIsNone(self.jobs()[0]["output_text"])
        self.assertEqual(self.client.active, 0)

    async def test_disable_and_same_language_skip_model_calls(self):
        self.add_post()
        update_channel_settings("1", **dict(self.settings, translation_enabled=False))
        self.add_post("2")
        update_channel_settings("2", **dict(self.settings, target_language="en"))
        await self.start_worker()
        await self.wait_for(lambda: not queue.pending_requests())
        self.assertEqual(self.client.calls, 0)
        self.assertEqual(self.jobs(), [])

    async def test_request_revision_protects_concurrent_settings_save(self):
        self.add_post()
        request = queue.pending_requests()[0]
        update_channel_settings("1", **dict(self.settings, prefix="Changed"))
        queue.acknowledge_request(request["post_id"], request["revision"])
        self.assertEqual(len(queue.pending_requests()), 1)
        self.assertGreater(queue.pending_requests()[0]["revision"], request["revision"])

    async def test_startup_seeds_existing_posts_without_intents(self):
        self.add_post()
        with closing(database.get_connection()) as conn, conn:
            conn.execute("DELETE FROM translation_requests")
        await self.start_worker()
        await self.wait_for(lambda: self.jobs() and self.jobs()[0]["status"] == "completed")

    def test_invalid_worker_configuration(self):
        for kwargs in ({"concurrency": 0}, {"concurrency": 9}, {"poll_seconds": float("nan")}, {"job_timeout": 0}):
            with self.assertRaises(TranslationConfigurationError):
                WorkerConfig(**kwargs)

    def test_worker_ownership_is_exclusive_across_processes(self):
        lock = WorkerLock()
        self.assertTrue(lock.acquire())
        script = (
            "import sys; from app.database import database; "
            "from app.translation.worker import WorkerLock; "
            "database.DATABASE_NAME = sys.argv[1]; "
            "lock = WorkerLock(); acquired = lock.acquire(); "
            "assert acquired == (sys.argv[2] == 'yes'); lock.close()"
        )
        try:
            other = subprocess.run([sys.executable, "-c", script, database.DATABASE_NAME, "no"],
                                   capture_output=True, text=True, timeout=15)
            self.assertEqual(other.returncode, 0, other.stderr)
        finally:
            lock.close()
        other = subprocess.run([sys.executable, "-c", script, database.DATABASE_NAME, "yes"],
                               capture_output=True, text=True, timeout=15)
        self.assertEqual(other.returncode, 0, other.stderr)
