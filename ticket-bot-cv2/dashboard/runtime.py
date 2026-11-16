"""Supervises the Discord bot process and keeps its console output."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from collections import deque

from dashboard import PROJECT_ROOT

CONSOLE_LINES = 200
RESTART_DELAY = 30.0
MAX_RESTART_DELAY = 120.0
# Discord's edge (Cloudflare error 1015) throttles a login by host for a while.
# Retrying inside that window only extends the ban, so wait it out instead.
RATE_LIMIT_DELAY = 900.0

ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")
IDENTITY_PATTERN = re.compile(r"Login successful logged in as (?P<name>.+?)\s*$")
FATAL_MARKERS = ("DISCORD_TOKEN is required", "Invalid bot token", "Bot failed to start", "rate-limiting")
RATE_LIMIT_MARKERS = ("429 Too Many Requests", "error 1015", "rate-limiting")

STATE_LABELS = {
    "online": ("Connected to Discord", "good"),
    "starting": ("Starting up", "waiting"),
    "missing-token": ("Waiting for DISCORD_TOKEN", "waiting"),
    "failed": ("Bot stopped with an error", "bad"),
    "stopped": ("Bot process stopped", "bad"),
}


class BotRuntime:
    """Runs ``python main.py`` and exposes its live state."""

    def __init__(self) -> None:
        self.lines: deque[str] = deque(maxlen=CONSOLE_LINES)
        self.identity: str | None = None
        self.ready = False
        self.fatal: str | None = None
        self.exit_code: int | None = None
        self.restarts = 0
        self.rate_limited = False
        self.started_at: float | None = None
        self._process: subprocess.Popen[str] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def token_configured(self) -> bool:
        return bool(os.getenv("DISCORD_TOKEN", "").strip())

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        # The bot is a blocking gateway client, so it runs in its own thread. A plain
        # subprocess keeps the web server's event loop out of its lifecycle.
        self._thread = threading.Thread(target=self._supervise, name="bot-supervisor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()

    def _supervise(self) -> None:
        while not self._stop.is_set():
            self.ready = False
            self.identity = None
            self.fatal = None
            self.exit_code = None
            self.rate_limited = False
            self.started_at = time.time()
            process = subprocess.Popen(
                [sys.executable, "main.py"],
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            self._process = process
            assert process.stdout is not None
            for raw in iter(process.stdout.readline, ""):
                line = ANSI_PATTERN.sub("", raw.rstrip())
                if not line:
                    continue
                self.lines.append(line)
                print(line, flush=True)
                self._observe(line)
                if self.fatal and process.poll() is None:
                    # discord.py can stay alive after a startup failure.
                    process.terminate()
            self.exit_code = process.wait()
            print(f"[dashboard] bot process exited with code {self.exit_code}", flush=True)
            # The bot is meant to stay online around the clock, so it is supervised
            # until the dashboard itself stops: any other exit - clean or not - is
            # followed by a restart, and a failed first login keeps retrying.
            if self._stop.is_set() or not self.token_configured:
                return
            # Keep retrying with a capped backoff so the bot connects by itself once
            # the Discord application settings it needs are fixed. A throttled host
            # gets a long, fixed wait: hammering the ban only lengthens it.
            delay = self._next_delay()
            self.restarts += 1
            if self.rate_limited:
                print(
                    "[dashboard] Discord is rate-limiting this host (Cloudflare 1015); "
                    f"next login attempt in {delay / 60:.0f} minutes",
                    flush=True,
                )
            else:
                print(f"[dashboard] restarting the bot in {delay:.0f}s", flush=True)
            self._stop.wait(delay)

    def _next_delay(self) -> float:
        """Seconds to wait before the next login attempt."""
        if self.rate_limited:
            return RATE_LIMIT_DELAY
        return min(RESTART_DELAY * (2 ** min(self.restarts, 3)), MAX_RESTART_DELAY)

    def _observe(self, line: str) -> None:
        match = IDENTITY_PATTERN.search(line)
        if match:
            self.identity = match.group("name")
        if "System Operational" in line:
            self.ready = True
        if any(marker in line for marker in RATE_LIMIT_MARKERS):
            self.rate_limited = True
        if self.fatal is None and any(marker in line for marker in FATAL_MARKERS):
            self.fatal = line.strip()

    def console(self) -> list[str]:
        return list(self.lines)

    def status(self) -> dict[str, object]:
        running = self.running
        if not self.token_configured:
            state = "missing-token"
        elif running and self.ready:
            state = "online"
        elif running:
            state = "starting"
        elif self.fatal:
            state = "failed"
        else:
            state = "stopped"
        uptime = int(time.time() - self.started_at) if running and self.started_at else 0
        return {
            "state": state,
            "running": running,
            "identity": self.identity,
            "fatal": self.fatal,
            "exit_code": self.exit_code,
            "restarts": self.restarts,
            "uptime_seconds": uptime,
            "token_configured": self.token_configured,
        }
