"""Initial selectable languages from the TranslateGemma Ollama language list.

This is an application allowlist, not a claim of equal quality for every pair.
Source: https://ollama.com/library/translategemma
"""

from types import MappingProxyType


LANGUAGES = MappingProxyType({
    "ar": "Arabic", "bg": "Bulgarian", "cs": "Czech", "da": "Danish",
    "de": "German", "el": "Greek", "en": "English", "es": "Spanish",
    "fa": "Persian", "fi": "Finnish", "fr": "French", "he": "Hebrew",
    "hi": "Hindi", "hr": "Croatian", "hu": "Hungarian", "id": "Indonesian",
    "it": "Italian", "ja": "Japanese", "ko": "Korean", "lt": "Lithuanian",
    "lv": "Latvian", "ms": "Malay", "nl": "Dutch", "no": "Norwegian",
    "pl": "Polish", "pt": "Portuguese", "ro": "Romanian", "ru": "Russian",
    "sk": "Slovak", "sl": "Slovenian", "sv": "Swedish", "th": "Thai",
    "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu", "vi": "Vietnamese",
    "zh-Hans": "Chinese (Simplified)", "zh-Hant": "Chinese (Traditional)",
})


def normalize_language(code: str) -> str:
    """Accept case-insensitive codes, with zh as an alias for Simplified Chinese."""
    if not isinstance(code, str):
        raise ValueError("Language must be a language code such as en, ar, or fr.")
    normalized = code.strip().replace("_", "-").casefold()
    if normalized == "zh":
        normalized = "zh-hans"
    for canonical in LANGUAGES:
        if canonical.casefold() == normalized:
            return canonical
    raise ValueError(
        f"Unsupported language code '{code}'. Use --languages to list available codes; automatic detection is not supported yet."
    )
