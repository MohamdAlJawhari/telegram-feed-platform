import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from app.database import database
from app.database.models import upsert_channel
from app.database.retention import enforce_retention
from app.rss.rss import generate_rss
from app.translation.output import get_channel_outputs


class RssWindowTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        for replacement in (
            patch.object(database, "DATABASE_NAME", str(Path(directory.name) / "test.db")),
            patch("app.database.retention.get_media_root", return_value=Path(directory.name) / "media"),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)
        for module in ("app.rss.rss", "app.database.retention"):
            clock = patch(module + ".datetime")
            clock.start().now.return_value = self.now
            self.addCleanup(clock.stop)
        database.create_database()
        upsert_channel("1", "News", "news")

    def seed(self, dates, channel_id="1", username="news"):
        with closing(database.get_connection()) as conn, conn:
            conn.executemany("""
                INSERT INTO posts(channel_id, channel_title, channel_username, message_id, text, date)
                VALUES (?, 'News', ?, ?, 'News', ?)
            """, [(channel_id, username, number, date) for number, date in enumerate(dates, 1)])

    def items(self):
        return ElementTree.fromstring(generate_rss("news")).findall("channel/item")

    def test_window_boundaries_offsets_and_channel_scope(self):
        self.seed([
            (self.now - timedelta(hours=24, seconds=1)).isoformat(),
            (self.now - timedelta(hours=24)).isoformat(),
            "2026-11-08T14:00:00+02:00",  # Same instant as the cutoff.
            self.now.isoformat(),
            (self.now + timedelta(seconds=1)).isoformat(),
            "invalid", None,
        ])
        self.seed([self.now.isoformat()], "2", "other")
        self.assertEqual([item.findtext("guid") for item in self.items()], ["1-4", "1-3", "1-2"])
        # Dashboard/history selection remains independent of the RSS window.
        self.assertEqual(len(get_channel_outputs("news")), 7)
        with patch("app.rss.rss.datetime") as clock:
            clock.now.return_value = self.now + timedelta(seconds=1)
            self.assertEqual([item.findtext("guid") for item in self.items()], ["1-5", "1-4"])

    def test_busy_channel_retains_and_publishes_more_than_700_recent_posts(self):
        self.seed([(self.now - timedelta(hours=1)).isoformat()] * 725)
        self.assertEqual(enforce_retention(), 0)
        self.assertEqual(len(self.items()), 725)
        # Once the posts age out of the RSS window, the normal history cap applies.
        with patch("app.database.retention.datetime") as clock:
            clock.now.return_value = self.now + timedelta(days=1)
            self.assertEqual(enforce_retention(), 25)

    def test_quiet_and_unknown_channels_have_empty_feeds(self):
        self.seed([(self.now - timedelta(days=2)).isoformat()])
        self.assertEqual(self.items(), [])
        self.assertEqual(ElementTree.fromstring(generate_rss("unknown")).findall("channel/item"), [])
