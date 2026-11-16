# Moving this project to another Base44 account

Everything needed to run this app is in this repository: the Discord bot
(`main.py`, `cogs/`, `views/`, `utils/`), the hosted administration dashboard
(`dashboard/`, served by `scripts/preview_dashboard.py`), the sandbox runbook
(`docker-compose.base44.yml`) and the environment manifest
(`.base44/environment.json`). Nothing lives only in the old account.

## 1. Import the repository

Import this GitHub repository into the paid Base44 account and open the
workspace. Base44 clones the repository, reads `.base44/environment.json`
(`previewPort` 3000, `healthPath` `/health`) and runs
`docker compose -f docker-compose.base44.yml up -d`.

The stack is a single `python:3.12-slim` service with the repository
bind-mounted at `/app`:

| Port | What |
|---|---|
| `3000` | Administration dashboard (password or Discord OAuth) plus `/health` |

Runtime data stays in the `ticket-bot-data` volume at `/app/data`
(`bot.db`, `triggers.db`, `bot.log`, `command_sync.json`). Remove the volume to
start over; it also holds every ticket, so back it up before deleting it.

## 2. Add the secrets

Set these in the new account's Secrets page (never in a file in the repository):

| Secret | Required | Where it comes from |
|---|---|---|
| `DISCORD_TOKEN` | yes | Discord Developer Portal → your application → **Bot** → Reset Token |
| `DISCORD_CLIENT_ID` | optional | Developer Portal → **OAuth2** → Client ID (only enables "Sign in with Discord" on the dashboard) |
| `DISCORD_CLIENT_SECRET` | optional | Developer Portal → **OAuth2** → Client Secret |

The platform writes them to `/run/base44/app.env`, which
`docker-compose.base44.yml` loads **after** `.env.base44-defaults`, so the values
from the dashboard always win. An imported app has no secrets until they are
added; the bot will not reach READY without `DISCORD_TOKEN`.

In the Developer Portal → **Bot** → **Privileged Gateway Intents**, enable
**Message Content** and **Server Members**. Without them Discord closes the
gateway with `4014 Disallowed intent(s)` and the dashboard shows the login
handshake hint.

## 3. Invite the bot

Use the invite link the dashboard shows (or the standard `applications.commands`
+ `bot` scopes) and grant **Administrator**. Two features depend on it:

- **Manage Expressions** — the bot uploads the emoji pack to the server on every
  startup (see below).
- **Manage Channels / Manage Roles** — ticket channels and support permissions.

## 4. Configure it from the dashboard

Sign in at the preview URL. On a fresh database the first screen asks you to
create the admin password (5 characters or more), or you can use
"Sign in with Discord" once `DISCORD_CLIENT_ID` and `DISCORD_CLIENT_SECRET` are
set.

In **Configuration** pick the server, then the assistance panel channel, ticket
category, log, staff and transcript channels, and upload the banner images. The
staff roles are resolved by name and need no IDs: administrators are **Founder**,
**Co-Founder**, **Assistant Founder**, and **Board of Directors**; Management ranks
are moderators. Primary and additional support are N/A. Saving the configuration
republishes the assistance panel.

## 5. What happens on the first boot

1. Both SQLite databases are created and migrated.
2. The emoji pack in `emojis/white` (24 PNGs) is uploaded into every server the
   bot is in, reusing copies that already exist. Discord only renders a custom
   emoji where it exists, which is why this is a server upload and not an
   application upload; the log line is
   `Server emoji pack ready for <server>: 24 emoji(s) available, 0 missing`.
3. Five extensions load, the persistent views register per server, and the
   assistance panel is published or edited in the configured channel with the
   real emoji IDs.
4. Slash commands sync only when their signature changed (`command_sync.json`).
5. The dashboard bridge starts and publishes live state for port 3000.

If emojis stay blank, check the log line `Emoji upload finished with … failure(s)`
— the usual causes are a missing **Manage Expressions** permission or a server
whose emoji slots are full. Those surfaces then fall back to Unicode glyphs
rather than showing broken mentions.

## 6. Verifying a fresh deployment

```bash
docker compose -f docker-compose.base44.yml ps
curl -s http://localhost:3000/health
docker compose -f docker-compose.base44.yml exec -T bot sh -ec \
  "grep -iE 'emoji|panel|Dashboard bridge' /app/data/bot.log | tail -20"
```

Checks and tests (all offline):

```bash
docker compose -f docker-compose.base44.yml exec -T bot sh -ec \
  "pip install -q -r requirements-dev.txt && ruff check . && python -m unittest discover -s tests"
```

## Sandbox notes that travel with the code

- Always run `docker-compose.base44.yml`, never `compose.yaml` or the root
  `Dockerfile`: those build a production image with the source baked in, so code
  edits would not show up in the preview.
- There is no hot reload. After editing bot code run
  `docker compose -f docker-compose.base44.yml restart bot` and reload the
  preview.
- Forgotten dashboard password: delete the stored hash and reload the page.

  ```bash
  docker compose -f docker-compose.base44.yml exec -T bot python -c "
  import sqlite3
  db = sqlite3.connect('/app/data/bot.db')
  db.execute(\"DELETE FROM dashboard_settings WHERE key='admin_password_hash'\"); db.commit()"
  ```

- Base44 has no publish button for this repository. Deployment is whatever the
  repository's own documentation describes (native Python, Docker, Railway,
  Render, VPS or panel hosting); see `README.md` → *Deployment*.
