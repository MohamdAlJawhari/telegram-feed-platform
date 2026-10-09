"""Bounded concurrent translation jobs, independent of Telegram and HTTP requests."""

import asyncio
import json
import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from app.database import database
from app.dashboard.translation_errors import translation_error_message
from app.llm.ollama_client import OllamaClient
from app.translation import queue, store
from app.translation.service import TranslationConfig, TranslationConfigurationError, TranslationService


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerConfig:
    concurrency: int = 2
    poll_seconds: float = 2.0
    job_timeout: float = 600.0

    def __post_init__(self):
        if type(self.concurrency) is not int or not 1 <= self.concurrency <= 8:
            raise TranslationConfigurationError("TRANSLATION_CONCURRENCY must be between 1 and 8.")
        for value in (self.poll_seconds, self.job_timeout):
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise TranslationConfigurationError("Worker polling and job timeouts must be positive finite seconds.")

    @classmethod
    def from_env(cls):
        load_dotenv()
        try:
            return cls(
                int(os.getenv("TRANSLATION_CONCURRENCY", "2")),
                float(os.getenv("TRANSLATION_POLL_SECONDS", "2")),
                float(os.getenv("TRANSLATION_JOB_TIMEOUT", "600")),
            )
        except ValueError as error:
            raise TranslationConfigurationError("Translation worker settings must be numeric.") from error


class WorkerLock:
    """OS-released ownership protects recovery across reloads/processes."""

    def __init__(self):
        self.file = None

    def acquire(self):
        path = Path(str(Path(database.DATABASE_NAME).resolve()) + ".translation.lock")
        handle = path.open("a+b")
        try:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.file = handle
        return True

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file = None


async def database_call(function, *args):
    """Finish a short transaction before cancellation can release worker ownership."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class TranslationWorker:
    def __init__(self, *, client=None, translation_config=None, worker_config=None):
        self.client = client
        self.owns_client = client is None
        self.translation_config = translation_config
        self.config = worker_config
        self.lock = WorkerLock()
        self.task = None
        self.jobs = set()
        self.digest = None
        self.error_message = None

    async def start(self):
        # Configuration/network failures belong to the supervisor, not app startup.
        if self.task is None:
            self.task = asyncio.create_task(self._supervise(), name="translation-supervisor")
            self.task.add_done_callback(self._observe_job)

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def _supervise(self):
        try:
            # Only one manager owns this database. Retry startup storage failures
            # without bringing down Telegram or the dashboard.
            while True:
                try:
                    if self.lock.file is None and not self.lock.acquire():
                        await asyncio.sleep(2)
                        continue
                    await database_call(queue.recover_running_jobs)
                    await database_call(queue.seed_requests)
                    break
                except Exception:
                    logger.exception("Translation queue initialization failed; retrying")
                    await asyncio.sleep(2)
            while True:
                try:
                    if self.config is None:
                        self.config = WorkerConfig.from_env()
                    if self.translation_config is None:
                        self.translation_config = TranslationConfig.from_env()
                    if self.client is None:
                        self.client = OllamaClient()
                    model = await self.client.require_model(self.translation_config.model)
                    digest = model.get("digest")
                    store._validate_digest(digest)
                    if digest != self.digest:
                        await database_call(queue.seed_requests)
                        self.digest = digest
                    await self._reconcile()
                    self.error_message = None
                    self.jobs = {task for task in self.jobs if not task.done()}
                    while len(self.jobs) < self.config.concurrency:
                        job = await database_call(store.claim_translation, self.translation_config, digest)
                        if job is None:
                            break
                        task = asyncio.create_task(self._run_job(job), name=f"translation-{job['id']}")
                        task.add_done_callback(self._observe_job)
                        self.jobs.add(task)
                except Exception as error:
                    message = translation_error_message(error)
                    if message != self.error_message:
                        logger.warning("Translation queue paused: %s (%s)", message, type(error).__name__)
                    self.error_message = message
                await asyncio.sleep(self.config.poll_seconds if self.config else 2)
        finally:
            for task in self.jobs:
                task.cancel()
            await asyncio.gather(*self.jobs, return_exceptions=True)
            self.jobs.clear()
            try:
                if self.lock.file is not None:
                    await database_call(queue.recover_running_jobs)
            finally:
                try:
                    if self.client is not None and self.owns_client:
                        await self.client.aclose()
                        self.client = None
                finally:
                    self.lock.close()

    @staticmethod
    def _observe_job(task):
        if not task.cancelled() and task.exception() is not None:
            logger.error("Translation worker task stopped unexpectedly", exc_info=task.exception())

    async def _reconcile(self):
        for request in await database_call(queue.pending_requests):
            try:
                await database_call(store.enqueue_translation, request["post_id"], self.translation_config, self.digest)
            except TranslationConfigurationError as error:
                logger.warning("Cannot queue translation for post %s: %s", request["post_id"], error)
            await database_call(queue.acknowledge_request, request["post_id"], request["revision"])

    async def _run_job(self, job):
        try:
            async def generate_and_save():
                snapshot = json.loads(job["snapshot_json"])
                service = TranslationService(self.client, self.translation_config)
                body = await service.translate(
                    job["processed_text"], snapshot["source_language"], snapshot["target_language"],
                )
                # Refresh the installed alias before accepting output from a long call.
                model = await self.client.require_model(self.translation_config.model)
                saved = await database_call(
                    store.complete_translation, job["id"], job["claim_token"], body,
                    self.translation_config, model.get("digest", ""),
                )
                logger.info("Translation job %s %s", job["id"], "completed" if saved else "superseded")
            await asyncio.wait_for(generate_and_save(), timeout=self.config.job_timeout)
        except asyncio.CancelledError:
            await database_call(queue.release_job, job["id"], job["claim_token"])
            raise
        except Exception as error:
            try:
                await database_call(store.fail_translation, job["id"], job["claim_token"], error)
            except Exception:
                logger.exception("Could not persist failure for translation job %s", job["id"])
                # Leave it pending if failure persistence becomes possible again.
                await database_call(queue.release_job, job["id"], job["claim_token"])
            logger.warning("Translation job %s failed: %s (%s)", job["id"], translation_error_message(error), type(error).__name__)
