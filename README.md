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
http://127.0.0.1:8000/telegram-feed
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
API. Step 6 provides saved results in feeds/dashboard. Failures are
retained without retries. Step 7 adds Ollama readiness and final source fallback.

## Dashboard workflow

### Translation settings and preview (implementation step 3)

Restart the application after updating the code. Startup adds the translation
settings columns to SQLite automatically while preserving existing channel rules.
Translation defaults to off for existing and newly added channels.

1. Open `/telegram-feed`, then **Settings** for an existing channel.
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
4. Switch between **Original** and **RSS output** to compare the Telegram message with the published version.
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

The queue runs independently of Telegram and web requests. Application concurrency
is automatic: one slot per two active channels with translation enabled, rounded
up, with an initial maximum of four. It also considers how many jobs are waiting
or running. Channels with fewer running jobs receive preference. No slot setting
is required from the user; legacy `TRANSLATION_CONCURRENCY` entries are ignored.
The operational timing settings remain available in `.env`:

```dotenv
TRANSLATION_POLL_SECONDS=2
TRANSLATION_JOB_TIMEOUT=600
```

The job timeout covers all chunks and result
validation; `OLLAMA_READ_TIMEOUT` still limits individual model requests. More
concurrency requires more memory. The reusable Ollama client has no translation
queue or scheduling logic.

Under healthy memory readings the worker starts with one slot and raises its limit
by at most one every two seconds, up to the channel-based target and four-slot
maximum. Low memory reduces the limit; critical RAM pressure pauses new starts.
Running jobs are allowed to finish when the limit falls. After memory pressure
clears, the worker waits ten seconds before gradually recovering. A model error
explicitly reporting insufficient memory pauses new starts for thirty seconds.
This does not retry failed jobs automatically.

RAM is monitored on Windows and Linux. If `nvidia-smi` is available, NVIDIA GPU
memory is also sampled, at most once every ten seconds. GPU pressure reduces
concurrency but does not pause all work: Ollama may have preallocated memory for
an already loaded model. GPU readings cover the visible NVIDIA devices
conservatively and are not proof of available capacity on the model's exact
device. AMD/Apple GPU memory is not monitored here. If RAM monitoring fails or
Ollama runs on another computer, the worker conservatively uses one slot.
Memory readings are precautions, not an out-of-memory guarantee, and reducing
application concurrency does not release memory preallocated by Ollama.

The dashboard's **Background translation** panel refreshes automatically with
running/waiting counts, friendly failures and memory warnings. It reads a shared
SQLite worker heartbeat, so another web process can display the active manager's
status. A heartbeat older than thirty seconds is shown as unavailable.

Ollama must also support parallel inference. On Windows, quit Ollama from the
system tray, open **Edit environment variables for your account**, add the user
variable `OLLAMA_NUM_PARALLEL` with value `2`, then reopen Ollama from the Start
menu. This configures the Ollama server; adding it to this project's `.env` does
not configure an already running server. See the
[official Ollama FAQ](https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests).

If Ollama cannot be reached or the configured model is missing, RSS still publishes
source text. Once a source fallback is included in a generated feed, that post is
final and cannot be translated later. Generation failures are also final, without
partial output or retries; other jobs continue. Unpublished pending jobs survive
restart, and interrupted unpublished running jobs return to pending
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
they arrive. Recovery resumes unpublished work; posts already finalized as source
are excluded. Saved results can be inspected in `translation_jobs`
and outstanding queue intents in `translation_requests` using a SQLite viewer.
The dashboard and RSS use saved translated output as described below (step 6).

Check automatic concurrency and memory backoff without stressing your computer:

```powershell
.\.venv\Scripts\python.exe -m app.translation.check_automatic
.\.venv\Scripts\python.exe -m app.translation.check_automatic --live
```

The first command simulates a healthy-to-critical-to-healthy memory transition,
verifies that running jobs finish while new jobs wait, and then verifies recovery.
The second translates ten temporary posts using real Ollama and real memory
readings. Both use temporary databases; expect `Automatic concurrency check passed`.
No manual concurrency change is necessary. Restart the application once to load
the new code, then watch the dashboard panel while saving channel settings or
receiving new posts. Enabling/disabling channels adjusts the worker's target
without restarting; the initial ceiling remains four. Do not create actual memory
pressure to test backoff—the simulated check and tests cover it safely.

### Saved translation delivery (implementation step 6)

RSS delivery never waits for translation. The feed and channel posts page share
the same output selection:

| State | Published RSS content |
| --- | --- |
| Completed and matches the current text/settings/model recipe | Saved translated body, then prefix/suffix |
| Pending or running | Processed source body, then prefix/suffix |
| Failed, unavailable, or not yet queued | Processed source body, then prefix/suffix |
| Translation disabled, same languages, or empty news body | Normally processed source output |

Both headline and body use the selected news language. Headlines exclude the
prefix/suffix. Media enclosures, Telegram links, publication dates and item GUIDs
remain intact. Captions are handled as text; images and audio are not translated.
Warnings appear in the dashboard instead of being inserted into the news body.
An invalid replacement rule also falls back to source text with affixes, with a
dashboard processing warning, so it does not prevent RSS delivery.

RSS and dashboard reads perform no Ollama calls and never enqueue or generate
translation work. RSS generation records final source fallback decisions in SQLite;
dashboard previews only read and do not finalize posts. The worker records the last verified model digest in SQLite, keyed by the
configured Ollama address and model. A matching completed translation remains
usable while Ollama is stopped or its model is missing; new or unfinished posts
still publish source text. Model changes are detected when the worker next checks
Ollama, not during a feed request. Switching the configured Ollama address/model,
changing rules/languages/affixes/original text, or changing the processing recipe
prevents outdated saved output from being served.

On **View Posts**, **Original** shows the untouched Telegram text. **RSS output**
shows what the feed would publish using the current saved settings, including
translations or source fallback. Per-post labels and friendly warnings explain
the selection. Output refreshes every five seconds without changing the selected
view. It also works for older posts outside the RSS feed's latest twenty items.
RSS descriptions encode markup as literal text, and characters XML cannot
represent are removed at serialization. The feed language is set only when all
selected news bodies have the same known language; mixed fallback/translated
feeds do not incorrectly claim a single language.

A post uses translation only if it is ready before source fallback is finalized.
Once included in RSS using source text, its body, headline language, prefix and
suffix are preserved, even after recovery, restart or settings changes. Late model
responses are rejected atomically. The application records feed generation, not
whether a particular subscriber has consumed the item. It cannot reconstruct
source deliveries made before this feature was installed.

Run the temporary-data delivery checks:

```powershell
.\.venv\Scripts\python.exe -m app.translation.check_delivery
.\.venv\Scripts\python.exe -m app.translation.check_delivery --live
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

Expect `Delivery check passed`. The offline check uses a simulated model; the live
check uses your installed model for the first result. Both verify read-only previews,
translated title/body, stable GUID, offline cached output, final source fallback
during a simulated outage/failure, stale-output rejection and
unchanged originals. They use temporary databases and never stop your real
Ollama service or change your project's posts/settings.

For a manual application test, restart the app, enable translation in a channel,
then open **View Posts → RSS output** and `/rss/<channel_username>`. Receive a
new post: the dashboard preview updates when translation completes, provided the
post has not already been finalized as source by RSS. RSS remains immediate.
Previously completed translations remain usable during an outage. Final source
posts stay unchanged when settings change.

### Post views and the demo channel

The **Channel posts** header includes **Content view** (Original / RSS output) and
**Display style** (Stacked / Cards). Stacked shows one post below another; Cards
shows two columns on larger screens and one on phones. The display style is
remembered in that browser across channels. Changing the layout does not change
the selected text version, and live refreshes update text, badges and RSS labels
together without switching views. Dates use the viewer's local time.

Each post has a compact status badge beside its metadata and an RSS output label.
Activate the badge to read its specific explanation. The **Status guide** help
button opens a compact desktop dialog or mobile panel, closed by default, with
all eleven definitions in English and Arabic (only Arabic text is RTL). Close
it with its close button or Escape; focus returns to the help button. Statuses
refer to the RSS version even while Original content is selected. Waiting,
translating, paused and disabled are separate states; published source output and
failed translations are clearly marked final. Post and service warnings do not
ask ordinary users to manage Ollama, model downloads, memory or environment
variables. They direct setup problems to an administrator.

Choose **Explore the demo channel** in the dashboard or open
`/telegram-feed/demo/posts`. The sample channel contains 23 fictional examples covering
all status definitions, Arabic translations, source fallbacks, text rules and
photo/video/document displays. It uses the same template, style controls and status
presentation as real channels, but does not read/write the project database, call
Ollama, poll live translation activity or publish sample posts to RSS. The sample
video is a labelled visual placeholder; the image and document are local assets.

To review the interface, restart the application and open the demo. Switch between
Stacked and Cards, toggle Original / RSS output, open the status guide and review
the examples. Resize to a phone width; cards become a single column. Reload the
page to verify the layout preference persists. On a real channel, a new translation
should update its message and badge without changing your current view.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_post_presentation tests.test_translation_output -q
```

### Ollama startup and readiness (implementation step 7)

Local Ollama startup is automatic; there is no user switch. The dashboard's
**Background translation** panel reports startup, readiness and warnings. Old
saved startup preferences are ignored. Availability/model checks run at
application startup and periodically, so enabling translation or
installing the missing model is noticed automatically.

If the configured local HTTP server is unreachable, the worker attempts to start
the installed `ollama serve` once per application run, in the background without
a visible terminal. The command is documented in the [Ollama CLI reference](https://docs.ollama.com/cli).
It uses the configured loopback address/port and preserves existing Ollama
environment settings. Windows discovery uses PATH or the usual installation in
`%LOCALAPPDATA%/Programs/Ollama`, described in [Ollama's Windows documentation](https://docs.ollama.com/windows).
Remote addresses, HTTPS endpoints and proxy paths receive checks only. The app
does not install Ollama, download models, repeatedly relaunch a stopped server,
or stop Ollama on shutdown. Its connection client and runtime helper remain
independent of the translation task for future reuse.

Startup failures, an unresponsive server and missing models produce friendly
warnings; RSS keeps working. A startup attempt has a 30-second grace period, after
which the warning asks you to open Ollama manually. Once readiness returns, new
posts can translate, while failed or source-finalized posts stay unchanged.

For automated checks that use fake connections and temporary databases:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_ollama_runtime tests.test_translation_recovery -q
.\.venv\Scripts\python.exe -m app.translation.check_delivery
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

For a manual check, quit Ollama and restart this
application. The dashboard should show startup progress and then readiness. If
automatic startup cannot find or start your installation, its warning explains
what to do. Closing the desktop window or signing out of ollama.com does not
necessarily stop the local server, including the background server this app
started. `ollama signout` concerns account access; local downloaded models keep
working. See the [CLI commands](https://docs.ollama.com/cli) and
[cloud authentication](https://docs.ollama.com/cloud).

To check the real missing-model response without removing yours or changing the
application configuration, keep Ollama running and use the independent client check:

```powershell
.\.venv\Scripts\python.exe -m app.llm.check_ollama --model translation-test-missing:latest
```

Expect a model-not-installed error (exit code 1 is intentional). No model is
downloaded and no database is changed. Ignore the error's `ollama pull` suggestion
for this deliberately nonexistent test model. The recovery tests above simulate the same
failure through the worker and verify source delivery using temporary posts.
Avoid changing your application's model just to test it against real feeds:
source fallbacks published during that test would become final.

To test a missing Ollama installation safely, run the simulated executable-not-found
test; it does not uninstall Ollama or change your real posts:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_ollama_runtime.OllamaRuntimeTest.test_launch_failure_is_actionable_and_not_repeated -v
```

For an actual server-off test, let the app perform its one automatic launch first.
Then end that Ollama server process in Task Manager while leaving this app running.
It will report the outage without launching a second server in the same run. Send
a new post and fetch RSS to verify final source fallback, then open Ollama manually
and test translation with another new post. The automatically launched server has
no desktop window and remains running after this application exits.

### Benchmark translation capacity

Use the benchmark to measure a burst from ten channels (one post each) through the
same background worker used by the application. It compares application limits
of 1, 2 and 4 by default, and uses a fresh temporary database for every round.
No real channels, posts or settings are read or changed. It warms the model once
before timing, so the results describe operation with a loaded model rather than
the first cold start. The report shows warm-up time separately.

With Ollama running, preferably while no other applications are requesting
generation, run:

```powershell
.\.venv\Scripts\python.exe -m app.translation.benchmark --output translation_benchmark_results.json
```

The console reports completed/failed/unfinished posts, total duration, average and
maximum application queue wait, time until the last successful translation is
ready, and successful posts per minute. Queue wait is measured before the worker
starts a job; its translation duration also includes any queue inside Ollama.
The configured worker poll interval is retained to measure actual scheduling
delays. Progress prints every ten seconds while a round is running.

The built-in texts are ten invented English news posts of varying lengths, with
French as the target. For a more reliable comparison, use three repeats (the
limit order reverses on alternate repeats). You can also supply exactly ten real
news texts as a UTF-8 JSON array of strings and select the language pair:

```powershell
.\.venv\Scripts\python.exe -m app.translation.benchmark --repeats 3 --output translation_benchmark_results.json
.\.venv\Scripts\python.exe -m app.translation.benchmark --file my_news.json --source ar --target en --limits 1 2 4 --output translation_benchmark_results.json
```

The optional JSON report includes the source texts and generated translations so
you can inspect accuracy separately. Timing alone does not measure output quality.
Failed or unfinished rounds do not receive a successful-throughput figure. Each
round has a default 1800-second deadline, configurable with `--round-timeout`;
individual jobs still use `TRANSLATION_JOB_TIMEOUT`.

The benchmark changes application concurrency only. It does not set, restart or
verify Ollama's `OLLAMA_NUM_PARALLEL`. Testing application limits above the
server's capacity measures extra queueing, and does not prove that those requests
ran in parallel inside the model. If you know the server capacity, use
`--server-parallel 2` to record that declared value in the report. To compare
different Ollama server capacities, configure and restart Ollama between separate
benchmark invocations and save each report under a different name. More capacity
requires sufficient memory; see the
[Ollama concurrency documentation](https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests).

For an offline check of the measurement/reporting logic:

```powershell
.\.venv\Scripts\python.exe -m app.translation.benchmark --simulate
```

Its output is clearly marked **SIMULATED** and cannot be used to choose a hardware
limit. The benchmark uses explicit fixed limits for comparison and does not change
the application's automatic scheduling policy.

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
