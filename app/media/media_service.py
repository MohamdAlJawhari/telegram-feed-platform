import os
import shutil
from pathlib import Path


MEDIA_ROOT = Path("data/media")

def get_media_root() -> Path:
    """
    Returns the root media folder.
    """
    return MEDIA_ROOT


def build_local_media_path(relative_path: str) -> Path:
    """
    Convert: testRssN8n/88.jpg
    into: data/media/testRssN8n/88.jpg
    """
    return MEDIA_ROOT / relative_path

def build_local_media_url(media_path: str) -> str:
    return f"/media/{media_path}"

def build_public_media_url(relative_path: str) -> str:
    """
    Convert: testRssN8n/88.jpg
    into: http://127.0.0.1:8000/media/testRssN8n/88.jpg
    """

    base_url = os.getenv("BASE_URL", "http://127.0.0.1:8000")
    return f"{base_url}/media/{relative_path}"


def get_relative_media_path(channel_key: str, filename: str) -> str:
    """
    Returns the path that will be stored in the database.
    Example:
        testRssN8n/88.jpg
    """
    return f"{channel_key}/{filename}"

def get_channel_folder(channel_key: str) -> Path:
    """
    Return the folder where this channel's media will be stored.
    Create it if it does not exist.
    """

    folder = MEDIA_ROOT / channel_key
    folder.mkdir(
        parents=True,
        exist_ok=True
    )
    return folder

async def download_media(message, channel_id: str, channel_username: str = "",):
    """
    Download one Telegram media file.
    """

    # Public usernames are readable and globally unique. Channels without a
    # username fall back to their stable Telegram ID.
    channel_key = channel_username or channel_id
    folder = get_channel_folder(channel_key)

    downloaded_file = await message.download_media(
        file=folder
    )

    if downloaded_file is None:
        return None

    original_path = Path(downloaded_file)

    filename = f"{message.id}{original_path.suffix}"
    new_path = folder / filename
    if original_path != new_path:
        shutil.move(original_path, new_path)

    # Return the relative path instead of the absolute/local path
    return get_relative_media_path(channel_key, filename)
