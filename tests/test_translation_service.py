import argparse
import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

from app.llm.ollama_client import (
    OllamaAPIError, OllamaClient, OllamaConfig, OllamaConnectionError,
    OllamaModelNotFoundError, OllamaResponseError, OllamaTimeoutError,
)
from app.translation.check_translation import run_check
from app.translation.languages import normalize_language
from app.translation.service import (
    TranslationConfig, TranslationConfigurationError, TranslationOutputError,
    TranslationService, split_text,
)


def reply(text, reason="stop"):
    return httpx.Response(200, json={"done": True, "done_reason": reason, "message": {"content": text}})


class TranslationServiceTest(unittest.IsolatedAsyncioTestCase):
    def client(self, handler):
        return OllamaClient(OllamaConfig(), transport=httpx.MockTransport(handler))

    async def test_prompt_and_generation_options(self):
        def handler(request):
            body = json.loads(request.content)
            self.assertEqual(body["model"], "translategemma:latest")
            self.assertEqual(len(body["messages"]), 1)
            self.assertEqual(body["messages"][0]["role"], "user")
            prompt = body["messages"][0]["content"]
            self.assertIn("English (en) to Arabic (ar)", prompt)
            self.assertIn("without any additional explanations", prompt)
            self.assertTrue(prompt.endswith("\n\n\nThe meeting starts at 10."))
            self.assertEqual(body["options"]["temperature"], 0)
            return reply("  يبدأ الاجتماع الساعة 10.  ")
        async with self.client(handler) as client:
            translated = await TranslationService(client, TranslationConfig()).translate("The meeting starts at 10.", " EN ", "ar")
        self.assertEqual(translated, "يبدأ الاجتماع الساعة 10.")

    async def test_empty_and_same_language_need_no_ollama(self):
        def handler(_):
            self.fail("No connection should be made")
        async with self.client(handler) as client:
            service = TranslationService(client, TranslationConfig())
            self.assertEqual(await service.translate(" \n\t", "ar", "en"), "")
            self.assertEqual(await service.translate("  News\n", "EN", "en"), "  News\n")
            self.assertEqual(await service.translate("新闻", "zh", "zh_Hans"), "新闻")

    async def test_invalid_languages_and_input_need_no_request(self):
        def handler(_):
            self.fail("Invalid input must not reach Ollama")
        async with self.client(handler) as client:
            service = TranslationService(client, TranslationConfig())
            for text, source, target in (("hi", "auto", "en"), ("hi", "en", "xx"), (None, "en", "fr"), ("hi", None, "fr"), ("\ud800", "en", "fr")):
                with self.subTest(source=source, target=target), self.assertRaises(TranslationConfigurationError):
                    await service.translate(text, source, target)

    async def test_long_text_uses_all_chunks_in_order(self):
        seen = []
        original = "First paragraph with some news.\n\n" + "Another report from Beirut. " * 12

        def handler(request):
            prompt = json.loads(request.content)["messages"][0]["content"]
            seen.append(prompt.split("\n\n\n", 1)[1])
            return reply(f"translated-{len(seen)}")
        async with self.client(handler) as client:
            result = await TranslationService(client, TranslationConfig(max_chunk_bytes=64)).translate(original, "en", "fr")
        chunks = split_text(original.strip(), 64)
        self.assertEqual(seen, [text for text, _ in chunks])
        self.assertEqual(result, "".join(f"translated-{i}{separator}" for i, (_, separator) in enumerate(chunks, 1)))

    async def test_failed_later_chunk_does_not_return_partial_output(self):
        calls = 0

        def handler(_):
            nonlocal calls
            calls += 1
            return reply("translated" if calls == 1 else " ")
        async with self.client(handler) as client:
            with self.assertRaisesRegex(TranslationOutputError, "chunk 2/"):
                await TranslationService(client, TranslationConfig(max_chunk_bytes=64)).translate("Word " * 60, "en", "fr")
        self.assertEqual(calls, 2)

    async def test_whitespace_after_full_chunk_is_preserved_without_translation(self):
        for separator in (" " * 100, "\n\t " * 100, "\u2003" * 100, " "):
            with self.subTest(separator=repr(separator[:10])):
                seen = []

                def handler(request):
                    prompt = json.loads(request.content)["messages"][0]["content"]
                    chunk = prompt.split("\n\n\n", 1)[1]
                    seen.append(chunk)
                    if not chunk.strip():
                        return reply("")
                    return reply("first" if chunk == "a" * 64 else "second")

                async with self.client(handler) as client:
                    service = TranslationService(client, TranslationConfig(max_chunk_bytes=64))
                    result = await service.translate("a" * 64 + separator + "b", "en", "fr")
                self.assertEqual(seen, ["a" * 64, "b"])
                self.assertEqual(result, "first" + separator + "second")

    async def test_truncated_output_is_rejected(self):
        async with self.client(lambda _: reply("unfinished", "length")) as client:
            with self.assertRaisesRegex(TranslationOutputError, "output limit"):
                await TranslationService(client, TranslationConfig()).translate("Some news", "en", "fr")

    async def test_malformed_output_keeps_client_error_type(self):
        async with self.client(lambda _: httpx.Response(200, json={"done": True, "message": {"content": None}})) as client:
            with self.assertRaises(OllamaResponseError):
                await TranslationService(client, TranslationConfig()).translate("News", "en", "fr")

    async def test_transport_and_api_errors_are_not_swallowed(self):
        for failure, expected in ((httpx.ConnectError, OllamaConnectionError), (httpx.ReadTimeout, OllamaTimeoutError)):
            with self.subTest(failure=failure):
                def handler(request):
                    raise failure("failed", request=request)
                async with self.client(handler) as client:
                    with self.assertRaises(expected):
                        await TranslationService(client, TranslationConfig()).translate("News", "en", "fr")
        for status, message, expected in ((404, "model 'missing' not found", OllamaModelNotFoundError), (404, "endpoint not found", OllamaAPIError), (500, "not enough memory", OllamaAPIError)):
            with self.subTest(status=status, message=message):
                async with self.client(lambda _: httpx.Response(status, json={"error": message})) as client:
                    with self.assertRaises(expected):
                        await TranslationService(client, TranslationConfig()).translate("News", "en", "fr")

    async def test_parallel_calls_keep_language_pairs_separate(self):
        both_started = asyncio.Event()
        prompts = []

        async def handler(request):
            prompt = json.loads(request.content)["messages"][0]["content"]
            prompts.append(prompt)
            if len(prompts) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), 1)
            return reply("français" if "to French (fr)" in prompt else "عربي")
        async with self.client(handler) as client:
            service = TranslationService(client, TranslationConfig())
            results = await asyncio.gather(service.translate("First", "en", "fr"), service.translate("Second", "en", "ar"))
        self.assertEqual(results, ["français", "عربي"])

    async def test_manual_pipeline_adds_untranslated_prefix_and_suffix(self):
        def handler(request):
            prompt = json.loads(request.content)["messages"][0]["content"]
            self.assertTrue(prompt.endswith("\n\n\nMeeting today"))
            self.assertNotIn("Latest news", prompt)
            self.assertNotIn("Source: Telegram", prompt)
            return reply("Réunion aujourd’hui")
        args = argparse.Namespace(
            model=None, chunk_bytes=None, base_url=None, remove_keywords="BREAKING",
            replace_words="Event=>Meeting", prefix="Latest news", suffix="Source: Telegram",
            show_processed=False, source="en", target="fr",
        )
        with patch("app.translation.check_translation.OllamaClient", side_effect=lambda _: self.client(handler)), patch("app.translation.check_translation.TranslationConfig.from_env", return_value=TranslationConfig()), patch("app.translation.check_translation.OllamaConfig.from_env", return_value=OllamaConfig()):
            result = await run_check(args, "BREAKING Event today")
        self.assertEqual(result, "Latest news\n\nRéunion aujourd’hui\n\nSource: Telegram")

    async def test_cancellation_propagates(self):
        async def handler(_):
            raise asyncio.CancelledError()
        async with self.client(handler) as client:
            with self.assertRaises(asyncio.CancelledError):
                await TranslationService(client, TranslationConfig()).translate("News", "en", "fr")


class TranslationConfigAndChunkTest(unittest.TestCase):
    def test_invalid_config(self):
        for values in ({"model": ""}, {"max_chunk_bytes": 0}, {"max_chunk_bytes": True}, {"max_chunk_bytes": 3201}):
            with self.subTest(values=values), self.assertRaises(TranslationConfigurationError):
                TranslationConfig(**values)

    def test_env_config(self):
        with patch("app.translation.service.load_dotenv"), patch.dict("os.environ", {"TRANSLATION_MODEL": "translategemma:12b", "TRANSLATION_MAX_CHUNK_BYTES": "128"}):
            self.assertEqual(TranslationConfig.from_env(), TranslationConfig("translategemma:12b", 128))
        with patch("app.translation.service.load_dotenv"), patch.dict("os.environ", {"TRANSLATION_MAX_CHUNK_BYTES": "invalid"}):
            with self.assertRaises(TranslationConfigurationError):
                TranslationConfig.from_env()

    def test_unicode_chunking_preserves_every_character(self):
        for text in ("", "short", "Word " * 500, "أخبار عاجلة\n\n" * 100, "新闻" * 100, "😀" * 100, "a" * 63 + "\r\n\r\n" + "b" * 100, "a" * 64 + " " * 100):
            with self.subTest(text=text[:20]):
                chunks = split_text(text, 64)
                self.assertEqual("".join(chunk + separator for chunk, separator in chunks), text)
                self.assertTrue(all(0 < len(chunk.encode("utf-8")) <= 64 for chunk, _ in chunks))

    def test_invalid_split_bound(self):
        with self.assertRaises(ValueError):
            split_text("news", 0)

    def test_language_aliases(self):
        self.assertEqual(normalize_language(" FR "), "fr")
        self.assertEqual(normalize_language("zh_hant"), "zh-Hant")


if __name__ == "__main__":
    unittest.main()
