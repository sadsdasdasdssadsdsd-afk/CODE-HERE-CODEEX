"""aiohttp application for the Florida State Dashboard."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import aiosqlite
from aiohttp import web
from urllib.parse import urlencode

from dashboard import STATIC_ROOT
from dashboard import ai_api, modules_api
from dashboard.auth import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    LoginThrottle,
    OAuthStates,
    Sessions,
    check_password_strength,
    hash_password,
    verify_password,
)
from dashboard.discord_api import DiscordAPI, DiscordAPIError
from dashboard.images import (
    IMAGE_SLOTS,
    SLOT_LABELS,
    UploadError,
    remove as remove_image,
    store as store_image,
)
from dashboard.runtime import BotRuntime
from utils import assistance
from utils import dashboard_store as store
from utils.ai import schema as ai_schema
from utils.modules import schema as modules_schema

logger = logging.getLogger("dashboard")

def resolve_port(default: int = 3000) -> int:
    """HTTP port for the dashboard.

    ``PREVIEW_STATUS_PORT`` is the explicit setting used by the sandbox and the
    production compose file. ``PORT`` is the PaaS convention (Render, Koyeb and
    friends) and is used when the explicit variable is absent.
    """

    for name in ("PREVIEW_STATUS_PORT", "PORT"):
        raw = os.getenv(name, "").strip()
        if not raw:
            continue
        try:
            return int(raw)
        except ValueError:
            logger.warning("Ignoring invalid %s=%r", name, raw)
    return default


PORT = resolve_port()
PUBLIC_API = {
    "/api/bootstrap",
    "/api/auth/login",
    "/api/auth/setup",
    "/api/auth/logout",
}
ACTIVITY_TYPES = ("playing", "watching", "listening", "competing", "custom")
PRESENCE_STATES = ("online", "idle", "dnd", "invisible")


def _project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path.cwd() / path


def database_path() -> Path:
    return _project_path(os.getenv("DATABASE_PATH", "bot.db"))


def uploads_dir() -> Path:
    return database_path().parent / "uploads"


def public_base_url(request: web.Request) -> str:
    """Absolute origin the bot can use for uploaded images."""

    suffix = os.getenv("BASE44_PUBLIC_HOST_SUFFIX", "").strip()
    if suffix:
        return f"https://{PORT}-{suffix}"
    forwarded_proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    return f"{forwarded_proto}://{request.host}"


def invite_url() -> str:
    """OAuth2 install link the owner uses to add the bot to a server."""

    client_id = os.getenv("DISCORD_CLIENT_ID", "").strip()
    if not client_id:
        return ""
    return (
        f"https://discord.com/oauth2/authorize?client_id={client_id}"
        "&scope=bot%20applications.commands&permissions=8"
    )


def _uptime_seconds(started_at: str | None) -> int:
    if not started_at:
        return 0
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return 0
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - started).total_seconds()))


# --------------------------------------------------------------------------- middleware
@web.middleware
async def guard(request: web.Request, handler):
    """Session + CSRF protection for every dashboard API route."""

    path = request.path
    is_api = path.startswith("/api")
    if is_api and path not in PUBLIC_API:
        session = request.app["sessions"].resolve(request.cookies.get(SESSION_COOKIE))
        if session is None:
            return web.json_response({"error": "Sign in to continue.", "signed_out": True}, status=401)
        request["session"] = session
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            token = request.headers.get("X-CSRF-Token", "")
            if not token or token != session["csrf"]:
                return web.json_response({"error": "Your session token expired. Reload the page."}, status=403)

    try:
        response = await handler(request)
    except DiscordAPIError as error:
        return web.json_response({"error": str(error)}, status=502)
    except UploadError as error:
        return web.json_response({"error": str(error)}, status=400)
    except web.HTTPException:
        raise
    except Exception:
        logger.exception("Dashboard request failed: %s %s", request.method, path)
        return web.json_response({"error": "The dashboard hit an unexpected error. Check the server logs."}, status=500)

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if path == "/" or path.startswith("/static/"):
        # The shell and its scripts must be revalidated on every load: a stale
        # cached app.js left returning administrators without the new views.
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


# --------------------------------------------------------------------------- helpers
def settings_of(request: web.Request) -> dict:
    return request["settings"]


async def load_settings(request: web.Request) -> dict:
    return await store.get_settings(request.app["db"])


async def guild_id_of(request: web.Request) -> int | None:
    return await store.active_guild_id(request.app["db"])


FIXED_ROLE_GROUPS = {
    "administrators": assistance.ADMIN_ROLE_NAMES,
    "moderators": assistance.MODERATOR_ROLE_NAMES,
    "support": assistance.SUPPORT_ROLE_NAMES,
}


async def resolved_roles(request: web.Request, guild_id: int) -> dict:
    """Match the hardcoded staff role names against the server's roles."""

    roles = await request.app["discord"].roles(guild_id)
    resolved = {group: assistance.fixed_roles(roles, names) for group, names in FIXED_ROLE_GROUPS.items()}
    resolved["names"] = {group: list(names) for group, names in FIXED_ROLE_GROUPS.items()}
    return resolved


def _parse_int(value) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if not text.isdigit():
        raise UploadError("IDs must be numeric snowflakes.")
    return int(text)


async def member_labels(request: web.Request, guild_id: int, user_ids: list[int]) -> dict[int, str]:
    """Best-effort display names; failures fall back to the raw ID."""

    api: DiscordAPI = request.app["discord"]
    unique = {uid for uid in user_ids if uid}
    results: dict[int, str] = {}
    for user_id in list(unique)[:30]:
        try:
            member = await api.member(guild_id, user_id)
            results[user_id] = member["label"]
        except (DiscordAPIError, asyncio.TimeoutError):
            results[user_id] = ""
    return results


# --------------------------------------------------------------------------- auth routes
async def handle_bootstrap(request: web.Request) -> web.Response:
    sessions: Sessions = request.app["sessions"]
    settings = await store.get_settings(request.app["db"])
    session = sessions.resolve(request.cookies.get(SESSION_COOKIE))
    password_set = bool(settings.get("admin_password_hash"))
    oauth_configured = bool(os.getenv("DISCORD_CLIENT_ID") and os.getenv("DISCORD_CLIENT_SECRET"))
    return web.json_response(
        {
            "authenticated": session is not None,
            "setup_required": not password_set,
            "password_set": password_set,
            "oauth_configured": oauth_configured,
            "csrf": session["csrf"] if session else None,
            "method": session["method"] if session else None,
            "user": session["user"] if session else None,
            "bot": request.app["runtime"].status(),
        }
    )


def _set_session_cookies(request: web.Request, response: web.Response, session: dict) -> None:
    secure = request.scheme == "https" or request.headers.get("X-Forwarded-Proto") == "https"
    response.set_cookie(
        SESSION_COOKIE,
        session["token"],
        httponly=True,
        samesite="Lax",
        max_age=SESSION_TTL_SECONDS,
        secure=secure,
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        session["csrf"],
        httponly=False,
        samesite="Lax",
        max_age=SESSION_TTL_SECONDS,
        secure=secure,
        path="/",
    )


def _issue_session(request: web.Request, session: dict) -> web.Response:
    response = web.json_response(
        {"ok": True, "csrf": session["csrf"], "method": session["method"], "user": session["user"]}
    )
    _set_session_cookies(request, response, session)
    return response


async def handle_setup(request: web.Request) -> web.Response:
    payload = await request.json()
    password = str(payload.get("password") or "")
    db = request.app["db"]
    settings = await store.get_settings(db)
    if settings.get("admin_password_hash"):
        return web.json_response({"error": "The dashboard password is already set."}, status=409)
    problem = check_password_strength(password)
    if problem:
        return web.json_response({"error": problem}, status=400)
    await store.set_settings(db, {"admin_password_hash": hash_password(password)})
    await store.log_activity(db, "config", "Dashboard administrator password created")
    session = request.app["sessions"].create({"name": "Dashboard administrator"}, method="password")
    return _issue_session(request, session)


async def handle_login(request: web.Request) -> web.Response:
    throttle: LoginThrottle = request.app["throttle"]
    address = request.remote or "unknown"
    if throttle.blocked(address):
        return web.json_response({"error": "Too many attempts. Wait five minutes and try again."}, status=429)
    payload = await request.json()
    password = str(payload.get("password") or "")
    settings = await store.get_settings(request.app["db"])
    if not verify_password(password, settings.get("admin_password_hash")):
        throttle.record_failure(address)
        return web.json_response({"error": "That password is not correct."}, status=401)
    throttle.reset(address)
    session = request.app["sessions"].create({"name": "Dashboard administrator"}, method="password")
    return _issue_session(request, session)


async def handle_logout(request: web.Request) -> web.Response:
    request.app["sessions"].destroy(request.cookies.get(SESSION_COOKIE))
    response = web.json_response({"ok": True})
    response.del_cookie(SESSION_COOKIE, path="/")
    response.del_cookie(CSRF_COOKIE, path="/")
    return response


OAUTH_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
OAUTH_TOKEN_URL = "https://discord.com/api/v10/oauth2/token"
OAUTH_STATE_COOKIE = "fsd_oauth_state"
ADMINISTRATOR_PERMISSION = 0x8


def _oauth_settings(request: web.Request) -> tuple[str, str]:
    return (
        os.getenv("DISCORD_CLIENT_ID", "").strip(),
        os.getenv("DISCORD_CLIENT_SECRET", "").strip(),
    )


def _redirect_uri(request: web.Request) -> str:
    return f"{public_base_url(request)}/auth/discord/callback"


async def handle_oauth_start(request: web.Request) -> web.Response:
    """Send an administrator through Discord's OAuth2 consent screen."""

    client_id, client_secret = _oauth_settings(request)
    if not client_id or not client_secret:
        raise web.HTTPBadRequest(
            text="Discord OAuth login is not configured. Add DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET, "
            "or sign in with the dashboard password."
        )
    state = request.app["states"].create()
    params = {
        "client_id": client_id,
        "response_type": "code",
        "scope": "identify guilds",
        "redirect_uri": _redirect_uri(request),
        "state": state,
        "prompt": "consent",
    }
    response = web.HTTPFound(f"{OAUTH_AUTHORIZE_URL}?{urlencode(params)}")
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        state,
        httponly=True,
        samesite="Lax",
        max_age=600,
        secure=request.scheme == "https" or request.headers.get("X-Forwarded-Proto") == "https",
        path="/",
    )
    return response


async def handle_oauth_callback(request: web.Request) -> web.Response:
    """Exchange the OAuth2 code and verify the account administers the server."""

    client_id, client_secret = _oauth_settings(request)
    if not client_id or not client_secret:
        raise web.HTTPBadRequest(text="Discord OAuth login is not configured.")
    state = request.query.get("state")
    if not request.app["states"].consume(state) or request.cookies.get(OAUTH_STATE_COOKIE) != state:
        raise web.HTTPForbidden(text="The Discord login request expired. Start again.")
    code = request.query.get("code")
    if not code:
        raise web.HTTPForbidden(text="Discord did not return an authorization code.")

    api: DiscordAPI = request.app["discord"]
    session = await api.session()
    async with session.post(
        OAUTH_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _redirect_uri(request),
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=aiohttp.ClientTimeout(total=15),
    ) as token_response:
        token_payload = await token_response.json(content_type=None)
    access_token = token_payload.get("access_token")
    if not access_token:
        raise web.HTTPForbidden(text="Discord rejected the login. Check the OAuth2 redirect URI.")

    user = await api.user_me(access_token)
    guilds = await api.user_guilds(access_token)
    guild_id = await guild_id_of(request)
    allowed = [
        guild
        for guild in guilds
        if guild.get("owner") or int(guild.get("permissions", 0)) & ADMINISTRATOR_PERMISSION
    ]
    if guild_id is not None:
        allowed = [guild for guild in allowed if str(guild.get("id")) == str(guild_id)]
    if not allowed:
        raise web.HTTPForbidden(
            text=(
                "Your Discord account does not administer the configured server. "
                "Ask the server owner for an administrator or owner account."
            )
        )

    dashboard_session = request.app["sessions"].create(
        {"id": user.get("id"), "name": user.get("global_name") or user.get("username") or "Administrator"},
        method="discord",
    )
    await store.log_activity(
        request.app["db"], "config", f"Administrator signed in with Discord ({dashboard_session['user']['name']})"
    )
    response = web.HTTPFound("/")
    _set_session_cookies(request, response, dashboard_session)
    response.del_cookie(OAUTH_STATE_COOKIE, path="/")
    return response


async def handle_change_password(request: web.Request) -> web.Response:
    payload = await request.json()
    current = str(payload.get("current") or "")
    new_password = str(payload.get("password") or "")
    db = request.app["db"]
    settings = await store.get_settings(db)
    stored_hash = settings.get("admin_password_hash")
    # A Discord-verified administrator may set the first password; afterwards the
    # current password is always required.
    if stored_hash:
        if not verify_password(current, stored_hash):
            return web.json_response({"error": "The current password is not correct."}, status=403)
    elif request["session"]["method"] != "discord":
        return web.json_response({"error": "Sign in with Discord to set the first password."}, status=403)
    problem = check_password_strength(new_password)
    if problem:
        return web.json_response({"error": problem}, status=400)
    await store.set_settings(db, {"admin_password_hash": hash_password(new_password)})
    await store.log_activity(db, "config", "Dashboard administrator password changed")
    return web.json_response({"ok": True})


# --------------------------------------------------------------------------- overview
async def _server_card(request: web.Request, state: dict, settings: dict) -> dict:
    """Facts for the "Connected server" card.

    The bot publishes them every 10 seconds, but a fresh deployment - or a host where
    the bot is still retrying its login - has none of those rows yet. Ask Discord's REST
    API instead, so a valid token always names the server instead of showing dashes.
    """

    card = {
        "id": str(state.get("guild_id") or settings.get("guild_id") or ""),
        "name": state.get("guild_name"),
        "icon": state.get("guild_icon"),
        "members": state.get("member_count"),
        "channels": state.get("channel_count"),
        "roles": state.get("role_count"),
        "guilds": state.get("guild_count"),
        "configured": bool(settings.get("guild_id")),
    }
    api: DiscordAPI = request.app["discord"]
    if card["name"] or not api.token:
        return card

    async def summary(guild_id: int) -> dict | None:
        try:
            return await api.server_summary(guild_id)
        except (DiscordAPIError, aiohttp.ClientError, asyncio.TimeoutError):
            return None

    # The home server, in the order the rest of the app resolves it: a saved server
    # ID, then the ASSISTANCE_GUILD_ID wiring, then whatever the bot last reported.
    candidates: list[int] = []
    for raw in (settings.get("guild_id"), assistance.GUILD_ID, state.get("guild_id")):
        if str(raw or "").isdigit() and int(raw) not in candidates:
            candidates.append(int(raw))

    live = None
    for guild_id in candidates:
        live = await summary(guild_id)
        if live is not None:
            break
    if live is None:
        try:
            guilds = await api.bot_guilds()
        except (DiscordAPIError, aiohttp.ClientError, asyncio.TimeoutError):
            return card
        # Nothing is configured yet: the biggest server the bot is in is the one it
        # actually runs the ticket system for, not whichever Discord lists first.
        for guild in guilds[:5]:
            found = await summary(int(guild["id"]))
            if found and (live is None or (found["members"] or 0) > (live["members"] or 0)):
                live = found
        if live is None:
            return card
        card["guilds"] = card["guilds"] or len(guilds)
    return {**card, **live, "guilds": card["guilds"]}


async def _bot_account(request: web.Request, state: dict) -> str | None:
    """The signed-in bot user, from the stored state or straight from Discord."""

    if state.get("bot_user"):
        return state["bot_user"]
    api: DiscordAPI = request.app["discord"]
    if not api.token:
        return None
    try:
        user = await api.bot_user()
    except (DiscordAPIError, aiohttp.ClientError, asyncio.TimeoutError):
        return None
    name = user.get("global_name") or user.get("username") or ""
    discriminator = user.get("discriminator") or ""
    if name and discriminator and discriminator != "0":
        return f"{name}#{discriminator}"
    return name or None


async def handle_overview(request: web.Request) -> web.Response:
    db = request.app["db"]
    settings = await store.get_settings(db)
    state = await store.read_bot_state(db)
    guild_id = await store.active_guild_id(db)
    counts = await store.ticket_counts(db, guild_id) if guild_id else {"total": 0, "open": 0, "closed": 0}
    activity = await store.recent_activity(db, 8)
    tickets = await store.list_tickets(db, guild_id, limit=6) if guild_id else []
    runtime = request.app["runtime"].status()
    server, bot_user = await asyncio.gather(
        _server_card(request, state, settings),
        _bot_account(request, state),
    )

    owner_ids = [int(ticket["creator_id"]) for ticket in tickets]
    labels = await member_labels(request, guild_id, owner_ids) if guild_id else {}
    for ticket in tickets:
        ticket["owner_name"] = labels.get(int(ticket["creator_id"])) or ""

    return web.json_response(
        {
            "server": server,
            "bot": {
                **runtime,
                "user": bot_user,
                "latency_ms": state.get("latency_ms"),
                "ready": bool(state.get("ready")) or runtime["state"] == "online",
                "uptime_seconds": _uptime_seconds(state.get("started_at")) or runtime["uptime_seconds"],
                "last_activity": state.get("last_activity"),
            },
            "presence": {
                "activity": state.get("activity"),
                "activity_type": state.get("activity_type"),
                "activity_text": state.get("activity_text"),
                "status": state.get("presence_status"),
                "custom_status": settings.get("custom_status_text") or "",
            },
            "tickets": counts,
            "recent_activity": activity,
            "recent_tickets": tickets,
            "rotation": {
                "enabled": settings.get("rotation_enabled") == "1",
                "count": len(await store.list_statuses(db)),
            },
            "server_clock": store.utc_now(),
        }
    )


async def handle_sleep(request: web.Request) -> web.Response:
    """Keep-alive probe for the Sleep mode page: local state only, no Discord REST."""

    db = request.app["db"]
    state = await store.read_bot_state(db)
    runtime = request.app["runtime"].status()
    return web.json_response(
        {
            "bot": {
                **runtime,
                "latency_ms": state.get("latency_ms"),
                # While a (re)start is in flight the process clock is the accurate one;
                # the stored timestamp only carries the ready session that came before.
                "uptime_seconds": runtime["uptime_seconds"] or _uptime_seconds(state.get("started_at")),
                "last_activity": state.get("last_activity"),
            },
            "server_clock": store.utc_now(),
        }
    )


# --------------------------------------------------------------------------- tickets
async def handle_tickets(request: web.Request) -> web.Response:
    db = request.app["db"]
    guild_id = await guild_id_of(request)
    if not guild_id:
        return web.json_response({"tickets": [], "counts": {"total": 0, "open": 0, "closed": 0}})
    status = request.query.get("status", "open")
    if status not in {"open", "closed", "all"}:
        status = "open"
    tickets = await store.list_tickets(db, guild_id, None if status == "all" else status, limit=100)
    labels = await member_labels(
        request,
        guild_id,
        [int(ticket["creator_id"]) for ticket in tickets] + [int(ticket["claimed_by"] or 0) for ticket in tickets],
    )
    for ticket in tickets:
        ticket["owner_name"] = labels.get(int(ticket["creator_id"])) or ""
        ticket["staff_name"] = labels.get(int(ticket["claimed_by"] or 0)) or ""
    return web.json_response(
        {
            "tickets": tickets,
            "counts": await store.ticket_counts(db, guild_id),
            "guild_id": str(guild_id),
        }
    )


async def handle_close_ticket(request: web.Request) -> web.Response:
    db = request.app["db"]
    ticket_id = int(request.match_info["ticket_id"])
    ticket = await store.get_ticket(db, ticket_id)
    if ticket is None:
        return web.json_response({"error": "That ticket no longer exists."}, status=404)
    if ticket["status"] != "open":
        return web.json_response({"error": "That ticket is already closed."}, status=409)
    guild_id = await guild_id_of(request)
    if guild_id and int(ticket.get("guild_id") or guild_id) != guild_id:
        return web.json_response({"error": "That ticket belongs to another server."}, status=400)
    session = request["session"]
    await store.enqueue_command(
        db,
        "close_ticket",
        {
            "ticket_id": ticket_id,
            "closed_by": "Dashboard administrator",
            "closed_by_id": int((session.get("user") or {}).get("id") or 0),
        },
    )
    await store.log_activity(db, "ticket", f"Dashboard asked the bot to close ticket #{ticket['ticket_number']:04d}")
    return web.json_response({"ok": True, "message": "The bot is closing the ticket."})


# --------------------------------------------------------------------------- presence
async def handle_status(request: web.Request) -> web.Response:
    db = request.app["db"]
    settings = await store.get_settings(db)
    state = await store.read_bot_state(db)
    return web.json_response(
        {
            "presence": {
                "activity": state.get("activity") or "",
                "activity_type": state.get("activity_type") or "playing",
                "activity_text": state.get("activity_text") or "",
                "status": state.get("presence_status") or "online",
                "updated_at": state.get("presence_updated_at"),
                "last_activity": state.get("last_activity"),
            },
            "rotation": {
                "enabled": settings.get("rotation_enabled") == "1",
                "presence_status": settings.get("presence_status") or "online",
                "entries": await store.list_statuses(db),
            },
            "activity_types": list(ACTIVITY_TYPES),
            "presence_states": list(PRESENCE_STATES),
            "recent_commands": await store.recent_commands(db, 6),
        }
    )


async def handle_apply_presence(request: web.Request) -> web.Response:
    payload = await request.json()
    activity = str(payload.get("activity") or "playing").lower()
    text = str(payload.get("text") or "").strip()
    status = str(payload.get("status") or "online").lower()
    if activity not in ACTIVITY_TYPES:
        return web.json_response({"error": "Unsupported activity type."}, status=400)
    if not text:
        return web.json_response({"error": "Enter the activity text."}, status=400)
    if len(text) > 128:
        return web.json_response({"error": "Activity text must be 128 characters or fewer."}, status=400)
    if status not in PRESENCE_STATES:
        return web.json_response({"error": "Unsupported status."}, status=400)
    db = request.app["db"]
    await store.set_settings(db, {"presence_status": status})
    await store.enqueue_command(db, "presence", {"activity": activity, "text": text, "status": status})
    await store.log_activity(db, "bot", f"Dashboard set presence to {activity} {text}")
    return web.json_response({"ok": True, "message": "Presence sent to the bot."})


async def handle_rotation_settings(request: web.Request) -> web.Response:
    payload = await request.json()
    db = request.app["db"]
    values: dict[str, str] = {}
    if "enabled" in payload:
        values["rotation_enabled"] = "1" if payload["enabled"] else "0"
    status = payload.get("presence_status")
    if status is not None:
        if str(status) not in PRESENCE_STATES:
            return web.json_response({"error": "Unsupported status."}, status=400)
        values["presence_status"] = str(status)
    if not values:
        return web.json_response({"error": "Nothing to update."}, status=400)
    await store.set_settings(db, values)
    await store.enqueue_command(db, "reload_settings", {})
    state = "enabled" if values.get("rotation_enabled") == "1" else "disabled"
    await store.log_activity(db, "bot", f"Status rotation {state}")
    return web.json_response({"ok": True})


async def handle_add_status(request: web.Request) -> web.Response:
    payload = await request.json()
    activity = str(payload.get("activity") or "playing").lower()
    text = str(payload.get("text") or "").strip()
    try:
        duration = int(payload.get("duration_seconds") or 600)
    except (TypeError, ValueError):
        return web.json_response({"error": "Duration must be a number of seconds."}, status=400)
    if activity not in ACTIVITY_TYPES:
        return web.json_response({"error": "Unsupported activity type."}, status=400)
    if not text or len(text) > 128:
        return web.json_response({"error": "Activity text must be 1-128 characters."}, status=400)
    if not 30 <= duration <= 86400:
        return web.json_response({"error": "Duration must be between 30 seconds and 24 hours."}, status=400)
    db = request.app["db"]
    await store.add_status(db, activity, text, duration)
    await store.enqueue_command(db, "reload_settings", {})
    await store.log_activity(db, "bot", f"Rotation entry added: {activity} {text} ({duration}s)")
    return web.json_response({"ok": True})


async def handle_update_status(request: web.Request) -> web.Response:
    status_id = int(request.match_info["status_id"])
    payload = await request.json()
    fields: dict = {}
    if "activity" in payload:
        activity = str(payload["activity"]).lower()
        if activity not in ACTIVITY_TYPES:
            return web.json_response({"error": "Unsupported activity type."}, status=400)
        fields["activity"] = activity
    if "text" in payload:
        text = str(payload["text"]).strip()
        if not text or len(text) > 128:
            return web.json_response({"error": "Activity text must be 1-128 characters."}, status=400)
        fields["text"] = text
    if "duration_seconds" in payload:
        try:
            duration = int(payload["duration_seconds"])
        except (TypeError, ValueError):
            return web.json_response({"error": "Duration must be a number of seconds."}, status=400)
        if not 30 <= duration <= 86400:
            return web.json_response({"error": "Duration must be between 30 seconds and 24 hours."}, status=400)
        fields["duration_seconds"] = duration
    if "enabled" in payload:
        fields["enabled"] = 1 if payload["enabled"] else 0
    db = request.app["db"]
    if not await store.update_status(db, status_id, **fields):
        return web.json_response({"error": "That rotation entry no longer exists."}, status=404)
    await store.enqueue_command(db, "reload_settings", {})
    return web.json_response({"ok": True})


async def handle_delete_status(request: web.Request) -> web.Response:
    status_id = int(request.match_info["status_id"])
    db = request.app["db"]
    if not await store.delete_status(db, status_id):
        return web.json_response({"error": "That rotation entry no longer exists."}, status=404)
    await store.enqueue_command(db, "reload_settings", {})
    await store.log_activity(db, "bot", "Rotation entry removed")
    return web.json_response({"ok": True})


# --------------------------------------------------------------------------- configuration
async def _ticket_config(db, guild_id: int) -> dict:
    async with db.execute(
        "SELECT channel_id, role_id, category_id, log_channel_id, ping_role_id FROM tickets WHERE guild_id = ?",
        (guild_id,),
    ) as cursor:
        row = await cursor.fetchone()
    async with db.execute(
        "SELECT role_id FROM additional_support_roles WHERE guild_id = ? ORDER BY role_id", (guild_id,)
    ) as cursor:
        extra_roles = [str(value) for (value,) in await cursor.fetchall()]
    keys = ("channel_id", "role_id", "category_id", "log_channel_id", "ping_role_id")
    values = dict(zip(keys, row)) if row else dict.fromkeys(keys)
    return {key: str(value) if value else "" for key, value in values.items()} | {
        "additional_support_roles": extra_roles
    }


async def handle_get_config(request: web.Request) -> web.Response:
    db = request.app["db"]
    settings = await store.get_settings(db)
    guild_id = await store.active_guild_id(db)
    ticket_config = await _ticket_config(db, guild_id) if guild_id else {}
    bot_in_guild = True
    if guild_id:
        try:
            await request.app["discord"].guild(guild_id)
        except DiscordAPIError as error:
            bot_in_guild = error.status != 404
    roles = await resolved_roles(request, guild_id) if guild_id and bot_in_guild else {}
    emoji_synced = (await store.read_bot_state(db)).get("emoji_synced") or 0
    return web.json_response(
        {
            "guild_id": settings.get("guild_id", "") or (str(guild_id) if guild_id else ""),
            "emoji_synced": emoji_synced,
            "bot_in_guild": bot_in_guild,
            "invite_url": invite_url(),
            "roles": roles,
            "settings": {
                key: value
                for key, value in settings.items()
                if key not in {"admin_password_hash"}
            },
            "ticket_config": ticket_config,
            "images": {
                slot: {
                    "label": label,
                    "path": settings.get(key, ""),
                    "setting": key,
                }
                for slot, key in IMAGE_SLOTS.items()
                for label in [SLOT_LABELS[slot]]
            },
            "public_base_url": settings.get("public_base_url") or public_base_url(request),
        }
    )


CHANNEL_SETTINGS = {
    "panel_channel_id": "Assistance panel channel",
    "staff_channel_id": "Staff notification channel",
    "transcript_channel_id": "Transcript channel",
}


async def handle_save_config(request: web.Request) -> web.Response:
    payload = await request.json()
    db = request.app["db"]
    values: dict[str, str | None] = {}
    changed: list[str] = []

    needs_invite = False
    if "guild_id" in payload:
        try:
            guild_id = _parse_int(payload.get("guild_id"))
        except UploadError as error:
            return web.json_response({"error": str(error)}, status=400)
        if guild_id and guild_id != await guild_id_of(request):
            try:
                await request.app["discord"].guild(guild_id, refresh=True)
            except DiscordAPIError as error:
                # A server the bot has not joined yet is still a valid choice: save it
                # so the owner can invite the bot and keep configuring.
                if error.status != 404:
                    return web.json_response({"error": str(error)}, status=400)
                needs_invite = True
        values["guild_id"] = str(guild_id) if guild_id else None
        changed.append("Discord server")
    target_guild = guild_id if "guild_id" in payload else await guild_id_of(request)

    for key, label in CHANNEL_SETTINGS.items():
        if key in payload:
            try:
                value = _parse_int(payload.get(key))
            except UploadError as error:
                return web.json_response({"error": str(error)}, status=400)
            values[key] = str(value) if value else None
            changed.append(label)

    # The welcome automation is its own system, configured here rather than in the
    # card modules. An explicit "0" is what switches it off, and it stays off.
    if "welcome_channel_id" in payload:
        try:
            value = _parse_int(payload.get("welcome_channel_id"))
        except UploadError as error:
            return web.json_response({"error": str(error)}, status=400)
        values["welcome_channel_id"] = str(value) if value else None
        changed.append("Welcome channel")
    if "welcome_message" in payload:
        text = " ".join(str(payload.get("welcome_message") or "").split())[:320]
        values["welcome_message"] = text or None
        changed.append("Welcome message")
    if "welcome_enabled" in payload:
        values["welcome_enabled"] = "1" if payload.get("welcome_enabled") else "0"
        changed.append("Welcome automation")

    # Staff roles are fixed in utils/assistance.py: match them by name in the
    # server on every save so the wiring never depends on manual role picking.
    support_role_ids: list[str] = []
    if target_guild:
        try:
            roles = await resolved_roles(request, target_guild)
        except DiscordAPIError:
            roles = {}
        if roles:
            values["admin_role_ids"] = store.dump_id_list(role["id"] for role in roles["administrators"])
            values["moderator_role_ids"] = store.dump_id_list(role["id"] for role in roles["moderators"])
            support_role_ids = [role["id"] for role in roles["support"]]
            values["support_role_id"] = support_role_ids[0] if support_role_ids else None
            changed.append("Staff roles")

    await store.set_settings(db, values)

    guild_id = await guild_id_of(request)
    ticket_fields = {
        "channel_id": "Ticket channel",
        "category_id": "Ticket category",
        "role_id": "Support role",
        "log_channel_id": "Ticket log channel",
    }
    ticket_payload = {key: payload[key] for key in ticket_fields if key in payload}
    if ticket_payload and guild_id:
        parsed = {}
        for key, label in ticket_fields.items():
            if key in ticket_payload:
                try:
                    parsed[key] = _parse_int(ticket_payload[key])
                except UploadError as error:
                    return web.json_response({"error": str(error)}, status=400)
                changed.append(label)
        assignments = ", ".join(f"{key} = ?" for key in parsed)
        await db.execute(
            "INSERT INTO tickets (guild_id) VALUES (?) ON CONFLICT(guild_id) DO NOTHING", (guild_id,)
        )
        await db.execute(f"UPDATE tickets SET {assignments} WHERE guild_id = ?", (*parsed.values(), guild_id))
        await db.commit()

    if target_guild and roles:
        await db.execute("DELETE FROM additional_support_roles WHERE guild_id = ?", (target_guild,))
        for role_id in support_role_ids:
            await db.execute(
                "INSERT OR IGNORE INTO additional_support_roles (guild_id, role_id) VALUES (?, ?)",
                (guild_id, int(role_id)),
            )
        if not support_role_ids:
            await db.execute("UPDATE tickets SET role_id = NULL WHERE guild_id = ?", (target_guild,))
        await db.commit()

    if not changed:
        return web.json_response({"error": "Nothing to save."}, status=400)

    await store.set_settings(db, {"public_base_url": public_base_url(request)})
    await store.enqueue_command(db, "reload_settings", {})
    if payload.get("republish_panel"):
        await store.enqueue_command(db, "republish_panel", {})
    await store.log_activity(db, "config", "Configuration updated: " + ", ".join(changed))
    message = "Configuration saved. The bot is applying it now."
    if needs_invite:
        message = "Server saved. The bot is not in that server yet — use Invite the bot to add it."
    return web.json_response({"ok": True, "message": message, "needs_invite": needs_invite})


async def handle_channels(request: web.Request) -> web.Response:
    guild_id = await guild_id_of(request)
    if not guild_id:
        return web.json_response({"error": "Save the Discord server ID first."}, status=400)
    refresh = request.query.get("refresh") == "1"
    api: DiscordAPI = request.app["discord"]
    return web.json_response(
        {
            "channels": await api.channels(guild_id, refresh=refresh),
            "categories": await api.categories(guild_id, refresh=refresh),
        }
    )


async def handle_roles(request: web.Request) -> web.Response:
    guild_id = await guild_id_of(request)
    if not guild_id:
        return web.json_response({"error": "Save the Discord server ID first."}, status=400)
    api: DiscordAPI = request.app["discord"]
    return web.json_response({"roles": await api.roles(guild_id, refresh=request.query.get("refresh") == "1")})


# --------------------------------------------------------------------------- images
async def handle_upload_image(request: web.Request) -> web.Response:
    slot = request.match_info["slot"]
    if slot not in IMAGE_SLOTS:
        return web.json_response({"error": "Unknown image slot."}, status=404)
    reader = await request.multipart()
    field = await reader.next()
    if field is None or field.name != "file":
        return web.json_response({"error": "Choose an image file to upload."}, status=400)
    data = await field.read(decode=False)
    db = request.app["db"]
    settings = await store.get_settings(db)
    path = store_image(uploads_dir(), slot, field.headers.get("Content-Type"), data)
    remove_image(uploads_dir(), settings.get(IMAGE_SLOTS[slot]))
    await store.set_settings(
        db,
        {IMAGE_SLOTS[slot]: path, "public_base_url": public_base_url(request)},
    )
    await store.enqueue_command(db, "republish_panel", {})
    await store.log_activity(db, "config", f"Uploaded {SLOT_LABELS[slot].lower()}")
    return web.json_response({"ok": True, "path": path, "message": f"{SLOT_LABELS[slot]} updated."})


async def handle_delete_image(request: web.Request) -> web.Response:
    slot = request.match_info["slot"]
    if slot not in IMAGE_SLOTS:
        return web.json_response({"error": "Unknown image slot."}, status=404)
    db = request.app["db"]
    settings = await store.get_settings(db)
    key = IMAGE_SLOTS[slot]
    remove_image(uploads_dir(), settings.get(key))
    await store.set_settings(db, {key: None})
    await store.enqueue_command(db, "republish_panel", {})
    await store.log_activity(db, "config", f"Removed {SLOT_LABELS[slot].lower()}")
    return web.json_response({"ok": True, "message": f"{SLOT_LABELS[slot]} removed."})


# --------------------------------------------------------------------------- static
async def handle_index(request: web.Request) -> web.Response:
    return web.FileResponse(STATIC_ROOT / "index.html")


async def handle_media(request: web.Request) -> web.Response:
    folder = request.match_info["folder"]
    name = request.match_info["name"]
    if "/" in name or name.startswith("."):
        raise web.HTTPNotFound()
    root = STATIC_ROOT / "emoji" if folder == "emoji" else uploads_dir()
    target = root / name
    if not target.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(target)


async def handle_favicon(request: web.Request) -> web.Response:
    # Browsers request /favicon.ico on their own; serve the dashboard shield mark so
    # the preview load stays free of 404s.
    return web.FileResponse(STATIC_ROOT / "emoji" / "shield.png")


async def handle_health(request: web.Request) -> web.Response:
    status = request.app["runtime"].status()
    crashed = status["state"] == "stopped" and bool(status["token_configured"])
    return web.json_response({"web": "ok", "bot": status}, status=503 if crashed else 200)


async def handle_console(request: web.Request) -> web.Response:
    return web.json_response(
        {"lines": request.app["runtime"].console()[-80:], "bot": request.app["runtime"].status()}
    )


# --------------------------------------------------------------------------- lifecycle
async def _startup(app: web.Application) -> None:
    db = await aiosqlite.connect(database_path())
    db.row_factory = None
    await db.execute("PRAGMA journal_mode = WAL")
    await db.execute("PRAGMA busy_timeout = 5000")
    await store.ensure_schema(db)
    await ai_schema.ensure_schema(db)
    await modules_schema.ensure_schema(db)
    # The bot creates its own tables on startup, but the dashboard answers pages
    # before that happened on a fresh deployment (or while the bot retries a login),
    # so the ticket tables must exist here as well. Same idempotent script, no data.
    from utils.database import ensure_canonical_schema

    await ensure_canonical_schema(db)
    app["db"] = db
    app["discord"] = DiscordAPI(os.getenv("DISCORD_TOKEN", "").strip())
    app["sessions"] = Sessions()
    app["throttle"] = LoginThrottle()
    app["states"] = OAuthStates()
    app["runtime"].start()


async def _cleanup(app: web.Application) -> None:
    app["runtime"].stop()
    await app["discord"].close()
    db = app.get("db")
    if db is not None:
        await db.close()


def create_app() -> web.Application:
    app = web.Application(middlewares=[guard], client_max_size=8 * 1024 * 1024)
    app["runtime"] = BotRuntime()
    app.add_routes(
        [
            web.get("/", handle_index),
            web.get("/favicon.ico", handle_favicon),
            web.get("/health", handle_health),
            web.get("/api/bootstrap", handle_bootstrap),
            web.post("/api/auth/setup", handle_setup),
            web.post("/api/auth/login", handle_login),
            web.post("/api/auth/logout", handle_logout),
            web.post("/api/auth/password", handle_change_password),
            web.get("/auth/discord", handle_oauth_start),
            web.get("/auth/discord/callback", handle_oauth_callback),
            web.get("/api/sleep", handle_sleep),
            web.get("/api/overview", handle_overview),
            web.get("/api/console", handle_console),
            web.get("/api/tickets", handle_tickets),
            web.post("/api/tickets/{ticket_id}/close", handle_close_ticket),
            web.get("/api/status", handle_status),
            web.post("/api/status/apply", handle_apply_presence),
            web.post("/api/status/rotation", handle_rotation_settings),
            web.post("/api/status/entries", handle_add_status),
            web.patch("/api/status/entries/{status_id}", handle_update_status),
            web.delete("/api/status/entries/{status_id}", handle_delete_status),
            web.get("/api/config", handle_get_config),
            web.put("/api/config", handle_save_config),
            web.get("/api/discord/channels", handle_channels),
            web.get("/api/discord/roles", handle_roles),
            web.post("/api/images/{slot}", handle_upload_image),
            web.delete("/api/images/{slot}", handle_delete_image),
            web.get("/media/{folder}/{name}", handle_media),
            web.static("/static", STATIC_ROOT),
        ]
    )
    ai_api.register(app)
    modules_api.register(app)
    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    web.run_app(create_app(), host=os.getenv("DASHBOARD_HOST", "127.0.0.1"), port=PORT, print=None)


if __name__ == "__main__":
    main()

