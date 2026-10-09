"""Channel settings and translation preview, independent of Telegram startup."""

import asyncio
import logging
import re
import sqlite3

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.database.models import get_channel, get_channel_settings, update_channel_settings
from app.llm.ollama_client import OllamaClient, OllamaConfig, OllamaError
from app.processing.processor import process_body, add_affixes
from app.dashboard.translation_errors import translation_error_message
from app.translation.languages import LANGUAGES, normalize_language
from app.translation.service import TranslationConfig, TranslationError, TranslationService


router = APIRouter()
templates = Jinja2Templates(directory="app/dashboard/templates")
logger = logging.getLogger(__name__)


def require_channel(channel_id):
    channel = get_channel(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found.")
    return channel


def render_settings(request, channel, settings, *, error=None, saved=False, status_code=200):
    return templates.TemplateResponse(
        request=request, name="channel_settings.html",
        context={
            "channel_id": channel["channel_id"], "channel": channel,
            "settings": settings, "languages": sorted(LANGUAGES.items(), key=lambda item: item[1]),
            "language_codes": LANGUAGES, "error": error, "saved": saved,
        }, status_code=status_code,
    )


@router.get("/dashboard/settings/{channel_id}")
def channel_settings_page(request: Request, channel_id: str, saved: bool = False):
    channel = require_channel(channel_id)
    return render_settings(request, channel, get_channel_settings(channel_id), saved=saved)


@router.post("/dashboard/settings/{channel_id}")
def save_channel_settings(
    request: Request, channel_id: str,
    prefix: str = Form(""), suffix: str = Form(""),
    remove_keywords: str = Form(""), replace_words: str = Form(""),
    translation_enabled: bool = Form(False),
    source_language: str = Form("en"), target_language: str = Form("fr"),
):
    channel = require_channel(channel_id)
    settings = dict(
        prefix=prefix, suffix=suffix, remove_keywords=remove_keywords,
        replace_words=replace_words, translation_enabled=translation_enabled,
        source_language=source_language, target_language=target_language,
    )
    try:
        settings["source_language"] = normalize_language(source_language)
        settings["target_language"] = normalize_language(target_language)
    except ValueError:
        return render_settings(
            request, channel, settings,
            error="Choose a supported source and target language.", status_code=422,
        )
    try:
        update_channel_settings(channel_id, **settings)
    except sqlite3.Error:
        logger.exception("Could not save settings for channel %s", channel_id)
        return render_settings(
            request, channel, settings,
            error="Could not save settings. Your entries are still here; please try again.", status_code=500,
        )
    return RedirectResponse(url=f"/dashboard/settings/{channel_id}?saved=true", status_code=303)


class PreviewInput(BaseModel):
    text: str = Field(default="", max_length=10000)
    prefix: str = Field(default="", max_length=4000)
    suffix: str = Field(default="", max_length=4000)
    remove_keywords: str = Field(default="", max_length=4000)
    replace_words: str = Field(default="", max_length=4000)
    translation_enabled: bool = False
    source_language: str = Field(default="en", max_length=20)
    target_language: str = Field(default="fr", max_length=20)


@router.get("/dashboard/settings/{channel_id}/translation-status")
async def translation_status(channel_id: str):
    require_channel(channel_id)
    try:
        config = TranslationConfig.from_env()
        connection = OllamaConfig.from_env()
        # Status checks should not wait as long as generation requests.
        connection = OllamaConfig(connection.base_url, connection.connect_timeout, min(connection.read_timeout, 10))
        async with OllamaClient(connection) as client:
            await client.require_model(config.model)
        return {"ready": True, "model": config.model, "message": f"Ollama connected. Model available: {config.model}"}
    except (OllamaError, TranslationError) as error:
        return JSONResponse(status_code=503, content={"ready": False, "message": translation_error_message(error)})
    except Exception:
        logger.exception("Ollama status check failed")
        return JSONResponse(status_code=500, content={"ready": False, "message": "Could not check Ollama. Please try again."})


@router.post("/dashboard/settings/{channel_id}/translation-preview")
async def translation_preview(channel_id: str, preview: PreviewInput):
    require_channel(channel_id)
    try:
        source = normalize_language(preview.source_language)
        target = normalize_language(preview.target_language)
    except ValueError:
        return JSONResponse(status_code=422, content={"message": "Choose a supported source and target language."})
    try:
        settings = preview.model_dump()
        processed = process_body(preview.text, settings)
    except (re.error, IndexError):
        return JSONResponse(status_code=422, content={"message": "A replacement rule is invalid. Check the replacement text and try again."})
    if not preview.translation_enabled or not processed.strip() or source == target:
        return {"processed_text": processed, "translated_text": add_affixes(processed, settings), "translated": False}
    try:
        connection = OllamaConfig.from_env()
        async with OllamaClient(connection) as client:
            service = TranslationService(client)
            # Bound the whole interactive preview, including all of its chunks.
            output = await asyncio.wait_for(
                service.translate(processed, source, target), timeout=connection.read_timeout,
            )
        return {"processed_text": processed, "translated_text": add_affixes(output, settings), "translated": True}
    except asyncio.TimeoutError as error:
        return JSONResponse(status_code=504, content={"message": translation_error_message(error)})
    except (OllamaError, TranslationError) as error:
        return JSONResponse(status_code=503, content={"message": translation_error_message(error)})
    except Exception:
        logger.exception("Translation preview failed for channel %s", channel_id)
        return JSONResponse(status_code=500, content={"message": "Could not preview translation. Your entries are unchanged; please try again."})
