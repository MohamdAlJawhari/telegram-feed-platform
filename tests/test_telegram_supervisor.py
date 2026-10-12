import asyncio
import unittest
from unittest.mock import AsyncMock

from telethon.errors import SessionRevokedError

from app.telegram.supervisor import TelegramAuthorizationRequired, TelegramSupervisor


class TelegramSupervisorTest(unittest.IsolatedAsyncioTestCase):
    async def test_retries_are_serial_and_backoff_is_capped(self):
        delays = []
        attempts = 0
        waiting = asyncio.Event()

        async def listener(on_connected):
            nonlocal attempts
            attempts += 1
            raise OSError("private error detail must not appear in health")

        async def sleep(delay):
            delays.append(delay)
            if len(delays) == 4:
                waiting.set()
                await asyncio.Event().wait()

        supervisor = TelegramSupervisor(listener, lambda: False,
                                        max_retry_seconds=20, sleep=sleep)
        supervisor.start()
        try:
            await asyncio.wait_for(waiting.wait(), 1)
            self.assertEqual(delays, [5, 10, 20, 20])
            self.assertEqual(attempts, 4)
            self.assertEqual(supervisor.status()["restart_count"], 4)
            self.assertNotIn("private", str(supervisor.status()))
        finally:
            await supervisor.stop()
        self.assertTrue(supervisor.task.cancelled())
        self.assertEqual(supervisor.status()["status"], "stopped")

    async def test_normal_exit_recovers_and_cancellation_cleans_up_once(self):
        attempts = 0
        connected = False
        cleanup = []
        ready = asyncio.Event()

        async def listener(on_connected):
            nonlocal attempts, connected
            attempts += 1
            if attempts == 1:
                return
            connected = True
            on_connected()
            ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                connected = False
                cleanup.append(True)

        supervisor = TelegramSupervisor(listener, lambda: connected, sleep=AsyncMock())
        task = supervisor.start()
        self.assertIs(supervisor.start(), task)
        try:
            await asyncio.wait_for(ready.wait(), 1)
            self.assertEqual(supervisor.status()["status"], "connected")
            self.assertIsNotNone(supervisor.status()["last_connected_at"])
            connected = False
            self.assertEqual(supervisor.status()["status"], "reconnecting")
        finally:
            await supervisor.stop()
        self.assertEqual(attempts, 2)
        self.assertEqual(cleanup, [True])

    async def test_stable_connection_resets_backoff(self):
        attempts = 0
        now = [0]
        delays = []
        waiting = asyncio.Event()

        async def listener(on_connected):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                on_connected()
                now[0] = 61
            raise ConnectionError()

        async def sleep(delay):
            delays.append(delay)
            if len(delays) == 2:
                waiting.set()
                await asyncio.Event().wait()

        supervisor = TelegramSupervisor(listener, lambda: False,
                                        clock=lambda: now[0], sleep=sleep)
        supervisor.start()
        try:
            await asyncio.wait_for(waiting.wait(), 1)
            self.assertEqual(delays, [5, 5])
        finally:
            await supervisor.stop()

    async def test_authorization_failure_waits_for_admin_without_retrying(self):
        for error in (TelegramAuthorizationRequired(), SessionRevokedError(request=None)):
            with self.subTest(error=type(error).__name__):
                listener = AsyncMock(side_effect=error)
                sleep = AsyncMock()
                supervisor = TelegramSupervisor(listener, lambda: False, sleep=sleep)
                supervisor.start()
                try:
                    await asyncio.sleep(0)
                    self.assertEqual(supervisor.status()["status"], "authorization_required")
                    self.assertFalse(supervisor.task.done())
                    listener.assert_awaited_once()
                    sleep.assert_not_awaited()
                finally:
                    await supervisor.stop()
