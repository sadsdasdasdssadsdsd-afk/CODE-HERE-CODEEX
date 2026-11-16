"""Skip redundant global slash-command syncs.

Discord rate-limits global bulk command overwrites heavily, so a bot that
restarts often (a dev sandbox does) eventually blocks inside ``tree.sync()``
until the request times out. The registered command set rarely changes, so the
signature of the last successful sync is cached and an unchanged tree is not
synced again.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("discord")


def command_signature(tree) -> str:
    """Stable fingerprint of the top-level commands and their parameters."""

    payload = []
    for command in sorted(tree.get_commands(), key=lambda item: item.name):
        parameters = getattr(command, "parameters", None) or []
        payload.append(
            {
                "name": command.name,
                "description": getattr(command, "description", "") or "",
                "parameters": [
                    {
                        "name": parameter.name,
                        "required": bool(getattr(parameter, "required", False)),
                        "description": getattr(parameter, "description", "") or "",
                    }
                    for parameter in parameters
                ],
            }
        )
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


class CommandSyncCache:
    """Remembers the command signature of the last successful global sync."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @classmethod
    def next_to_database(cls, database_path: str) -> "CommandSyncCache":
        """Keep the cache beside the bot database; in-memory DBs disable it."""

        if database_path == ":memory:":
            return cls(Path("/dev/null"))
        return cls(Path(database_path).expanduser().parent / "command_sync.json")

    @property
    def enabled(self) -> bool:
        return self.path != Path("/dev/null")

    def _read_signature(self) -> str | None:
        try:
            return json.loads(self.path.read_text("utf-8")).get("signature")
        except (OSError, ValueError, AttributeError):
            return None

    def is_current(self, tree) -> bool:
        return self.enabled and self._read_signature() == command_signature(tree)

    def remember(self, tree) -> None:
        if not self.enabled:
            return
        record = {
            "signature": command_signature(tree),
            "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(record), "utf-8")
        except OSError:
            logger.warning("Could not record the command sync fingerprint at %s", self.path)
