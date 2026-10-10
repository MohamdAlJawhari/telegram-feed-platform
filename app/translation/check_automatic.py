"""Check automatic concurrency in a temporary database, without Telegram."""

import argparse
import asyncio
import io
import logging
import sys
import tempfile
from contextlib import closing, redirect_stdout
from pathlib import Path

from app.database import database
from app.database.models import save_post, update_channel_settings, upsert_channel
from app.dashboard.translation_errors import translation_error_message
from app.llm.ollama_client import OllamaClient
from app.translation.activity import get_activity
from app.translation.capacity import AutomaticCapacity, MemoryReading
from app.translation.check_workers import DemoClient, read_jobs, until
from app.translation.service import TranslationConfig
from app.translation.worker import TranslationWorker, WorkerConfig


class DemoCapacity(AutomaticCapacity):
    """Advance the clock explicitly to keep the offline check short."""
    now = 0

    def update(self, *args, **kwargs):
        return super().update(*args, now=self.now)


async def check(live):
    original_database = database.DATABASE_NAME
    client = OllamaClient() if live else DemoClient()
    worker = None
    with tempfile.TemporaryDirectory(prefix="automatic-translation-check-") as directory:
        database.DATABASE_NAME = str(Path(directory) / "check.db")
        try:
            with redirect_stdout(io.StringIO()):
                database.create_database()
                for channel in range(1, 11):
                    upsert_channel(str(channel), str(channel), str(channel))
                    update_channel_settings(str(channel), "", "", "", "", True, "en", "fr")
                    save_post(str(channel), str(channel), str(channel), 1,
                              "The city library will reopen on Monday after repairs.", "text", None, "2026-10-09")
            config = TranslationConfig.from_env() if live else TranslationConfig()
            await client.require_model(config.model)
            worker = TranslationWorker(client=client, translation_config=config,
                                       worker_config=WorkerConfig.from_env() if live else
                                       WorkerConfig(poll_seconds=0.05, automatic=True))
            if not live:
                print("SIMULATED model, memory readings and clock; no hardware capacity is measured.")
                memory = [MemoryReading("healthy")]
                worker.memory_reader = lambda: memory[0]
                worker.capacity = DemoCapacity()
                client.gate.clear()
            else:
                print("Live model and memory readings; waiting for ten temporary channel jobs...", flush=True)
            await worker.start()
            if not live:
                await until(lambda: client.active == 1, 10)
                for limit in (2, 3, 4):
                    worker.capacity.now = (limit - 1) * 2
                    await until(lambda: client.active == limit, 10)
                print("PASS: Ten translation-enabled channels ramped from one to four jobs.")
                memory[0] = MemoryReading("critical", "Simulated critical RAM pressure.")
                await until(lambda: worker.effective_limit == 0, 10)
                assert client.active == 4
                print("PASS: Critical memory paused new starts without cancelling running jobs.")
                client.gate.set()
                await until(lambda: client.active == 0, 10)
                assert sum(job["status"] == "pending" for job in read_jobs()) == 6
                print("PASS: Four jobs completed while six stayed queued.")
                memory[0] = MemoryReading("healthy")
                worker.capacity.now = 30
                await until(lambda: worker.effective_limit > 0, 10)
            await until(lambda: len(read_jobs()) == 10 and all(
                job["status"] in ("completed", "failed") for job in read_jobs()
            ), 620 if live else 10)
            jobs = read_jobs()
            assert all(job["status"] == "completed" for job in jobs), "One or more translations failed; see warnings above."
            with closing(database.get_connection()) as conn:
                assert conn.execute("SELECT COUNT(*) FROM posts WHERE text = ?", (
                    "The city library will reopen on Monday after repairs.",
                )).fetchone()[0] == 10
            print("PASS: All ten posts translated and saved; original text was preserved.")
            if live:
                print("Memory state:", worker.memory.state)
                if worker.memory.note:
                    print(worker.memory.note)
                print("Example translation:", jobs[0]["output_text"])
            else:
                assert client.maximum == 4
                print("PASS: Memory recovery resumed queued work, with no more than four simultaneous jobs.")
            status = get_activity()
            assert status["automatic"]
            print("PASS: Dashboard activity uses the shared automatic worker status.")
            print("Automatic concurrency check passed. Only a temporary database was used.")
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
    parser.add_argument("--live", action="store_true", help="Use your local Ollama and real memory readings.")
    args = parser.parse_args()
    try:
        asyncio.run(check(args.live))
    except KeyboardInterrupt:
        print("Automatic concurrency check cancelled.", file=sys.stderr)
        return 130
    except Exception as error:
        message = str(error) if isinstance(error, AssertionError) else translation_error_message(error)
        print(f"Automatic concurrency check failed: {message} ({type(error).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
