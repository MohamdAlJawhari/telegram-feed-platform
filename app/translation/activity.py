"""Shared worker heartbeat and queue counts for dashboard processes."""

import json
from contextlib import closing
from datetime import datetime, timezone

from app.database.database import get_connection


def queue_counts():
    with closing(get_connection()) as conn:
        conn.execute("BEGIN")
        channels = conn.execute("""
            SELECT COUNT(*) FROM channels c JOIN channel_settings s ON s.channel_id = c.channel_id
            WHERE c.enabled = 1 AND s.translation_enabled = 1
        """).fetchone()[0]
        counts = conn.execute("""
            SELECT
                COUNT(DISTINCT CASE WHEN j.status = 'pending' OR
                    (r.post_id IS NOT NULL AND COALESCE(j.status, '') != 'running')
                    THEN p.id END) AS waiting,
                COUNT(DISTINCT CASE WHEN j.status = 'failed' AND
                    COALESCE(j.error_type, '') != 'Superseded' THEN p.id END) AS failed
            FROM posts p JOIN channels c ON (
                p.channel_id = c.channel_id OR
                (p.channel_id IS NULL AND lower(p.channel_username) = lower(c.channel_username))
            ) JOIN channel_settings s ON s.channel_id = c.channel_id
            LEFT JOIN post_translations t ON t.post_id = p.id
            LEFT JOIN translation_jobs j ON j.id = t.current_job_id
            LEFT JOIN translation_requests r ON r.post_id = p.id
            WHERE c.enabled = 1 AND s.translation_enabled = 1
        """).fetchone()
        return {"channels": channels, "waiting": counts["waiting"], "failed": counts["failed"]}


def publish_activity(payload):
    with closing(get_connection()) as conn, conn:
        conn.execute("""
            INSERT INTO translation_activity(id, payload, updated_at) VALUES (1, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, updated_at = CURRENT_TIMESTAMP
        """, (json.dumps(payload),))


def get_activity():
    with closing(get_connection()) as conn:
        row = conn.execute("SELECT * FROM translation_activity WHERE id = 1").fetchone()
    if row:
        try:
            payload = json.loads(row["payload"])
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["updated_at"]).replace(tzinfo=timezone.utc)).total_seconds()
            if isinstance(payload, dict) and age <= 30:
                return payload
        except (ValueError, TypeError):
            pass
    return {"state": "unavailable", "running": None, "waiting": None, "automatic": True,
            "message": "Background translation activity is unavailable. RSS remains available.",
            "memory_note": ""}
