import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dashboard.translation_activity import router
from app.llm.ollama_client import OllamaConnectionError, OllamaModelNotFoundError
from app.llm.ollama_runtime import OllamaRuntime, OllamaStartupError, launch_local_server


class OllamaRuntimeTest(unittest.IsolatedAsyncioTestCase):
    def client(self, error=None, url="http://127.0.0.1:11434"):
        return SimpleNamespace(config=SimpleNamespace(base_url=url),
                               require_model=AsyncMock(side_effect=error, return_value={"digest": "v1"}))

    async def test_unavailable_local_server_launches_once_and_recovers(self):
        launcher = Mock(return_value=Mock(poll=Mock(return_value=None)))
        runtime = OllamaRuntime(launcher=launcher)
        client = self.client(OllamaConnectionError("Offline"))
        for _ in range(2):
            with self.assertRaisesRegex(OllamaStartupError, "Starting Ollama"):
                await runtime.require_model(client, "model", auto_start=True)
        launcher.assert_called_once_with(client.config.base_url)
        client.require_model.side_effect = None
        self.assertEqual(await runtime.require_model(client, "model", auto_start=True), {"digest": "v1"})
        launcher.assert_called_once()

    async def test_remote_disabled_https_and_proxy_addresses_never_launch(self):
        for url, enabled in (("http://remote.test:11434", True), ("http://127.0.0.1:11434", False),
                             ("https://localhost:11434", True), ("http://localhost:11434/proxy", True)):
            launcher = Mock()
            runtime = OllamaRuntime(launcher=launcher)
            with self.assertRaises(OllamaConnectionError):
                await runtime.require_model(self.client(OllamaConnectionError("Offline"), url), "model", auto_start=enabled)
            launcher.assert_not_called()

    async def test_installed_server_and_missing_model_do_not_launch_or_download(self):
        launcher = Mock()
        runtime = OllamaRuntime(launcher=launcher)
        await runtime.require_model(self.client(), "model", auto_start=True)
        with self.assertRaises(OllamaModelNotFoundError):
            await runtime.require_model(self.client(OllamaModelNotFoundError("Missing")), "model", auto_start=True)
        launcher.assert_not_called()

    async def test_launch_failure_is_actionable_and_not_repeated(self):
        launcher = Mock(side_effect=FileNotFoundError("Private executable path"))
        runtime = OllamaRuntime(launcher=launcher)
        for _ in range(2):
            with self.assertRaisesRegex(OllamaStartupError, "check that it is installed"):
                await runtime.require_model(self.client(OllamaConnectionError("Offline")), "model", auto_start=True)
        launcher.assert_called_once()

    async def test_startup_deadline_and_early_exit_are_reported(self):
        clock = Mock(return_value=0)
        process = Mock(poll=Mock(return_value=None))
        runtime = OllamaRuntime(launcher=Mock(return_value=process), clock=clock)
        client = self.client(OllamaConnectionError("Offline"))
        with self.assertRaises(OllamaStartupError):
            await runtime.require_model(client, "model", auto_start=True)
        clock.return_value = 31
        with self.assertRaisesRegex(OllamaStartupError, "still isn't responding"):
            await runtime.require_model(client, "model", auto_start=True)
        process.poll.return_value = 1
        with self.assertRaisesRegex(OllamaStartupError, "stopped after"):
            await runtime.require_model(client, "model", auto_start=True)

    def test_launcher_uses_cli_loopback_host_and_no_shell(self):
        with patch("app.llm.ollama_runtime.shutil.which", return_value="installed-ollama"), \
                patch("app.llm.ollama_runtime.subprocess.Popen") as spawn:
            launch_local_server("http://localhost:11435")
        args, options = spawn.call_args
        self.assertEqual(args[0], ["installed-ollama", "serve"])
        self.assertEqual(options["env"]["OLLAMA_HOST"], "127.0.0.1:11435")
        self.assertFalse(options.get("shell", False))
        if __import__("os").name == "nt":
            self.assertTrue(options["creationflags"] & __import__("subprocess").CREATE_NO_WINDOW)

    def test_dashboard_has_no_startup_switch_or_settings_endpoint(self):
        app = FastAPI()
        app.include_router(router)
        response = TestClient(app).post("/telegram-feed/ollama-settings", data={"ollama_auto_start": "false"})
        self.assertEqual(response.status_code, 404)
        markup = Path("app/dashboard/templates/dashboard.html").read_text(encoding="utf-8")
        self.assertNotIn('name="ollama_auto_start"', markup)
