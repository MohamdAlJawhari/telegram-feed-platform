"""Optional local server startup, independent of prompts and translation jobs."""

import asyncio
import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib.parse import urlparse

from app.llm.ollama_client import OllamaConnectionError


class OllamaStartupError(OllamaConnectionError):
    """Contains a safe, actionable message about an automatic startup attempt."""


def local_server_address(base_url):
    url = urlparse(base_url)
    if (url.scheme != "http" or url.hostname not in ("localhost", "127.0.0.1", "::1")
            or url.path not in ("", "/") or url.username or url.query or url.fragment):
        return None
    host = "[::1]" if url.hostname == "::1" else "127.0.0.1"
    return f"{host}:{url.port or 80}"


def launch_local_server(base_url):
    """Start the installed CLI without a shell or visible terminal; never download."""
    address = local_server_address(base_url)
    if address is None:
        raise OSError("Automatic startup requires a local HTTP server address.")
    executable = shutil.which("ollama")
    if executable is None and os.name == "nt":
        install = os.environ.get("LOCALAPPDATA")
        candidate = Path(install) / "Programs/Ollama/ollama.exe" if install else None
        if candidate and candidate.is_file():
            executable = str(candidate)
    if executable is None:
        raise FileNotFoundError("Ollama is not installed or its executable could not be found.")
    environment = os.environ.copy()
    environment["OLLAMA_HOST"] = address
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    return subprocess.Popen(
        [executable, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, env=environment, close_fds=True, **options,
    )


class OllamaRuntime:
    """Check readiness on every call, but launch at most once per runtime instance.

    The server stays running when this application stops so other tasks can use it.
    Availability checks never retry a generation request.
    """

    def __init__(self, launcher=launch_local_server, clock=time.monotonic):
        self.launcher = launcher
        self.clock = clock
        self.attempted = False
        self.started_at = None
        self.process = None
        self.startup_error = None
        self._launch_lock = asyncio.Lock()

    async def require_model(self, client, model, *, auto_start):
        try:
            # A stalled server must not hold the supervisor heartbeat indefinitely.
            return await asyncio.wait_for(client.require_model(model), timeout=10)
        except OllamaConnectionError:
            base_url = getattr(getattr(client, "config", None), "base_url", "")
            if not auto_start or local_server_address(base_url) is None:
                raise
            async with self._launch_lock:
                if not self.attempted:
                    self.attempted = True
                    self.started_at = self.clock()
                    try:
                        self.process = await asyncio.to_thread(self.launcher, base_url)
                    except OSError:
                        self.startup_error = "We couldn't start Ollama automatically. Open Ollama manually or check that it is installed."
            if self.startup_error:
                raise OllamaStartupError(self.startup_error) from None
            if self.process is not None and self.process.poll() is not None:
                raise OllamaStartupError("Ollama stopped after automatic startup. Open Ollama manually and check its settings.") from None
            if self.clock() - self.started_at < 30:
                raise OllamaStartupError("Starting Ollama in the background.") from None
            raise OllamaStartupError("Ollama still isn't responding after automatic startup. Open Ollama manually and check that it is running.") from None
