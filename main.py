# http://127.0.0.1:8000/channels
import asyncio

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from app.database.database import create_database
from app.database.models import get_all_posts
from app.rss.rss import generate_rss
from app.telegram.telegram_client import start_telegram
from app.api.channels import router as channels_router

app = FastAPI()

app.include_router(channels_router)

MEDIA_FOLDER = Path("data/media")
MEDIA_FOLDER.mkdir(parents=True, exist_ok=True)

app.mount(
    "/media",
    StaticFiles(directory=MEDIA_FOLDER),
    name="media",
)

@app.on_event("startup")
async def startup_event():
    create_database()
    asyncio.create_task(start_telegram())
    
@app.get("/")
def home():
    return {
        "status": "running",
        "message": "Telegram Feed Platform is working!"
    }

@app.get("/posts")
def posts():

    posts = get_all_posts()

    result = []

    for post in posts:

        result.append({
            "channel_id": post[0],
            "channel_title": post[1],
            "channel_username": post[2],
            "message_id": post[3],
            "text": post[4],
            "message_type": post[5],
            "media_path": post[6],
            "date": post[7]
        })

    return result

@app.get("/rss/{channel_name}")
def rss(channel_name: str):

    return Response(
        content=generate_rss(channel_name),
        media_type="application/rss+xml"
    )


@app.get("/debug")
def debug():
    return get_all_posts()
