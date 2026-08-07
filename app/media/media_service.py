import shutil
from pathlib import Path


MEDIA_ROOT = Path("data/media")


def get_channel_folder(channel_id: str) -> Path:
    """
    Return the folder where this channel's media will be stored.
    Create it if it does not exist.
    """

    folder = MEDIA_ROOT / channel_id

    folder.mkdir(
        parents=True,
        exist_ok=True
    )

    return folder

async def download_media(message, channel_id: str):
    """
    Download one Telegram media file.
    """

    folder = get_channel_folder(channel_id)

    downloaded_file = await message.download_media(
        file=folder
    )

    if downloaded_file is None:
        return None

    original_path = Path(downloaded_file)

    new_path = folder / f"{message.id}{original_path.suffix}"

    shutil.move(
        original_path,
        new_path
    )

    return str(new_path)
