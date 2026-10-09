"""Test translation and text processing without starting Telegram or FastAPI."""

import argparse
import asyncio
import sys
from pathlib import Path

from app.llm.ollama_client import OllamaClient, OllamaConfig, OllamaError
from app.processing.processor import process_body, add_affixes
from app.translation.languages import LANGUAGES
from app.translation.service import TranslationConfig, TranslationError, TranslationService


async def run_check(args, text):
    config = TranslationConfig.from_env()
    if args.model is not None or args.chunk_bytes is not None:
        config = TranslationConfig(
            args.model if args.model is not None else config.model,
            args.chunk_bytes if args.chunk_bytes is not None else config.max_chunk_bytes,
        )
    connection = OllamaConfig.from_env()
    if args.base_url:
        connection = OllamaConfig(args.base_url, connection.connect_timeout, connection.read_timeout)
    settings = {
        "remove_keywords": args.remove_keywords, "replace_words": args.replace_words,
        "prefix": args.prefix, "suffix": args.suffix,
    }
    processed = process_body(text, settings)
    if args.show_processed:
        print("Processed input:", file=sys.stderr)
        print(processed, file=sys.stderr)
    async with OllamaClient(connection) as client:
        service = TranslationService(client, config)
        translated = await service.translate(processed, args.source, args.target)
        return add_affixes(translated, settings)


def main():
    # Support Arabic and other scripts even when Windows redirects output.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--languages", action="store_true", help="List selectable language codes.")
    parser.add_argument("--source", help="Source language code, such as en or ar.")
    parser.add_argument("--target", help="Target language code, such as fr or en.")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--text", help="Text to translate.")
    inputs.add_argument("--file", type=Path, help="Read a UTF-8 text file (also accepts a BOM).")
    parser.add_argument("--model", help="Override TRANSLATION_MODEL for this check.")
    parser.add_argument("--base-url", help="Override Ollama address for this check.")
    parser.add_argument("--chunk-bytes", type=int, help="Override chunk size (64–3200 UTF-8 bytes).")
    parser.add_argument("--remove-keywords", default="")
    parser.add_argument("--replace-words", default="", help="Rules in old=>new format.")
    parser.add_argument("--prefix", default="")
    parser.add_argument("--suffix", default="")
    parser.add_argument("--show-processed", action="store_true")
    args = parser.parse_args()
    if args.languages:
        for code, name in sorted(LANGUAGES.items(), key=lambda item: item[1]):
            print(f"{code:8} {name}")
        return 0
    if not args.source or not args.target or (args.text is None and args.file is None):
        parser.error("--source, --target, and either --text or --file are required")
    try:
        text = args.file.read_text(encoding="utf-8-sig") if args.file else args.text
        output = asyncio.run(run_check(args, text))
        print(output if output else "(No text to translate.)")
    except (OllamaError, TranslationError, OSError, UnicodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Translation cancelled.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
