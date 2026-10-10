"""Shared RSS/dashboard output selection: SQLite reads only, no generation."""

import re
from contextlib import closing

from app.database.database import get_connection
from app.dashboard.translation_errors import translation_error_message
from app.llm.ollama_client import (
    OllamaConfig, OllamaAPIError, OllamaConnectionError,
    OllamaModelNotFoundError, OllamaTimeoutError,
)
from app.processing.processor import process_body, add_affixes
from app.translation.activity import get_activity
from app.translation.languages import LANGUAGES, normalize_language
from app.translation.service import TranslationConfig
from app.translation.store import _snapshot


DEFAULT_SETTINGS = dict(prefix="", suffix="", remove_keywords="", replace_words="",
                        translation_enabled=False, source_language="en", target_language="fr")


def failure_message(job):
    errors = {"OllamaConnectionError": OllamaConnectionError,
              "OllamaModelNotFoundError": OllamaModelNotFoundError,
              "OllamaTimeoutError": OllamaTimeoutError, "TimeoutError": OllamaTimeoutError}
    kind = job["error_type"]
    if kind in errors:
        return translation_error_message(errors[kind](""))
    if kind == "OllamaAPIError":
        busy = re.search(r"HTTP (429|503)\b", job["error_message"] or "")
        return translation_error_message(OllamaAPIError("", int(busy[1]) if busy else 500))
    return "The translation could not be completed."


def select_output(conn, post, settings, config, digest, unavailable_message=None):
    original = post["text"] or ""
    try:
        body = process_body(original, settings)
    except (re.error, IndexError):
        return dict(output_text=add_affixes(original, settings), body_text=original,
                    translation_status="processing_error", translation_label="Processing warning",
                    translation_message="A replacement rule is invalid. RSS is using the source text with prefix and suffix.",
                    output_language=None)
    result = dict(output_text=add_affixes(body, settings), body_text=body,
                  translation_status="disabled", translation_label="Translation off",
                  translation_message="RSS uses the normally processed source text.", output_language=None)
    if not settings["translation_enabled"]:
        return result
    try:
        source, target = normalize_language(settings["source_language"]), normalize_language(settings["target_language"])
    except ValueError:
        result.update(translation_status="unavailable", translation_label="Translation unavailable",
                      translation_message="Check the channel's translation languages. RSS is using source text.")
        return result
    result["output_language"] = source
    if not body or source == target:
        result.update(translation_status="skipped", translation_label="Translation not needed",
                      translation_message="The language is unchanged or there is no news text to translate.")
        return result
    result.update(translation_status="pending", translation_label="Translation pending",
                  translation_message="RSS is using source text while translation is pending.")
    if config is None or not digest:
        result.update(translation_status="unavailable", translation_label="Translation unavailable",
                      translation_message=(unavailable_message or "Waiting for Ollama to verify the translation model.")
                                          + " RSS is using source text.")
        return result
    job = conn.execute("""
        SELECT j.* FROM translation_jobs j JOIN post_translations t ON t.current_job_id = j.id
        WHERE t.post_id = ?
    """, (post["id"],)).fetchone()
    if job is None:
        if unavailable_message:
            result.update(translation_status="unavailable", translation_label="Translation unavailable",
                          translation_message=unavailable_message + " RSS is using source text.")
        return result
    snapshot = _snapshot(conn, post["id"], config, digest)
    if snapshot is None or snapshot[0] != job["fingerprint"]:
        return result
    if job["status"] == "completed" and job["translated_body"] and job["translated_body"].strip():
        result.update(output_text=add_affixes(job["translated_body"], settings), body_text=job["translated_body"],
                      translation_status="completed", translation_label=f"Translated · {LANGUAGES[target]}",
                      translation_message="RSS uses this saved translation.", output_language=target)
    elif job["status"] == "failed" and job["error_type"] != "Superseded":
        result.update(translation_status="failed", translation_label="Translation failed",
                      translation_message=failure_message(job) + " RSS is using source text.")
    elif unavailable_message:
        result.update(translation_status="unavailable", translation_label="Translation unavailable",
                      translation_message=unavailable_message + " RSS is using source text.")
    elif job["status"] == "running":
        result.update(translation_status="running", translation_label="Translating",
                      translation_message="RSS is using source text while translation is running.")
    return result


def get_channel_outputs(channel_username, limit=None):
    """Return original rows and their published version on one consistent read snapshot."""
    config = None
    base_url = None
    unavailable_message = None
    try:
        config = TranslationConfig.from_env()
        base_url = OllamaConfig.from_env().base_url.rstrip("/")
    except Exception as error:
        unavailable_message = translation_error_message(error)
    background = get_activity()
    if background.get("connection_error") or background["state"] == "paused":
        unavailable_message = background.get("connection_error") or background["message"]
    with closing(get_connection()) as conn:
        conn.execute("BEGIN")
        channel = conn.execute("SELECT * FROM channels WHERE lower(channel_username) = lower(?) ORDER BY id LIMIT 1",
                               (channel_username,)).fetchone()
        channel_id = channel["channel_id"] if channel else None
        posts = conn.execute("""
            SELECT * FROM posts WHERE channel_id = ? OR (
                lower(channel_username) = lower(?) AND (channel_id IS NULL OR ? IS NULL)
            ) ORDER BY id DESC LIMIT ?
        """, (channel_id, channel_username, channel_id, -1 if limit is None else limit)).fetchall()
        identity = conn.execute("SELECT digest FROM translation_model_identity WHERE base_url = ? AND model = ?",
                                (base_url, config.model.strip())).fetchone() if config else None
        digest = identity[0] if identity else None
        settings_row = conn.execute("SELECT * FROM channel_settings WHERE channel_id = ?", (channel_id,)).fetchone()
        settings = dict(DEFAULT_SETTINGS)
        if settings_row:
            settings.update({key: settings_row[key] if settings_row[key] is not None else default
                             for key, default in DEFAULT_SETTINGS.items()})
        result = []
        for row in posts:
            post = dict(row)
            post.update(select_output(conn, row, settings, config, digest, unavailable_message))
            result.append(post)
        return result
