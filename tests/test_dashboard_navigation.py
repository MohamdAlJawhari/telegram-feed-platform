import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from app.dashboard.routes import router


class DashboardNavigationTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        app.mount("/static", StaticFiles(directory="app/dashboard/static"))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_old_page_redirect_preserves_query_without_database_access(self):
        with patch("app.dashboard.routes.get_all_channels", side_effect=AssertionError("No database needed")):
            response = self.client.get("/dashboard?success=1", follow_redirects=False)
        self.assertEqual(response.status_code, 308)
        self.assertEqual(response.headers["location"], "/telegram-feed?success=1")

    def test_new_page_and_website_icon(self):
        with patch("app.dashboard.routes.get_all_channels", return_value=[]), \
                patch("app.dashboard.routes.get_translation_channels", return_value=[]), \
                patch("app.dashboard.routes.get_dashboard_statistics", return_value=dict(channels=0, posts=0, media=0)):
            response = self.client.get("/telegram-feed")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('"/dashboard/', response.text)
        self.assertIn('rel="icon" type="image/svg+xml" href="/static/favicon.svg"', response.text)
        icon = self.client.get("/static/favicon.svg")
        self.assertEqual(icon.status_code, 200)
        self.assertIn("image/svg+xml", icon.headers["content-type"])

    def test_channel_actions_return_to_new_page(self):
        with patch("app.dashboard.routes.set_channel_enabled"), patch("app.dashboard.routes.delete_channel"):
            for path in ("/telegram-feed/toggle/1", "/telegram-feed/delete/1"):
                response = self.client.post(path, follow_redirects=False)
                self.assertEqual(response.status_code, 303)
                self.assertEqual(response.headers["location"], "/telegram-feed")
        with patch("app.dashboard.routes.get_channel_information", new=AsyncMock(side_effect=ValueError("Missing"))):
            response = self.client.post("/telegram-feed/add-channel", data={"username": "test"}, follow_redirects=False)
        self.assertEqual(response.headers["location"], "/telegram-feed?error=1")

    def test_legacy_subpages_and_actions_redirect_to_new_prefix(self):
        for method, path in (("GET", "/dashboard/settings/1?saved=true"),
                             ("GET", "/dashboard/channel/news/output"),
                             ("POST", "/dashboard/settings/1/translation-preview")):
            response = self.client.request(method, path, follow_redirects=False)
            self.assertEqual(response.status_code, 308)
            self.assertEqual(response.headers["location"], path.replace("/dashboard", "/telegram-feed", 1))
        with patch("app.dashboard.routes.set_channel_enabled") as toggle, \
                patch("app.dashboard.routes.get_all_channels", return_value=[]), \
                patch("app.dashboard.routes.get_translation_channels", return_value=[]), \
                patch("app.dashboard.routes.get_dashboard_statistics", return_value=dict(channels=0, posts=0, media=0)):
            response = self.client.post("/dashboard/toggle/1", data={"enabled": "on"}, follow_redirects=True)
            toggle.assert_called_once_with(channel_id="1", enabled=True)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.url.path, "/telegram-feed")
