import tempfile
import unittest
from pathlib import Path

import app.media.media_service as media_service


class FakeMessage:
    id = 90

    def __init__(self):
        self.download_count = 0

    async def download_media(self, file):
        self.download_count += 1
        downloaded_file = file / "telegram-photo.jpg"
        downloaded_file.write_bytes(b"photo")
        return str(downloaded_file)


class MediaServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_media_is_downloaded_once_to_stable_channel_folder(self):
        original_media_root = media_service.MEDIA_ROOT

        with tempfile.TemporaryDirectory() as temp_directory:
            media_service.MEDIA_ROOT = Path(temp_directory)
            try:
                message = FakeMessage()
                media_path = await media_service.download_media(
                    message,
                    channel_id="12345",
                    channel_username="testRssN8n",
                )

                self.assertEqual(message.download_count, 1)
                self.assertEqual(media_path, "testRssN8n/90.jpg")
                self.assertTrue(
                    (Path(temp_directory) / "testRssN8n" / "90.jpg").is_file()
                )
            finally:
                media_service.MEDIA_ROOT = original_media_root

    async def test_channel_id_is_used_when_username_is_missing(self):
        original_media_root = media_service.MEDIA_ROOT

        with tempfile.TemporaryDirectory() as temp_directory:
            media_service.MEDIA_ROOT = Path(temp_directory)
            try:
                message = FakeMessage()
                media_path = await media_service.download_media(
                    message,
                    channel_id="12345",
                )

                self.assertEqual(media_path, "12345/90.jpg")
            finally:
                media_service.MEDIA_ROOT = original_media_root


if __name__ == "__main__":
    unittest.main()
