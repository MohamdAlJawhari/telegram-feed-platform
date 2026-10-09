# Telegram Feed Platform

A FastAPI application that listens for posts from selected Telegram channels, stores them locally, downloads attached media, and exposes the collected content through a dashboard, JSON API, and per-channel RSS feeds.

## Features

- Add and manage Telegram channels from a web dashboard
- Enable, pause, or delete tracked channels
- Collect new Telegram messages in real time with Telethon
- Store posts and channel settings in SQLite
- Download photos, videos, audio, and documents locally
- Generate RSS feeds for individual channels
- Remove keywords, replace text, and add per-channel prefixes or suffixes
- Compare original and processed content in the post viewer
- Access channels and posts through JSON endpoints

## Requirements

- Python 3.10 or newer
- A Telegram account
- Telegram API credentials (`api_id` and `api_hash`)

## Installation

Clone the repository and enter its directory:

```bash
git clone <repository-url>
cd telegram_feed_platform
```

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Or on Linux and macOS:

```bash
source .venv/bin/activate
```

Install the dependencies:

```bash
python -m pip install -r requirements.txt
```

## Configuration

Create a `.env` file in the project root:

```dotenv
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
BASE_URL=http://127.0.0.1:8000
```

| Variable | Required | Description |
| --- | --- | --- |
| `TELEGRAM_API_ID` | Yes | Numeric Telegram application ID |
| `TELEGRAM_API_HASH` | Yes | Telegram application hash |
| `BASE_URL` | No | Public application URL used for RSS media links; defaults to `http://127.0.0.1:8000` |

Do not commit the `.env` file or Telegram session files. Both are excluded by `.gitignore`.

## Running the application

Start FastAPI from the project root:

```bash
python -m uvicorn main:app --reload
```

On the first run, Telethon may ask for your Telegram phone number, login code, and two-step verification password. The authenticated session is saved under `sessions/`, so later starts normally do not require another login.

Open the dashboard:

```text
http://127.0.0.1:8000/dashboard
```

Interactive API documentation is available at:

```text
http://127.0.0.1:8000/docs
```

The database and required local directories are created automatically at startup.

## Ollama connection (implementation step 1)

The reusable async client lives in `app/llm/ollama_client.py`. It supports any
installed model; translation prompts, channel settings, and background workers
will be added in later steps. Existing feeds do not depend on Ollama yet.

Optional `.env` settings (these defaults work for a local Ollama installation):

```dotenv
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_CONNECT_TIMEOUT=5
OLLAMA_READ_TIMEOUT=180
```

Timeouts are seconds. The read timeout limits network inactivity, not the total
duration of a task. The server must be reachable from the machine running Python.
Environment proxy settings are ignored for this connection. No model is downloaded
automatically, and errors are not retried automatically in this transport layer.

Run these checks from the project root, without starting FastAPI or Telegram:

```powershell
.\.venv\Scripts\python.exe -m app.llm.check_ollama
.\.venv\Scripts\python.exe -m app.llm.check_ollama --model translategemma:latest
.\.venv\Scripts\python.exe -m app.llm.check_ollama --model definitely-missing-model:latest
.\.venv\Scripts\python.exe -m app.llm.check_ollama --base-url http://127.0.0.1:1
```

The first two print the connection and installed models. The third prints a
missing-model error and a suggested pull command. The fourth prints a connection
error without changing your configuration. Failed checks exit with status 1.
You can also stop Ollama, repeat the first check, then reopen Ollama and retry.

To test generation with TranslateGemma's documented prompt format:

```powershell
$prompt = "You are a professional English (en) to French (fr) translator. Your goal is to accurately convey the meaning and nuances of the original English text while adhering to French grammar, vocabulary, and cultural sensitivities. Produce only the French translation, without any additional explanations or commentary. Please translate the following English text into French:`n`n`nHello, how are you?"
.\.venv\Scripts\python.exe -m app.llm.check_ollama --model translategemma:latest --prompt $prompt
```

Expect a French greeting. The first request may take longer while the model loads.
This is a manual model check, not the channel translation feature.

Other application services can reuse the client:

```python
from app.llm.ollama_client import OllamaClient

async def ask_model(model, messages):
    async with OllamaClient() as client:
        return await client.chat(model, messages)
```

For multiple requests, reuse one client per application event loop and close it
at shutdown. The client allows overlapping requests; actual parallel inference
depends on Ollama settings and available memory. Persistent workers and concurrency
limits belong to the later background-processing step.

Client errors distinguish invalid configuration, connection failure, timeout,
missing models, HTTP/API failure (with status code), and invalid responses.
Task-specific checks such as empty translations belong to the translation service.

## Translation service (implementation step 2)

`app/translation/service.py` builds the TranslateGemma prompt and validates task
output. It borrows an `OllamaClient`; it does not own the connection or change
channel settings, RSS, the database, or Telegram collection yet.

Optional `.env` settings:

```dotenv
TRANSLATION_MODEL=translategemma:latest
TRANSLATION_MAX_CHUNK_BYTES=1600
```

Use explicit source and target language codes. The initial application allowlist
contains 38 languages, including Arabic, Hebrew, English, French, and Persian.
It is a subset of the [Ollama model's language list](https://ollama.com/library/translategemma),
not a guarantee of equal translation quality. Automatic detection and regional
variants are not included yet; `zh` aliases `zh-Hans`.

From the project folder:

```powershell
# List language codes (does not contact Ollama).
.\.venv\Scripts\python.exe -m app.translation.check_translation --languages

# Basic translation: expect French, preserving the number 10.
.\.venv\Scripts\python.exe -m app.translation.check_translation --source en --target fr --text "The meeting starts at 10."

# Test body translation followed by an unchanged prefix and suffix.
.\.venv\Scripts\python.exe -m app.translation.check_translation --source en --target fr --text "BREAKING The event starts at 10." --remove-keywords BREAKING --replace-words "event=>meeting" --prefix "Latest news" --suffix "Source: Telegram" --show-processed

# Same language: returns processed text without a request to Ollama.
.\.venv\Scripts\python.exe -m app.translation.check_translation --source en --target en --text "The meeting starts at 10." --base-url http://127.0.0.1:1

# Each command should report a readable error and exit with status 1.
.\.venv\Scripts\python.exe -m app.translation.check_translation --source auto --target en --text "News"
.\.venv\Scripts\python.exe -m app.translation.check_translation --source en --target fr --text "News" --model missing-test-model:latest
.\.venv\Scripts\python.exe -m app.translation.check_translation --source en --target fr --text "News" --base-url http://127.0.0.1:1
```

The processing order is **remove keywords → replace words → translate → add
prefix/suffix**. `--show-processed` prints the news body submitted for translation.
Prefixes and suffixes stay in the language you wrote them; removal and replacement
rules apply only to the body. No image or audio content is processed.

For an Arabic sample or a long post, save a UTF-8 text file and run:

```powershell
.\.venv\Scripts\python.exe -m app.translation.check_translation --source ar --target en --file .\sample-news.txt
```

For the empty-input check, pass a whitespace-only UTF-8 file. Expect
`(No text to translate.)` without an Ollama request. The file reader accepts a UTF-8
BOM and reports missing files or invalid encoding as readable errors.

Long input is split into bounded UTF-8 chunks, preferring paragraph and word
boundaries. To exercise chunking with a smaller sample, add `--chunk-bytes 64`.
Boundaries retain their separators; line formatting within a chunk depends on
the model. Very long unbroken tokens may be split across chunks. Smaller chunks
can reduce context and translation quality. The first model load can take longer.

Empty output, malformed responses, and an output-limit finish reason are errors.
If any chunk fails, the service returns no partial translation. These checks cannot
prove factual accuracy or detect every omission; review names, numbers, and meaning
in representative news before enabling automatic publication.

Calls can overlap using the same service/client; chunks for each call run in order.
The service has no automatic retries, persistence, or concurrency limits yet.
Those belong to the later background-worker stage. Connection and model errors
retain their `OllamaError` types; language/configuration and output errors use
`TranslationError` types.

Reusable service example (caller owns the client lifecycle):

```python
from app.llm.ollama_client import OllamaClient
from app.translation.service import TranslationService

async def translate_news(text):
    async with OllamaClient() as client:
        return await TranslationService(client).translate(text, "ar", "en")
```

## Saved translations and job storage (implementation step 4)

Startup now creates `translation_jobs` and `post_translations` alongside the existing
tables. Original Telegram posts remain unchanged. A job stores the original-input
snapshot, processed body, settings, language pair, model name/digest, processing
recipe version, and chunk size. Successful jobs save the translated body and final
output separately, with the untranslated prefix/suffix added last.

Jobs move through **pending → running → completed** or **failed**. Each running
attempt has its own ownership token, so duplicate claims and callbacks from older
attempts cannot complete a newer attempt. Database operations use short transactions;
they do not hold a database lock while a model generates text.

An identical request reuses its job and saved result. Changing original text, rules,
prefix/suffix, languages, model identity, or recipe requires a fresh job. Reads and
completion both check current post/channel settings before accepting output. Reverting
to previously completed settings can reuse that exact result. Disabled translation,
matching languages, empty bodies, and untracked channels have no current translation.

Test the storage foundation without contacting Ollama:

```powershell
.\.venv\Scripts\python.exe -m app.translation.check_storage
```

Expect `pending -> running`, then `completed`, followed by PASS messages for cached
reuse, changed settings, rejecting a stale attempt, failure storage, persistence,
and preservation of the original post. This check uses a temporary database that
is removed afterward; it does not modify your real channels, posts, or settings.

Optionally use your installed Ollama model for the first saved result:

```powershell
.\.venv\Scripts\python.exe -m app.translation.check_storage --live
```

Expect a French news body with the English prefix/suffix unchanged. The remaining
safety checks simulate settings changes and a failure. Your real database is still
untouched. Run the automated checks with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The storage API lives in `app/translation/store.py`. Callers provide the current
`TranslationConfig` and installed model digest (from `OllamaClient.require_model`)
when queueing, claiming, reading, or completing a job. Refresh model identity before
completion if an alias such as `latest` could have changed. Bump `RECIPE_VERSION`
when prompts/options/processing behavior change to invalidate older outputs.

Previews continue to use unsaved input and do not persist it. Step 5 adds automatic
queueing, worker startup, and recovery of unfinished running jobs using this storage
API. Reading saved results in feeds/dashboard belongs to Step 6. Failures are
retained without automatic retry; retry controls follow in Step 7.

## Dashboard workflow

### Translation settings and preview (implementation step 3)

Restart the application after updating the code. Startup adds the translation
settings columns to SQLite automatically while preserving existing channel rules.
Translation defaults to off for existing and newly added channels.

1. Open `/dashboard`, then **Settings** for an existing channel.
2. Confirm the **Translation** section shows Ollama connected and your model available.
3. Turn on **Enable translation**, then choose the source and target languages.
4. Paste sample news into **Sample news text**. Enter a prefix/suffix in the language
   you want readers to see and optionally add keyword/replacement rules using the existing Add buttons.
5. Click **Preview translation** before saving. Compare the processed body with the
   final output; only the news body should be translated, with the prefix/suffix added afterward.
6. Change a setting and preview again. Reload without saving to confirm the preview
   did not write settings. Then click **Save Settings** and reload to confirm persistence.
7. Repeat with another channel to confirm its language settings are independent.
8. Stop Ollama and click **Check connection** or **Preview translation**. Expect a
   readable error with your inputs still present. Restart Ollama and retry.

Additional checks: disabling translation or selecting the same language shows the
processed body with prefix/suffix without contacting Ollama. An empty news body also
skips translation and shows only any prefix/suffix supplied. Connection errors tell
you to open Ollama and retry; missing-model errors tell you to add the selected model
in Ollama. Technical diagnostic messages remain available through the CLI checks.
No model is downloaded automatically.

Previews use unsaved form values and never save posts or settings. Edits made during
a preview invalidate its result rather than displaying stale output. Sample text is
limited to 10,000 characters and each processing field to 4,000 for preview requests.
The interactive preview has a total deadline equal to `OLLAMA_READ_TIMEOUT`, including
all chunks. Failed requests re-enable the preview button so you can retry.

This step saves preferences and provides interactive previews. Automatic translation
of collected posts and RSS output still belongs to the later storage/worker/feed steps.

### Managing channels

1. Enter a public Telegram channel username and select **Add Channel**.
2. Keep the channel enabled to collect newly received messages.
3. Use **View Posts** to inspect captured text and media.
4. Switch between **Original** and **Processed** to compare the Telegram message with the RSS output.
5. Use **Settings** to configure text-processing rules.
6. Copy the RSS or JSON API link from the channel card.

The listener collects new messages after the channel is added and enabled. Adding a channel does not import its complete message history.

## Channel processing settings

Processing is applied when an RSS feed is generated, in this order:

1. Remove keywords
2. Replace words
3. Add prefix
4. Add suffix

Keyword removal accepts one value per line or comma-separated values:

```text
BREAKING
Advertisement
Urgent
```

Replacement rules use `old=>new`:

```text
Israel=>Occupation
USA=>United States
Breaking=>News
```

Matching and replacement are case-sensitive.

## RSS feeds

Each channel has an RSS endpoint:

```text
http://127.0.0.1:8000/rss/{channel_username}
```

Example:

```text
http://127.0.0.1:8000/rss/testRssN8n
```

RSS descriptions contain processed text. Media attachments are exposed as RSS enclosures using `BASE_URL`.

If the feed is accessed through a tunnel or public domain, set `BASE_URL` to that public origin so enclosure URLs are reachable by RSS clients:

```dotenv
BASE_URL=https://feeds.example.com
```

## API endpoints

### General

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/` | Application health message |
| `GET` | `/posts` | All stored posts as JSON |
| `GET` | `/rss/{channel_name}` | Processed RSS feed for a channel |
| `GET` | `/docs` | Swagger UI |

### Channels

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/channels/` | List enabled channels |
| `POST` | `/channels/` | Add or update a Telegram channel |
| `PATCH` | `/channels/{channel_id}` | Enable or disable a channel |
| `DELETE` | `/channels/{channel_id}` | Delete a channel |

Add a channel:

```bash
curl -X POST http://127.0.0.1:8000/channels/ \
  -H "Content-Type: application/json" \
  -d '{"username":"testRssN8n"}'
```

Disable a channel:

```bash
curl -X PATCH http://127.0.0.1:8000/channels/123456789 \
  -H "Content-Type: application/json" \
  -d '{"enabled":false}'
```

## Local data

Runtime data is stored in:

```text
telegram.db                 SQLite database
data/media/<channel>/       Downloaded Telegram media
sessions/                   Telethon authentication session
```

These runtime files are excluded from version control.

## Project structure

```text
app/
├── api/                    JSON channel endpoints
├── dashboard/              Dashboard routes, templates, CSS, and JavaScript
├── database/               SQLite schema and data-access functions
├── media/                  Media download and URL helpers
├── processing/             Text-processing rules
├── rss/                    RSS feed generation
├── telegram/               Telethon client and message listener
└── utils/                  Shared helpers
data/media/                 Downloaded attachments
sessions/                   Telegram login session
tests/                      Database and media tests
main.py                     FastAPI application entry point
requirements.txt            Python dependencies
```

## Running tests

The tests use Python's built-in `unittest` runner:

```bash
python -m unittest discover -s tests -v
```

Tests use temporary directories and do not require the production SQLite database.

## Troubleshooting

### Background translation workers (implementation step 5)

When translation is enabled in a channel's saved settings, stored posts and new
Telegram messages are queued automatically. Only message text and media captions
are translated. The worker applies keyword removal, replacements, translation,
then prefixes/suffixes. Original posts remain unchanged.

The queue runs independently of Telegram and web requests. Its default limit is
two concurrent jobs across channels, with preference for channels that have fewer
jobs running. Configure the application in `.env`, then restart it:

```dotenv
TRANSLATION_CONCURRENCY=2
TRANSLATION_POLL_SECONDS=2
TRANSLATION_JOB_TIMEOUT=600
```

`TRANSLATION_CONCURRENCY` accepts 1–8. The job timeout covers all chunks and result
validation; `OLLAMA_READ_TIMEOUT` still limits individual model requests. More
concurrency requires more memory. The reusable Ollama client has no translation
queue or scheduling logic.

Ollama must also support parallel inference. On Windows, quit Ollama from the
system tray, open **Edit environment variables for your account**, add the user
variable `OLLAMA_NUM_PARALLEL` with value `2`, then reopen Ollama from the Start
menu. This configures the Ollama server; adding it to this project's `.env` does
not configure an already running server. See the
[official Ollama FAQ](https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests).

If Ollama cannot be reached or the configured model is missing, new requests stay
in SQLite and are processed once Ollama is available. Generation failures are
saved as `failed` without partial output; other jobs continue. Failed jobs are
not automatically retried in this step; retry controls are planned for step 7.
Pending jobs survive restart, and interrupted running jobs return to pending
with fresh attempt tokens. A per-database OS file lock allows only one worker
manager to recover jobs, including during application reloads. Other processes
can still save posts/settings and queue requests. Leave the `.translation.lock`
file in place; the OS releases ownership when its process exits.

Run the offline worker check (simulated translation and an intentional connection
failure), then the live check with Ollama open:

```powershell
.\.venv\Scripts\python.exe -m app.translation.check_workers
.\.venv\Scripts\python.exe -m app.translation.check_workers --live
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

Both worker checks use temporary databases and require no Telegram connection.
Expect `PASS` messages and `Worker check passed`. The offline check verifies
overlapping requests, the concurrency limit, restart recovery, stale callback
rejection, cached-result reuse, and failure persistence. The live check translates
two sample posts and saves both results; it verifies the application worker path,
while actual server inference concurrency depends on Ollama's configuration and
available memory.

For an application check, restart the app, enable translation for two channels,
and save their settings. Stored posts will be queued; new messages also queue as
they arrive. Quit Ollama and receive another message, then reopen Ollama to verify
that queued work resumes. Saved results can be inspected in `translation_jobs`
and outstanding queue intents in `translation_requests` using a SQLite viewer.
The dashboard and RSS use saved translated output starting in step 6.

### Missing Telegram credentials

If startup reports that `TELEGRAM_API_ID` or `TELEGRAM_API_HASH` is missing, confirm that `.env` exists in the project root and contains both values.

### Telegram login repeats

Confirm that the process can write to `sessions/` and that the session file is not being deleted between runs.

### Media URLs are incorrect in RSS

Set `BASE_URL` to the origin from which RSS clients can reach the application. Restart the application after changing `.env`.

### Updated styles do not appear

Force-refresh the page with `Ctrl+Shift+R`, disable the browser cache in developer tools, or clear the site data for `127.0.0.1`.

### A channel receives no new posts

Confirm that the channel is enabled in the dashboard and that the Telegram account used by Telethon can access it.
