"""Check RSS translation and fallback using temporary posts, without Telegram."""

import argparse
import asyncio
import sys
import tempfile
from contextlib import closing
from html import unescape
from pathlib import Path
from xml.etree import ElementTree

from app.database import database
from app.database.models import save_post, update_channel_settings, upsert_channel
from app.dashboard.translation_errors import translation_error_message
from app.llm.ollama_client import OllamaClient, OllamaConnectionError
from app.rss.rss import generate_rss
from app.translation.activity import publish_activity
from app.translation.check_workers import DemoClient, until
from app.translation.output import get_channel_outputs
from app.translation.service import TranslationConfig
from app.translation import store
from app.translation.worker import TranslationWorker, WorkerConfig


def item():
    return ElementTree.fromstring(generate_rss("delivery_check")).find("channel/item")


async def check(live):
    original_database = database.DATABASE_NAME
    client = OllamaClient() if live else DemoClient()
    worker = None
    with tempfile.TemporaryDirectory(prefix="translation-delivery-check-") as directory:
        database.DATABASE_NAME = str(Path(directory) / "check.db")
        try:
            database.create_database()
            upsert_channel("delivery", "Delivery check", "delivery_check")
            settings = dict(prefix="Latest news", suffix="Source: Telegram", remove_keywords="BREAKING",
                            replace_words="event=>meeting", translation_enabled=True, source_language="en", target_language="fr")
            update_channel_settings("delivery", **settings)
            original = "BREAKING The event starts at ten."
            save_post("delivery", "Delivery check", "delivery_check", 1, original,
                      "text", None, "2026-10-09T10:00:00+00:00")
            before = item()
            guid = before.find("guid").text
            assert "meeting" in before.find("description").text
            print("PASS: RSS delivered processed source text before any translation was available.")
            config = TranslationConfig.from_env()
            await client.require_model(config.model)
            worker = TranslationWorker(client=client, translation_config=config,
                                       worker_config=WorkerConfig(poll_seconds=0.1))
            await worker.start()
            print("Waiting for the saved translation...", flush=True)
            await until(lambda: get_channel_outputs("delivery_check")[0]["translation_status"] == "completed",
                        620 if live else 10)
            await worker.stop()
            output = get_channel_outputs("delivery_check")[0]
            after = item()
            assert unescape(after.find("description").text) == output["output_text"]
            assert after.find("title").text == output["body_text"].splitlines()[0]
            assert after.find("guid").text == guid
            print("PASS: RSS and dashboard share the saved translation; headline translated and item ID unchanged.")
            print("Saved RSS output:\n" + output["output_text"])

            # Simulate an outage without stopping the user's actual Ollama service.
            publish_activity(dict(state="paused", message="We couldn't reach Ollama."))
            assert item().find("guid").text == guid
            assert get_channel_outputs("delivery_check")[0]["translation_status"] == "completed"
            print("PASS: A completed translation remained available during a simulated Ollama outage.")

            save_post("delivery", "Delivery check", "delivery_check", 2, original,
                      "text", None, "2026-10-09T10:01:00+00:00")
            assert get_channel_outputs("delivery_check")[0]["translation_status"] == "unavailable"
            assert "meeting" in item().find("description").text
            print("PASS: New posts still reached RSS in source language, with a dashboard warning.")

            digest = worker.digest
            with closing(database.get_connection()) as conn:
                second_id = conn.execute("SELECT id FROM posts WHERE message_id = 2").fetchone()[0]
            store.enqueue_translation(second_id, config, digest)
            job = store.claim_translation(config, digest, post_id=second_id)
            store.fail_translation(job["id"], job["claim_token"], OllamaConnectionError("Simulated failure"))
            assert get_channel_outputs("delivery_check")[0]["translation_status"] == "failed"
            assert "meeting" in item().find("description").text
            print("PASS: Failed translation published source text without holding up RSS.")

            settings["prefix"] = "Updated news"
            update_channel_settings("delivery", **settings)
            views = get_channel_outputs("delivery_check")
            assert all(view["translation_status"] != "completed" for view in views)
            assert all(view["output_text"].startswith("Updated news") for view in views)
            with closing(database.get_connection()) as conn:
                assert all(row[0] == original for row in conn.execute("SELECT text FROM posts"))
            print("PASS: Changed settings rejected stale translations; originals remained unchanged.")
            print("Delivery check passed. Only a temporary database was used.")
        finally:
            try:
                if worker is not None:
                    await worker.stop()
                if live:
                    await client.aclose()
            finally:
                database.DATABASE_NAME = original_database


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Translate the first temporary post using your installed Ollama model.")
    args = parser.parse_args()
    try:
        asyncio.run(check(args.live))
    except KeyboardInterrupt:
        print("Delivery check cancelled.", file=sys.stderr)
        return 130
    except Exception as error:
        message = str(error) if isinstance(error, AssertionError) else translation_error_message(error)
        print(f"Delivery check failed: {message or type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
