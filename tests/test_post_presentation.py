import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dashboard.demo_posts import get_demo_posts
from app.dashboard.post_status import STATUS_GUIDE, STATUS_APPEARANCE, present_post, present_activity
from app.dashboard.posts import router


class PostPresentationTest(unittest.TestCase):
    def sample(self, status="pending", **values):
        return dict(translation_status=status, translation_label="Translated · Arabic",
                    translation_message=values.get("message", ""), text="News", body_text=values.get("body", "News"))

    def test_states_define_delivery_without_changing_original_or_output(self):
        for state, expected in (("completed", "Translated · Arabic"), ("disabled", "Translation disabled"),
                                ("pending", "Waiting for translation"), ("running", "Translating"),
                                ("skipped", "Translation not needed"), ("source_final", "Published without translation"),
                                ("failed", "Translation failed"), ("processing_error", "Content processing warning")):
            post = self.sample(state)
            post["output_text"] = "RSS text"
            output = present_post(post)
            self.assertEqual(output["translation_label"], expected)
            self.assertEqual(output["text"], post["text"])
            self.assertEqual(output["output_text"], post["output_text"])
            self.assertEqual(post["translation_status"], state)
            if state in ("failed", "source_final"):
                self.assertIn("Final", output["rss_label"])

    def test_paused_pending_channels_and_resource_pause_but_completed_output_still_usable(self):
        for context in ({"channel_enabled": False}, {"background": {"state": "paused"}},
                        {"background": {"state": "ready", "memory_state": "critical", "limit": 0}}):
            self.assertEqual(present_post(self.sample(), **context)["translation_label"], "Translation paused")
            self.assertEqual(present_post(self.sample("completed"), **context)["translation_status"], "completed")
        self.assertEqual(present_post(self.sample("running"), channel_enabled=False)["translation_status"], "running")
        self.assertEqual(present_post(self.sample("unavailable"), channel_enabled=False)["translation_status"], "paused")

    def test_failures_hide_provider_and_technical_instructions(self):
        for detail, label in (("Cannot connect to Ollama. Check OLLAMA_BASE_URL.", "Translation unavailable"),
                              ("Model 'translategemma:latest' isn't installed. Run ollama pull.", "Translation needs setup"),
                              ("Ollama is busy right now.", "Translation paused"),
                              ("Translation isn't set up correctly.", "Translation needs setup")):
            output = present_post(self.sample("unavailable", message=detail))
            self.assertEqual(output["translation_label"], label)
            self.assertNotIn("ollama", output["translation_message"].lower())
            self.assertNotIn("translategemma", output["translation_message"].lower())
        failure = present_post(self.sample("failed", message="Ollama is taking longer than expected."))
        self.assertIn("too long", failure["translation_message"])
        self.assertIn("not be retried", failure["translation_message"])

    def test_demo_is_available_without_database_or_ollama_and_covers_every_status(self):
        app = FastAPI()
        app.include_router(router)
        with patch("app.database.database.get_connection", side_effect=AssertionError("Demo cannot use the database")), \
                patch("app.dashboard.post_status.get_connection", side_effect=AssertionError("Demo cannot use the database")), \
                patch("app.dashboard.post_status.get_activity", side_effect=AssertionError("Demo cannot read live activity")), \
                patch("app.dashboard.posts.get_channel_outputs", side_effect=AssertionError("Demo cannot read real posts")), \
                patch("app.llm.ollama_client.OllamaClient.__init__", side_effect=AssertionError("Demo cannot use Ollama")):
            page = TestClient(app).get("/telegram-feed/demo/posts")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Demo News", page.text)
        self.assertIn('data-demo="true"', page.text)
        self.assertNotIn('href="https://t.me/', page.text)
        self.assertNotIn("Ollama", page.text)
        samples = get_demo_posts()
        self.assertEqual(len({post["id"] for post in samples}), len(samples))
        labels = {post["translation_label"].split(" · ")[0] for post in samples}
        self.assertEqual(labels, {label for label, _ in STATUS_GUIDE})
        self.assertEqual({post["message_type"] for post in samples}, {"text", "photo", "video", "document"})
        # Content controls are within the same channel header as the title.
        header = page.text.split('<header class="posts-header">')[1].split("</header>")[0]
        self.assertIn("content-mode-help", header)
        self.assertIn('data-layout="cards"', header)
        self.assertIn('id="status-guide-trigger"', header)
        self.assertIn('<dialog id="status-guide"', page.text)
        self.assertNotIn('<details class="status-guide"', page.text)
        self.assertEqual(page.text.count('class="guide-entry"'), 11)
        self.assertEqual(page.text.count('lang="ar" dir="rtl"'), 22)
        self.assertEqual(set(STATUS_APPEARANCE), {label for label, _ in STATUS_GUIDE})
        for label, definition in STATUS_GUIDE:
            self.assertIn(definition.replace("'", "&#39;"), page.text)
            tone, icon, arabic_title, arabic_definition = STATUS_APPEARANCE[label]
            self.assertIn(arabic_title, page.text)
            self.assertIn(arabic_definition, page.text)
            self.assertIn(f'id="icon-{icon}"', page.text)
        self.assertIn('Statuses describe the RSS version', page.text)

    def test_service_activity_is_friendly_and_preserves_counts(self):
        activity = present_activity(dict(state="paused", message="We couldn't reach Ollama.",
                                        connection_error="We couldn't reach Ollama.", running=2, waiting=8,
                                        memory_note="GPU memory low"))
        self.assertNotIn("Ollama", activity["message"])
        self.assertNotIn("connection_error", activity)
        self.assertEqual(activity["running"], 2)
        self.assertEqual(activity["memory_note"], "")
