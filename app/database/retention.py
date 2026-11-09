"""Keep channel history and the full RSS window; retry retired attachment cleanup."""

import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.database.database import get_connection
from app.media.media_service import get_media_root

POSTS_PER_CHANNEL = 700
logger = logging.getLogger(__name__)


def prune_posts(conn, channel_id=None, channel_username=None):
    """Delete dependents and posts in the caller's transaction, newest messages first.

    Legacy rows join their registered channel by username; unknown usernames
    remain separate channels. Stable Telegram IDs survive username changes.
    """
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS retired_posts(id INTEGER PRIMARY KEY)")
    conn.execute("DELETE FROM retired_posts")
    where = ""
    parameters = []
    if channel_id is not None or channel_username is not None:
        where = """WHERE p.channel_id = ? OR (
            p.channel_id IS NULL AND lower(p.channel_username) = lower(?)
        )"""
        parameters.extend((channel_id, channel_username))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    conn.execute(f"""
        INSERT INTO retired_posts
        SELECT id FROM (
            SELECT p.id, p.date, ROW_NUMBER() OVER (
                PARTITION BY CASE WHEN p.channel_id IS NOT NULL THEN 'id:' || p.channel_id
                    WHEN c.channel_id IS NOT NULL THEN 'id:' || c.channel_id
                    ELSE 'username:' || lower(p.channel_username) END
                ORDER BY p.message_id DESC, p.id DESC
            ) AS position
            FROM posts p LEFT JOIN channels c ON p.channel_id IS NULL
                AND c.id = (SELECT MIN(id) FROM channels
                            WHERE lower(channel_username) = lower(p.channel_username))
            {where}
        ) WHERE position > ?
            AND (julianday(date) IS NULL OR julianday(date) < julianday(?))
    """, (*parameters, POSTS_PER_CHANNEL, cutoff.isoformat()))
    count = conn.execute("SELECT COUNT(*) FROM retired_posts").fetchone()[0]
    if not count:
        return 0
    conn.execute("""
        INSERT OR IGNORE INTO retired_media(path)
        SELECT media_path FROM posts WHERE id IN (SELECT id FROM retired_posts)
            AND media_path IS NOT NULL AND media_path != ''
    """)
    for table in ("post_translations", "translation_requests", "rss_source_fallbacks", "translation_jobs"):
        conn.execute(f"DELETE FROM {table} WHERE post_id IN (SELECT id FROM retired_posts)")
    conn.execute("DELETE FROM posts WHERE id IN (SELECT id FROM retired_posts)")
    return count


def _safe_media_path(root, relative):
    candidate = root / relative
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root.resolve()) or resolved == root.resolve():
        return None
    # Refuse symlinks/junctions at every level, including the media root.
    for part in (candidate, *candidate.parents):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            return None
    if resolved.is_relative_to((root / "channel-profiles").resolve()):
        return None
    return candidate


def cleanup_retired_media():
    """Only unlink queued files inside media; retained references always win."""
    root = Path(get_media_root()).absolute()
    with closing(get_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        pending = conn.execute("SELECT path FROM retired_media").fetchall()
        if not pending:
            return
        referenced = {(root / row[0]).resolve() for row in conn.execute(
            "SELECT DISTINCT media_path FROM posts WHERE media_path IS NOT NULL"
        )}
        for row in pending:
            relative = row[0]
            target = _safe_media_path(root, relative)
            if target is None:
                logger.warning("Skipped unsafe or protected retired media path: %s", relative)
                conn.execute("DELETE FROM retired_media WHERE path = ?", (relative,))
                continue
            try:
                if target.resolve() not in referenced:
                    target.unlink(missing_ok=True)
                conn.execute("DELETE FROM retired_media WHERE path = ?", (relative,))
            except OSError:
                logger.warning("Will retry retired media cleanup: %s", relative, exc_info=True)


def commit_with_retention(conn, channel_id, channel_username):
    prune_posts(conn, channel_id, channel_username)
    conn.commit()
    try:
        cleanup_retired_media()
    except (OSError, sqlite3.Error):
        logger.warning("Retired media cleanup deferred", exc_info=True)


def enforce_retention():
    with closing(get_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        count = prune_posts(conn)
    cleanup_retired_media()
    return count
