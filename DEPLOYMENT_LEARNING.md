# Learning deployment through the Telegram Feed project

This guide explains the deployment decisions and commands as a learning path. For the chronological record and verified results, read [DEPLOYMENT.md](DEPLOYMENT.md). Examples describe the current Windows Server setup. Inspection commands are safe to run; commands that install, stop, restart, copy, or change permissions affect the server and should be used for a specific purpose.

## Lesson 1: Deployment has several separate responsibilities

A working program on your computer already has code and dependencies. A deployment adds a stable place to run it, a public address, access control, startup management, recovery, backups, and monitoring.

Our responsibilities were:

| Responsibility | Component we chose |
| --- | --- |
| Run the Python application | Python 3.13 and a virtual environment |
| Receive Telegram messages and serve RSS | Existing FastAPI/Telethon application |
| Keep processes running without a login | Windows services through WinSW |
| Run translation inference | Ollama in its own Windows service |
| Give the server a public hostname | DuckDNS |
| Encrypt browser/feed traffic | Caddy and a managed TLS certificate |
| Protect administration | Caddy HTTP Basic authentication |
| Run recurring short jobs | Windows Task Scheduler |
| Preserve recoverable data | Online SQLite snapshots and ZIP backups |
| Notice public outages | HetrixTools, running outside the server |

This separation explains why deployment involved multiple commands. Each tool solved one part of the problem.

## Lesson 2: Why Windows services were chosen

Your server was already running Windows Server 2022 and Kerio MailServer. Ollama and its models were already installed. We wanted to deploy without changing the server's operating system or interrupting the mail service.

Docker Desktop is the desktop product commonly downloaded for Windows. Docker explicitly excludes Windows Server 2022 from its supported operating systems. Installing or downloading it does not establish a supported production configuration. [Docker Desktop requirements](https://docs.docker.com/desktop/setup/install/windows-install/)

That does not mean containers cannot run on Windows Server. Microsoft documents Windows container runtimes such as Docker CE/Moby, Mirantis Container Runtime, and containerd. Windows containers and Linux containers have different operating-system requirements. A Linux-based container setup could instead run on a suitable Linux host or virtual machine. [Microsoft's container setup guide](https://learn.microsoft.com/en-us/virtualization/windowscontainers/quick-start/set-up-environment)

We chose native Windows services because the existing application and local Ollama installation could run directly on this server. This was a practical decision for this environment and timetable. A container deployment remained possible, but would have required its own preparation and validation.

We did not build a Docker image or launch a Compose stack for this project. Downloading Docker did not place the application in a container. We do not know from this session whether the Docker software you downloaded is installed, running, or used by another project. Do not remove it based only on this guide.

### What Docker would have changed

A Dockerfile describes an application's runtime and dependencies. Building it produces an image. Running the image creates a container. Compose describes several related containers, their networks, persistent volumes, and settings.

Docker would make the runtime more reproducible and make multi-service deployments easier to distribute. Windows services use installed executables and XML configurations, so we manage the runtime and file permissions ourselves.

Both approaches still need correct ports, secrets, persistent storage, authentication, backups, monitoring, and application-level error handling. A restart policy restarts exited containers when the engine is available; a health check alone does not repair a failed background task. [Docker restart policies](https://docs.docker.com/engine/containers/start-containers-automatically/)

For a future project on a dedicated Linux server, Docker Compose is often a useful starting point. For this existing Windows server, native services gave us a workable deployment without first creating a separate Linux environment. If Ollama is containerized with NVIDIA acceleration, GPU runtime configuration is also required. [Ollama Docker instructions](https://docs.ollama.com/docker)

## Lesson 3: Understand the traffic before changing ports

```text
Your browser / RSS reader / external monitor
                  |
       unews.duckdns.org:9443
                  |
                Caddy
       HTTPS and administration password
                  |
         127.0.0.1:18080
                  |
          Telegram Feed application
             /           \
   database and media    127.0.0.1:11434
                              |
                            Ollama
```

Telegram also connects to the application through Telethon's outbound connection. We did not open a separate inbound Telegram port.

In `https://unews.duckdns.org:9443/rss/testRssN8n`:

- `https` describes encrypted HTTP.
- `unews.duckdns.org` is the hostname resolved through DNS.
- `9443` is the server port receiving the HTTPS request.
- `/rss/testRssN8n` identifies the feed within the application.

`127.0.0.1` means this computer itself. On your laptop it refers to your laptop; on the server it refers to the server. Internal application and Ollama listeners are reachable locally, while Caddy receives public traffic.

### Commands we used to investigate

```powershell
# List listeners on the standard web ports.
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in 80,443 } |
    Select-Object LocalAddress, LocalPort, OwningProcess

# Identify the process owning a listener, using its CURRENT PID.
Get-Process -Id 6052 | Select-Object Id, ProcessName, Path
```

`6052` was the historical Kerio PID, not a permanent identifier. Process IDs change when programs restart. For later troubleshooting, retrieve `OwningProcess` again before identifying a process.

We found Kerio on port 443. We kept it there and chose public port 9443 for this application. Two DNS names pointing to one IP do not independently reserve the same port for two web servers.

Later, another process occupied port 8000. We left it alone and changed the application's internal port to 18080. We changed Caddy's destination too, because both ends must agree on the forwarding address. These numbers were choices based on this server, not requirements of FastAPI.

## Lesson 4: Make Python reproducible and independent of your login

The virtual environment stores the application's installed Python packages separately from other projects. It still depends on a base Python interpreter; it is not a complete virtual machine or a portable copy of Python. [Python virtual environments](https://docs.python.org/3.13/library/venv.html)

The first environment referenced Python under `C:\Users\Administrator\AppData\Local\Programs\Python\Python313`. It worked in your Administrator terminal. The LocalService account could not access that private installation.

We installed Python for all users at `C:\Python313`, preserved the old environment as `.venv-user-backup`, and created the final environment:

```powershell
C:\Python313\python.exe -m venv C:\TelegramFeed\.venv
C:\TelegramFeed\.venv\Scripts\python.exe -m pip install --upgrade pip
C:\TelegramFeed\.venv\Scripts\python.exe -m pip install -r C:\TelegramFeed\requirements-production.txt
```

`-m` asks Python to execute a module. `venv` creates the environment. `pip` installs packages. `-r` reads the dependency list. The production file pins package versions so installation does not automatically select different versions on every server.

The final application command is:

```powershell
C:\TelegramFeed\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 18080 --workers 1
```

| Part | Meaning |
| --- | --- |
| Explicit `.venv` Python path | Use this project's interpreter and packages |
| `-m uvicorn` | Start the ASGI web server |
| `main:app` | Load the object named `app` from `main.py` |
| `--host 127.0.0.1` | Accept local connections rather than exposing Python publicly |
| `--port 18080` | Listen on our selected internal port |
| `--workers 1` | Run one application worker |

We used one worker because application startup creates a Telegram listener and a translation worker. More web workers would create more copies of those background components. A larger application could separate ingestion, translation, and HTTP serving into different processes before adding web workers.

## Lesson 5: Separate code, configuration, and persistent data

| Type | Examples | Handling |
| --- | --- | --- |
| Code | `main.py`, `app`, `scripts` | Replace during a reviewed application update |
| Configuration | `.env`, service XML, Caddyfile | Preserve and change deliberately |
| Persistent state | `telegram.db`, `sessions`, `data/media` | Preserve across deployments and back up |
| Rebuildable runtime | Python environment, executables, model downloads | Reinstall or recreate when necessary |

GitHub distributes code. Ignored local files such as `.env`, database, media, and Telegram sessions are not automatically included when downloading the repository.

We chose a fresh database and session for this deployment. Existing data was moved into a timestamped backup folder first. Moving it changed what the application would load on its next startup; it did not convert old posts into new ones.

The server configuration includes:

```dotenv
BASE_URL=https://unews.duckdns.org:9443
OLLAMA_BASE_URL=http://127.0.0.1:11434
```

`BASE_URL` gives the application the public origin for generated links. It does not create DNS or HTTPS. `OLLAMA_BASE_URL` tells the application where to send translation requests. These are two different connections.

For another project, explicitly identify what must survive a code update before copying or replacing files. Docker volumes serve the same persistence purpose in a container deployment.

## Lesson 6: A service has a different identity from your terminal

WinSW wraps a normal executable so Windows Service Control Manager can start and stop it. It reads an XML file sharing its executable's base filename.

| Service | Program launched | Working directory |
| --- | --- | --- |
| `TelegramFeed` | Project Python running Uvicorn | `C:\TelegramFeed` |
| `TelegramFeedCaddy` | `C:\Caddy\caddy.exe` | `C:\Caddy` |
| `TelegramFeedOllama` | `C:\Ollama\ollama.exe serve` | `C:\Ollama` |

The working directory matters because the application currently uses relative paths for its SQLite database, media, and session. Starting in a different directory could look for different files.

Each configuration includes settings like:

```xml
<startmode>Automatic</startmode>
<onfailure action="restart" delay="10 sec"/>
<serviceaccount>
  <domain>NT AUTHORITY</domain>
  <user>LocalService</user>
</serviceaccount>
```

Automatic startup tells Windows to start the service at boot. The failure action requests a restart after a process failure. LocalService is a built-in account with fewer privileges than an administrator. All three services share that identity in our setup; it is not an isolation boundary between them.

We installed and started the application wrapper with:

```powershell
C:\TelegramFeed\Services\TelegramFeedService.exe install
C:\TelegramFeed\Services\TelegramFeedService.exe start
```

The corresponding Caddy and Ollama wrappers use the same `install`, `start`, and `stop` commands. `install` registers a service; `start` launches it. Registration alone does not prove that the child application stays healthy. [WinSW configuration](https://raw.githubusercontent.com/winsw/winsw/v2.12.0/doc/xmlConfigFile.md)

### Why we changed file permissions

An administrator can read files that a service cannot. `icacls` edits the access-control list attached to a file or folder.

| Permission notation | Meaning |
| --- | --- |
| `F` | Full control |
| `M` | Modify, including creating/changing/deleting files |
| `RX` | Read and execute |
| `(OI)` | Files can inherit this permission |
| `(CI)` | Subdirectories can inherit this permission |
| `/T` | Apply the operation recursively |
| `/grant` | Add permissions |
| `/grant:r` | Replace explicit grants for the specified identity |
| `/inheritance:r` | Disable inheritance and remove inherited entries |

The identities used were SYSTEM (`S-1-5-18`), Administrators (`S-1-5-32-544`), and LocalService (`S-1-5-19`). Numerical SIDs avoid differences in translated Windows account names.

The first recursive permission instructions caused files to lose effective access entries. That was an error in the deployment instructions, not a necessary installation step. We repaired explicit permissions and handled directory inheritance separately. Do not replay that original failed command. Read the corrected deployment record if repairing this server.

Permission commands can make files inaccessible or give an account the ability to alter them. Use named project folders and verify access after changing it. Avoid copying permission commands indiscriminately onto Windows directories or another user's profile.

For the supervision update, we used:

```powershell
icacls C:\TelegramFeed\app /grant "*S-1-5-19:RX" /T /Q
icacls C:\TelegramFeed\main.py /grant "*S-1-5-19:RX"
```

These added read/execute access for LocalService to the copied application files. They did not create administrator authentication for web visitors; Caddy handles that separately.

## Lesson 7: DNS, encryption, access control, and firewalls do different jobs

DNS maps a hostname to an IP. DuckDNS registered our names, and the updater sends the account token to DuckDNS over HTTPS to update that mapping. It runs through `TelegramFeed-DuckDNS` at boot and every five minutes.

```powershell
Resolve-DnsName unews.duckdns.org -Type A
Start-ScheduledTask -TaskName 'TelegramFeed-DuckDNS'
Get-ScheduledTaskInfo -TaskName 'TelegramFeed-DuckDNS'
```

The first command inspects the DNS answer. The second launches the registered job. The third reports scheduler information. The task's `status.txt` records the updater's result.

Caddy handles HTTPS and proxies accepted traffic to the local application. It obtains and renews the TLS certificate. Our certificate verification uses public port 80 because another service already occupies 443. [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https)

```powershell
C:\Caddy\caddy.exe hash-password
C:\Caddy\caddy.exe validate --config C:\Caddy\Caddyfile --adapter caddyfile
C:\Caddy\caddy.exe reload --config C:\Caddy\Caddyfile --adapter caddyfile
```

The first command generates a hash for the password you enter. The second checks configuration validity. The third applies a changed configuration to the running Caddy instance. Validation does not prove public connectivity; the browser and external monitor provide that evidence.

Our Caddy routing leaves feed/media access public and requires the `admin` password for management routes. It blocks debug/documentation endpoints and checks the Origin of requests that change data. HTTP Basic authentication is a browser password prompt, not an application account/session system. It relies on HTTPS to protect credentials in transit.

We created Windows Firewall allow rules scoped to Caddy:

```powershell
New-NetFirewallRule -DisplayName 'TelegramFeed Caddy HTTP' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 80 -Program 'C:\Caddy\caddy.exe'
New-NetFirewallRule -DisplayName 'TelegramFeed Caddy HTTPS' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 9443 -Program 'C:\Caddy\caddy.exe'
```

These let matching inbound traffic pass Windows Firewall. They do not open a hosting provider's separate firewall, start a listener, or provide authentication. The application on localhost port 18080 and Ollama on localhost port 11434 do not need public allow rules.

## Lesson 8: Translation is an independent dependency

We copied the complete existing Ollama installation and model directory into `C:\Ollama`. `robocopy` transferred the directories without deleting the originals:

```powershell
robocopy "C:\Users\Administrator\AppData\Local\Programs\Ollama" "C:\Ollama" /E /R:2 /W:2
robocopy "C:\Users\Administrator\.ollama\models" "C:\Ollama\models" /E /R:2 /W:2
```

`/E` includes subdirectories, `/R:2` limits retries, and `/W:2` sets the retry wait. For this tool, exit codes 0–7 are successful outcomes and 8 or higher indicates failure. The actual model-store copy was about 32 GB, including additional existing blobs. We did not delete the old copies or configure automatic cleanup of them.

The Ollama service specifies:

```xml
<env name="OLLAMA_HOST" value="127.0.0.1:11434"/>
<env name="OLLAMA_MODELS" value="C:\Ollama\models"/>
```

Only one server can own that listener. The desktop Ollama process initially conflicted with the new service. We switched to the service and disabled desktop startup to avoid competing instances. `ollama list` confirms what the responding server can see, but must be combined with service status to know which server is responding.

Translation failure does not stop publication. The app stores the source-text fallback and leaves already published fallback posts unchanged. Later posts can translate once Ollama returns. This is an example of designing the application to continue its core work when an optional dependency fails.

## Lesson 9: Recovery has two levels

Windows can restart an exited Python process. It cannot infer that a Telegram task failed while HTTP is still responding.

We therefore added `app/telegram/supervisor.py`, updated the listener and lifespan in `main.py`, and added `app/api/health.py`. The supervisor reconnects after temporary errors with increasing delays. A revoked Telegram session reports authorization required and waits for an administrator to sign in locally. It does not prompt for codes inside a noninteractive service.

The health endpoints distinguish saved-feed readiness from Telegram connection status:

```powershell
Invoke-RestMethod http://127.0.0.1:18080/health | ConvertTo-Json -Depth 4
```

`rss_ready: true` means the database's posts table was readable. `telegram.status: connected` means the supervised listener reports a live connection. `/health/telegram` returns a failing HTTP status when that connection is not healthy. These checks do not prove translation quality or every possible RSS rendering/write operation.

We tested terminating the verified application process and observed Windows restart it. `Stop-Service` is different: it is an intentional stop and normally does not trigger crash recovery. The in-process `restart_count` resets when Python restarts; it does not count Windows service restarts.

## Lesson 10: Backups and monitoring answer different questions

A backup asks, "What can we restore?" Monitoring asks, "Can people reach the feed now?"

The backup script uses SQLite's online backup API for the application database and Telegram session, allowing the app to stay running. It copies code, configuration, media, and service settings into a private ZIP. It excludes large downloadable models, runtimes, and logs. It verifies the archive before retaining it and keeps the latest seven completed backups.

```powershell
C:\Python313\python.exe C:\TelegramFeed\scripts\backup.py --output C:\TelegramFeedBackups --keep 7
```

`TelegramFeed-DailyBackup` runs this daily at 03:00 server-local time as SYSTEM. Backups contain secrets and are restricted to SYSTEM and administrators. They are ordinary, unencrypted ZIPs and are not served by the website.

The scheduled run was verified by its completed ZIP and a new successful `last-result.json`. `267009` was an intermediate "task still running" result, not a failure. A `Ready` task is waiting for its next run; the result and archive show what happened during the previous run.

HetrixTools checks the actual public RSS URL from outside the server, using `</rss>` as a content keyword. The missing-keyword exercise verified both DOWN and recovery emails without interrupting the application. This checks feed availability and basic response content; it does not guarantee that a new Telegram post was received recently.

The monitor runs independently of the server. Its free account requires a login at least every 90 days. The remaining backup limitation is that confirmed backups currently reside on the server; no automatic off-server destination has been configured. These facts are recorded without making further disruptive testing a requirement.

## Lesson 11: Operate the deployment deliberately

These are the everyday commands. Run state-changing commands on the server in an elevated PowerShell window.

| Command | Effect |
| --- | --- |
| `Get-Service TelegramFeed, TelegramFeedCaddy, TelegramFeedOllama` | Inspect service status |
| `Restart-Service TelegramFeed` | Briefly stop and start the application |
| `Stop-Service TelegramFeed` | Intentionally stop news collection and RSS serving |
| `Start-Service TelegramFeed` | Start it again |
| `Set-Service TelegramFeed -StartupType Disabled` | Prevent normal service startup, including at boot |
| `Set-Service TelegramFeed -StartupType Automatic` | Restore automatic startup; does not itself start it immediately |
| `Get-Content C:\TelegramFeed\Services\logs\*.log -Tail 30` | Read recent application/service logs |
| `Get-ScheduledTaskInfo -TaskName 'TelegramFeed-DailyBackup'` | Inspect the backup task's timing and result |

Stopping the application makes its website and RSS unavailable. Stopping Ollama leaves source-text publication available. Stopping Caddy makes the public HTTPS entry point unavailable even if Python is still running. Stopping these project services does not stop Kerio. DNS and backup tasks are separately scheduled and keep operating unless changed.

For a code update, back up, stop the application, replace only intended code files, install changed dependencies if necessary, preserve data/configuration, start the service, and check health plus a new post. Do not run an extra manual Uvicorn instance alongside the service against the same session and port.

## Lesson 12: Apply the same reasoning to another project

Start with questions, then choose tools:

1. What must run continuously, and what is only a scheduled job?
2. What operating system and runtime are supported on the host?
3. What already owns public ports, and what must remain undisturbed?
4. Which files are code, configuration, secrets, and persistent state?
5. Which account runs the program, and what access does it actually need?
6. Which routes are public, and which require authentication?
7. What happens when a process exits or a background component fails?
8. How can you verify a complete user action from outside the server?
9. What can you restore, and how will you learn about a failure?

You can reuse this process for Python APIs, Node applications, bots, dashboards, and background workers. On Linux the service manager may be systemd, or the application may run through Docker Compose. On Windows it may be a native service. DNS, TLS, persistent data, secrets, process supervision, and monitoring remain part of the deployment design.

A deployment should give you a clear ownership map: every running component has a reason, a startup method, a configuration location, a log location, and a way to stop or repair it.

## Optional read-only exercises

Run these on the server to connect the concepts with your actual installation:

```powershell
# Locate the Python CLI and inspect available Python versions.
Get-Command python, py -ErrorAction SilentlyContinue
py --list

# Check the application's underlying interpreter.
Get-Content C:\TelegramFeed\.venv\pyvenv.cfg

# See identity, executable wrapper and automatic startup configuration.
Get-CimInstance Win32_Service |
    Where-Object { $_.Name -in 'TelegramFeed','TelegramFeedCaddy','TelegramFeedOllama' } |
    Select-Object Name, State, StartMode, StartName, PathName

# Observe this deployment's listeners and their owning process IDs.
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in 80,9443,18080,11434 } |
    Select-Object LocalAddress, LocalPort, OwningProcess

# Inspect scheduled job state.
Get-ScheduledTask -TaskName 'TelegramFeed-DuckDNS','TelegramFeed-DailyBackup' |
    Select-Object TaskName, State

# Inspect Docker availability without starting or changing containers.
Get-Command docker -ErrorAction SilentlyContinue
```

If the last command finds Docker, `docker version` and `docker ps` are additional read-only checks. They tell you whether its CLI can contact an engine and whether it has running containers. A stopped engine may make them fail. Their results should be examined before deciding whether Docker is unused or safe to remove.

You do not need to print `.env`, DuckDNS tokens, or Telegram session contents for these exercises.
