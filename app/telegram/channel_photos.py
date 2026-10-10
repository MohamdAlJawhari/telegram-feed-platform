"""Optional channel avatars: local reads for the UI, background Telegram refresh."""

import asyncio
import logging
import tempfile
import time
from hashlib import sha256
from pathlib import Path

from telethon.errors import FloodWaitError
from telethon.tl.types import PeerChannel

from app.media.media_service import get_media_root

logger = logging.getLogger(__name__)


def photo_paths(channel_id):
    # Stable IDs, rather than user-controlled usernames, form cache filenames.
    key = sha256(str(channel_id).encode()).hexdigest()
    folder = get_media_root() / "channel-profiles"
    return folder / f"{key}.jpg", folder / f"{key}.checked"


def cached_channel_photo_url(channel_id):
    photo, _ = photo_paths(channel_id)
    try:
        stat = photo.stat()
        if stat.st_size:
            return f"/media/channel-profiles/{photo.name}?v={stat.st_mtime_ns}"
    except OSError:
        pass
    return None


def with_channel_photos(channels):
    return [dict(channel, avatar_url=cached_channel_photo_url(channel["channel_id"])) for channel in map(dict, channels)]


class ChannelPhotoCache:
    def __init__(self, client, get_channels, *, refresh_seconds=86400, retry_seconds=300):
        self.client = client
        self.get_channels = get_channels
        self.refresh_seconds = refresh_seconds
        self.retry_seconds = retry_seconds
        self.retry_after = {}
        self.telegram_retry_after = 0

    async def refresh_channel(self, channel):
        channel_id = str(channel["channel_id"])
        now = time.time()
        if now < max(self.telegram_retry_after, self.retry_after.get(channel_id, 0)):
            return
        photo, checked = photo_paths(channel_id)
        try:
            if now - checked.stat().st_mtime < self.refresh_seconds:
                return
        except OSError:
            pass
        try:
            async def download():
                entity = await self.client.get_entity(channel["channel_username"] or PeerChannel(int(channel_id)))
                return await self.client.download_profile_photo(entity, file=bytes, download_big=False)

            contents = await asyncio.wait_for(download(), timeout=15)
            if contents is not None and (not isinstance(contents, bytes) or not contents):
                raise ValueError("Invalid profile photo response")
            photo.parent.mkdir(parents=True, exist_ok=True)
            if contents is None:
                photo.unlink(missing_ok=True)
            else:
                # Never expose partially downloaded files to a browser.
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=photo.parent, suffix=".tmp", delete=False) as output:
                        temporary = Path(output.name)
                        output.write(contents)
                    temporary.replace(photo)
                finally:
                    if temporary:
                        temporary.unlink(missing_ok=True)
            checked.touch()
            self.retry_after.pop(channel_id, None)
        except FloodWaitError as error:
            self.telegram_retry_after = time.time() + max(error.seconds, self.retry_seconds)
            logger.warning("Channel photo refresh paused by Telegram's rate limit.")
        except Exception:
            self.retry_after[channel_id] = time.time() + self.retry_seconds
            logger.warning("Could not refresh profile photo for channel %s; keeping its cached avatar.", channel_id)

    async def run(self):
        while True:
            try:
                if self.client.is_connected():
                    channels = [dict(channel) for channel in self.get_channels()]
                    ids = {str(channel["channel_id"]) for channel in channels}
                    self.retry_after = {key: value for key, value in self.retry_after.items() if key in ids}
                    for channel in channels:
                        await self.refresh_channel(channel)
                        await asyncio.sleep(.25)
            except Exception:
                logger.warning("Channel photo refresh unavailable; news delivery continues.")
            await asyncio.sleep(60)
