import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, events

from app.database.models import save_post
from app.media.media_service import download_media

# --------------------------------------------------
# Configuration
# --------------------------------------------------

load_dotenv()

api_id = os.getenv("TELEGRAM_API_ID")
api_hash = os.getenv("TELEGRAM_API_HASH")

if not api_id or not api_hash:
    raise ValueError(
        "TELEGRAM_API_ID or TELEGRAM_API_HASH is missing from the .env file."
    )

API_ID = int(api_id)
API_HASH = api_hash

# Ensure the sessions folder exists
Path("sessions").mkdir(exist_ok=True)

client = TelegramClient(
    "sessions/telegram_session",
    API_ID,
    API_HASH,
)


# You can use either channel titles or usernames
ALLOWED_CHANNELS = {
    "testrssn8n",
    "bintjbeilnews",
}


# --------------------------------------------------
# Helper functions
# --------------------------------------------------

def get_message_type(message) -> str:
    """Return the Telegram message type."""

    if message.photo:
        return "photo"
    if message.video:
        return "video"
    if message.voice:
        return "voice"
    if message.audio:
        return "audio"
    if message.document:
        return "document"
    return "text"


def is_allowed_channel(channel) -> bool:
    """Check the channel title and username."""

    channel_title = (getattr(channel, "title", "") or "").lower()
    channel_username = (getattr(channel, "username", "") or "").lower()
    return (
        channel_title in ALLOWED_CHANNELS
        or channel_username in ALLOWED_CHANNELS
    )


async def save_telegram_message(channel, message) -> None:
    """Save one Telegram message to the database."""

    channel_title = getattr(channel, "title", "") or "Unknown channel"
    channel_username = getattr(channel, "username", "") or ""
    channel_id = str(channel.id)
    message_type = get_message_type(message)

    media_path = None
    if message_type != "text":
        try:
            media_path = await download_media(
                message,
                channel_id,
                channel_username,
            )
            print(f"Media saved: {media_path}")
        except Exception as error:
            # Media is optional: preserve the post even when its download fails.
            print(f"⚠️ Media download failed; saving post without media: {error}")

    save_post(
        channel_id=channel_id,
        channel_title=channel_title,
        channel_username=channel_username,
        message_id=message.id,
        text=message.text or "",
        message_type=message_type,
        media_path=media_path,
        date=message.date,
    )

    print("--------------------------------")
    print(f"✅ Saved from: {channel_title}")
    print(f"Username: @{channel_username}" if channel_username else "Username: None")
    print(f"Type: {message_type}")
    print(f"Text: {message.text or '[No text]'}")


# --------------------------------------------------
# New message listener
# --------------------------------------------------

@client.on(events.NewMessage)
async def new_message_handler(event) -> None:
    """Receive and save new messages from allowed channels."""

    channel = await event.get_chat()
    if not is_allowed_channel(channel):
        return
    await save_telegram_message(
        channel=channel,
        message=event.message,
    )


# --------------------------------------------------
# Test one channel
# --------------------------------------------------

async def test_connection(
    channel_username: str = "testRssN8n",
) -> None:
    """Connect to Telegram and save the latest channel message."""

    await client.start()
    try:
        print("✅ Connected successfully!")

        channel = await client.get_entity(channel_username)
        messages = await client.get_messages(channel, limit=1)
        if not messages:
            print("No messages found.")
            return
        latest_message = messages[0]
        print("\nLatest message:\n")
        await save_telegram_message(
            channel=channel,
            message=latest_message,
        )

    finally:
        await client.disconnect()


# --------------------------------------------------
# Start continuous Telegram listener
# --------------------------------------------------

async def start_telegram() -> None:
    """Keep listening for new Telegram messages."""

    await client.start()
    print("✅ Telegram connected.")
    print("Waiting for new messages...")
    await client.run_until_disconnected()
