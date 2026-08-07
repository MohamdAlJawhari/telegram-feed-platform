# http://127.0.0.1:8000/channels
import asyncio

from fastapi import FastAPI
from fastapi.responses import Response

from app.database.models import get_all_posts
from app.rss.rss import generate_rss
from app.telegram.telegram_client import start_telegram

app = FastAPI()

@app.on_event("startup")
async def startup_event():

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
            "channel_title": post[0],
            "channel_username": post[1],
            "message_id": post[2],
            "text": post[3],
            "date": post[4]
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