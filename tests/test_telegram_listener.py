import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.telegram.supervisor import TelegramAuthorizationRequired


class TelegramListenerTest(unittest.IsolatedAsyncioTestCase):
    def load_listener(self, client):
        # Load the real listener with a fake client: no credentials, session
        # database, or Telegram network requests are used by these tests.
        spec = importlib.util.spec_from_file_location(
            "listener_under_test", Path("app/telegram/telegram_client.py"))
        module = importlib.util.module_from_spec(spec)
        with patch("telethon.TelegramClient", return_value=client), \
                patch("dotenv.load_dotenv"), \
                patch.dict("os.environ", TELEGRAM_API_ID="123", TELEGRAM_API_HASH="test"):
            spec.loader.exec_module(module)
        return module

    def fake_client(self, authorized=True):
        return SimpleNamespace(
            on=lambda event: lambda handler: handler,
            connect=AsyncMock(),
            is_user_authorized=AsyncMock(return_value=authorized),
            catch_up=AsyncMock(),
            start=Mock(side_effect=AssertionError("Never prompt in a service")),
            run_until_disconnected=AsyncMock(side_effect=ConnectionError()),
            disconnect=AsyncMock(),
        )

    async def test_missing_authorization_disconnects_without_prompting(self):
        client = self.fake_client(authorized=False)
        module = self.load_listener(client)
        callback = Mock()
        with self.assertRaises(TelegramAuthorizationRequired):
            await module.start_telegram(on_connected=callback)
        client.start.assert_not_called()
        client.disconnect.assert_awaited_once()
        client.catch_up.assert_not_awaited()
        callback.assert_not_called()

    async def test_authorized_listener_requests_catchup_and_cleans_up_photo_task(self):
        client = self.fake_client()
        module = self.load_listener(client)
        photo_stopped = asyncio.Event()

        async def photo_loop():
            try:
                await asyncio.Event().wait()
            finally:
                photo_stopped.set()

        async def listen():
            await asyncio.sleep(0)
            raise ConnectionError()

        client.run_until_disconnected.side_effect = listen
        callback = Mock()
        with patch.object(module, "ChannelPhotoCache") as cache:
            cache.return_value.run = photo_loop
            with self.assertRaises(ConnectionError):
                await module.start_telegram(on_connected=callback)
        client.start.assert_not_called()
        client.catch_up.assert_awaited_once()
        client.disconnect.assert_awaited_once()
        callback.assert_called_once_with()
        self.assertTrue(photo_stopped.is_set())

    async def test_cancellation_during_connect_disconnects(self):
        client = self.fake_client()
        started = asyncio.Event()

        async def connect():
            started.set()
            await asyncio.Event().wait()

        client.connect.side_effect = connect
        module = self.load_listener(client)
        task = asyncio.create_task(module.start_telegram())
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        client.disconnect.assert_awaited_once()
