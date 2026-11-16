"""Minimal Discord REST client used by the dashboard.

Only the endpoints the dashboard needs are implemented, and every call is made
server side: the bot token never reaches the browser.
"""

from __future__ import annotations

import time
from typing import Any

import aiohttp

API_BASE = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/Rapptz/discord.py 2.7.1)"
CACHE_TTL_SECONDS = 60

CHANNEL_TYPE_LABELS = {
    0: "Text channel",
    2: "Voice channel",
    4: "Category",
    5: "Announcement channel",
    13: "Stage channel",
    15: "Forum channel",
    16: "Media channel",
}

PICKABLE_CHANNEL_TYPES = {0, 5, 15, 16}


class DiscordAPIError(RuntimeError):
    """A Discord REST call failed; the message is safe to show in the dashboard."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _friendly_error(status: int, payload: Any) -> str:
    detail = ""
    if isinstance(payload, dict):
        detail = str(payload.get("message") or "")
    if status == 401:
        return "Discord rejected the bot token. Update DISCORD_TOKEN in Secrets."
    if status == 403:
        return (
            "The bot is missing the View Channels / Manage Channels permission for this server, "
            "so Discord refused the request."
        )
    if status == 404:
        return "Discord could not find that server. Check the server ID and that the bot is a member."
    if status == 429:
        return "Discord rate limited the request. Try again in a few seconds."
    return detail or f"Discord returned HTTP {status}."


class DiscordAPI:
    """Cached access to the guild, its channels and its roles."""

    def __init__(self, token: str) -> None:
        self.token = token
        self._session: aiohttp.ClientSession | None = None
        self._cache: dict[str, tuple[float, Any]] = {}

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENT})
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _request(self, path: str, *, params: dict | None = None, token: str | None = None) -> Any:
        session = await self.session()
        headers = {"Authorization": f"Bot {self.token}"} if token is None else {"Authorization": f"Bearer {token}"}
        async with session.get(f"{API_BASE}{path}", params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as response:
            if response.status >= 400:
                try:
                    payload = await response.json(content_type=None)
                except Exception:
                    payload = {}
                raise DiscordAPIError(response.status, _friendly_error(response.status, payload))
            return await response.json()

    def _cached(self, key: str, refresh: bool):
        if refresh:
            return None
        entry = self._cache.get(key)
        if entry is None:
            return None
        stored_at, value = entry
        if time.time() - stored_at > CACHE_TTL_SECONDS:
            return None
        return value

    def _store(self, key: str, value: Any) -> Any:
        self._cache[key] = (time.time(), value)
        return value

    async def bot_user(self) -> dict:
        cached = self._cached("bot_user", False)
        if cached is not None:
            return cached
        return self._store("bot_user", await self._request("/users/@me"))

    async def guild(self, guild_id: int, *, refresh: bool = False) -> dict:
        key = f"guild:{guild_id}"
        cached = self._cached(key, refresh)
        if cached is not None:
            return cached
        payload = await self._request(f"/guilds/{guild_id}", params={"with_counts": "true"})
        return self._store(
            key,
            {
                "id": payload.get("id"),
                "name": payload.get("name"),
                "icon": (
                    f"https://cdn.discordapp.com/icons/{payload['id']}/{payload['icon']}.png?size=128"
                    if payload.get("icon")
                    else None
                ),
                "member_count": payload.get("approximate_member_count"),
                "owner_id": payload.get("owner_id"),
            },
        )

    async def bot_guilds(self, *, refresh: bool = False) -> list[dict]:
        """The servers the bot itself is in (a bot token lists them here too)."""

        cached = self._cached("bot_guilds", refresh)
        if cached is not None:
            return cached
        payload = await self._request("/users/@me/guilds")
        return self._store("bot_guilds", [guild for guild in payload if guild.get("id")])

    async def server_summary(self, guild_id: int, *, refresh: bool = False) -> dict:
        """Name, icon and live counts for the overview card.

        Used when the bot has not published its state yet (a fresh deployment, or a
        host where the bot is still logging in), so the card can still name the server.
        """

        key = f"summary:{guild_id}"
        cached = self._cached(key, refresh)
        if cached is not None:
            return cached
        guild = await self._request(f"/guilds/{guild_id}", params={"with_counts": "true"})
        channels = await self._request(f"/guilds/{guild_id}/channels")
        roles = await self._request(f"/guilds/{guild_id}/roles")
        icon = (
            f"https://cdn.discordapp.com/icons/{guild['id']}/{guild['icon']}.png?size=128"
            if guild.get("icon")
            else None
        )
        return self._store(
            key,
            {
                "id": str(guild.get("id") or guild_id),
                "name": guild.get("name") or "",
                "icon": icon,
                "members": guild.get("approximate_member_count"),
                "channels": len(channels),
                "roles": len(roles),
            },
        )

    async def channels(self, guild_id: int, *, refresh: bool = False) -> list[dict]:
        key = f"channels:{guild_id}"
        cached = self._cached(key, refresh)
        if cached is not None:
            return cached
        payload = await self._request(f"/guilds/{guild_id}/channels")
        categories = {str(item["id"]): item.get("name") for item in payload if item.get("type") == 4}
        channels = []
        for item in payload:
            channel_type = int(item.get("type", 0))
            if channel_type not in PICKABLE_CHANNEL_TYPES:
                continue
            channels.append(
                {
                    "id": str(item["id"]),
                    "name": item.get("name") or "unnamed",
                    "type": channel_type,
                    "type_label": CHANNEL_TYPE_LABELS.get(channel_type, "Channel"),
                    "category": categories.get(str(item.get("parent_id"))),
                    "position": item.get("position", 0),
                }
            )
        channels.sort(key=lambda item: (item["category"] or "", item["position"], item["name"].lower()))
        return self._store(key, channels)

    async def categories(self, guild_id: int, *, refresh: bool = False) -> list[dict]:
        key = f"categories:{guild_id}"
        cached = self._cached(key, refresh)
        if cached is not None:
            return cached
        payload = await self._request(f"/guilds/{guild_id}/channels")
        categories = [
            {"id": str(item["id"]), "name": item.get("name") or "unnamed", "position": item.get("position", 0)}
            for item in payload
            if int(item.get("type", 0)) == 4
        ]
        categories.sort(key=lambda item: (item["position"], item["name"].lower()))
        return self._store(key, categories)

    async def roles(self, guild_id: int, *, refresh: bool = False) -> list[dict]:
        key = f"roles:{guild_id}"
        cached = self._cached(key, refresh)
        if cached is not None:
            return cached
        payload = await self._request(f"/guilds/{guild_id}/roles")
        roles = []
        for item in payload:
            if item.get("name") == "@everyone" or int(item.get("id", 0)) == guild_id:
                continue
            color = int(item.get("color") or 0)
            roles.append(
                {
                    "id": str(item["id"]),
                    "name": item.get("name") or "role",
                    "color": f"#{color:06x}",
                    "position": item.get("position", 0),
                    "managed": bool(item.get("managed")),
                }
            )
        roles.sort(key=lambda item: -item["position"])
        return self._store(key, roles)

    async def user_me(self, access_token: str) -> dict:
        return await self._request("/users/@me", token=access_token)

    async def user_guilds(self, access_token: str) -> list[dict]:
        return await self._request("/users/@me/guilds", token=access_token)

    async def members(self, guild_id: int, limit: int = 6, *, refresh: bool = False) -> list[dict]:
        """A few real members, so card previews use live names."""

        key = f"members:{guild_id}:{limit}"
        cached = self._cached(key, refresh)
        if cached is not None:
            return cached
        payload = await self._request(f"/guilds/{guild_id}/members", params={"limit": limit})
        records = []
        for item in payload:
            user = item.get("user") or {}
            label = item.get("nick") or user.get("global_name") or user.get("username") or ""
            records.append(
                {
                    "id": str(user.get("id") or ""),
                    "label": label,
                    "username": user.get("username") or "",
                    "bot": bool(user.get("bot")),
                }
            )
        return self._store(key, records)

    async def member(self, guild_id: int, user_id: int) -> dict:
        """Display name for a guild member, cached for five minutes."""

        key = f"member:{guild_id}:{user_id}"
        entry = self._cache.get(key)
        if entry and time.time() - entry[0] <= 300:
            return entry[1]
        payload = await self._request(f"/guilds/{guild_id}/members/{user_id}")
        user = payload.get("user") or {}
        label = payload.get("nick") or user.get("global_name") or user.get("username") or ""
        record = {
            "id": str(user.get("id") or user_id),
            "label": label,
            "username": user.get("username") or "",
            "avatar": (
                f"https://cdn.discordapp.com/avatars/{user.get('id')}/{user['avatar']}.png?size=64"
                if user.get("avatar")
                else None
            ),
        }
        self._cache[key] = (time.time(), record)
        return record

    async def invalidate(self, guild_id: int) -> None:
        for key in list(self._cache):
            if key.endswith(f":{guild_id}"):
                self._cache.pop(key, None)
