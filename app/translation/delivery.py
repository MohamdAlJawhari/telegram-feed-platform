"""Final source fallback decisions, shared by RSS delivery and the queue."""


def saved_fallback(conn, post_id):
    row = conn.execute("SELECT * FROM rss_source_fallbacks WHERE post_id = ?", (post_id,)).fetchone()
    if row is None:
        return None
    return {key: row[key] for key in ("output_text", "body_text", "output_language",
                                    "translation_status", "translation_label", "translation_message")}


def finish_source(conn, post_id, output):
    """Called in a write transaction: revoke attempts before they can replace source."""
    message = output["translation_message"]
    if output["translation_status"] in ("pending", "running"):
        message = "Translation wasn't ready when RSS published this post."
    conn.execute("""
        INSERT OR IGNORE INTO rss_source_fallbacks(
            post_id, output_text, body_text, output_language,
            translation_status, translation_label, translation_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (post_id, output["output_text"], output["body_text"], output["output_language"],
          "failed" if output["translation_status"] == "failed" else "source_final",
          "Translation failed" if output["translation_status"] == "failed" else "Source text published",
          message + " This post will remain in source language; it won't be retried."))
    conn.execute("""
        UPDATE translation_jobs SET status = 'failed', claim_token = NULL,
            error_type = 'SourceDelivered', error_message = 'Source fallback is final.',
            updated_at = CURRENT_TIMESTAMP
        WHERE post_id = ? AND status IN ('pending', 'running')
    """, (post_id,))
    conn.execute("DELETE FROM translation_requests WHERE post_id = ?", (post_id,))
    return saved_fallback(conn, post_id)
