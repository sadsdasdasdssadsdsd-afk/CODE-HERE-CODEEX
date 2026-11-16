<p align="center">
  <img src="assets/readme/title.svg" width="100%" alt="Ticket Bot with CV2 — Discord support, engineered in purple">
</p>

<p align="center">
  <a href="https://github.com/Nystic-Shadow/TICKET-BOT-CV2/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Nystic-Shadow/TICKET-BOT-CV2/ci.yml?branch=main&style=for-the-badge&label=CI&labelColor=0B0714&color=8B5CF6"></a>
  <img alt="Python 3.11 to 3.14" src="https://img.shields.io/badge/Python-3.11%E2%80%933.14-8B5CF6?style=for-the-badge&labelColor=0B0714">
  <img alt="discord.py 2.7.1" src="https://img.shields.io/badge/discord.py-2.7.1-8B5CF6?style=for-the-badge&labelColor=0B0714">
  <img alt="MIT License" src="https://img.shields.io/github/license/Nystic-Shadow/TICKET-BOT-CV2?style=for-the-badge&labelColor=0B0714&color=8B5CF6">
</p>

<p align="center">
  An advanced, multi-server Discord ticket system built with <strong>discord.py Components V2</strong>.<br>
  Persistent panels, guided ticket creation, staff workflows, transcripts, reminders, triggers, and application-owned emojis—ready for local, Docker, VPS, Railway, Render, or panel hosting.
</p>

<p align="center"><strong>Developed by Nystic Shadow</strong></p>

<p align="center">
  <img src="assets/readme/hero-art.webp" width="100%" alt="A violet ticket workflow moving through four interface stages">
</p>

## Why this bot

Ticket Bot with CV2 is designed for communities that want a polished support workflow without a separate dashboard or JSON configuration file. Server administrators configure the ticket system inside Discord, while deployment settings and secrets live in environment variables.

The running bot automatically uses the name and avatar configured in the Discord Developer Portal. No bot name, application branding, token, server ID, role ID, or channel ID is hard-coded.

| Area | Implementation |
|---|---|
| Interface | Discord Components V2 using `LayoutView`, `Container`, `TextDisplay`, selects, buttons, media galleries, and files |
| Configuration | Hardcoded assistance content in `utils/assistance.py` plus `.env` or host-provided environment variables |
| Storage | Per-server SQLite state with WAL mode, indexes, migrations, and restart-safe reminders |
| Panels | One hardcoded Components V2 assistance panel with a persistent dropdown that survives restarts |
| Theme | Gold `#D9A04A` assistance surfaces with a 24-piece server emoji pack |
| Deployment | Native Python, Docker Compose, Docker, Railway, Render, Linux service, Windows, or Pterodactyl |
| Identity | Name and avatar come directly from the Discord application |
| License | MIT |

## Contents

- [Interface previews](#interface-previews)
- [Features A–Z](#features-az)
- [Ticket lifecycle](#ticket-lifecycle)
- [Commands](#commands)
- [Discord application setup](#discord-application-setup)
- [Environment variables](#environment-variables)
- [Local installation](#local-installation)
- [First server setup](#first-server-setup)
- [Deployment](#deployment)
- [Server emojis](#server-emojis)
- [Storage and backups](#storage-and-backups)
- [Project structure](#project-structure)
- [Development and CI](#development-and-ci)
- [Security](#security)
- [Troubleshooting](#troubleshooting)
- [License](#license)

## Interface previews

These previews are deterministic renders based on the bot's actual Components V2 layout code and tracked white emoji assets.

<table>
  <tr>
    <td width="50%">
      <img src="assets/readme/ticket-panel.png" alt="Persistent ticket panel with a category dropdown">
      <p align="center"><strong>Persistent ticket panel</strong><br>Dropdown and button modes are supported.</p>
    </td>
    <td width="50%">
      <img src="assets/readme/ticket-controls.png" alt="Open ticket with a direct URL banner and claim and close controls">
      <p align="center"><strong>Ticket controls</strong><br>Claim, close, banner, creator, and category context.</p>
    </td>
  </tr>
</table>

<p align="center">
  <strong>Hardcoded assistance panel</strong> — fixed copy, three support topics, Terms of Service button, and branded ticket surfaces, republished on every startup.
</p>

## Features A–Z

| | Feature | What it does |
|---:|---|---|
| A | Server emoji pack | Uploads the 24 tracked white PNGs into every server the bot is in, reuses copies that already exist (refreshing their artwork when a PNG changed), and uses the IDs Discord assigned. |
| B | Hardcoded assistance panel | Publishes one dropdown panel with three fixed support topics, no setup command required. |
| C | Claim controls | Lets staff claim tickets with an atomic database update that prevents two agents claiming simultaneously. |
| D | Durable per-server data | Isolates configuration, tickets, roles, limits, blacklists, panels, and reminders by guild ID. |
| E | Environment-first configuration | Loads local `.env` values while allowing production host variables to take priority. |
| F | FAQ navigator | Provides an interactive Components V2 FAQ for users and staff. |
| G | Guild-aware operation | Supports multiple Discord servers from one bot process with independent settings. |
| H | Help and health commands | Includes interactive help, bot information, and latency checks. |
| I | Interaction safety | Uses ephemeral responses where appropriate and restricts automatic mentions. |
| J | Panel refresh on startup | Reposts the hardcoded panel on every startup so content edits ship with a restart. |
| K | Keyword triggers | Stores cached, case-insensitive, exact-match automatic responses per server. |
| L | Limits and logs | Enforces per-user ticket limits and writes creation, claim, close, and deletion events to the configured log channel. |
| M | Maintenance mode | Temporarily blocks new ticket creation without affecting existing tickets. |
| N | Native bot identity | Reads the bot name and avatar from the Discord Developer Portal automatically. |
| O | Open, close, and reopen lifecycle | Removes creator send access on close and restores it on staff reopen. |
| P | Persistent components | Re-registers panel, ticket-control, and closed-ticket views after every restart. |
| Q | Queue-safe creation | Uses a per-guild creation lock plus a unique ticket-number index to avoid duplicate channels and numbers. |
| R | Reminders and rate limits | Persists follow-up reminders for 1 minute to 7 days and applies per-server creation cooldowns. |
| S | Setup wizard and support roles | Configures a primary support role and any number of additional support roles. |
| T | Transcripts and transfer | Sends text transcripts to the creator and log channel when deleted, and allows staff reassignment. |
| U | User access management | Lets staff add or remove members from an active ticket channel. |
| V | Variable templates | Supports user, ticket, server, channel, time, claim, and close placeholders. |
| W | Webhook submission | Uses a one-time webhook to present the creator's ticket reason cleanly, with a normal-message fallback. |
| X | eXact-match autoresponders | Responds only when a message exactly matches a configured trigger after case folding and trimming. |
| Y | Your panel design | Supports a custom title, description, accent color, and public direct image URL. |
| Z | Zero secrets in source | Keeps tokens, runtime databases, logs, manifests, and local `.env` files out of Git. |

## Ticket lifecycle

```mermaid
flowchart LR
    A[User opens panel] --> B[Choose category]
    B --> C[Review support rules]
    C --> D{Creation checks}
    D -->|maintenance / blacklist / limit / cooldown| E[Private error]
    D -->|allowed| F[Private ticket channel]
    F --> G[Claim or transfer]
    G --> H[Resolve and close]
    H --> I{Staff decision}
    I -->|Reopen| F
    I -->|Delete| J[Transcript to creator and logs]
```

Before a channel is created, the bot rechecks maintenance mode, blacklist status, cooldown, open-ticket limit, and the next ticket number inside the per-server creation lock. If the database insert fails after Discord creates the channel, the bot removes that channel as a rollback.

## Commands

Most commands are hybrid commands, so both `/command` and the configured prefix form such as `!command` are available. Slash commands are recommended.

### Everyone

| Command | Purpose |
|---|---|
| `/help` | Open the interactive command guide. |
| `/botinfo` | Show the portal-defined bot identity, runtime versions, creation date, and server count. |
| `/ping` | Show Gateway latency. |
| `/faq` | Open the interactive ticket FAQ. |
| `/variables` | List every supported message variable. |
| `/trigger get <keyword>` | Inspect one configured exact-match trigger. |
| `/trigger list` | List active triggers for the current server. |

### Ticket members and staff

| Command | Access | Purpose |
|---|---|---|
| `/close` | Creator or support staff | Close the current ticket and show persistent delete/reopen controls. |
| `/ticket info` | Ticket channel access | Show creator, timeline, message count, status, and access list. |
| `/remind <time> [message]` | Ticket channel or support staff | Schedule a persistent follow-up using `5m`, `1h`, or `2d`. |
| `/claim` | Support staff | Claim an unclaimed ticket. |
| `/reopen` | Support staff | Reopen a closed ticket and restore creator access. |
| `/rename <name>` | Support staff | Sanitize and rename the current ticket channel. |
| `/ticket transfer <member>` | Support staff | Assign the ticket to another support member. |
| `/ticket adduser <user>` | Support staff | Grant a user access to the ticket. |
| `/ticket removeuser <user>` | Support staff | Remove a user's ticket overwrite. |

Administrators count as support staff throughout the permission checks.

### Administrators

| Command | Purpose |
|---|---|
| `/setlimit <1-10>` | Set the maximum open tickets per user. |
| `/supportrole add <role>` | Add another role that can manage tickets. |
| `/supportrole remove <role>` | Remove an additional support role. |
| `/supportrole list` | Show the primary and additional support roles. |
| `/blacklist add <user>` | Block a user from opening new tickets. |
| `/blacklist remove <user>` | Restore a user's ability to open tickets. |
| `/blacklist list` | Show the server blacklist. |
| `/maintenance` | Toggle ticket creation on or off. |
| `/announce <message>` | Send one Components V2 announcement to every open ticket. |
| `/trigger add <keyword> <message>` | Add a 1–50 character exact-match trigger. |
| `/trigger remove <keyword>` | Delete an exact-match trigger. |

The prefix-only `!sync` command is restricted to the application owner and manually synchronizes global application commands.

## Discord application setup

### 1. Create the application

1. Open the [Discord Developer Portal](https://discord.com/developers/applications).
2. Select **New Application** and choose any name.
3. Open **Bot**, create the bot user if needed, and set the name and avatar you want users to see.
4. Reset/copy the bot token and store it only in `DISCORD_TOKEN`.

Treat the bot token like a password. Never paste it into Python files, screenshots, commits, issues, or chat messages.

### 2. Enable the required intents

Under **Bot → Privileged Gateway Intents**, enable:

- **Server Members Intent**
- **Message Content Intent**

The bot requests both in `main.py`. Member data supports role and ticket-author workflows; message content supports prefix commands and exact-match triggers. Discord explains the enablement and verification rules in its [Gateway documentation](https://docs.discord.com/developers/events/gateway#privileged-intents).

### 3. Configure installation

Under **Installation → Default Install Settings**, configure a **Guild Install** with:

- `bot`
- `applications.commands`

Request only the permissions required by the workflow:

- View Channels
- Send Messages
- Read Message History
- Manage Channels
- Manage Roles
- Manage Messages
- Manage Webhooks
- Attach Files

Administrator permission is not required. Place the bot's role above roles whose channel overwrites it must manage. Discord's [installation guide](https://docs.discord.com/developers/quick-start/getting-started#adding-scopes-and-bot-permissions) and [permissions overview](https://docs.discord.com/developers/platform/oauth2-and-permissions) explain the scopes and permission model.

### 4. Install to a test server

Use the install link from the portal, select a server you manage, and test the complete lifecycle there before deploying to a production community.

## Environment variables

Copy `.env.example` to `.env`. The application loads the project-root `.env` automatically for local development. Existing process or hosting-platform variables are never overwritten, so production secret managers remain authoritative.

### Required

| Variable | Example | Description |
|---|---|---|
| `DISCORD_TOKEN` | `your_token_here` | Bot token from the Developer Portal. Secret; never commit it. |

### Bot behavior and presentation

| Variable | Default | Description |
|---|---|---|
| `BOT_PREFIX` | `!` | Prefix for hybrid/prefix commands. |
| `BOT_STATUS` | `!help \| /help` | Activity text shown in Discord. |
| `BOT_STATUS_TYPE` | `INVISIBLE` | `PLAYING`, `WATCHING`, `LISTENING`, `STREAMING`, `IDLE`, `DND`, or `INVISIBLE`. `INVISIBLE` shows the bot as offline in Discord while it stays connected and keeps answering commands, tickets and questions; the presence is re-applied after a reconnect. |
| `STREAM_URL` | empty | Public stream URL used only with `BOT_STATUS_TYPE=STREAMING`. |
| `SUPPORT_SERVER_URL` | empty | Optional Discord invite shown in help and mention responses. |
| `DEFAULT_PANEL_FOOTER` | `Developed by Nystic Shadow` | Default footer value stored with new ticket configuration. |
| `TICKET_BANNER_URL` | empty | Public direct image URL shown above the **Claim Ticket** and **Close Ticket** buttons. |
| `TICKET_RULES_TEXT` | built-in sentence | Rules shown before the user confirms ticket creation. Use `\n` for line breaks. |

`TICKET_BANNER_URL` must be a direct, publicly reachable image URL. A webpage containing an image is not the same as the image URL itself.

### Runtime, logging, and emoji sync

| Variable | Default | Description |
|---|---|---|
| `DATABASE_PATH` | `bot.db` | Primary SQLite database path. |
| `TRIGGERS_DATABASE_PATH` | `triggers.db` | Exact-match trigger database path. |
| `LOG_PATH` | `bot.log` | Rotating application log path. |
| `LOG_LEVEL` | `INFO` | Python logging level such as `INFO`, `WARNING`, or `DEBUG`. |
| `AUTO_SYNC_GUILD_EMOJIS` | `true` | Upload the tracked white assets into every server the bot is in during startup. |
| `EMOJI_DIRECTORY` | `emojis` | Local emoji asset directory; the pack lives in `emojis/white`. |

## Local installation

### Windows PowerShell

```powershell
git clone https://github.com/Nystic-Shadow/TICKET-BOT-CV2.git
Set-Location TICKET-BOT-CV2

py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

Copy-Item .env.example .env
notepad .env
python main.py
```

If PowerShell blocks activation for the current session:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
```

### Linux or macOS

```bash
git clone https://github.com/Nystic-Shadow/TICKET-BOT-CV2.git
cd TICKET-BOT-CV2

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

cp .env.example .env
nano .env
python main.py
```

A successful startup initializes both databases, uploads the emoji pack to the server, loads five extensions, registers persistent views, synchronizes slash commands, sets the portal-defined bot presence, and prints the ready state.

## First server setup

This build is **hardcoded for one private community**: there is no `/setup` wizard and no panel
commands. Configure the bot through environment variables and restart it.

1. Set `ASSISTANCE_GUILD_ID`, `ASSISTANCE_PANEL_CHANNEL_ID`, `ASSISTANCE_TICKET_CATEGORY_ID`,
   `ASSISTANCE_SUPPORT_ROLE_ID`, and optionally `ASSISTANCE_LOG_CHANNEL_ID`.
2. Set the banner artwork URLs: `ASSISTANCE_PANEL_BANNER_URL`, `ASSISTANCE_PANEL_FOOTER_URL`,
   `ASSISTANCE_TICKET_BANNER_URL`, `ASSISTANCE_TICKET_FOOTER_URL`.
3. Start the bot. It writes those IDs into the ticket configuration and publishes the assistance
   panel in the panel channel, editing the previous panel message when it still exists.
4. Open a test ticket and verify the "New Ticket Opened" message, claim, close, reopen, transcript,
   and delete behavior.

Panel copy, the three support topics, the Terms of Service text, and the ticket greeting live in
`utils/assistance.py`; change them there and restart the bot to republish.

## Deployment

This project is a long-running Discord Gateway worker. It does **not** expose an HTTP port and should not be deployed as a static site, serverless function, or cron job.

For a hands-off 24/7 install — a managed Render worker or a VPS that starts the bot on
boot — use the ready-made kit in [`deploy/`](deploy/README.md).

### Deployment matrix

| Target | Recommended mode | Persistent storage |
|---|---|---|
| Local development | Native Python + `.env` | Project directory |
| Docker host or NAS | `docker compose` | Included named volume |
| Railway | Root `Dockerfile` | Volume mounted at `/app/data` |
| Render | Docker background worker | Persistent disk mounted at `/app/data` |
| Linux VPS | Virtual environment + `systemd` | `/opt/...` or another durable path |
| Windows server | Virtual environment + Task Scheduler/service manager | Local durable directory |
| Pterodactyl | Python egg/process | Server allocation directory |

Because the project uses SQLite and an in-process creation lock, run **exactly one bot instance** against a database. Do not horizontally scale replicas that share the same SQLite files.

### Docker Compose — recommended

Docker Compose loads `.env`, builds the included non-root image, restarts the bot unless stopped, and stores databases/logs in a named volume.

```powershell
Copy-Item .env.example .env
notepad .env
docker compose up -d --build
docker compose logs -f bot
```

Linux/macOS uses `cp .env.example .env` instead of `Copy-Item`.

Useful lifecycle commands:

```bash
docker compose ps
docker compose restart bot
docker compose logs --tail 200 bot
docker compose down
```

`docker compose down` preserves the named volume. Do not add `--volumes` unless you intentionally want to delete the bot's databases and logs. Docker documents `.env`/`env_file` behavior in its [Compose environment guide](https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/).

### Plain Docker

```bash
docker build -t ticket-bot-cv2 .
docker volume create ticket-bot-data

docker run -d \
  --name ticket-bot-cv2 \
  --restart unless-stopped \
  --env-file .env \
  -e DATABASE_PATH=/app/data/bot.db \
  -e TRIGGERS_DATABASE_PATH=/app/data/triggers.db \
  -e LOG_PATH=/app/data/bot.log \
  -v ticket-bot-data:/app/data \
  ticket-bot-cv2
```

### Railway

1. Push or fork this repository.
2. Create a Railway project and deploy from the GitHub repository.
3. Railway automatically detects the root `Dockerfile`.
4. Open the service **Variables** tab and paste the contents of `.env`; fill `DISCORD_TOKEN`.
5. Add a Railway volume mounted at `/app/data`.
6. Set:

```dotenv
DATABASE_PATH=/app/data/bot.db
TRIGGERS_DATABASE_PATH=/app/data/triggers.db
LOG_PATH=/app/data/bot.log
```

7. Do not generate a public domain or configure a health-check path; this bot has no web server.
8. Keep one replica.

Railway documents root Dockerfile detection, raw environment-variable import, and `/app/data` volume mounts in its [Dockerfile](https://docs.railway.com/builds/dockerfiles), [variables](https://docs.railway.com/variables), and [volumes](https://docs.railway.com/volumes) guides.

### Render

1. Create a **Background Worker**, not a Web Service.
2. Connect the GitHub repository and choose the Docker runtime.
3. Let Render use the root `Dockerfile`.
4. Add environment variables from `.env` and provide `DISCORD_TOKEN`.
5. Attach a persistent disk at `/app/data`.
6. Set the three runtime paths to `/app/data/bot.db`, `/app/data/triggers.db`, and `/app/data/bot.log`.
7. Deploy one worker instance.

Render background workers are intended for continuous processes without inbound traffic. Render filesystems are ephemeral by default, and persistent disks for workers require a supported paid plan; see the official [service types](https://render.com/docs/service-types), [environment variables](https://render.com/docs/configure-environment-variables), and [persistent disks](https://render.com/docs/disks) documentation.

### Linux VPS with systemd

Example for a Debian/Ubuntu-style server:

```bash
sudo useradd --system --create-home --home-dir /opt/ticket-bot ticketbot
sudo git clone https://github.com/Nystic-Shadow/TICKET-BOT-CV2.git /opt/ticket-bot/app
sudo chown -R ticketbot:ticketbot /opt/ticket-bot

sudo -u ticketbot python3 -m venv /opt/ticket-bot/app/.venv
sudo -u ticketbot /opt/ticket-bot/app/.venv/bin/python -m pip install -r /opt/ticket-bot/app/requirements.txt
sudo -u ticketbot cp /opt/ticket-bot/app/.env.example /opt/ticket-bot/app/.env
sudo chmod 600 /opt/ticket-bot/app/.env
sudo nano /opt/ticket-bot/app/.env
```

Create `/etc/systemd/system/ticket-bot.service`:

```ini
[Unit]
Description=Ticket Bot with CV2
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ticketbot
Group=ticketbot
WorkingDirectory=/opt/ticket-bot/app
EnvironmentFile=/opt/ticket-bot/app/.env
ExecStart=/opt/ticket-bot/app/.venv/bin/python /opt/ticket-bot/app/main.py
Restart=on-failure
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

Enable and inspect it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ticket-bot
sudo systemctl status ticket-bot
sudo journalctl -u ticket-bot -f
```

### Windows always-on host

1. Complete the Windows local installation.
2. Keep the project in a permanent directory, not a temporary download folder.
3. Create a Task Scheduler task triggered **At startup**.
4. Set the program to `.venv\Scripts\python.exe`.
5. Set the argument to the absolute path of `main.py`.
6. Set **Start in** to the repository directory so `.env`, databases, emojis, and logs resolve correctly.
7. Configure restart on failure and run whether the user is logged in or not.

### Pterodactyl or another Python panel

- Runtime: Python 3.11 or newer
- Install command: `python -m pip install -r requirements.txt`
- Startup command: `python main.py`
- Working directory: repository root
- Network port: not required by the bot
- Secrets: use panel variables where available; otherwise upload a private `.env`
- Persistence: keep `bot.db`, `triggers.db`, WAL files, and logs on durable storage

## Server emojis

The source keeps the original custom-emoji names and references (`<:icons_help:1382704281945112645>`),
while `emojis/white/` provides 24 coordinated white PNGs whose filenames carry the legacy ID they
replace. Discord only renders a custom emoji in the servers where that emoji exists, so on startup
the bot uploads the whole pack to **every server it is in** and then uses the IDs Discord assigned:

1. Reads the pack from `emojis/white` (no network download, no manifest).
2. Lists the server's current emojis and reuses a copy that already has the pack's name, editing it
   in place when the PNG changed since it was uploaded.
3. Uploads the missing artwork with `POST /guilds/{guild_id}/emojis`.
4. Remembers the legacy-to-server-emoji ID mapping per server for the rest of the run.
5. Resolves every panel, ticket, help and dashboard emoji to that server's copy.

The bot needs **Manage Expressions** for this, which Administrator already includes. A server that
is already full keeps its existing emojis and logs which pack emojis could not be added; those
surfaces fall back to Unicode glyphs instead of showing a broken mention. The pack is uploaded again
whenever the bot joins a new server, and existing copies are never duplicated.

Automatic upload is enabled by default:

```dotenv
AUTO_SYNC_GUILD_EMOJIS=true
EMOJI_DIRECTORY=emojis
```

Set `AUTO_SYNC_GUILD_EMOJIS=false` to keep the bot from touching server emojis; the panel then uses
the Unicode fallbacks from `utils/assistance.py`.

## Storage and backups

The bot creates runtime data only when it runs:

| Path | Contents |
|---|---|
| `bot.db` | Server configuration, categories, panels, tickets, user history, blacklists, support roles, rate limits, and reminders |
| `triggers.db` | Per-server exact-match autoresponders |
| `bot.log` | Rotating application log, up to 5 MiB per file with three backups |
| `*.db-wal`, `*.db-shm` | SQLite WAL runtime files |

All of these are ignored by Git. They can contain Discord IDs, ticket text, staff actions, and server-specific configuration.

For a simple consistent backup, stop the process, copy both databases to private storage, then restart. Container deployments should back up the named volume or hosting-provider disk. Never publish a production database in a release or support issue.

## Project structure

```text
.
├── .github/workflows/ci.yml       # Python 3.11–3.14 quality checks
├── assets/readme/                 # Animated header, hero art, and UI previews
├── cogs/                          # Tickets, help, triggers, and mention response
├── emojis/white/                 # 24 tracked white server emoji assets
├── scripts/                       # Sandbox preview dashboard entry point
├── tests/                         # Database, startup, emoji sync, and source-safety tests
├── utils/                         # Config, data, theme, helpers, variables, and emoji sync
├── views/                         # Components V2 panels, controls, setup, and modals
├── .env.example                   # Safe environment template
├── compose.yaml                   # Local/hosted container deployment
├── Dockerfile                     # Non-root production image
├── Procfile                       # Worker declaration for compatible platforms
├── main.py                        # Bot startup and persistent-view registration
└── README.md                      # Complete project documentation
```

## Development and CI

Create the environment and install development tools:

```bash
python -m venv .venv
python -m pip install -r requirements-dev.txt
```

Run the same checks used by GitHub Actions:

```bash
ruff check .
python -m compileall -q .
python -m unittest discover -s tests -v
```

The CI workflow runs on every push and pull request across Python 3.11, 3.12, 3.13, and 3.14 with read-only repository permissions.

For contributions:

1. Fork the repository.
2. Create a focused branch.
3. Keep credentials and runtime data out of the diff.
4. Add or update tests for behavior changes.
5. Run lint, compile, and unit tests.
6. Open a pull request with the reason and validation results.

## Security

- Keep `DISCORD_TOKEN` only in `.env` or the host's secret manager.
- Never commit `.env`, databases, WAL files, or logs.
- Rotate the token immediately if it is exposed.
- Use a dedicated test server before production rollout.
- Request only the Discord permissions listed in this README.
- Use direct image URLs from domains you trust.
- Protect production database backups because transcripts and ticket descriptions may contain sensitive user content.
- Define a retention policy appropriate for your community.
- Run only one instance against the SQLite files.

The trigger system sends responses with mentions disabled, ticket submission webhooks disable mentions, and server emoji uploads authenticate only with the bot token.

## Troubleshooting

| Problem | Check |
|---|---|
| Gateway closes with code `4014` | Enable **Server Members Intent** and **Message Content Intent** in the Developer Portal. |
| Slash commands are missing | Confirm the app was installed with `applications.commands`, restart once, and inspect the command-sync log. |
| Bot cannot create a ticket | Grant Manage Channels and Manage Roles, then verify its role position and category overrides. |
| Controls do not pin | Grant Manage Messages in ticket channels. |
| Creator does not receive a transcript | The user may have DMs disabled; the configured log channel still receives the file when accessible. |
| Banner or panel image is blank | Use a public direct `http://` or `https://` image URL under 200 characters. |
| Emojis do not appear | Keep `AUTO_SYNC_GUILD_EMOJIS=true`, grant the bot **Manage Expressions**, check the server's emoji slots, and inspect `Emoji upload finished with … failure(s)` in the logs. |
| Data disappears after redeploy | Mount persistent storage and point all database/log paths to that mount. |
| Duplicate or inconsistent tickets | Stop extra replicas; this SQLite deployment supports one process. |
| `.env` seems ignored | Run from the repository root and confirm a host-level variable is not overriding the file. |
| Database reports locking | Stop duplicate bot processes and keep the database on a normal local/persistent filesystem. |

## License

Released under the MIT License.

Copyright © 2026 Nystic Shadow.

<p align="center">
  <strong>Ticket Bot with CV2</strong><br>
  Built and maintained by Nystic Shadow 💜
</p>
