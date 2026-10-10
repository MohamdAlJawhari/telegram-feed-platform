import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from telethon.errors import FloodWaitError

from app.telegram.channel_photos import ChannelPhotoCache, cached_channel_photo_url, photo_paths, with_channel_photos


class ChannelPhotosTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.patch = patch("app.media.media_service.MEDIA_ROOT", Path(self.directory.name))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.channel = dict(channel_id="123", channel_title="News", channel_username="news", enabled=True)
        self.client = AsyncMock()
        self.client.download_profile_photo.return_value = b"photo"
        self.cache = ChannelPhotoCache(self.client, lambda: [self.channel])

    async def test_download_is_cached_small_and_reused_across_restart(self):
        await self.cache.refresh_channel(self.channel)
        photo, checked = photo_paths("123")
        self.assertEqual(photo.read_bytes(), b"photo")
        self.assertTrue(checked.exists())
        self.assertTrue(cached_channel_photo_url("123").startswith("/media/channel-profiles/"))
        self.client.download_profile_photo.assert_awaited_once_with(self.client.get_entity.return_value, file=bytes, download_big=False)
        await ChannelPhotoCache(self.client, lambda: []).refresh_channel(self.channel)
        self.assertEqual(self.client.download_profile_photo.await_count, 1)
        self.assertEqual(list(photo.parent.glob("*.tmp")), [])

    async def test_removed_photo_clears_cache_and_negative_lookup_is_cached(self):
        await self.cache.refresh_channel(self.channel)
        _, checked = photo_paths("123")
        os.utime(checked, (0, 0))
        self.client.download_profile_photo.return_value = None
        await self.cache.refresh_channel(self.channel)
        self.assertIsNone(cached_channel_photo_url("123"))
        await self.cache.refresh_channel(self.channel)
        self.assertEqual(self.client.download_profile_photo.await_count, 2)

    async def test_failure_keeps_old_photo_and_backs_off_without_blocking(self):
        await self.cache.refresh_channel(self.channel)
        photo, checked = photo_paths("123")
        os.utime(checked, (0, 0))
        self.client.download_profile_photo.side_effect = asyncio.TimeoutError()
        with self.assertLogs("app.telegram.channel_photos", level="WARNING"):
            await self.cache.refresh_channel(self.channel)
        self.assertEqual(photo.read_bytes(), b"photo")
        await self.cache.refresh_channel(self.channel)
        self.assertEqual(self.client.download_profile_photo.await_count, 2)

    async def test_rate_limit_pauses_other_channel_requests(self):
        self.client.get_entity.side_effect = FloodWaitError(request=None, capture=60)
        with self.assertLogs("app.telegram.channel_photos", level="WARNING"):
            await self.cache.refresh_channel(self.channel)
        await self.cache.refresh_channel(dict(self.channel, channel_id="456"))
        self.assertEqual(self.client.get_entity.await_count, 1)

    async def test_cancellation_propagates_and_invalid_download_does_not_mark_success(self):
        self.client.download_profile_photo.return_value = "unexpected path"
        with self.assertLogs("app.telegram.channel_photos", level="WARNING"):
            await self.cache.refresh_channel(self.channel)
        self.assertIsNone(cached_channel_photo_url("123"))
        self.assertFalse(photo_paths("123")[1].exists())
        self.cache.retry_after.clear()
        self.client.get_entity.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.cache.refresh_channel(self.channel)

    async def test_safe_filenames_and_local_presentation_without_telegram_calls(self):
        self.assertEqual(photo_paths("../escape")[0].parent, Path(self.directory.name) / "channel-profiles")
        self.assertIsNone(with_channel_photos([self.channel])[0]["avatar_url"])
        self.client.get_entity.assert_not_called()
        await self.cache.refresh_channel(self.channel)
        presented = with_channel_photos([self.channel])[0]
        self.assertTrue(presented["avatar_url"])
        self.assertNotIn("avatar_url", self.channel)
