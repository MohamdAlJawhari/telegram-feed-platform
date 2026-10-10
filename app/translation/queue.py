"""Durable queue intents; producers need no model identity or network access."""

from contextlib import closing

from app.database.database import get_connection


def seed_requests():
    """Reconcile stored posts on startup or when an installed model changes."""
    with closing(get_connection()) as conn, conn:
        conn.execute("""
            INSERT OR IGNORE INTO translation_requests(post_id)
            SELECT p.id FROM posts p
            WHERE NOT EXISTS (SELECT 1 FROM rss_source_fallbacks f WHERE f.post_id = p.id) AND (EXISTS (
                SELECT 1 FROM channels c JOIN channel_settings s ON s.channel_id = c.channel_id
                WHERE s.translation_enabled = 1 AND (
                    p.channel_id = c.channel_id OR
                    (p.channel_id IS NULL AND lower(p.channel_username) = lower(c.channel_username))
                )
            ) OR EXISTS (SELECT 1 FROM post_translations t WHERE t.post_id = p.id))
        """)


def pending_requests(limit=100):
    with closing(get_connection()) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM translation_requests ORDER BY post_id LIMIT ?", (limit,),
        )]


def acknowledge_request(post_id, revision):
    # A settings save/new ingestion during reconciliation must remain queued.
    with closing(get_connection()) as conn, conn:
        conn.execute("DELETE FROM translation_requests WHERE post_id = ? AND revision = ?", (post_id, revision))


def recover_running_jobs():
    """Only the exclusive worker owner may recover abandoned attempts."""
    with closing(get_connection()) as conn, conn:
        conn.execute("""
            UPDATE translation_jobs SET status = 'pending', claim_token = NULL,
                started_at = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE status = 'running'
        """)


def release_job(job_id, token):
    with closing(get_connection()) as conn, conn:
        conn.execute("""
            UPDATE translation_jobs SET status = 'pending', claim_token = NULL,
                started_at = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'running' AND claim_token = ?
        """, (job_id, token))


def finalized_jobs(job_ids):
    """Identify running requests whose source fallback has become final."""
    if not job_ids:
        return set()
    placeholders = ",".join("?" for _ in job_ids)
    with closing(get_connection()) as conn:
        return {row[0] for row in conn.execute(f"""
            SELECT j.id FROM translation_jobs j JOIN rss_source_fallbacks f ON f.post_id = j.post_id
            WHERE j.id IN ({placeholders})
        """, tuple(job_ids))}
