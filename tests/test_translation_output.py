import os
import re
import tempfile
import unittest
from contextlib import closing
from html import unescape
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import database
from app.database.models import save_post, update_channel_settings, upsert_channel
from app.dashboard.posts import router
from app.llm.ollama_client import OllamaConfig, OllamaConnectionError
from app.rss.rss import generate_rss
from app.translation.activity import publish_activity
from app.translation.identity import remember_model
from app.translation.output import get_channel_outputs
from app.translation.service import TranslationConfig, TranslationConfigurationError
from app.translation import store


class TranslationOutputTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        db = patch.object(database, "DATABASE_NAME", str(Path(directory.name) / "test.db"))
        db.start()
        self.addCleanup(db.stop)
        database.create_database()
        self.config = TranslationConfig()
        self.connection = OllamaConfig()
        for replacement in (
            patch("app.translation.output.TranslationConfig.from_env", return_value=self.config),
            patch("app.translation.output.OllamaConfig.from_env", return_value=self.connection),
            patch.dict(os.environ, {"BASE_URL": "https://example.test"}),
            patch("app.llm.ollama_client.OllamaClient.__init__", side_effect=AssertionError("Readers must not contact Ollama")),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.digest = "model-v1"
        remember_model(self.connection.base_url, self.config.model, self.digest)
        upsert_channel("1", "News", "news")
        self.settings = dict(prefix="Latest news", suffix="Source: Telegram", remove_keywords="BREAKING",
                             replace_words="Event=>Meeting", translation_enabled=True, source_language="en", target_language="fr")
        update_channel_settings("1", **self.settings)
        self.add_post()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def add_post(self, message_id=1, text="BREAKING Event today", media_path=None):
        save_post("1", "News", "news", message_id, text,
                  "photo" if media_path else "text", media_path, "2026-10-09T10:00:00+00:00")
        with closing(database.get_connection()) as conn:
            return conn.execute("SELECT id FROM posts WHERE message_id = ?", (message_id,)).fetchone()[0]

    def current(self):
        return get_channel_outputs("news")[0]

    def queue(self, post_id=1):
        return store.enqueue_translation(post_id, self.config, self.digest)

    def complete(self, post_id=1, translated="Réunion aujourd'hui"):
        self.queue(post_id)
        job = store.claim_translation(self.config, self.digest, post_id=post_id)
        self.assertTrue(store.complete_translation(job["id"], job["claim_token"], translated, self.config, self.digest))
        return job

    def settings_change(self, **changes):
        self.settings.update(changes)
        update_channel_settings("1", **self.settings)

    def feed(self):
        return ElementTree.fromstring(generate_rss("news")).find("channel")

    def test_dashboard_pending_and_running_reads_do_not_publish_or_mutate(self):
        self.queue()
        for expected in ("pending", "running"):
            output = self.current()
            self.assertEqual(output["translation_status"], expected)
            self.assertEqual(output["output_text"], "Latest news\n\nMeeting today\n\nSource: Telegram")
            if expected == "pending":
                store.claim_translation(self.config, self.digest)
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute("SELECT attempts FROM translation_jobs").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT text FROM posts").fetchone()[0], "BREAKING Event today")

    def test_completed_body_title_language_affixes_and_guid(self):
        before = "1-1"
        self.complete()
        output = self.current()
        self.assertEqual(output["translation_status"], "completed")
        self.assertEqual(output["output_text"], "Latest news\n\nRéunion aujourd'hui\n\nSource: Telegram")
        channel = self.feed()
        self.assertEqual(channel.find("item/title").text, "Réunion aujourd'hui")
        self.assertEqual(channel.find("language").text, "fr")
        self.assertEqual(channel.find("item/guid").text, before)

    def test_failed_translation_has_friendly_warning_and_source_fallback(self):
        self.queue()
        job = store.claim_translation(self.config, self.digest)
        store.fail_translation(job["id"], job["claim_token"], OllamaConnectionError("OLLAMA_BASE_URL internal detail"))
        output = self.current()
        self.assertEqual(output["translation_status"], "failed")
        self.assertIn("couldn't reach Ollama", output["translation_message"])
        self.assertNotIn("OLLAMA_BASE_URL", output["translation_message"])
        self.assertIn("Meeting today", self.feed().find("item/description").text)

    def test_offline_cached_completed_output_remains_usable(self):
        self.complete()
        publish_activity(dict(state="paused", message="We couldn't reach Ollama."))
        self.assertEqual(self.current()["translation_status"], "completed")
        self.assertEqual(self.feed().find("item/title").text, "Réunion aujourd'hui")

    def test_offline_pending_and_missing_identity_deliver_source(self):
        self.queue()
        publish_activity(dict(state="paused", message="The selected translation model isn't installed in Ollama."))
        self.assertEqual(self.current()["translation_status"], "unavailable")
        self.assertIn("isn't installed", self.current()["translation_message"])
        with closing(database.get_connection()) as conn, conn:
            conn.execute("DELETE FROM translation_model_identity")
        self.assertEqual(self.feed().find("item/title").text, "Meeting today")

    def test_every_setting_change_invalidates_completed_output(self):
        self.complete()
        original = dict(self.settings)
        for name, value in (("prefix", "Updated"), ("suffix", "Changed"), ("remove_keywords", "Event"),
                            ("replace_words", "Event=>Conference"), ("source_language", "ar"), ("target_language", "de")):
            self.settings_change(**{name: value})
            self.assertNotEqual(self.current()["translation_status"], "completed", name)
            self.settings = dict(original)
            update_channel_settings("1", **self.settings)
        self.assertEqual(self.current()["translation_status"], "completed")

    def test_model_digest_recipe_and_original_text_changes_invalidate_output(self):
        self.complete()
        remember_model(self.connection.base_url, self.config.model, "model-v2")
        self.assertEqual(self.current()["translation_status"], "pending")
        remember_model(self.connection.base_url, self.config.model, self.digest)
        with patch.object(store, "RECIPE_VERSION", "new-recipe"):
            self.assertEqual(self.current()["translation_status"], "pending")
        with patch("app.translation.output.TranslationConfig.from_env", return_value=TranslationConfig(max_chunk_bytes=800)):
            self.assertEqual(self.current()["translation_status"], "pending")
        with closing(database.get_connection()) as conn, conn:
            conn.execute("UPDATE posts SET text = 'Changed original'")
        self.assertEqual(self.current()["translation_status"], "pending")
        self.assertIn("Changed original", self.current()["output_text"])

    def test_changed_ollama_address_or_model_does_not_reuse_other_identity(self):
        self.complete()
        with patch("app.translation.output.OllamaConfig.from_env", return_value=OllamaConfig("http://localhost:11435")):
            self.assertEqual(self.current()["translation_status"], "unavailable")
        with patch("app.translation.output.TranslationConfig.from_env", return_value=TranslationConfig(model="other:latest")):
            self.assertEqual(self.current()["translation_status"], "unavailable")

    def test_disabled_same_language_and_empty_caption_skip_translation(self):
        self.complete()
        self.settings_change(translation_enabled=False)
        self.assertEqual(self.current()["translation_status"], "disabled")
        self.settings_change(translation_enabled=True, target_language="en")
        self.assertEqual(self.current()["translation_status"], "skipped")
        self.add_post(2, "", "news/2.jpg")
        self.assertEqual(self.current()["translation_status"], "skipped")
        self.assertIsNotNone(self.feed().find("item/enclosure"))

    def test_invalid_rules_or_configuration_do_not_break_rss(self):
        self.settings_change(replace_words=r"Event=>\1")
        self.assertEqual(self.current()["translation_status"], "processing_error")
        self.assertIn("BREAKING Event today", self.feed().find("item/description").text)
        self.settings_change(replace_words="Event=>Meeting")
        with patch("app.translation.output.TranslationConfig.from_env", side_effect=TranslationConfigurationError("Internal setting")):
            self.assertEqual(self.current()["translation_status"], "source_final")
            self.assertIn("BREAKING Event today", self.feed().find("item/description").text)

    def test_rss_source_publication_revokes_running_callback_and_survives_recovery(self):
        self.queue()
        job = store.claim_translation(self.config, self.digest)
        before = ElementTree.tostring(self.feed())
        self.assertEqual(self.current()["translation_status"], "source_final")
        self.assertFalse(store.complete_translation(job["id"], job["claim_token"], "Late translation", self.config, self.digest))
        self.settings_change(prefix="Changed", target_language="ar")
        remember_model(self.connection.base_url, self.config.model, "new-digest")
        from app.translation import queue
        queue.seed_requests()
        queue.recover_running_jobs()
        store.enqueue_translation(1, self.config, "new-digest")
        self.assertIsNone(store.claim_translation(self.config, "new-digest"))
        self.assertEqual(ElementTree.tostring(self.feed()), before)
        response = self.client.get("/telegram-feed/channel/news/output")
        self.assertEqual(response.json()["posts"][0]["output_text"], self.current()["output_text"])

    def test_source_publication_without_model_identity_prevents_future_queueing(self):
        with closing(database.get_connection()) as conn, conn:
            conn.execute("DELETE FROM translation_model_identity")
        self.feed()
        remember_model(self.connection.base_url, self.config.model, self.digest)
        self.assertIsNone(self.queue())
        self.assertEqual(self.current()["translation_status"], "source_final")
        second = self.add_post(2, "A different post")
        self.complete(second, "Un autre article")
        self.assertEqual(self.current()["translation_status"], "completed")

    def test_failed_post_is_final_even_after_settings_or_model_change(self):
        self.queue()
        job = store.claim_translation(self.config, self.digest)
        store.fail_translation(job["id"], job["claim_token"], OllamaConnectionError("Unavailable"))
        before = self.current()["output_text"]
        self.settings_change(prefix="Updated")
        store.enqueue_translation(1, self.config, "new-digest")
        self.assertIsNone(store.claim_translation(self.config, "new-digest"))
        self.assertEqual(self.current()["output_text"], before)

    def test_failed_feed_serialization_does_not_finalize_source_or_revoke_job(self):
        self.queue()
        job = store.claim_translation(self.config, self.digest)
        with patch("app.rss.rss._render_rss", side_effect=ValueError("Feed serialization failed")):
            with self.assertRaises(ValueError):
                generate_rss("news")
        self.assertEqual(self.current()["translation_status"], "running")
        self.assertTrue(store.complete_translation(job["id"], job["claim_token"], "Réunion", self.config, self.digest))

    def test_media_links_dates_and_mixed_language_metadata(self):
        self.complete()
        self.add_post(2, "A new event", "news/2.jpg")
        channel = self.feed()
        self.assertIsNone(channel.find("language"))
        item = next(item for item in channel.findall("item") if item.find("guid").text == "1-2")
        self.assertEqual(item.find("link").text, "https://t.me/news/2")
        self.assertEqual(item.find("enclosure").attrib["type"], "image/jpeg")
        self.assertTrue(item.find("enclosure").attrib["url"].endswith("/media/news/2.jpg"))
        self.assertIn("09 Oct 2026", item.find("pubDate").text)

    def test_xml_controls_and_html_are_safe_literal_text(self):
        malicious = '<script>alert("x")</script> & news\x00'
        self.complete(translated=malicious)
        channel = self.feed()
        description = channel.find("item/description").text
        self.assertNotIn("<script>", description)
        self.assertIn('<script>alert("x")</script> & news', unescape(description))
        self.assertNotIn("\x00", description)

    def test_legacy_rows_and_renamed_channel_use_stable_post_ids(self):
        with closing(database.get_connection()) as conn, conn:
            conn.execute("UPDATE posts SET channel_id = NULL")
        self.complete()
        self.assertEqual(self.current()["translation_status"], "completed")
        with closing(database.get_connection()) as conn, conn:
            conn.execute("UPDATE posts SET channel_id = '1'")
        upsert_channel("1", "Renamed", "new_name")
        self.assertEqual(get_channel_outputs("new_name")[0]["id"], 1)

    def test_dashboard_original_and_output_endpoint_are_safe_and_match_feed(self):
        self.complete(translated="<img src=x onerror=alert(1)> Texte traduit")
        page = self.client.get("/telegram-feed/channel/news")
        self.assertEqual(page.status_code, 200)
        self.assertIn("BREAKING Event today", page.text)
        self.assertIn("Translated · French", page.text)
        self.assertNotIn("<img src=x onerror=alert(1)>", page.text)
        response = self.client.get("/telegram-feed/channel/news/output")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["posts"][0]["output_text"], unescape(self.feed().find("item/description").text))

    def test_dashboard_can_show_posts_outside_twenty_item_feed(self):
        for index in range(2, 23):
            self.add_post(index, f"Event {index}")
        self.assertEqual(len(self.feed().findall("item")), 20)
        self.assertEqual(len(self.client.get("/telegram-feed/channel/news/output").json()["posts"]), 22)

    def test_viewer_asset_url_changes_when_script_changes(self):
        from app.dashboard import posts as posts_view

        # An old script expects a different HTML structure. A fresh URL must
        # bypass its cached copy when the viewer is updated.
        with tempfile.TemporaryDirectory() as directory:
            assets = Path(directory)
            for name in posts_view._PAGE_ASSETS:
                target = assets / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((posts_view._STATIC_DIRECTORY / name).read_bytes())
            with patch.object(posts_view, "_STATIC_DIRECTORY", assets):
                before = self.client.get("/telegram-feed/channel/news")
                script = assets / "js/channel_posts.js"
                script.write_bytes(script.read_bytes() + b"\n// Updated viewer\n")
                after = self.client.get("/telegram-feed/channel/news")
        pattern = r'src="(/static/js/channel_posts\.js\?v=[a-f0-9]+)"'
        self.assertNotEqual(re.search(pattern, before.text)[1], re.search(pattern, after.text)[1])
        self.assertEqual(before.headers["cache-control"], "no-cache")
        self.assertEqual(after.headers["cache-control"], "no-cache")

    def test_unknown_channel_and_empty_feed(self):
        self.assertEqual(get_channel_outputs("unknown"), [])
        self.assertEqual(self.client.get("/telegram-feed/channel/unknown").status_code, 200)
        self.assertEqual(ElementTree.fromstring(generate_rss("unknown")).findall("channel/item"), [])
