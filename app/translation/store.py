"""Persistent job states and cached results; no model calls or background workers."""

import hashlib
import json
import re
import uuid
from contextlib import closing

from app.database.database import get_connection
from app.processing.processor import add_affixes, process_body
from app.translation.languages import normalize_language
from app.translation.service import TranslationConfig, TranslationConfigurationError, TranslationOutputError
from app.translation.delivery import saved_fallback, finish_source
from app.dashboard.translation_errors import translation_error_message


# Bump this when prompts, generation options, or processing semantics change.
RECIPE_VERSION = "translategemma-body-before-affixes-v1"


def _validate_digest(model_digest):
    if not isinstance(model_digest, str) or not model_digest.strip():
        raise TranslationConfigurationError("An installed model digest is required to identify cached translations.")


def _snapshot(connection, post_id, config, model_digest):
    _validate_digest(model_digest)
    post = connection.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if post is None:
        raise TranslationConfigurationError("Post not found.")
    # Reconciled and legacy rows both keep their original database row ID.
    if post["channel_id"] is not None:
        channel = connection.execute("SELECT * FROM channels WHERE channel_id = ?", (post["channel_id"],)).fetchone()
    else:
        channel = connection.execute(
            "SELECT * FROM channels WHERE lower(channel_username) = lower(?) ORDER BY id LIMIT 1",
            (post["channel_username"],),
        ).fetchone()
    if channel is None:
        return None
    row = connection.execute("SELECT * FROM channel_settings WHERE channel_id = ?", (channel["channel_id"],)).fetchone()
    if row is None or not row["translation_enabled"]:
        return None
    try:
        source = normalize_language(row["source_language"])
        target = normalize_language(row["target_language"])
    except ValueError as error:
        raise TranslationConfigurationError(str(error)) from error
    settings = {
        name: row[name] or ""
        for name in ("remove_keywords", "replace_words", "prefix", "suffix")
    }
    try:
        body = process_body(post["text"] or "", settings)
    except (re.error, IndexError) as error:
        raise TranslationConfigurationError("A replacement rule is invalid.") from error
    if source == target or not body:
        return None
    snapshot = {
        "post_id": post["id"], "channel_id": channel["channel_id"],
        "original_text": post["text"] or "", "settings": settings,
        "source_language": source, "target_language": target,
        "model": config.model.strip(), "model_digest": model_digest.strip(),
        "max_chunk_bytes": config.max_chunk_bytes, "recipe_version": RECIPE_VERSION,
    }
    encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest(), encoded, body


def _is_current(connection, job, config, model_digest):
    if job["status"] != "failed" and saved_fallback(connection, job["post_id"]) is not None:
        return False
    pointer = connection.execute(
        "SELECT current_job_id FROM post_translations WHERE post_id = ?", (job["post_id"],),
    ).fetchone()
    if pointer is None or pointer[0] != job["id"]:
        return False
    try:
        snapshot = _snapshot(connection, job["post_id"], config, model_digest)
    except TranslationConfigurationError:
        return False
    return snapshot is not None and snapshot[0] == job["fingerprint"]


def _supersede(connection, job_id):
    connection.execute("""
        UPDATE translation_jobs SET status = 'failed', claim_token = NULL,
            error_type = 'Superseded',
            error_message = 'The input or translation settings changed.',
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status IN ('pending', 'running')
    """, (job_id,))


def enqueue_translation(post_id: int, config: TranslationConfig, model_digest: str):
    """Reuse the identical job or select a new snapshot atomically.

    Returns None when the body doesn't need translation. Failed jobs are retained
    without automatic retries. Superseded jobs may be requeued if settings revert.
    """
    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        if saved_fallback(connection, post_id) is not None:
            job = connection.execute("""
                SELECT j.* FROM translation_jobs j JOIN post_translations t ON t.current_job_id = j.id
                WHERE t.post_id = ? AND j.status = 'failed'
            """, (post_id,)).fetchone()
            return dict(job) if job else None
        # Existing failures from earlier application versions are terminal too.
        previous = connection.execute("""
            SELECT j.* FROM translation_jobs j JOIN post_translations t ON t.current_job_id = j.id
            WHERE t.post_id = ? AND j.status = 'failed' AND COALESCE(j.error_type, '') != 'Superseded'
        """, (post_id,)).fetchone()
        if previous is not None:
            return dict(previous)
        snapshot = _snapshot(connection, post_id, config, model_digest)
        pointer = connection.execute("SELECT current_job_id FROM post_translations WHERE post_id = ?", (post_id,)).fetchone()
        if snapshot is None:
            if pointer:
                _supersede(connection, pointer[0])
            connection.execute("DELETE FROM post_translations WHERE post_id = ?", (post_id,))
            return None
        fingerprint, encoded, body = snapshot
        connection.execute("""
            INSERT OR IGNORE INTO translation_jobs(post_id, fingerprint, snapshot_json, processed_text)
            VALUES (?, ?, ?, ?)
        """, (post_id, fingerprint, encoded, body))
        job = connection.execute(
            "SELECT * FROM translation_jobs WHERE post_id = ? AND fingerprint = ?", (post_id, fingerprint),
        ).fetchone()
        if pointer and pointer[0] != job["id"]:
            _supersede(connection, pointer[0])
        if job["status"] == "failed" and job["error_type"] == "Superseded":
            connection.execute("""
                UPDATE translation_jobs SET status = 'pending', error_type = NULL,
                    error_message = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (job["id"],))
        connection.execute("""
            INSERT INTO post_translations(post_id, current_job_id) VALUES (?, ?)
            ON CONFLICT(post_id) DO UPDATE SET current_job_id = excluded.current_job_id
        """, (post_id, job["id"]))
        return dict(connection.execute("SELECT * FROM translation_jobs WHERE id = ?", (job["id"],)).fetchone())


def get_current_translation(post_id: int, config: TranslationConfig, model_digest: str):
    """Return a fresh current job (any status), or None for stale/disabled output.

    Callers must supply the current configuration and installed model digest.
    This read does not queue work. Historical output is never used as a fallback.
    """
    _validate_digest(model_digest)
    with closing(get_connection()) as connection, connection:
        # Keep all freshness checks on one consistent read snapshot.
        connection.execute("BEGIN")
        job = connection.execute("""
            SELECT j.* FROM translation_jobs j JOIN post_translations p ON p.current_job_id = j.id
            WHERE p.post_id = ?
        """, (post_id,)).fetchone()
        return dict(job) if job and _is_current(connection, job, config, model_digest) else None


def get_translation_job(job_id: int):
    """Historical record for diagnostics; use get_current_translation for output."""
    with closing(get_connection()) as connection:
        job = connection.execute("SELECT * FROM translation_jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(job) if job else None


def claim_translation(config: TranslationConfig, model_digest: str, *, post_id: int | None = None,
                      active_only: bool = False):
    """Atomically claim one current pending job; simultaneous callers get distinct jobs."""
    _validate_digest(model_digest)
    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        candidates = connection.execute("""
            SELECT j.* FROM translation_jobs j JOIN post_translations p ON p.current_job_id = j.id
            JOIN posts source ON source.id = j.post_id
            WHERE j.status = 'pending' AND (? IS NULL OR j.post_id = ?)
              AND (? = 0 OR EXISTS (
                  SELECT 1 FROM channels c WHERE c.enabled = 1 AND (
                      c.channel_id = source.channel_id OR
                      (source.channel_id IS NULL AND lower(c.channel_username) = lower(source.channel_username))
                  )
              ))
            ORDER BY (
                SELECT COUNT(*) FROM translation_jobs active JOIN posts other ON other.id = active.post_id
                WHERE active.status = 'running' AND (
                    (source.channel_id IS NOT NULL AND other.channel_id = source.channel_id) OR
                    (source.channel_id IS NULL AND other.channel_id IS NULL
                     AND lower(other.channel_username) = lower(source.channel_username))
                )
            ), j.id
        """, (post_id, post_id, int(active_only)))
        for job in candidates:
            if not _is_current(connection, job, config, model_digest):
                _supersede(connection, job["id"])
                continue
            token = uuid.uuid4().hex
            connection.execute("""
                UPDATE translation_jobs SET status = 'running', claim_token = ?, attempts = attempts + 1,
                    started_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'pending'
            """, (token, job["id"]))
            return dict(connection.execute("SELECT * FROM translation_jobs WHERE id = ?", (job["id"],)).fetchone())
    return None


def complete_translation(job_id: int, claim_token: str, translated_body: str,
                         config: TranslationConfig, model_digest: str) -> bool:
    """Save output only for the current running attempt; reject outdated callbacks."""
    _validate_digest(model_digest)
    if not isinstance(translated_body, str) or not translated_body.strip():
        raise TranslationOutputError("Cannot save an empty translated body.")
    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        job = connection.execute("SELECT * FROM translation_jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None or job["status"] != "running" or job["claim_token"] != claim_token:
            return False
        if not _is_current(connection, job, config, model_digest):
            _supersede(connection, job_id)
            return False
        snapshot = json.loads(job["snapshot_json"])
        body = translated_body.strip()
        output = add_affixes(body, snapshot["settings"])
        connection.execute("""
            UPDATE translation_jobs SET status = 'completed', translated_body = ?, output_text = ?,
                claim_token = NULL, error_type = NULL, error_message = NULL,
                completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (body, output, job_id))
        return True


def fail_translation(job_id: int, claim_token: str, error: Exception) -> bool:
    """Retain failure details without replacing originals or persisting partial output."""
    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute("""
            UPDATE translation_jobs SET status = 'failed', claim_token = NULL,
                error_type = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'running' AND claim_token = ?
        """, (type(error).__name__, str(error)[:2000], job_id, claim_token))
        if cursor.rowcount != 1:
            return False
        job = connection.execute("SELECT * FROM translation_jobs WHERE id = ?", (job_id,)).fetchone()
        snapshot = json.loads(job["snapshot_json"])
        finish_source(connection, job["post_id"], dict(
            output_text=add_affixes(job["processed_text"], snapshot["settings"]),
            body_text=job["processed_text"], output_language=snapshot["source_language"],
            translation_status="failed", translation_message=translation_error_message(error) + " RSS is using source text.",
        ))
        return True
