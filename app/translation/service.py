"""Translate text with TranslateGemma; connection details stay in the LLM client."""

import os
import re
from dataclasses import dataclass

from dotenv import load_dotenv

from app.llm.ollama_client import OllamaClient
from app.translation.languages import LANGUAGES, normalize_language


class TranslationError(Exception):
    """Task-specific errors. Transport failures remain OllamaError subclasses."""


class TranslationConfigurationError(TranslationError):
    pass


class TranslationOutputError(TranslationError):
    pass


@dataclass(frozen=True)
class TranslationConfig:
    model: str = "translategemma:latest"
    max_chunk_bytes: int = 1600

    def __post_init__(self):
        if not isinstance(self.model, str) or not self.model.strip():
            raise TranslationConfigurationError("TRANSLATION_MODEL must not be empty.")
        # Byte bounds conservatively limit input tokens, including non-Latin text.
        if type(self.max_chunk_bytes) is not int or not 64 <= self.max_chunk_bytes <= 3200:
            raise TranslationConfigurationError("TRANSLATION_MAX_CHUNK_BYTES must be an integer between 64 and 3200.")

    @classmethod
    def from_env(cls):
        load_dotenv()
        try:
            return cls(
                model=os.getenv("TRANSLATION_MODEL", "translategemma:latest").strip(),
                max_chunk_bytes=int(os.getenv("TRANSLATION_MAX_CHUNK_BYTES", "1600")),
            )
        except ValueError as error:
            raise TranslationConfigurationError("TRANSLATION_MAX_CHUNK_BYTES must be an integer.") from error


def split_text(text: str, max_bytes: int) -> list[tuple[str, str]]:
    """Return (text, trailing separator) chunks without losing source characters.

    Prefer whitespace boundaries; an unusually long unbroken token is split at
    a Unicode character boundary. Chunking may reduce context across boundaries.
    """
    if type(max_bytes) is not int or max_bytes < 4:
        raise ValueError("Chunk size must allow at least one UTF-8 character (4 bytes).")
    chunks = []
    remaining = text
    while remaining:
        # A hard split can end exactly before whitespace. Keep the full run as
        # formatting on the previous chunk, even when it exceeds the byte limit.
        leading_whitespace = re.match(r"\s+", remaining) if chunks else None
        if leading_whitespace:
            separator = leading_whitespace.group()
            previous_text, previous_separator = chunks[-1]
            chunks[-1] = (previous_text, previous_separator + separator)
            remaining = remaining[len(separator):]
            if not remaining:
                break
        if len(remaining.encode("utf-8")) <= max_bytes:
            chunks.append((remaining, ""))
            break
        candidate = remaining.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
        boundaries = [match for match in re.finditer(r"\s+", candidate) if match.start() > 0]
        if boundaries:
            # Prefer a paragraph boundary, otherwise the last word boundary.
            paragraphs = [match for match in boundaries if "\n\n" in match.group() or "\r\n\r\n" in match.group()]
            boundary = (paragraphs or boundaries)[-1]
            end = boundary.start()
            separator = re.match(r"\s+", remaining[end:]).group()
            chunks.append((remaining[:end], separator))
            remaining = remaining[end + len(separator):]
        else:
            chunks.append((candidate, ""))
            remaining = remaining[len(candidate):]
    return chunks


def build_prompt(text: str, source: str, target: str) -> str:
    """Ollama's documented TranslateGemma single-user-message format."""
    source_name, target_name = LANGUAGES[source], LANGUAGES[target]
    return (
        f"You are a professional {source_name} ({source}) to {target_name} ({target}) translator. "
        f"Your goal is to accurately convey the meaning and nuances of the original {source_name} text "
        f"while adhering to {target_name} grammar, vocabulary, and cultural sensitivities.\n"
        f"Produce only the {target_name} translation, without any additional explanations or commentary. "
        f"Please translate the following {source_name} text into {target_name}:\n\n\n{text}"
    )


class TranslationService:
    """Borrow a client; its owner handles lifecycle and can share it across tasks.

    No mutable per-request state or service-wide locks: independent translations
    can overlap. Chunks within one translation run in order. No storage or retries.
    """

    def __init__(self, client: OllamaClient, config: TranslationConfig | None = None):
        self.client = client
        self.config = config or TranslationConfig.from_env()

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        try:
            source = normalize_language(source_language)
            target = normalize_language(target_language)
        except ValueError as error:
            raise TranslationConfigurationError(str(error)) from error
        if not isinstance(text, str):
            raise TranslationConfigurationError("Translation input must be text.")
        if not text.strip():
            return ""
        if source == target:
            return text
        try:
            chunks = split_text(text.strip(), self.config.max_chunk_bytes)
        except UnicodeEncodeError as error:
            raise TranslationConfigurationError("Translation input contains invalid Unicode characters.") from error
        translated = []
        for index, (chunk, separator) in enumerate(chunks, start=1):
            response = await self.client.chat(
                self.config.model,
                [{"role": "user", "content": build_prompt(chunk, source, target)}],
                options={"temperature": 0, "num_ctx": 8192, "num_predict": 4096},
            )
            # The generic client validates response structure; task validation is here.
            if response.get("done_reason") == "length":
                raise TranslationOutputError(
                    f"Translation chunk {index}/{len(chunks)} reached the output limit. No partial translation returned."
                )
            output = response["message"]["content"].strip()
            if not output:
                raise TranslationOutputError(
                    f"Translation chunk {index}/{len(chunks)} returned empty text. No partial translation returned."
                )
            translated.append(output + separator)
        return "".join(translated)
