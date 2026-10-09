import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import app.database.database as database
from app.database.models import save_post, update_channel_settings, upsert_channel
from app.llm.ollama_client import OllamaConnectionError
from app.translation.service import TranslationConfig, TranslationConfigurationError, TranslationOutputError
from app.translation.store import (
    claim_translation, complete_translation, enqueue_translation, fail_translation,
    get_current_translation, get_translation_job,
)


class TranslationStoreTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database_patch = patch.object(database, "DATABASE_NAME", str(Path(directory.name) / "jobs.db"))
        database_patch.start()
        self.addCleanup(database_patch.stop)
        database.create_database()
        self.config = TranslationConfig()
        self.digest = "model-version-1"
        upsert_channel("1", "News", "news")
        self.settings = dict(
            prefix="Latest", suffix="Source", remove_keywords="BREAKING", replace_words="Event=>Meeting",
            translation_enabled=True, source_language="en", target_language="fr",
        )
        update_channel_settings("1", **self.settings)
        save_post("1", "News", "news", 1, "BREAKING Event at 10", "text", None, "2026-10-09")
        with closing(database.get_connection()) as connection:
            self.post_id = connection.execute("SELECT id FROM posts").fetchone()[0]

    def enqueue(self):
        return enqueue_translation(self.post_id, self.config, self.digest)

    def claim(self):
        return claim_translation(self.config, self.digest, post_id=self.post_id)

    def complete(self, job, text="Réunion à 10"):
        return complete_translation(job["id"], job["claim_token"], text, self.config, self.digest)

    def change_settings(self, **changes):
        self.settings.update(changes)
        update_channel_settings("1", **self.settings)

    def test_success_preserves_original_and_reuses_saved_result(self):
        queued = self.enqueue()
        self.assertEqual(queued["status"], "pending")
        self.assertEqual(queued["processed_text"], "Meeting at 10")
        running = self.claim()
        self.assertEqual(running["status"], "running")
        self.assertEqual(running["attempts"], 1)
        self.assertTrue(self.complete(running))
        saved = get_current_translation(self.post_id, self.config, self.digest)
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["translated_body"], "Réunion à 10")
        self.assertEqual(saved["output_text"], "Latest\n\nRéunion à 10\n\nSource")
        self.assertEqual(self.enqueue()["id"], queued["id"])
        self.assertIsNone(self.claim())
        with closing(database.get_connection()) as connection:
            self.assertEqual(connection.execute("SELECT text FROM posts WHERE id = ?", (self.post_id,)).fetchone()[0], "BREAKING Event at 10")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM translation_jobs").fetchone()[0], 1)

    def test_job_states_survive_connection_reopen_and_migration(self):
        queued = self.enqueue()
        database.create_database()
        self.assertEqual(get_translation_job(queued["id"])["status"], "pending")
        running = self.claim()
        database.create_database()
        self.assertEqual(get_translation_job(running["id"])["claim_token"], running["claim_token"])
        restarted = subprocess.run([
            sys.executable, "-c",
            "import sys; import app.database.database as db; "
            "from app.translation.store import get_translation_job; "
            "db.DATABASE_NAME = sys.argv[1]; "
            "job = get_translation_job(int(sys.argv[2])); "
            "assert job['status'] == 'running'; assert job['claim_token'] == sys.argv[3]",
            database.DATABASE_NAME, str(running["id"]), running["claim_token"],
        ], capture_output=True, text=True, timeout=20)
        self.assertEqual(restarted.returncode, 0, restarted.stderr)
        self.complete(running)
        database.create_database()
        self.assertEqual(self.enqueue()["status"], "completed")

    def test_failure_is_saved_without_automatic_retry_or_partial_output(self):
        self.enqueue()
        job = self.claim()
        self.assertTrue(fail_translation(job["id"], job["claim_token"], OllamaConnectionError("Connection unavailable")))
        failed = get_current_translation(self.post_id, self.config, self.digest)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error_type"], "OllamaConnectionError")
        self.assertEqual(failed["error_message"], "Connection unavailable")
        self.assertIsNone(failed["output_text"])
        self.assertEqual(self.enqueue()["status"], "failed")
        self.assertIsNone(self.claim())
        self.assertFalse(self.complete(job))

    def test_every_processing_setting_and_language_change_invalidates_output(self):
        for changes in (
            {"prefix": "Updated"}, {"suffix": "Updated"}, {"remove_keywords": "BREAKING,at"},
            {"replace_words": "Event=>Report"}, {"source_language": "ar"}, {"target_language": "de"},
        ):
            with self.subTest(changes=changes):
                old = self.enqueue()
                self.change_settings(**changes)
                self.assertIsNone(get_current_translation(self.post_id, self.config, self.digest))
                fresh = self.enqueue()
                self.assertNotEqual(old["id"], fresh["id"])

    def test_model_name_digest_and_chunk_size_invalidate_cache(self):
        original = self.enqueue()
        for config, digest in (
            (TranslationConfig(model="translategemma:12b"), self.digest),
            (self.config, "model-version-2"),
            (TranslationConfig(max_chunk_bytes=128), self.digest),
        ):
            with self.subTest(config=config, digest=digest):
                self.assertIsNone(get_current_translation(self.post_id, config, digest))
                fresh = enqueue_translation(self.post_id, config, digest)
                self.assertNotEqual(fresh["id"], original["id"])

    def test_recipe_version_invalidates_cache(self):
        old = self.enqueue()
        with patch("app.translation.store.RECIPE_VERSION", "next-recipe"):
            self.assertNotEqual(old["id"], self.enqueue()["id"])

    def test_changed_original_text_invalidates_output(self):
        old = self.enqueue()
        with closing(database.get_connection()) as connection, connection:
            connection.execute("UPDATE posts SET text = 'New report' WHERE id = ?", (self.post_id,))
        self.assertIsNone(get_current_translation(self.post_id, self.config, self.digest))
        self.assertNotEqual(old["id"], self.enqueue()["id"])

    def test_old_running_job_cannot_overwrite_new_result(self):
        self.enqueue()
        old = self.claim()
        self.change_settings(target_language="de")
        newest = self.enqueue()
        running = self.claim()
        self.assertTrue(self.complete(running, "Treffen um 10"))
        self.assertFalse(self.complete(old, "Late French translation"))
        self.assertFalse(fail_translation(old["id"], old["claim_token"], RuntimeError("late failure")))
        current = get_current_translation(self.post_id, self.config, self.digest)
        self.assertEqual(current["id"], newest["id"])
        self.assertEqual(current["translated_body"], "Treffen um 10")

    def test_completion_checks_live_settings_even_without_new_enqueue(self):
        self.enqueue()
        job = self.claim()
        self.change_settings(prefix="New")
        self.assertFalse(self.complete(job))
        self.assertEqual(get_translation_job(job["id"])["error_type"], "Superseded")
        self.assertIsNone(get_current_translation(self.post_id, self.config, self.digest))

    def test_claim_rejects_stale_pending_jobs(self):
        old = self.enqueue()
        self.change_settings(target_language="de")
        self.assertIsNone(self.claim())
        self.assertEqual(get_translation_job(old["id"])["status"], "failed")

    def test_disabled_same_language_and_empty_body_remove_current_job(self):
        for changes in (
            {"translation_enabled": False}, {"source_language": "en", "target_language": "en"},
            {"remove_keywords": "BREAKING Event at 10"},
        ):
            with self.subTest(changes=changes):
                update_channel_settings("1", **dict(self.settings, translation_enabled=True, source_language="en", target_language="fr", remove_keywords="BREAKING"))
                self.enqueue()
                self.change_settings(**changes)
                self.assertIsNone(self.enqueue())
                self.assertIsNone(get_current_translation(self.post_id, self.config, self.digest))

    def test_reverting_settings_reuses_completed_output(self):
        self.enqueue()
        original = self.claim()
        self.complete(original)
        self.change_settings(prefix="New")
        self.enqueue()
        self.change_settings(prefix="Latest")
        restored = self.enqueue()
        self.assertEqual(restored["id"], original["id"])
        self.assertEqual(restored["status"], "completed")

    def test_superseded_attempt_token_cannot_finish_requeued_job(self):
        self.enqueue()
        old = self.claim()
        self.change_settings(prefix="New")
        self.enqueue()
        self.change_settings(prefix="Latest")
        self.assertEqual(self.enqueue()["status"], "pending")
        new = self.claim()
        self.assertEqual(old["id"], new["id"])
        self.assertNotEqual(old["claim_token"], new["claim_token"])
        self.assertFalse(self.complete(old))
        self.assertTrue(self.complete(new))

    def test_concurrent_enqueues_and_claims_are_atomic(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            enqueued = list(pool.map(lambda _: self.enqueue(), range(4)))
            claimed = list(pool.map(lambda _: self.claim(), range(4)))
        self.assertEqual(len({job["id"] for job in enqueued}), 1)
        self.assertEqual(sum(job is not None for job in claimed), 1)

    def test_different_channels_can_have_running_jobs(self):
        self.enqueue()
        upsert_channel("2", "Second", "second")
        update_channel_settings("2", **self.settings)
        save_post("2", "Second", "second", 1, "Second report", "text", None, "2026-10-09")
        with closing(database.get_connection()) as connection:
            post_id = connection.execute("SELECT id FROM posts WHERE channel_id = '2'").fetchone()[0]
        enqueue_translation(post_id, self.config, self.digest)
        first = claim_translation(self.config, self.digest)
        second = claim_translation(self.config, self.digest)
        self.assertNotEqual(first["post_id"], second["post_id"])
        self.assertEqual((first["status"], second["status"]), ("running", "running"))

    def test_invalid_parameters_and_empty_result(self):
        with self.assertRaises(TranslationConfigurationError):
            enqueue_translation(self.post_id, self.config, "")
        with self.assertRaises(TranslationConfigurationError):
            enqueue_translation(999, self.config, self.digest)
        self.enqueue()
        job = self.claim()
        for operation in (
            lambda: claim_translation(self.config, ""),
            lambda: get_current_translation(self.post_id, self.config, ""),
            lambda: complete_translation(job["id"], job["claim_token"], "News", self.config, ""),
        ):
            with self.assertRaises(TranslationConfigurationError):
                operation()
        self.assertFalse(complete_translation(job["id"], "wrong-token", "News", self.config, self.digest))
        with self.assertRaises(TranslationOutputError):
            self.complete(job, " ")
        self.assertEqual(get_translation_job(job["id"])["status"], "running")

    def test_legacy_post_without_channel_id_can_be_translated(self):
        with closing(database.get_connection()) as connection, connection:
            connection.execute("UPDATE posts SET channel_id = NULL WHERE id = ?", (self.post_id,))
        queued = self.enqueue()
        self.assertEqual(json.loads(queued["snapshot_json"])["channel_id"], "1")

    def test_untracked_or_deleted_post_never_exposes_saved_output(self):
        self.enqueue()
        self.complete(self.claim())
        with closing(database.get_connection()) as connection, connection:
            connection.execute("DELETE FROM channels WHERE channel_id = '1'")
        self.assertIsNone(get_current_translation(self.post_id, self.config, self.digest))
        self.assertIsNone(self.enqueue())


if __name__ == "__main__":
    unittest.main()
