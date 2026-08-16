from app.telegram.telegram_client import client


async def get_channel_information(username: str):

    channel = await client.get_entity(username)

    return {
        "channel_id": str(channel.id),
        "channel_title": channel.title,
        "channel_username": channel.username or "",
    }