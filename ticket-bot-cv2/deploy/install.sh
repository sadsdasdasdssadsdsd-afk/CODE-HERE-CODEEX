#!/usr/bin/env bash
# One-shot 24/7 installer for a Debian/Ubuntu server: installs Docker, uses source
# already uploaded to /opt/ticket-bot/app (or clones REPO_URL), starts the bot and
# dashboard with docker compose, and enables them on boot.
#
#   sudo REPO_URL=https://github.com/<owner>/<repo>.git bash deploy/install.sh
#
# After it finishes, fill DISCORD_TOKEN in /opt/ticket-bot/app/.env and run
#   sudo systemctl restart ticket-bot
set -euo pipefail

APP_DIR=/opt/ticket-bot/app
REPO_URL="${REPO_URL:-}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo REPO_URL=<git url> bash deploy/install.sh" >&2
  exit 1
fi

if [ ! -f "$APP_DIR/deploy/docker-compose.prod.yml" ]; then
  if [ -z "$REPO_URL" ]; then
    echo "Project source is missing from $APP_DIR." >&2
    echo "Upload the project there, or set REPO_URL to a Git repository." >&2
    exit 1
  fi
  if ! command -v git >/dev/null 2>&1; then
    apt-get update
    apt-get install -y git
  fi
  mkdir -p "$(dirname "$APP_DIR")"
  git clone --depth 1 "$REPO_URL" "$APP_DIR"
fi

if ! command -v docker >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ca-certificates curl git
  curl -fsSL https://get.docker.com | sh
fi

if ! docker compose version >/dev/null 2>&1; then
  apt-get install -y docker-compose-plugin
fi

cd "$APP_DIR"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created $APP_DIR/.env - fill DISCORD_TOKEN before the bot can log in."
fi
chmod 600 .env

docker compose -f deploy/docker-compose.prod.yml up -d --build

install -m 644 deploy/ticket-bot.service /etc/systemd/system/ticket-bot.service
systemctl daemon-reload
systemctl enable --now ticket-bot

docker compose -f deploy/docker-compose.prod.yml ps
echo
echo "Bot installed in $APP_DIR."
echo "1. Edit $APP_DIR/.env (DISCORD_TOKEN, AI_API_KEY, and the server settings)."
echo "2. sudo systemctl restart ticket-bot"
echo "3. From your computer, open an SSH tunnel: ssh -L 3001:127.0.0.1:3001 <user>@<server-ip>"
echo "4. Open http://127.0.0.1:3001 and create the dashboard admin password on first visit."
