"""Manual connection/model/chat check, independent of FastAPI and Telegram."""

import argparse
import asyncio
import sys

from app.llm.ollama_client import OllamaClient, OllamaConfig, OllamaError


async def run_check(args):
    config = OllamaConfig.from_env()
    if args.base_url:
        config = OllamaConfig(args.base_url, config.connect_timeout, config.read_timeout)
    async with OllamaClient(config) as client:
        models = await client.list_models()
        print(f"Connected to Ollama at {config.base_url}")
        print("Installed models:")
        for model in models:
            print(f"  {model['name']}")
        if not models:
            print("  (none)")
        if args.model:
            await client.require_model(args.model)
            print(f"Model available: {args.model}")
        if args.prompt:
            print("Waiting for model response...", flush=True)
            result = await client.chat(args.model, [{"role": "user", "content": args.prompt}])
            print(result["message"]["content"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="Override the Ollama address for this check only.")
    parser.add_argument("--model", help="Verify an installed model without downloading it.")
    parser.add_argument("--prompt", help="Send a generic chat request to the selected model.")
    args = parser.parse_args()
    if args.prompt and not args.model:
        parser.error("--prompt requires --model")
    try:
        asyncio.run(run_check(args))
    except OllamaError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Check cancelled.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
