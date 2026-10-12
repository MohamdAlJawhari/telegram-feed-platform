# Deployment record: Telegram Feed on Windows Server

Last updated: 12 October 2026 (Beirut time).

This is the story of moving the application from a development computer to a public server. It records the choices we made, the problems we solved, and the remaining work. It describes the actual deployment so far; unfinished steps are marked clearly.

## What we wanted

The application should collect Telegram news and publish RSS feeds without someone keeping a terminal or Remote Desktop session open. It should start when Windows starts and recover from process crashes. People should be able to read RSS publicly, while channel management and settings require a password.

Translation is optional. If Ollama is unavailable, RSS continues using the processed source text. We do not automatically retry failed items that have already been published using their source text.

## The server and the deployment choice

The server runs Windows Server 2022, has 32 GB RAM, and an NVIDIA GeForce RTX 2060. We have administrator access through Remote Desktop.

We initially considered Docker. Docker Desktop does not support Windows Server 2022. A Linux virtual machine could provide a Docker deployment, but would add another operating system, networking, and startup configuration. To get this existing Windows server online with less setup, we chose native Windows services instead.

Ollama and `translategemma:latest` were already installed on the server. That helped us avoid rebuilding the translation setup from scratch, although Ollama still needs a service installation.

## How the pieces fit together

```text
Telegram -> application -> saved posts and downloaded media
                         -> background translation through Ollama

Browser or RSS reader -> Caddy HTTPS -> application on localhost
```

The application receives news, stores it, and produces RSS. Ollama supplies translations. Caddy sits in front of the application: it provides HTTPS, checks the administrator password, and forwards allowed requests to the application.

DuckDNS gives the server a memorable hostname. It does not host the application or create the HTTPS certificate. Windows Task Scheduler keeps that hostname pointed to the server, and Caddy manages the certificate.

## 1. We reserved the public names and configured DNS updates

The DuckDNS account contains `unews.duckdns.org` and `unews-rss.duckdns.org`. Both were pointed to the same server public IPv4 address. The deployed application currently uses `unews.duckdns.org`; the second name is reserved, not configured as another Caddy website.

We created `C:\TelegramFeed-DNS` with access restricted to SYSTEM and administrators. It contains:

| File | Purpose |
| --- | --- |
| `token.txt` | Private DuckDNS account token |
| `update.ps1` | Updates both hostnames over HTTPS |
| `status.txt` | Result of the most recent update |

The updater leaves the IP parameter empty, allowing DuckDNS to detect the outgoing public IPv4 address. We verified the DNS answer during setup. If the server's network arrangement changes, verify that this detected address still matches the address used to reach the server.

Our first updater reported only a generic failure. We replaced it with an `Invoke-RestMethod` version that handles the response and records a sanitized error without exposing the token. The replacement returned `DNS update successful`.

We registered the scheduled task `TelegramFeed-DuckDNS`. It runs as SYSTEM at boot and every five minutes, without a logged-in user. A `LastTaskResult` of `0` confirmed a successful execution.

## 2. We checked ports before exposing the website

Port 443 was already used by Kerio MailServer. We kept Kerio running to preserve the existing mail service. Giving two hostnames the same IP does not let two independent servers both listen on the same IP and port.

We therefore placed this application's public HTTPS listener on port **9443**. The `:9443` part must remain in its URLs. Public port 80 is used for certificate verification and HTTP-to-HTTPS redirection.

Windows Firewall rules allow TCP ports 80 and 9443 for `C:\Caddy\caddy.exe`. We did not need to change Kerio's port 443.

## 3. We copied the application and started with fresh data

The project was placed in `C:\TelegramFeed`, along with the private `.env` configuration. We chose a fresh start rather than migrating old posts and settings.

Initial checks found an existing database and Telegram session. We moved the database, session directory, and data directory into a timestamped backup folder before starting fresh. The backup is on this same server; it is not an off-server disaster-recovery backup.

We created a new Python environment, installed `requirements-production.txt`, and authorized a new Telegram session interactively. Login codes and passwords were entered privately, not added to source code.

The important configuration values are:

```dotenv
BASE_URL=https://unews.duckdns.org:9443
OLLAMA_BASE_URL=http://127.0.0.1:11434
```

`BASE_URL` is the public origin, without `/telegram-feed`. It lets generated RSS media links use the public HTTPS address. Telegram API credentials also remain in `.env`; this document intentionally contains no credentials.

The local page opened, the channel could be added, and Telegram connected successfully.

## 4. We added HTTPS and administrator authentication

We downloaded Caddy to `C:\Caddy\caddy.exe` and created `C:\Caddy\Caddyfile`.

The administrator username is **`admin`**. The password was chosen interactively using `caddy hash-password`; the configuration stores the resulting hash, not the original password. The browser displays a standard username/password prompt. We did not build a separate application account system or login page.

Caddy's configuration provides:

- Public GET/HEAD access to `/rss/*`, `/media/*`, `/static/*`, and the existing `/posts` JSON API.
- Password protection for the remaining application routes, including settings and channel management APIs.
- A 404 response for `/debug`, API documentation, and the OpenAPI schema.
- An exact Origin check for POST, PUT, PATCH, and DELETE requests, helping prevent another website from submitting changes using the browser's saved credentials. Custom management API clients must also supply the configured Origin.
- Automatic certificate management through Let's Encrypt using HTTP verification on public port 80. TLS-ALPN verification is disabled because Kerio occupies port 443.
- Certificate storage in `C:\Caddy\storage`, so changing the Windows account that runs Caddy does not change where certificates are kept.

The application binds only to localhost. Caddy provides the public entry point; the Python server is not intended to be exposed directly.

Initially Caddy could not find its configuration because the file had been saved with the wrong name. We corrected it to `Caddyfile`, without `.txt`, validated the configuration, and started Caddy. The public HTTPS page opened and administrator authentication worked.

## 5. We installed Windows services

We used WinSW 2.12.0 to run the existing executables as Windows services. Each renamed wrapper has an XML configuration with the same base filename.

| Windows service | Wrapper and configuration folder | Purpose |
| --- | --- | --- |
| `TelegramFeed` | `C:\TelegramFeed\Services` | Runs Python/Uvicorn |
| `TelegramFeedCaddy` | `C:\Caddy` | Runs HTTPS and authentication |
| `TelegramFeedOllama` | `C:\Ollama` | Runs the local Ollama API |

All three services run as Windows' built-in **LocalService** account. This avoids giving the application the administrator's Windows password. They are configured for automatic startup and restart ten seconds after process failure. Service logs rotate to limit their size.

The application runs with **one worker**, without development reload. Extra web workers would also start extra Telegram listeners and background translation workers, which is unsuitable for the current design.

### The permission problem we corrected

The first recursive permission command left executable files without effective access entries. Even elevated PowerShell could not launch the service wrappers. We restored explicit access on existing files, then configured inheritable access on the two root folders separately.

The deployment permissions grant SYSTEM and administrators full access and LocalService modify access. We verified the executable access entries and installed both services successfully. Do not repeat the original recursive command that combined removing inheritance and granting directory inheritance flags on every file.

### Why we reinstalled Python outside the user profile

The first virtual environment used Python from the Administrator's private profile. It worked in an administrator terminal, but LocalService could not access that interpreter.

We installed Python **3.13.16 for all users** at `C:\Python313`. We renamed the old virtual environment to `.venv-user-backup`, created a new `.venv` using this system installation, and reinstalled dependencies. Posts, settings, and the authorized Telegram session were preserved.

### Why the internal application port changed

After Python access was fixed, the application could initialize but failed to bind to port 8000. Another process was already listening on that port. This also meant Caddy could reach the wrong application and receive `{"detail":"Not Found"}`.

We left the unidentified existing process alone and moved Telegram Feed to **127.0.0.1:18080**. We updated both the application service arguments and Caddy's forwarding addresses.

The final application service command is:

```powershell
C:\TelegramFeed\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 18080 --workers 1
```

Caddy forwards requests to `127.0.0.1:18080`. Port 18080 does not need a public firewall rule.

## What has been verified

Both `TelegramFeed` and `TelegramFeedCaddy` report `Running`. The local page and the public HTTPS page work. New Telegram news appears in RSS using the new public links.

| Address | Purpose |
| --- | --- |
| `http://127.0.0.1:18080/telegram-feed` | Local server troubleshooting; bypasses Caddy authentication |
| `https://unews.duckdns.org:9443/telegram-feed` | Public management page, protected by Caddy |
| `https://unews.duckdns.org:9443/rss/testRssN8n` | Verified public RSS feed example |

The local address only works from the server itself. Public security depends on keeping the application bound to localhost and using Caddy for external access.

Caddy's warning that the configuration is not formatted is cosmetic; it did not prevent validation or operation.

## 6. We installed Ollama as a service

Before this step, server checks showed:

- No Windows service with an Ollama name was found.
- No `OLLAMA_MODELS` override was reported in the process, user, or machine environment.
- `C:\Users\Administrator\.ollama\models` exists, matching Ollama's default model location.
- An Ollama process is listening on `127.0.0.1:11434`.
- Earlier `ollama list` output showed `translategemma:latest` and `gpt-oss:latest` installed.

We copied the complete existing Ollama installation to `C:\Ollama` and its model store to `C:\Ollama\models`. The copy included both installed models and additional existing blobs; the server's copy report showed approximately 32 GB, more than the two model sizes alone suggested. We preserved the original installation and models.

We created the WinSW service `TelegramFeedOllama`, running `C:\Ollama\ollama.exe serve` with `OLLAMA_HOST=127.0.0.1:11434` and `OLLAMA_MODELS=C:\Ollama\models`. The shared directory grants LocalService the needed access.

Initially the desktop process still occupied port 11434 and the service stopped. After switching away from the desktop instance, a listener from the new `C:\Ollama` installation appeared. We checked the service rather than killing that new process: `TelegramFeedOllama` was running and both models were listed. We restarted the application, and translation worked on the website.

GPU use under the service account and operation after a full reboot have not yet been confirmed. The desktop application's automatic startup should remain disabled to avoid two servers competing for port 11434.

RSS must remain independent of this service: Ollama being stopped or its model being unavailable must not prevent the application or RSS from starting.

## 7. We installed Telegram supervision and health checks

The supervision update was implemented in the development workspace and passed all 163 tests. We then stopped the application service, copied the updated and new application files plus tests to the server, granted LocalService read/execute access to the application code, and started the service again.

It supervises the Telegram listener independently of the web server. Temporary failures or an unexpected normal exit are retried after 5, 10, 20, 40, then 60 seconds. A connection that remains stable for at least a minute resets the delay. There is only one listener attempt at a time, and application shutdown cancels it cleanly.

The service uses its saved Telegram session without interactive prompts. If authorization is missing or revoked, health reporting and logs say that local sign-in is required; repeating the same login attempt cannot repair it. An administrator must stop the application, authorize the session interactively, and restart it.

After connecting, the listener requests missed updates using Telethon's saved session state. This is best-effort catch-up, not a guarantee of replaying the complete channel history after every outage.

Two read-only endpoints are added:

| Endpoint | Meaning |
| --- | --- |
| `/health` | Returns 200 when the database's posts table is readable, or 503 when unavailable; includes separate Telegram status |
| `/health/telegram` | Returns 200 only when the database is readable and the supervised Telegram listener is connected; otherwise 503 |

Ollama availability is deliberately not a requirement for these checks. A Telegram outage can produce `status: degraded` with `rss_ready: true`: existing posts can still be served. Database readability alone does not prove that every RSS rendering or database write will succeed, so monitoring should also request a real feed.

The existing Caddy configuration protects these endpoints with the administrator password. Local checks can use `http://127.0.0.1:18080/health` and `http://127.0.0.1:18080/health/telegram`.

The update consists of the updated `main.py` and `app/telegram/telegram_client.py`, plus the new `app/telegram/supervisor.py` and `app/api/health.py`. The configuration, database, session, media, virtual environment, and service configuration were preserved. No dependency changes were required.

The server's `/health` response confirmed `status: ok`, `rss_ready: true`, `database: readable`, and Telegram `status: connected`, with zero supervisor restarts since startup. The reported connection time was 12 October 2026 at 20:53:54 Beirut time (17:53:54 UTC). A new-post check after this particular update, failure recovery, and reboot verification still need to be performed.

## What remains before deployment is finished

1. **Recovery verification:** confirm new posts after the supervision update, test disconnecting Remote Desktop, process crashes, and a full server reboot. Verify that new posts arrive after recovery and that Ollama failures produce the expected source-text RSS fallback. Confirm GPU use under the Ollama service account. A reboot also interrupts Kerio, so choose an acceptable time for the shared server.
2. **Backups:** schedule consistent SQLite backups plus media, session, and configuration backups to storage outside the server. Test restoring them. This is not configured yet.
3. **External monitoring:** check a public RSS endpoint from outside the server and configure failure alerts. This is not configured yet.

Automatic startup brings services back after Windows boots. It cannot serve RSS while the entire server is powered off or disconnected. Continuous availability through a complete server outage would require another host or an externally hosted feed copy.

## Everyday checks and maintenance

Run these commands on the server in an elevated PowerShell window:

```powershell
# Check the three installed services.
Get-Service TelegramFeed, TelegramFeedCaddy, TelegramFeedOllama

# Read recent service logs.
Get-Content C:\TelegramFeed\Services\logs\*.log -Tail 30
Get-Content C:\Caddy\logs\*.log -Tail 30
Get-Content C:\Ollama\logs\*.log -Tail 30

# Check the DNS update task and its last result.
Get-ScheduledTaskInfo -TaskName 'TelegramFeed-DuckDNS'
Get-Content C:\TelegramFeed-DNS\status.txt

# Validate a changed Caddy configuration before loading it.
C:\Caddy\caddy.exe validate --config C:\Caddy\Caddyfile --adapter caddyfile
```

After successful Caddy validation, reload it with:

```powershell
C:\Caddy\caddy.exe reload --config C:\Caddy\Caddyfile --adapter caddyfile
```

To restart the application after a code or `.env` change:

```powershell
Restart-Service TelegramFeed
```

For future code updates, back up first, stop the application service, and replace the code while preserving `.env`, `telegram.db`, `sessions`, and `data`. Install any changed dependencies into the server's virtual environment, restart, and verify a new post and the public RSS feed. Keep one application instance using the Telegram session.

Never publish the DuckDNS token, Telegram credentials, session files, or administrator password in GitHub or this document.

## References

- [Docker Desktop support on Windows](https://docs.docker.com/desktop/setup/install/windows-install/)
- [DuckDNS update API](https://www.duckdns.org/spec.jsp)
- [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https)
- [Caddy password authentication](https://caddyserver.com/docs/caddyfile/directives/basic_auth)
- [WinSW configuration](https://raw.githubusercontent.com/winsw/winsw/v2.12.0/doc/xmlConfigFile.md)
- [Python installation on Windows](https://docs.python.org/3.13/using/windows.html)
- [Ollama on Windows](https://docs.ollama.com/windows)
