"""Actionable dashboard messages; retain technical error types in the client."""

import asyncio

from app.llm.ollama_client import (
    OllamaAPIError, OllamaConfigurationError, OllamaConnectionError,
    OllamaModelNotFoundError, OllamaTimeoutError,
)
from app.translation.service import TranslationConfigurationError
from app.llm.ollama_runtime import OllamaStartupError


def translation_error_message(error: Exception) -> str:
    if isinstance(error, OllamaStartupError):
        return str(error)
    if isinstance(error, OllamaConnectionError):
        return "We couldn't reach Ollama. Open Ollama on the computer running this application, then try again."
    if isinstance(error, OllamaModelNotFoundError):
        return "The selected translation model isn't installed in Ollama. Add the model in Ollama, then try again."
    if isinstance(error, (OllamaTimeoutError, asyncio.TimeoutError)):
        return "Ollama is taking longer than expected. Wait a moment and try again. For previews, you can also try shorter text."
    if isinstance(error, (OllamaConfigurationError, TranslationConfigurationError)):
        return "Translation isn't set up correctly. Ask the person who manages this application to check its translation settings."
    if isinstance(error, OllamaAPIError):
        if error.status_code in (429, 503):
            return "Ollama is busy right now. Wait a moment, then try again."
        return "Ollama couldn't run this request. Restart Ollama and try again. If this continues, ask the person who manages this application to check it."
    return "Ollama couldn't produce a complete translation. Try again, or use a shorter sample."
