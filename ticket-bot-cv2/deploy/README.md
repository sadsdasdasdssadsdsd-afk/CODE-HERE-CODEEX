# 24/7 deployment on a Linux VPS

This bot must run as a persistent process. Discord delivers server events over a Gateway WebSocket, and the bot needs to keep that connection open. A small always-on VPS is a good fit; short-lived request functions and hosts that sleep are not.

The deployment files run the bot supervisor and dashboard in one Docker container. The browser does not need to stay open; the dashboard waits for a request while the bot stays connected. SQLite data and uploads live in a persistent Docker volume. Docker and systemd restart the service after failures and server reboots.

## No-cost Windows host with Docker Desktop

You can use your Windows PC instead of renting a server. This costs no hosting fee, but the PC must stay powered on, connected to the internet, and signed in to Windows. Sleep, shutdown, internet outages, and Windows updates take the bot offline. Docker Desktop must start after you sign in; the Compose service then uses `restart: unless-stopped` to recover after Docker or Windows restarts. The dashboard is included in the container, stays private on `127.0.0.1:3001`, and does not require a browser tab to remain open.

1. Revoke and replace any provider or Discord credentials that were shared in chat. Put the new values only in the project-root `.env` file (`AI_API_KEY` and `DISCORD_TOKEN`); never paste them into chat.
2. Stop the existing local Python dashboard/bot from the terminal where it was started with **Ctrl+C**. Only one bot process should use the Discord token, and the existing local dashboard already occupies port 3000.
3. Open PowerShell in the project root and start the persistent container:

   ```powershell
   $docker = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'
   & $docker compose -f deploy/docker-compose.prod.yml up -d --build
   ```

4. Check its state and recent startup output:

   ```powershell
   & $docker compose -f deploy/docker-compose.prod.yml ps
   & $docker compose -f deploy/docker-compose.prod.yml logs --tail 80 bot
   ```

   Wait for the container health check and for the logs to show `System Operational`. Open the dashboard at `http://127.0.0.1:3001` when needed.

5. In Docker Desktop settings, keep **Start Docker Desktop when you sign in** enabled. In Windows Power & battery settings, set sleep to **Never while plugged in**. Keep the PC plugged in and signed in for continuous operation. The current Compose service persists its data in the `ticket-bot-data` volume; do not run a second local bot alongside it.

This local setup is not independent hosting: when the PC is off or disconnected, the bot is offline. To keep it online while this PC is off, it needs a different machine that stays powered on.

## No-cost cloud VM (conditional)

Oracle Cloud's Always Free tier currently includes eligible compute VMs for the life of an account. It is the best no-monthly-fee option for this persistent bot, but it is not a 24/7 guarantee: Oracle can reclaim an Always Free instance after seven days if its CPU, network, and (for A1) memory utilization stay below the published thresholds. Capacity can also be temporarily unavailable. Check the [Always Free resource limits and reclamation rules](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) before creating the VM.

1. Create an Oracle Cloud Free Tier account. Oracle requires a credit/debit card for identity verification and may place a temporary authorization hold; see the [official Free Tier FAQ](https://www.oracle.com/cloud/free/faq/). Use only resources marked **Always Free-eligible** and do not upgrade the tenancy to a paid plan for this deployment.
2. In the tenancy's home region, create an Ubuntu Linux VM using an Always Free-eligible shape. The A1 shape has a free monthly allowance equivalent to 2 OCPUs and 12 GB RAM; a 1 OCPU / 6 GB VM is ample for this bot. Confirm the shape is labeled Always Free-eligible in the console before launch. If Oracle reports out of host capacity, try another availability domain or retry later.
3. Add your SSH public key during creation and assign a public IPv4 address. In the cloud firewall/security list, allow inbound TCP port 22 only from your current public IP. Do not open ports 3000 or 3001. The bot connects to Discord using outbound HTTPS/WebSocket, so no inbound bot port is needed.
4. Upload the deployment ZIP to the VM with `scp` (use the SSH private key that matches the public key added during VM creation):

   ```powershell
   scp -i <private-key-path> <deployment-zip-path> ubuntu@<server-ip>:/tmp/ticket-bot-cv2.zip
   ```

   Then connect and unpack the source. The ZIP excludes `.env`, `.venv`, local databases, logs, and Base44-specific files. A fresh VM starts with a clean SQLite volume; if you need existing ticket history, copy a backup of the local Docker volume before starting the cloud service.

   ```bash
   ssh -i <private-key-path> ubuntu@<server-ip>
   sudo apt-get update && sudo apt-get install -y unzip
   sudo mkdir -p /opt/ticket-bot/app
   sudo unzip -oq /tmp/ticket-bot-cv2.zip -d /opt/ticket-bot/app
   sudo cp /opt/ticket-bot/app/.env.example /opt/ticket-bot/app/.env
   sudo chmod 600 /opt/ticket-bot/app/.env
   sudo nano /opt/ticket-bot/app/.env
   ```

   Enter the bot/provider secrets directly on the VM. Keep `.env` mode `600` and out of Git.
5. Run the install script from the uploaded project:

   ```bash
   cd /opt/ticket-bot/app
   sudo bash deploy/install.sh
   ```

   The installer uses the uploaded source, builds the Docker image, starts the service, and enables it after server reboots. It also supports `REPO_URL` when a Git repository is available.
6. Verify the service with `sudo systemctl status ticket-bot` and `sudo docker compose -f deploy/docker-compose.prod.yml logs --tail 80 bot`. The bot log should show `Login successful` and `System Operational`.

The Compose file binds the dashboard to the VM's loopback interface on port 3001. To open it from your PC, use an SSH tunnel:

```bash
ssh -L 3001:127.0.0.1:3001 <user>@<server-ip>
```

Then open `http://127.0.0.1:3001`. No browser tab needs to stay open on the VM. If the free VM is reclaimed or unavailable, the bot stops until the VM is restored; do not simulate activity to defeat the provider's idle policy.

## Paid fallback

If a paid provider is acceptable, use a small Linux VM with at least 1 GiB RAM and verify current pricing with the provider. These are unmanaged VMs, so you maintain the operating system and backups.

Choose Ubuntu 24.04, add your own SSH public key, and configure the cloud firewall to allow inbound SSH (port 22) only. The dashboard port is bound to the server's loopback interface; do not open port 3001 publicly.

## Install

1. Create the VPS and connect over SSH.
2. Install Docker Engine and the Compose plugin, following Docker's official Ubuntu instructions.
3. Upload this project to `/opt/ticket-bot/app` with WinSCP or clone the repository there. Do not upload `.venv`, `__pycache__`, or a local `.env`.
4. On the VPS, prepare the secret file:

   ```bash
   cd /opt/ticket-bot/app
   cp .env.example .env
   chmod 600 .env
   nano .env
   ```

   Enter the rotated `DISCORD_TOKEN`, the working Groq `AI_API_KEY`, and the server/channel IDs needed by the bot. Keep this file on the VPS and out of Git.

5. Build and start the service, then enable its boot unit:

   ```bash
   sudo docker compose -f deploy/docker-compose.prod.yml up -d --build
   sudo install -m 644 deploy/ticket-bot.service /etc/systemd/system/ticket-bot.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now ticket-bot
   ```

6. Check the bot and dashboard process:

   ```bash
   sudo docker compose -f deploy/docker-compose.prod.yml ps
   sudo docker compose -f deploy/docker-compose.prod.yml logs --tail 80 bot
   ```

   The bot log should show `Login successful` and `System Operational`.

## Open the dashboard securely

The dashboard listens only on the VPS's loopback port. From your PC, leave this SSH tunnel open while using it:

```bash
ssh -L 3001:127.0.0.1:3001 <user>@<server-ip>
```

Then open `http://127.0.0.1:3001` in your browser. Create or use the dashboard admin password. The SSH tunnel encrypts the connection; no public dashboard port or domain is required.

## Data, restarts, and updates

- Compose keeps the SQLite databases, logs, and uploads in the `ticket-bot-data` Docker volume.
- `restart: unless-stopped` restarts a crashed container. The systemd unit starts it after a VPS reboot.
- Keep exactly one bot instance connected to this Discord token and database.
- After uploading updated code, rebuild with:

  ```bash
  cd /opt/ticket-bot/app
  sudo docker compose -f deploy/docker-compose.prod.yml up -d --build
  ```

- Back up the `ticket-bot-data` volume regularly. A VPS is not a backup.

## Before going live

Rotate the Discord bot token if the token previously shared in chat has not already been rotated. Add the new value only to the VPS `.env` file. The local bot and VPS copy must not run at the same time with the same token.
