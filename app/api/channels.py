from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database.models import (
    get_enabled_channels,
    upsert_channel,
    set_channel_enabled,
    delete_channel,

)
from app.telegram.telegram_service import (
    get_channel_information,
)

class ChannelCreate(BaseModel):
    username: str

class ChannelUpdate(BaseModel):
    enabled: bool

router = APIRouter(
    prefix="/channels",
    tags=["Channels"],
)

@router.get("/")
def get_channels():

    rows = get_enabled_channels()

    result = []

    for channel in rows:

        result.append({
            "channel_id": channel[0],
            "channel_title": channel[1],
            "channel_username": channel[2],
        })

    return result

@router.post("/")
async def create_channel(channel: ChannelCreate):

    try:
        telegram_channel = await get_channel_information(
            channel.username
        )

    except Exception:
        raise HTTPException(
            status_code=404,
            detail="Telegram channel not found."
        )

    upsert_channel(
        channel_id=telegram_channel["channel_id"],
        channel_title=telegram_channel["channel_title"],
        channel_username=telegram_channel["channel_username"],
    )

    return {
        "message": "Channel added successfully.",
        "channel": telegram_channel,
    }

@router.patch("/{channel_id}")
def update_channel(
    channel_id: str,
    channel: ChannelUpdate,
):

    set_channel_enabled(
        channel_id=channel_id,
        enabled=channel.enabled,
    )

    return {
        "message": "Channel updated successfully."
    }

@router.delete("/{channel_id}")
def remove_channel(channel_id: str):

    delete_channel(channel_id)

    return {
        "message": "Channel deleted successfully."
    }