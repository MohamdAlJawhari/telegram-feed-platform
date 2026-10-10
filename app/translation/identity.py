"""Last verified model identity; feed readers never contact Ollama."""

from contextlib import closing

from app.database.database import get_connection
from app.translation.store import _validate_digest


def remember_model(base_url, model, digest):
    _validate_digest(digest)
    with closing(get_connection()) as conn, conn:
        conn.execute("""
            INSERT INTO translation_model_identity(base_url, model, digest)
            VALUES (?, ?, ?)
            ON CONFLICT(base_url, model) DO UPDATE SET digest = excluded.digest,
                verified_at = CURRENT_TIMESTAMP
        """, (base_url.rstrip("/"), model.strip(), digest.strip()))
