"""Exercise persistent translation storage in a temporary database only."""

import argparse
import asyncio
import sys
import tempfile
from contextlib import closing
from pathlib import Path

import app.database.database as database
from app.database.models import save_post, update_channel_settings, upsert_channel
from app.llm.ollama_client import OllamaClient, OllamaConnectionError, OllamaError
from app.translation.service import TranslationConfig, TranslationError, TranslationOutputError, TranslationService
from app.translation.store import (
    claim_translation, complete_translation, enqueue_translation, fail_translation,
    get_current_translation, get_translation_job,
)


async def exercise_store(live=False):
    config = TranslationConfig.from_env() if live else TranslationConfig()
    digest = "storage-demo-model-v1"
    original_database = database.DATABASE_NAME
    with tempfile.TemporaryDirectory(prefix="translation-storage-check-") as directory:
        database.DATABASE_NAME = str(Path(directory) / "check.db")
        try:
            database.create_database()
            upsert_channel("demo", "Storage check", "storage_check")
            settings = dict(
                prefix="Latest news", suffix="Source: Telegram", remove_keywords="BREAKING",
                replace_words="event=>meeting", translation_enabled=True,
                source_language="en", target_language="fr",
            )
            update_channel_settings("demo", **settings)
            original = "BREAKING The event starts at 10."
            save_post("demo", "Storage check", "storage_check", 1, original, "text", None, "2026-10-09")
            with closing(database.get_connection()) as connection:
                post_id = connection.execute("SELECT id FROM posts").fetchone()[0]
            if live:
                async with OllamaClient() as client:
                    installed = await client.require_model(config.model)
                    digest = installed.get("digest", "")
                    pending = enqueue_translation(post_id, config, digest)
                    running = claim_translation(config, digest, post_id=post_id)
                    print("Status: pending -> running. Waiting for Ollama...", flush=True)
                    try:
                        body = await TranslationService(client, config).translate(
                            running["processed_text"], "en", "fr",
                        )
                        # Refresh identity before saving: an updated model alias/config
                        # must not complete a job for an older model version.
                        current_config = TranslationConfig.from_env()
                        current_model = await client.require_model(current_config.model)
                        saved = complete_translation(
                            running["id"], running["claim_token"], body,
                            current_config, current_model.get("digest", ""),
                        )
                        if not saved:
                            raise TranslationOutputError("Settings or model changed during this check; please run it again.")
                    except (OllamaError, TranslationError) as error:
                        fail_translation(running["id"], running["claim_token"], error)
                        raise
            else:
                pending = enqueue_translation(post_id, config, digest)
                running = claim_translation(config, digest, post_id=post_id)
                print("Status: pending -> running (simulated translation).")
                assert complete_translation(running["id"], running["claim_token"], "La réunion commence à 10 heures.", config, digest)
            result = get_current_translation(post_id, config, digest)
            assert result and result["status"] == "completed"
            print("Status: completed")
            print("Saved output:\n" + result["output_text"])
            repeated = enqueue_translation(post_id, config, digest)
            assert repeated["id"] == pending["id"] and repeated["status"] == "completed"
            assert claim_translation(config, digest, post_id=post_id) is None
            print("PASS: Repeated request reused the completed job; no new model call.")

            settings["prefix"] = "Updated prefix"
            update_channel_settings("demo", **settings)
            assert get_current_translation(post_id, config, digest) is None
            changed = enqueue_translation(post_id, config, digest)
            assert changed["id"] != pending["id"]
            stale_attempt = claim_translation(config, digest, post_id=post_id)
            print("PASS: Changed settings created a new pending job.")
            settings["target_language"] = "de"
            update_channel_settings("demo", **settings)
            latest = enqueue_translation(post_id, config, digest)
            assert not complete_translation(stale_attempt["id"], stale_attempt["claim_token"], "Outdated result", config, digest)
            assert get_current_translation(post_id, config, digest)["id"] == latest["id"]
            print("PASS: An old running attempt could not overwrite the newer job.")

            failing = claim_translation(config, digest, post_id=post_id)
            assert fail_translation(failing["id"], failing["claim_token"], OllamaConnectionError("Simulated connection failure"))
            failed = get_current_translation(post_id, config, digest)
            assert failed["status"] == "failed" and failed["output_text"] is None
            print("PASS: Failure saved as failed, without partial output.")
            database.create_database()
            assert get_translation_job(pending["id"])["output_text"] == result["output_text"]
            assert get_translation_job(failed["id"])["status"] == "failed"
            with closing(database.get_connection()) as connection:
                assert connection.execute("SELECT text FROM posts WHERE id = ?", (post_id,)).fetchone()[0] == original
            print("PASS: Saved states survived reopening; original post is unchanged.")
            print("Storage check passed. Temporary database removed; project data was not modified.")
        finally:
            database.DATABASE_NAME = original_database


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use your local Ollama for the first result; other safety checks are simulated.")
    args = parser.parse_args()
    try:
        asyncio.run(exercise_store(args.live))
    except (OllamaError, TranslationError, OSError) as error:
        print(f"Storage check failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Storage check cancelled.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
