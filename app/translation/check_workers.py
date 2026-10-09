"""Check background workers without Telegram or changes to project data."""

import argparse
import asyncio
import logging
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from app.database import database
from app.database.models import save_post, update_channel_settings, upsert_channel
from app.dashboard.translation_errors import translation_error_message
from app.llm.ollama_client import OllamaClient, OllamaConnectionError
from app.translation import queue, store
from app.translation.service import TranslationConfig
from app.translation.worker import TranslationWorker, WorkerConfig


class DemoClient:
    """Simulated model for the offline check, never used by the application."""

    def __init__(self):
        self.active = 0
        self.maximum = 0
        self.gate = asyncio.Event()
        self.gate.set()
        self.fail = False

    async def require_model(self, model):
        return {"digest": "worker-check-model-v1"}

    async def chat(self, model, messages, **kwargs):
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        try:
            await self.gate.wait()
            await asyncio.sleep(0.1)
            if self.fail:
                raise OllamaConnectionError("Simulated connection failure")
            return {"message": {"content": "La réunion commence à dix heures."}, "done": True}
        finally:
            self.active -= 1


def read_jobs():
    with closing(database.get_connection()) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM translation_jobs ORDER BY id")]


async def until(predicate, timeout):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.05)
    await asyncio.wait_for(poll(), timeout)


async def check(live=False):
    original_database = database.DATABASE_NAME
    client = OllamaClient() if live else DemoClient()
    worker = None
    with tempfile.TemporaryDirectory(prefix="translation-worker-check-") as directory:
        database.DATABASE_NAME = str(Path(directory) / "check.db")
        try:
            database.create_database()
            settings = dict(prefix="Latest news", suffix="Source: Telegram", remove_keywords="BREAKING",
                            replace_words="event=>meeting", translation_enabled=True,
                            source_language="en", target_language="fr")
            for channel in ("one", "two"):
                upsert_channel(channel, channel, channel)
                update_channel_settings(channel, **settings)
                save_post(channel, channel, channel, 1, "BREAKING The event starts at ten.", "text", None, "2026-10-09")
            config = TranslationConfig.from_env() if live else TranslationConfig()
            # Fail quickly if a live check cannot reach the installed model.
            await client.require_model(config.model)
            if not live:
                client.gate.clear()
            worker = TranslationWorker(client=client, translation_config=config,
                                       worker_config=WorkerConfig(concurrency=2, poll_seconds=0.1))
            await worker.start()
            print("Waiting for two channel jobs...", flush=True)
            if not live:
                await until(lambda: client.active == 2, 10)
                assert client.maximum == 2
                print("PASS: Two channel translations ran concurrently; limit stayed at two.")
                # Stop with requests in flight, then recover them with fresh claim tokens.
                old = read_jobs()[0]
                await worker.stop()
                assert all(job["status"] == "pending" and job["claim_token"] is None for job in read_jobs())
                print("PASS: Shutdown returned unfinished jobs to the persistent queue.")
                client.gate.set()
                await worker.start()
            await until(lambda: len(read_jobs()) == 2 and all(job["status"] in ("completed", "failed") for job in read_jobs()), 620 if live else 10)
            assert all(job["status"] == "completed" for job in read_jobs()), "A translation failed; inspect warnings above."
            print("PASS: Both channel jobs completed and saved their output.")
            for job in read_jobs():
                print(f"Post {job['post_id']}:\n{job['output_text']}")
            if not live:
                assert not store.complete_translation(old["id"], old["claim_token"], "outdated", config, "worker-check-model-v1")
                print("PASS: A callback from before restart could not overwrite the result.")
            queue.seed_requests()
            await until(lambda: not queue.pending_requests(), 10)
            assert len(read_jobs()) == 2
            print("PASS: Identical requests reused the completed jobs.")
            if not live:
                client.fail = True
                save_post("one", "one", "one", 2, "The next event starts tomorrow.", "text", None, "2026-10-09")
                await until(lambda: len(read_jobs()) == 3 and read_jobs()[-1]["status"] == "failed", 10)
                assert read_jobs()[-1]["output_text"] is None
                print("PASS: A connection failure was saved without partial output.")
            with closing(database.get_connection()) as conn:
                originals = conn.execute("SELECT text FROM posts WHERE message_id = 1").fetchall()
                assert all(row[0] == "BREAKING The event starts at ten." for row in originals)
            print("PASS: Original posts are unchanged.")
            print("Worker check passed. Only a temporary database was used.")
        finally:
            try:
                if worker is not None:
                    await worker.stop()
                if live:
                    await client.aclose()
            finally:
                database.DATABASE_NAME = original_database


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Translate two temporary posts using your local Ollama.")
    args = parser.parse_args()
    try:
        asyncio.run(check(args.live))
    except KeyboardInterrupt:
        print("Worker check cancelled.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"Worker check failed: {translation_error_message(error)} ({type(error).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
