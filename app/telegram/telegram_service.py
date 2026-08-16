from app.telegram.telegram_client import client
from app.utils.telegram import normalize_username


async def get_channel_information(username: str):

    username = normalize_username(username)

    channel = await client.get_entity(username)
    
    return {
        "channel_id": str(channel.id),
        "channel_title": channel.title,
        "channel_username": channel.username or "",
    }