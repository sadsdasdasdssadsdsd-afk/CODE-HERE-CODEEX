# Run the bot and dashboard

This package runs the Discord bot and its local dashboard from one process.

## Windows PowerShell

Open PowerShell in the extracted project folder and run:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
python scripts/run_local_dashboard.py
```

In `.env`, add your Discord bot token to `DISCORD_TOKEN`. Add a fresh provider key to `AI_API_KEY` to enable the AI features. The other Discord IDs and dashboard settings are documented in `.env.example`.

Open the dashboard at **http://127.0.0.1:3000**. On first launch, create its dashboard password in the browser. To use port 3001, add `PREVIEW_STATUS_PORT=3001` to `.env`; the URL then becomes **http://127.0.0.1:3001**.

Keep the PowerShell window open: this launcher serves the dashboard and supervises the bot. Closing it stops both. The dashboard listens on localhost by default.

## Bot without the dashboard

If you only want the bot, use `python main.py` instead of `python scripts/run_local_dashboard.py`.

## Docker

The included `compose.yaml` runs the bot worker:

```powershell
Copy-Item .env.example .env
notepad .env
docker compose up -d --build
docker compose logs -f bot
```

Docker Compose keeps the bot running while its host is running and restarts it after a container exit. It does not keep a shut-down computer online, and this Compose file does not start the dashboard.

## Secrets

The ZIP intentionally excludes the real `.env`. Create your own from `.env.example` and enter secrets on your machine. A key pasted into chat should be replaced with a newly issued key.
