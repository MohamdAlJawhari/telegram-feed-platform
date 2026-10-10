import asyncio
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from app.database import database
from app.database.models import save_post, update_channel_settings, upsert_channel
from app.llm.ollama_client import OllamaConfig, OllamaConnectionError, OllamaModelNotFoundError
from app.llm.ollama_runtime import OllamaRuntime
from app.rss.rss import generate_rss
from app.translation.activity import get_activity
from app.translation.check_workers import DemoClient, until
from app.translation.output import get_channel_outputs
from app.translation.service import TranslationConfig
from app.translation.worker import TranslationWorker, WorkerConfig


class ReadinessClient(DemoClient):
    def __init__(self, error):
        super().__init__()
        self.config = OllamaConfig()
        self.error = error
        self.translations = 0

    async def require_model(self, model):
        if self.error:
            raise self.error
        return await super().require_model(model)

    async def chat(self, *args, **kwargs):
        self.translations += 1
        return await super().chat(*args, **kwargs)


class TranslationRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def check_recovery(self, error, launches):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(database, "DATABASE_NAME", str(Path(directory) / "test.db")), \
                patch("app.translation.output.TranslationConfig.from_env", return_value=TranslationConfig()), \
                patch("app.translation.output.OllamaConfig.from_env", return_value=OllamaConfig()):
            database.create_database()
            # A disabled preference saved by the previous version cannot disable
            # automatic startup after upgrading. Leave legacy data untouched.
            with closing(database.get_connection()) as conn, conn:
                conn.execute("CREATE TABLE application_settings(id INTEGER PRIMARY KEY, ollama_auto_start INTEGER)")
                conn.execute("INSERT INTO application_settings VALUES (1, 0)")
            upsert_channel("1", "News", "news")
            update_channel_settings("1", "Header", "Footer", "", "", True, "en", "fr")
            def post(index):
                save_post("1", "News", "news", index, f"English news {index}", "text", None, "2026-10-10T10:00:00+00:00")
            client = ReadinessClient(error)
            launcher = Mock(return_value=Mock(poll=Mock(return_value=None)))
            worker = TranslationWorker(client=client, runtime=OllamaRuntime(launcher=launcher),
                                       translation_config=TranslationConfig(), worker_config=WorkerConfig(poll_seconds=0.02))
            try:
                await worker.start()
                await until(lambda: get_activity().get("state") == "paused", 5)
                self.assertEqual(launcher.call_count, launches)
                post(1)
                generate_rss("news")
                old = get_channel_outputs("news")[0]
                self.assertEqual(old["translation_status"], "source_final")
                client.error = None
                post(2)
                await until(lambda: get_channel_outputs("news")[0]["translation_status"] == "completed", 5)
                self.assertEqual(client.translations, 1)
                self.assertEqual(get_channel_outputs("news")[1]["output_text"], old["output_text"])
                generate_rss("news")
                await worker.stop()
                await worker.start()
                await until(lambda: get_activity().get("state") == "ready", 5)
                await asyncio.sleep(0.1)
                self.assertEqual(client.translations, 1)
                self.assertEqual(launcher.call_count, launches)
                with closing(database.get_connection()) as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM translation_requests").fetchone()[0], 0)
            finally:
                await worker.stop()

    async def test_connection_recovery_translates_only_new_posts(self):
        await self.check_recovery(OllamaConnectionError("Offline"), 1)

    async def test_installing_missing_model_translates_only_new_posts_without_launching(self):
        await self.check_recovery(OllamaModelNotFoundError("Missing"), 0)
