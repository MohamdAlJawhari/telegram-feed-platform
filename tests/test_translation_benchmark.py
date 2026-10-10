import argparse
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.database import database
from app.llm.ollama_client import OllamaConnectionError
from app.translation.benchmark import load_posts, measure_round, parser, positive_seconds, BenchmarkError
from app.translation.check_workers import DemoClient
from app.translation.service import TranslationConfig
from app.translation.worker import WorkerConfig


class TranslationBenchmarkTest(unittest.IsolatedAsyncioTestCase):
    async def test_ten_channel_round_measures_jobs_without_touching_project_database(self):
        original = database.DATABASE_NAME
        client = DemoClient()
        result = await measure_round(
            client, TranslationConfig(), WorkerConfig(poll_seconds=0.01), load_posts(),
            "en", "fr", concurrency=2, round_timeout=10,
        )
        self.assertEqual(database.DATABASE_NAME, original)
        self.assertTrue(result["successful"])
        self.assertEqual(result["completed"], 10)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["unfinished"], 0)
        self.assertEqual(client.maximum, 2)
        self.assertEqual(len(result["posts"]), 10)
        self.assertGreater(result["longest_queue_wait_seconds"], result["average_queue_wait_seconds"])
        self.assertGreater(result["posts_per_minute"], 0)
        for record in result["posts"]:
            self.assertEqual(record["status"], "completed")
            self.assertTrue(record["translated_text"])
            self.assertGreaterEqual(record["finished_after_seconds"], record["queue_wait_seconds"])
            self.assertGreater(record["service_seconds"], 0)

    async def test_new_round_does_not_reuse_database_results(self):
        class CountingClient(DemoClient):
            calls = 0

            async def chat(self, *args, **kwargs):
                self.calls += 1
                return await super().chat(*args, **kwargs)

        client = CountingClient()
        for limit in (2, 4):
            result = await measure_round(client, TranslationConfig(), WorkerConfig(poll_seconds=0.01),
                                         load_posts(), "en", "fr", concurrency=limit, round_timeout=10)
            self.assertTrue(result["successful"])
        self.assertEqual(client.calls, 20)
        self.assertEqual(client.maximum, 4)

    async def test_failed_jobs_are_not_reported_as_successful_throughput(self):
        client = DemoClient()
        client.fail = True
        result = await measure_round(client, TranslationConfig(), WorkerConfig(poll_seconds=0.01),
                                     load_posts(), "en", "fr", concurrency=4, round_timeout=10)
        self.assertFalse(result["successful"])
        self.assertEqual(result["failed"], 10)
        self.assertEqual(result["completed"], 0)
        self.assertIsNone(result["posts_per_minute"])
        self.assertIsNone(result["last_completion_seconds"])
        self.assertTrue(all(record["error_type"] == "OllamaConnectionError" for record in result["posts"]))

    async def test_round_timeout_cancels_active_jobs_and_restores_database(self):
        original = database.DATABASE_NAME
        client = DemoClient()
        client.gate.clear()
        result = await measure_round(client, TranslationConfig(), WorkerConfig(poll_seconds=0.01),
                                     load_posts(), "en", "fr", concurrency=2, round_timeout=0.1)
        self.assertFalse(result["successful"])
        self.assertEqual(result["unfinished"], 10)
        self.assertIn("time limit", result["abort_reason"])
        self.assertEqual(client.active, 0)
        self.assertEqual(database.DATABASE_NAME, original)

    async def test_connection_failure_aborts_instead_of_waiting_for_queue_forever(self):
        class OfflineClient(DemoClient):
            async def require_model(self, model):
                raise OllamaConnectionError("Offline")

        result = await measure_round(OfflineClient(), TranslationConfig(), WorkerConfig(poll_seconds=0.01),
                                     load_posts(), "en", "fr", concurrency=2, round_timeout=5)
        self.assertFalse(result["successful"])
        self.assertEqual(result["unfinished"], 10)
        self.assertIn("couldn't reach", result["abort_reason"])

    def test_custom_samples_require_ten_nonempty_texts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.json"
            texts = [f"خبر رقم {index}" for index in range(10)]
            path.write_text(json.dumps(texts, ensure_ascii=False), encoding="utf-8-sig")
            self.assertEqual(load_posts(path), texts)
            for data in (["one"], [" "] * 10, [1] * 10, {"text": "news"}):
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(BenchmarkError):
                    load_posts(path)

    def test_cli_rejects_invalid_limits_and_timeouts(self):
        self.assertEqual(parser().parse_args([]).limits, [1, 2, 4])
        for value in ("0", "-2", "nan", "inf"):
            with self.assertRaises(argparse.ArgumentTypeError):
                positive_seconds(value)


if __name__ == "__main__":
    unittest.main()
