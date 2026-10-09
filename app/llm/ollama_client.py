"""Async Ollama access. Translation prompts and job scheduling belong elsewhere."""

import math
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx
from dotenv import load_dotenv


class OllamaError(Exception):
    """Base exception for all client errors."""


class OllamaConfigurationError(OllamaError):
    pass


class OllamaConnectionError(OllamaError):
    pass


class OllamaTimeoutError(OllamaError):
    pass


class OllamaModelNotFoundError(OllamaError):
    pass


class OllamaResponseError(OllamaError):
    pass


class OllamaAPIError(OllamaError):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class OllamaConfig:
    base_url: str = "http://127.0.0.1:11434"
    connect_timeout: float = 5.0
    read_timeout: float = 180.0

    def __post_init__(self):
        try:
            url = httpx.URL(self.base_url)
        except (httpx.InvalidURL, TypeError) as error:
            raise OllamaConfigurationError("OLLAMA_BASE_URL is invalid.") from error
        if (url.scheme not in ("http", "https") or not url.host
                or url.userinfo or url.query or url.fragment):
            raise OllamaConfigurationError(
                "OLLAMA_BASE_URL must be an HTTP(S) address without credentials, query, or fragment."
            )
        for name, value in (("OLLAMA_CONNECT_TIMEOUT", self.connect_timeout),
                            ("OLLAMA_READ_TIMEOUT", self.read_timeout)):
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise OllamaConfigurationError(f"{name} must be positive finite seconds.")

    @classmethod
    def from_env(cls):
        load_dotenv()
        try:
            return cls(
                os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip(),
                float(os.getenv("OLLAMA_CONNECT_TIMEOUT", "5")),
                float(os.getenv("OLLAMA_READ_TIMEOUT", "180")),
            )
        except ValueError as error:
            raise OllamaConfigurationError("Ollama timeouts must be numeric seconds.") from error


class OllamaClient:
    """Reuse within one event loop; concurrent requests share the connection pool.

    Use async with or call aclose() at shutdown. No automatic retries: the caller
    decides when a failed task should run again. Generation has no fixed model.
    """

    def __init__(self, config: OllamaConfig | None = None, *, transport=None):
        self.config = config or OllamaConfig.from_env()
        self._http = httpx.AsyncClient(
            base_url=self.config.base_url.rstrip("/") + "/",
            timeout=httpx.Timeout(
                connect=self.config.connect_timeout, read=self.config.read_timeout,
                write=self.config.read_timeout, pool=self.config.connect_timeout,
            ),
            transport=transport, trust_env=False,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.aclose()

    async def aclose(self):
        await self._http.aclose()

    async def _request(self, method, path, *, payload=None, model=None):
        if self._http.is_closed:
            raise OllamaConnectionError("This Ollama client is closed; create a new client.")
        try:
            response = await self._http.request(method, path, json=payload)
        except httpx.TimeoutException as error:
            raise OllamaTimeoutError(
                "Ollama request timed out. The model may still be loading or the server may be busy."
            ) from error
        except httpx.RequestError as error:
            raise OllamaConnectionError(
                "Cannot connect to Ollama. Start Ollama and check OLLAMA_BASE_URL."
            ) from error
        try:
            data = response.json()
        except ValueError as error:
            if not response.is_success:
                raise OllamaAPIError(
                    f"Ollama returned HTTP {response.status_code} without a JSON error message.",
                    response.status_code,
                ) from error
            raise OllamaResponseError("Ollama returned invalid JSON.") from error
        detail = data.get("error") if isinstance(data, dict) else None
        if not response.is_success:
            # Endpoint/proxy 404s must not suggest downloading a model. Ollama
            # identifies a missing model explicitly in its error message.
            if (
                response.status_code == 404 and model and isinstance(detail, str)
                and re.fullmatch(
                    r"model ['\"][^'\"]+['\"] not found(?:, try pulling it first)?",
                    detail.strip(),
                )
            ):
                raise OllamaModelNotFoundError(
                    f"Ollama could not find model '{model}'. Check installed models or run: ollama pull {model}"
                )
            raise OllamaAPIError(
                f"Ollama returned HTTP {response.status_code}: {detail or 'request failed'}",
                response.status_code,
            )
        if not isinstance(data, dict):
            raise OllamaResponseError("Ollama returned an unexpected response structure.")
        if detail is not None:
            raise OllamaResponseError(f"Ollama reported an error: {detail}")
        return data

    async def list_models(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "api/tags")
        models = data.get("models")
        if not isinstance(models, list) or any(
            not isinstance(model, dict) or not isinstance(model.get("name"), str)
            for model in models
        ):
            raise OllamaResponseError("Ollama returned an invalid model list.")
        return models

    async def check_connection(self) -> None:
        """Check API availability without loading a model."""
        await self.list_models()

    async def require_model(self, model: str) -> dict[str, Any]:
        """Check installation; generation may still fail when loading the model."""
        self._validate_model(model)
        name = model if ":" in model.rsplit("/", 1)[-1] else model + ":latest"
        for installed in await self.list_models():
            if installed["name"] == name:
                return installed
        raise OllamaModelNotFoundError(
            f"Model '{model}' is not installed. Run: ollama pull {model}"
        )

    @staticmethod
    def _validate_model(model):
        if not isinstance(model, str) or not model.strip():
            raise OllamaConfigurationError("A non-empty model name is required.")

    async def chat(
        self, model: str, messages: list[dict[str, Any]], *,
        options: dict[str, Any] | None = None, keep_alive: str | int | None = None,
    ) -> dict[str, Any]:
        """Return the full non-streaming response, including generation metadata."""
        self._validate_model(model)
        if not messages or any(
            not isinstance(message, dict)
            or message.get("role") not in ("system", "user", "assistant", "tool")
            or not isinstance(message.get("content"), str)
            for message in messages
        ):
            raise OllamaConfigurationError("Chat requires messages with valid roles and string content.")
        payload = {"model": model, "messages": messages, "stream": False}
        if options is not None:
            payload["options"] = options
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        data = await self._request("POST", "api/chat", payload=payload, model=model)
        message = data.get("message")
        if (data.get("done") is not True or not isinstance(message, dict)
                or not isinstance(message.get("content"), str)):
            raise OllamaResponseError("Ollama returned an incomplete or invalid chat response.")
        return data
