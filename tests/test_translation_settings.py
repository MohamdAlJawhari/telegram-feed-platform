import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.database.database as database
from app.database.models import get_channel_settings, update_channel_settings, upsert_channel, get_dashboard_statistics, get_channel_summaries, get_translation_channels
from app.dashboard.settings import router
from app.llm.ollama_client import OllamaConnectionError, OllamaModelNotFoundError, OllamaTimeoutError
from app.translation.service import TranslationOutputError


class TranslationSettingsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database_patch = patch.object(database, "DATABASE_NAME", str(Path(self.directory.name) / "test.db"))
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        database.create_database()
        upsert_channel("1", "First", "first")
        upsert_channel("2", "Second", "second")
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def fake_ollama(self):
        client = AsyncMock()
        client.__aenter__.return_value = client
        return client

    def test_dashboard_counts_media_and_channels_with_translation_enabled(self):
        self.assertEqual(get_dashboard_statistics()["translation_channels"], 0)

        settings = get_channel_settings("1")
        settings["translation_enabled"] = True
        update_channel_settings("1", **settings)
        with closing(database.get_connection()) as connection:
            connection.execute("UPDATE channels SET enabled = 0 WHERE channel_id = '1'")
            connection.executemany(
                "INSERT INTO posts (channel_id, channel_title, channel_username, message_id, message_type) VALUES ('1', 'First', 'first', ?, ?)",
                enumerate(("photo", "video", "document", "photo", "text"), start=1),
            )
            connection.commit()
        stats = get_dashboard_statistics()
        self.assertEqual(stats["media"], 4)
        self.assertEqual(stats["posts"], 5)
        self.assertEqual(stats["translation_channels"], 1)
        translated_channels = get_translation_channels()
        self.assertEqual(len(translated_channels), 1)
        self.assertEqual(translated_channels[0]["channel_id"], "1")
        self.assertEqual(translated_channels[0]["enabled"], 0)
        self.assertEqual(translated_channels[0]["source_language"], "en")
        self.assertEqual(translated_channels[0]["target_language"], "fr")
        settings["translation_enabled"] = False
        update_channel_settings("1", **settings)
        self.assertEqual(get_dashboard_statistics()["translation_channels"], 0)
        self.assertEqual(get_translation_channels(), [])

    def test_channel_summary_pairs_latest_text_and_date_by_message_id(self):
        with closing(database.get_connection()) as connection:
            connection.executemany(
                "INSERT INTO posts (channel_id, channel_title, channel_username, message_id, text, date) VALUES ('1', 'First', 'first', ?, ?, ?)",
                ((1, "Older message", "2026-10-11T10:00:00+00:00"),
                 (2, "Latest message", "2026-10-11T09:00:00+00:00")),
            )
            connection.commit()
        summaries = {item["channel_id"]: item for item in get_channel_summaries()}
        self.assertEqual(summaries["1"]["latest_post"], "Latest message")
        self.assertEqual(summaries["1"]["latest_post_date"], "2026-10-11T09:00:00+00:00")
        self.assertIsNone(summaries["2"]["latest_post"])
        self.assertIsNone(summaries["2"]["latest_post_date"])

    def test_page_defaults_and_channel_not_found(self):
        response = self.client.get("/telegram-feed/settings/1")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="translation-enabled"', response.text)
        self.assertIn('value="ar"', response.text)
        self.assertIn("background translation", response.text)
        self.assertFalse(get_channel_settings("1")["translation_enabled"])
        self.assertEqual(self.client.get("/telegram-feed/settings/missing").status_code, 404)

    def test_save_survives_reload_and_repeated_migration(self):
        response = self.client.post("/telegram-feed/settings/1", data={
            "prefix": "News", "suffix": "Source", "remove_keywords": "BREAKING",
            "replace_words": "Event=>Meeting", "translation_enabled": "true",
            "source_language": " AR ", "target_language": "EN",
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        database.create_database()
        self.assertEqual(get_channel_settings("1"), dict(
            prefix="News", suffix="Source", remove_keywords="BREAKING", replace_words="Event=>Meeting",
            translation_enabled=1, source_language="ar", target_language="en",
        ))
        self.assertFalse(get_channel_settings("2")["translation_enabled"])
        page = self.client.get(response.headers["location"])
        self.assertIn("Settings saved.", page.text)
        self.assertIn('value="ar" selected', page.text)
        response = self.client.post("/telegram-feed/settings/1", data={"source_language": "ar", "target_language": "en"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(get_channel_settings("1")["translation_enabled"])

    def test_invalid_language_retains_form_without_saving(self):
        response = self.client.post("/telegram-feed/settings/1", data={
            "prefix": "<script>unsaved</script>", "suffix": "Keep this", "source_language": "auto", "target_language": "en",
        })
        self.assertEqual(response.status_code, 422)
        self.assertIn("Choose a supported", response.text)
        self.assertIn("&lt;script&gt;unsaved&lt;/script&gt;", response.text)
        self.assertIn("Keep this", response.text)
        self.assertEqual(get_channel_settings("1")["prefix"], "")

    def test_failed_save_preserves_form(self):
        with patch("app.dashboard.settings.update_channel_settings", side_effect=sqlite3.OperationalError("locked")), self.assertLogs("app.dashboard.settings", level="ERROR"):
            response = self.client.post("/telegram-feed/settings/1", data={"prefix": "Keep this"})
        self.assertEqual(response.status_code, 500)
        self.assertIn("Keep this", response.text)
        self.assertIn("Could not save settings", response.text)

    def test_preview_uses_unsaved_values_without_saving(self):
        ollama = self.fake_ollama()
        translated = AsyncMock(return_value="Translated preview")
        with patch("app.dashboard.settings.OllamaClient", return_value=ollama), patch("app.dashboard.settings.TranslationService") as service:
            service.return_value.translate = translated
            response = self.client.post("/telegram-feed/settings/1/translation-preview", json={
                "text": "BREAKING Event today", "prefix": "Latest", "suffix": "Source",
                "remove_keywords": "BREAKING", "replace_words": "Event=>Meeting",
                "translation_enabled": True, "source_language": "en", "target_language": "ar",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["processed_text"], "Meeting today")
        self.assertEqual(response.json()["translated_text"], "Latest\n\nTranslated preview\n\nSource")
        translated.assert_awaited_once_with("Meeting today", "en", "ar")
        self.assertEqual(get_channel_settings("1")["prefix"], "")
        self.assertFalse(get_channel_settings("1")["translation_enabled"])
        ollama.__aexit__.assert_awaited_once()

    def test_disabled_same_language_and_empty_preview_skip_ollama(self):
        for body in (
            {"text": "News", "translation_enabled": False},
            {"text": "News", "translation_enabled": True, "source_language": "en", "target_language": "en"},
            {"text": " ", "translation_enabled": True},
        ):
            with self.subTest(body=body), patch("app.dashboard.settings.OllamaClient") as client:
                response = self.client.post("/telegram-feed/settings/1/translation-preview", json=body)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.json()["translated"])
                client.assert_not_called()

    def test_prefix_only_is_not_translated_and_rules_do_not_modify_affixes(self):
        for body, expected in (
            ({"text": "BREAKING", "remove_keywords": "BREAKING", "prefix": "BREAKING", "suffix": "Event", "translation_enabled": True}, "BREAKING\n\n\n\nEvent"),
            ({"text": "Event", "replace_words": "Event=>Meeting", "prefix": "Event", "suffix": "Event", "translation_enabled": False}, "Event\n\nMeeting\n\nEvent"),
        ):
            with self.subTest(body=body), patch("app.dashboard.settings.OllamaClient") as client:
                response = self.client.post("/telegram-feed/settings/1/translation-preview", json=body)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["translated_text"], expected)
                client.assert_not_called()

    def test_connection_messages_are_actionable_in_status_and_preview(self):
        error = OllamaConnectionError("Cannot connect to Ollama. Start Ollama and check OLLAMA_BASE_URL.")
        with patch("app.dashboard.settings.OllamaClient", side_effect=error):
            status = self.client.get("/telegram-feed/settings/1/translation-status")
            preview = self.client.post("/telegram-feed/settings/1/translation-preview", json={"text": "News", "translation_enabled": True})
        for response in (status, preview):
            self.assertEqual(response.status_code, 503)
            self.assertIn("Open Ollama", response.json()["message"])
            self.assertIn("try again", response.json()["message"])
            self.assertNotIn("OLLAMA_BASE_URL", response.json()["message"])

    def test_preview_errors_are_readable_and_not_saved(self):
        for error, status in (
            (OllamaConnectionError("Cannot connect to Ollama"), 503),
            (OllamaModelNotFoundError("Model missing"), 503),
            (OllamaTimeoutError("Request timed out"), 503),
            (TranslationOutputError("Empty output"), 503),
            (asyncio.TimeoutError(), 504),
        ):
            with self.subTest(error=error), patch("app.dashboard.settings.OllamaClient", return_value=self.fake_ollama()), patch("app.dashboard.settings.TranslationService") as service:
                service.return_value.translate = AsyncMock(side_effect=error)
                response = self.client.post("/telegram-feed/settings/1/translation-preview", json={"text": "News", "prefix": "Unsaved", "translation_enabled": True})
                self.assertEqual(response.status_code, status)
                self.assertTrue(response.json()["message"])
                self.assertNotIn("translated_text", response.json())
                self.assertEqual(get_channel_settings("1")["prefix"], "")

    def test_preview_validation_and_missing_channel(self):
        for body in ({"source_language": "auto"}, {"text": "a" * 10001}, {"replace_words": "News=>\\g<unknown>", "text": "News"}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/telegram-feed/settings/1/translation-preview", json=body).status_code, 422)
        self.assertEqual(self.client.post("/telegram-feed/settings/missing/translation-preview", json={}).status_code, 404)
        self.assertEqual(self.client.get("/telegram-feed/settings/missing/translation-status").status_code, 404)

    def test_status_checks_model_and_reports_errors(self):
        client = self.fake_ollama()
        with patch("app.dashboard.settings.OllamaClient", return_value=client):
            response = self.client.get("/telegram-feed/settings/1/translation-status")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ready"])
        client.require_model.assert_awaited_once()
        client.require_model.side_effect = OllamaModelNotFoundError("Model missing")
        with patch("app.dashboard.settings.OllamaClient", return_value=client):
            response = self.client.get("/telegram-feed/settings/1/translation-status")
        self.assertEqual(response.status_code, 503)
        self.assertIn("isn't installed", response.json()["message"])
        self.assertIn("Add the model in Ollama", response.json()["message"])

    def test_missing_settings_row_can_be_saved(self):
        with closing(database.get_connection()) as connection, connection:
            connection.execute("DELETE FROM channel_settings WHERE channel_id = '1'")
        self.client.post("/telegram-feed/settings/1", data={"prefix": "Restored"})
        self.assertEqual(get_channel_settings("1")["prefix"], "Restored")


class TranslationSettingsMigrationTest(unittest.TestCase):
    def test_legacy_rules_are_preserved_and_translation_defaults_off(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(database, "DATABASE_NAME", str(Path(directory) / "old.db")):
            with closing(database.get_connection()) as connection, connection:
                connection.execute("CREATE TABLE channel_settings (channel_id TEXT PRIMARY KEY, prefix TEXT, suffix TEXT, remove_keywords TEXT, replace_words TEXT)")
                connection.execute("INSERT INTO channel_settings VALUES ('1', 'News', 'Source', 'BREAKING', 'Event=>Meeting')")
            database.create_database()
            database.create_database()
            self.assertEqual(get_channel_settings("1"), dict(
                prefix="News", suffix="Source", remove_keywords="BREAKING", replace_words="Event=>Meeting",
                translation_enabled=0, source_language="en", target_language="fr",
            ))


if __name__ == "__main__":
    unittest.main()
