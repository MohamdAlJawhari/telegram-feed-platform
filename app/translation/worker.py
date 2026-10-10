"""Bounded concurrent translation jobs, independent of Telegram and HTTP requests."""

import asyncio
import json
import logging
import math
import os
from functools import partial
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from app.database import database
from app.dashboard.translation_errors import translation_error_message
from app.llm.ollama_client import OllamaClient, OllamaConfig
from app.translation import queue, store
from app.translation import activity
from app.translation.identity import remember_model
from app.translation.capacity import AutomaticCapacity, MemoryMonitor, MemoryReading, MAX_AUTOMATIC_SLOTS
from app.translation.service import TranslationConfig, TranslationConfigurationError, TranslationService


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerConfig:
    concurrency: int = 2
    poll_seconds: float = 2.0
    job_timeout: float = 600.0
    automatic: bool = False

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
                MAX_AUTOMATIC_SLOTS,
                float(os.getenv("TRANSLATION_POLL_SECONDS", "2")),
                float(os.getenv("TRANSLATION_JOB_TIMEOUT", "600")),
                automatic=True,
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
    def __init__(self, *, client=None, translation_config=None, worker_config=None, memory_reader=None):
        self.client = client
        self.owns_client = client is None
        self.translation_config = translation_config
        self.config = worker_config
        self.lock = WorkerLock()
        self.task = None
        self.jobs = set()
        self.digest = None
        self.error_message = None
        self.capacity = AutomaticCapacity()
        self.memory_reader = memory_reader
        self.memory = MemoryReading("unknown")
        self.effective_limit = 0
        self.last_job_error = None

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
                    if self.memory_reader is None:
                        base_url = getattr(getattr(self.client, "config", None), "base_url", "")
                        local = urlparse(base_url).hostname in ("127.0.0.1", "localhost", "::1")
                        self.memory_reader = MemoryMonitor(local=local).sample
                    self.jobs = {task for task in self.jobs if not task.done()}
                    await self._publish("checking")
                    model = await self.client.require_model(self.translation_config.model)
                    digest = model.get("digest")
                    store._validate_digest(digest)
                    await self._remember_model(digest)
                    if digest != self.digest:
                        await database_call(queue.seed_requests)
                        self.digest = digest
                    await self._reconcile()
                    self.error_message = None
                    self.jobs = {task for task in self.jobs if not task.done()}
                    counts = await database_call(activity.queue_counts)
                    if self.config.automatic:
                        try:
                            self.memory = await database_call(self.memory_reader)
                            if not isinstance(self.memory, MemoryReading):
                                raise ValueError("Invalid memory reading")
                        except Exception:
                            self.memory = MemoryReading("unknown", "Memory monitoring is unavailable; using one translation at a time.")
                        self.effective_limit = self.capacity.update(
                            counts["channels"], counts["waiting"] + len(self.jobs), self.memory,
                        )
                    else:
                        self.effective_limit = self.config.concurrency
                    while len(self.jobs) < self.effective_limit:
                        claim = partial(store.claim_translation, active_only=self.config.automatic)
                        job = await database_call(claim, self.translation_config, digest)
                        if job is None:
                            break
                        if len(self.jobs) >= self.effective_limit:
                            await database_call(queue.release_job, job["id"], job["claim_token"])
                            break
                        task = asyncio.create_task(self._run_job(job), name=f"translation-{job['id']}")
                        task.add_done_callback(self._observe_job)
                        self.jobs.add(task)
                    await self._publish("ready")
                except Exception as error:
                    message = translation_error_message(error)
                    if message != self.error_message:
                        logger.warning("Translation queue paused: %s (%s)", message, type(error).__name__)
                    self.error_message = message
                    try:
                        await self._publish("paused")
                    except Exception:
                        logger.exception("Could not publish translation activity")
                await asyncio.sleep(self.config.poll_seconds if self.config else 2)
        finally:
            for task in self.jobs:
                task.cancel()
            await asyncio.gather(*self.jobs, return_exceptions=True)
            self.jobs.clear()
            try:
                if self.lock.file is not None:
                    await database_call(queue.recover_running_jobs)
                    await self._publish("stopped")
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

    async def _publish(self, state):
        counts = await database_call(activity.queue_counts)
        running = sum(not task.done() for task in self.jobs)
        if state == "stopped":
            message = "Background translation is stopped. RSS remains available."
        elif self.error_message:
            message = self.error_message
        elif counts["failed"]:
            message = self.last_job_error or "Some posts could not be translated. RSS remains available."
        elif running or counts["waiting"]:
            message = f"Translating {running} posts; {counts['waiting']} waiting."
        else:
            message = "Translation is idle."
        memory_note = self.memory.note
        if self.capacity.pressure == "resource_failure":
            memory_note = "New translations are paused briefly after a memory-related failure."
        await database_call(activity.publish_activity, {
            "state": state, "automatic": self.config.automatic if self.config else True,
            "running": running, **counts, "limit": self.effective_limit,
            "maximum": MAX_AUTOMATIC_SLOTS if self.config and self.config.automatic else (self.config.concurrency if self.config else 4),
            "memory_state": self.memory.state, "memory_note": memory_note,
            "message": message,
            "connection_error": self.error_message,
        })

    async def _reconcile(self):
        for request in await database_call(queue.pending_requests):
            try:
                await database_call(store.enqueue_translation, request["post_id"], self.translation_config, self.digest)
            except TranslationConfigurationError as error:
                logger.warning("Cannot queue translation for post %s: %s", request["post_id"], error)
            await database_call(queue.acknowledge_request, request["post_id"], request["revision"])

    async def _remember_model(self, digest):
        connection = getattr(self.client, "config", None) or OllamaConfig.from_env()
        await database_call(remember_model, connection.base_url, self.translation_config.model, digest)

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
                await self._remember_model(model.get("digest", ""))
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
            self.last_job_error = translation_error_message(error)
            if self.config.automatic and any(fragment in str(error).lower() for fragment in (
                "out of memory", "not enough memory", "requires more system memory", "failed to allocate",
            )):
                self.capacity.resource_failure()
                self.effective_limit = 0
            try:
                await database_call(store.fail_translation, job["id"], job["claim_token"], error)
            except Exception:
                logger.exception("Could not persist failure for translation job %s", job["id"])
                # Leave it pending if failure persistence becomes possible again.
                await database_call(queue.release_job, job["id"], job["claim_token"])
            logger.warning("Translation job %s failed: %s (%s)", job["id"], translation_error_message(error), type(error).__name__)
