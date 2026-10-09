import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

from app.llm.ollama_client import (
    OllamaAPIError, OllamaClient, OllamaConfig, OllamaConfigurationError,
    OllamaConnectionError, OllamaModelNotFoundError, OllamaResponseError,
    OllamaTimeoutError,
)


MESSAGES = [{"role": "user", "content": "Hello"}]


class OllamaClientTest(unittest.IsolatedAsyncioTestCase):
    def client(self, handler):
        return OllamaClient(OllamaConfig(), transport=httpx.MockTransport(handler))

    async def test_models_and_default_tag(self):
        def handler(request):
            self.assertEqual(request.url.path, "/api/tags")
            return httpx.Response(200, json={"models": [{"name": "translategemma:latest", "digest": "abc"}]})
        async with self.client(handler) as client:
            await client.check_connection()
            model = await client.require_model("translategemma")
            self.assertEqual(model["digest"], "abc")

    async def test_chat_preserves_metadata_and_options(self):
        def handler(request):
            self.assertEqual(request.url.path, "/api/chat")
            body = json.loads(request.content)
            self.assertFalse(body["stream"])
            self.assertEqual(body["model"], "another-model")
            self.assertEqual(body["messages"], MESSAGES)
            self.assertEqual(body["options"], {"temperature": 0})
            self.assertEqual(body["keep_alive"], "5m")
            return httpx.Response(200, json={"message": {"content": "Hi"}, "done": True, "eval_count": 2})
        async with self.client(handler) as client:
            result = await client.chat("another-model", MESSAGES, options={"temperature": 0}, keep_alive="5m")
        self.assertEqual(result["message"]["content"], "Hi")
        self.assertEqual(result["eval_count"], 2)

    async def test_transport_failures(self):
        for failure, expected in (
            (httpx.ConnectError, OllamaConnectionError),
            (httpx.ReadError, OllamaConnectionError),
            (httpx.ConnectTimeout, OllamaTimeoutError),
            (httpx.ReadTimeout, OllamaTimeoutError),
            (httpx.PoolTimeout, OllamaTimeoutError),
        ):
            with self.subTest(failure=failure):
                def handler(request):
                    raise failure("failed", request=request)
                async with self.client(handler) as client:
                    with self.assertRaises(expected):
                        await client.check_connection()

    async def test_missing_model_preflight(self):
        async with self.client(lambda _: httpx.Response(200, json={"models": []})) as client:
            with self.assertRaisesRegex(OllamaModelNotFoundError, "ollama pull missing"):
                await client.require_model("missing")

    async def test_missing_model_at_generation(self):
        for detail in ("model 'missing' not found", "model 'missing' not found, try pulling it first"):
            with self.subTest(detail=detail):
                async with self.client(lambda _: httpx.Response(404, json={"error": detail})) as client:
                    with self.assertRaises(OllamaModelNotFoundError):
                        await client.chat("missing", MESSAGES)

    async def test_chat_endpoint_404_preserves_api_error(self):
        for detail in ("endpoint not found", "model not found", "not found", None, {"message": "not found"}):
            with self.subTest(detail=detail):
                async with self.client(lambda _: httpx.Response(404, json={"error": detail})) as client:
                    with self.assertRaises(OllamaAPIError) as raised:
                        await client.chat("missing", MESSAGES)
                    self.assertEqual(raised.exception.status_code, 404)
                    self.assertNotIn("ollama pull", str(raised.exception))
                    if isinstance(detail, str):
                        self.assertIn(detail, str(raised.exception))

    async def test_wrong_endpoint_is_not_a_missing_model(self):
        async with self.client(lambda _: httpx.Response(404, json={"error": "not found"})) as client:
            with self.assertRaises(OllamaAPIError) as raised:
                await client.list_models()
            self.assertEqual(raised.exception.status_code, 404)

    async def test_api_errors_preserve_status(self):
        for status in (400, 429, 500, 503):
            with self.subTest(status=status):
                async with self.client(lambda _: httpx.Response(status, json={"error": "model cannot load"})) as client:
                    with self.assertRaises(OllamaAPIError) as raised:
                        await client.chat("model", MESSAGES)
                    self.assertEqual(raised.exception.status_code, status)
                    self.assertIn("model cannot load", str(raised.exception))

    async def test_non_json_http_error(self):
        async with self.client(lambda _: httpx.Response(502, text="Bad gateway")) as client:
            with self.assertRaises(OllamaAPIError):
                await client.list_models()

    async def test_invalid_model_responses(self):
        for data in ([], {}, {"models": "invalid"}, {"models": [{}]}, {"error": "failed"}):
            with self.subTest(data=data):
                async with self.client(lambda _: httpx.Response(200, json=data)) as client:
                    with self.assertRaises(OllamaResponseError):
                        await client.list_models()
        async with self.client(lambda _: httpx.Response(200, text="not JSON")) as client:
            with self.assertRaises(OllamaResponseError):
                await client.list_models()

    async def test_invalid_chat_responses(self):
        for data in (
            {"done": False, "message": {"content": "partial"}},
            {"done": True}, {"done": True, "message": {"content": None}},
        ):
            with self.subTest(data=data):
                async with self.client(lambda _: httpx.Response(200, json=data)) as client:
                    with self.assertRaises(OllamaResponseError):
                        await client.chat("model", MESSAGES)

    async def test_closed_client(self):
        client = self.client(lambda _: httpx.Response(200, json={"models": []}))
        await client.aclose()
        await client.aclose()
        with self.assertRaisesRegex(OllamaConnectionError, "closed"):
            await client.list_models()

    async def test_invalid_input_does_not_make_request(self):
        def handler(_):
            self.fail("Invalid input must not reach Ollama")
        async with self.client(handler) as client:
            for model, messages in (("", MESSAGES), ("model", []), ("model", [{"role": "bad", "content": "hi"}])):
                with self.assertRaises(OllamaConfigurationError):
                    await client.chat(model, messages)

    async def test_concurrent_requests_are_not_serialized(self):
        both_started = asyncio.Event()
        started = 0

        async def handler(request):
            nonlocal started
            started += 1
            if started == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)
            body = json.loads(request.content)
            return httpx.Response(200, json={"done": True, "message": {"content": body["model"]}})

        async with self.client(handler) as client:
            results = await asyncio.gather(client.chat("first", MESSAGES), client.chat("second", MESSAGES))
        self.assertEqual([result["message"]["content"] for result in results], ["first", "second"])

    async def test_cancellation_propagates(self):
        async def handler(_):
            raise asyncio.CancelledError()
        async with self.client(handler) as client:
            with self.assertRaises(asyncio.CancelledError):
                await client.chat("model", MESSAGES)


class OllamaConfigTest(unittest.TestCase):
    def test_invalid_configuration(self):
        for values in (
            {"base_url": "ftp://localhost"}, {"base_url": "localhost"},
            {"base_url": "http://user:password@localhost"},
            {"connect_timeout": 0}, {"read_timeout": float("nan")},
            {"read_timeout": float("inf")},
        ):
            with self.subTest(values=values), self.assertRaises(OllamaConfigurationError):
                OllamaConfig(**values)

    def test_environment_configuration(self):
        with patch("app.llm.ollama_client.load_dotenv"), patch.dict("os.environ", {
            "OLLAMA_BASE_URL": "http://localhost:11434", "OLLAMA_CONNECT_TIMEOUT": "2", "OLLAMA_READ_TIMEOUT": "30",
        }):
            config = OllamaConfig.from_env()
            self.assertEqual(config.connect_timeout, 2)
            self.assertEqual(config.read_timeout, 30)
        with patch("app.llm.ollama_client.load_dotenv"), patch.dict("os.environ", {"OLLAMA_READ_TIMEOUT": "wrong"}):
            with self.assertRaises(OllamaConfigurationError):
                OllamaConfig.from_env()


if __name__ == "__main__":
    unittest.main()
