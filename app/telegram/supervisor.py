"""Recover the Telegram listener independently of the HTTP server."""

import asyncio
from datetime import datetime, timezone
import logging
import time

from telethon.errors import AuthKeyDuplicatedError, UnauthorizedError


logger = logging.getLogger(__name__)


class TelegramAuthorizationRequired(RuntimeError):
    """The saved session needs an administrator to sign in interactively."""


class TelegramSupervisor:
    def __init__(self, listener, is_connected, *, retry_seconds=5,
                 max_retry_seconds=60, stable_seconds=60,
                 sleep=asyncio.sleep, clock=time.monotonic):
        self.listener = listener
        self.is_connected = is_connected
        self.retry_seconds = retry_seconds
        self.max_retry_seconds = max_retry_seconds
        self.stable_seconds = stable_seconds
        self.sleep = sleep
        self.clock = clock
        self.task = None
        self.state = "stopped"
        self.restart_count = 0
        self.last_connected_at = None
        self.connected_since = None

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run(), name="telegram-supervisor")
        return self.task

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.state = "stopped"

    def mark_connected(self):
        self.state = "connected"
        self.connected_since = self.clock()
        self.last_connected_at = datetime.now(timezone.utc).isoformat()

    def status(self):
        state = self.state
        if state == "connected":
            if self.task is None or self.task.done():
                state = "stopped"
            elif not self.is_connected():
                state = "reconnecting"
        messages = {
            "stopped": "Telegram listener is stopped. Existing RSS posts remain available.",
            "connecting": "Connecting to Telegram. Existing RSS posts remain available.",
            "connected": "Connected to Telegram and listening for new posts.",
            "reconnecting": "Reconnecting to Telegram. Existing RSS posts remain available.",
            "authorization_required": "Telegram sign-in is required. Stop the application service, sign in locally, then restart it. Existing RSS posts remain available.",
        }
        return {
            "status": state,
            "message": messages[state],
            "restart_count": self.restart_count,
            "last_connected_at": self.last_connected_at,
        }

    async def run(self):
        delay = self.retry_seconds
        try:
            while True:
                self.state = "connecting"
                self.connected_since = None
                try:
                    await self.listener(on_connected=self.mark_connected)
                    # A listener returning normally is also an unexpected exit.
                    raise ConnectionError("Telegram listener returned")
                except asyncio.CancelledError:
                    raise
                except (TelegramAuthorizationRequired, UnauthorizedError,
                        AuthKeyDuplicatedError) as error:
                    self.state = "authorization_required"
                    logger.error("Telegram sign-in is required. Stop the application service, sign in locally, then restart it. (%s)", type(error).__name__)
                    # Retrying cannot repair a revoked session. Keep HTTP/RSS alive
                    # and wait for an administrator to authorize and restart.
                    await asyncio.Event().wait()
                except Exception as error:
                    if (self.connected_since is not None
                            and self.clock() - self.connected_since >= self.stable_seconds):
                        delay = self.retry_seconds
                    self.state = "reconnecting"
                    self.restart_count += 1
                    logger.warning("Telegram listener disconnected; retrying in %s seconds. Existing RSS posts remain available. (%s)", delay, type(error).__name__)
                    await self.sleep(delay)
                    delay = min(delay * 2, self.max_retry_seconds)
        finally:
            self.state = "stopped"
