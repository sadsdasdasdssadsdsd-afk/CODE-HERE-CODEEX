"""Card module endpoints for the Florida State Dashboard.

The dashboard never sends a card itself: it shows the five templates rendered
from the live settings (banner artwork included) and stores the configuration
the bot's card commands read on their next use. The previews are built from the
server's real name, roles and members, so what the owner sees is what the cards
will look like — never a dummy server.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from aiohttp import web

from dashboard import images as image_utils
from dashboard.discord_api import DiscordAPI
from utils import dashboard_store as store
from utils.modules import banners, cards, icons
from utils.modules import settings as module_settings

UPLOAD_PREFIX = "/media/uploads/"


# --------------------------------------------------------------------------- live preview
async def _live_context(request: web.Request) -> dict[str, Any]:
    """Real server details for the preview cards, with safe fallbacks."""

    db = request.app["db"]
    api: DiscordAPI = request.app["discord"]
    guild_id = await store.active_guild_id(db)
    context: dict[str, Any] = {
        "label": "This server",
        "roles": [],
        "members": [],
        "bot": "the bot",
    }
    if not guild_id:
        return context
    try:
        guild = await api.guild(guild_id)
        context["label"] = str(guild.get("name") or context["label"])
    except Exception:
        pass
    try:
        context["roles"] = [role["name"] for role in await api.roles(guild_id)]
    except Exception:
        pass
    try:
        members = await api.members(guild_id, 6)
        context["members"] = [member["label"] for member in members if member.get("label")]
    except Exception:
        pass
    try:
        context["bot"] = str((await api.bot_user()).get("username") or context["bot"])
    except Exception:
        pass
    return context


def _role_like(roles: list[str], *needles: str) -> str:
    for role in roles:
        lowered = role.lower()
        if any(needle in lowered for needle in needles):
            return role
    return roles[0] if roles else ""


def _sample_data(settings: dict[str, Any], context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """One preview payload per card type, filled with the live server's values."""

    label = str(settings.get("server_label") or context["label"])
    roles = [role for role in context["roles"] if role]
    members = context["members"] or [context["bot"]]
    rank = _role_like(roles, "high rank", "senior", "director", "founder") or "the new rank"
    punishment = _role_like(roles, "muted", "timeout", "jail", "suspend") or rank
    target = members[0]
    staff = members[1] if len(members) > 1 else target
    approver = members[2] if len(members) > 2 else target
    actor = members[3] if len(members) > 3 else staff
    member_total = str(len(members)) if members else "—"
    template = str(settings.get("welcome_message") or module_settings.DEFAULT_WELCOME)

    return {
        "infraction": {
            "member_mention": f"@{target}",
            "punishment": f"@{punishment}",
            "approved_by": f"@{approver}",
            "appealable": "Yes",
            "reason": "Issued after a staff review of the reported incident.",
            "actor_name": actor,
        },
        "promotion": {
            "member_mention": f"@{target}",
            "rank": f"@{rank}",
            "approved_by": f"@{approver}",
            "notes": "Consistently active in tickets and sessions.",
            "actor_name": actor,
        },
        "feedback": {
            "staff_mention": f"@{staff}",
            "rating": "★★★★★ (5/5)",
            "submitted_by": f"@{target}",
            "feedback": "Professional, quick and clear from the first reply.",
            "actor_name": actor,
        },
        "session": {
            "host_mention": f"@{target}",
            "session": "Management Session",
            "attendees": "All ranks present",
            "duration": "1 hour",
            "actor_mention": f"@{actor}",
            "summary": "Session closed with every point covered and no incidents.",
            "actor_name": actor,
        },
        "welcome": {
            "greeting": template.replace("{user}", f"@{target}").replace("{server}", label),
            "member_count": member_total,
            "joined": "just now",
            "link_label": "Dashboard",
        },
    }


def _banner_url(kind: str, settings: dict[str, Any]) -> str:
    name = str(settings.get(f"banner_image_{kind}") or "")
    version = name.rsplit("-", 1)[-1].split(".")[0] if name else ""
    suffix = f"?v={version}" if version else ""
    return f"/api/modules/banner/{kind}{suffix}"


def _serialise(spec: cards.CardSpec, settings: dict[str, Any]) -> dict[str, Any]:
    """The card as the dashboard renders it, with Unicode icon fallbacks."""

    return {
        "kind": spec.kind,
        "label": module_settings.KIND_LABELS[spec.kind],
        "command": module_settings.COMMAND_NAMES[spec.kind],
        "accent": f"#{spec.accent:06X}",
        "banner_url": _banner_url(spec.kind, settings),
        "custom_banner": bool(settings.get(f"banner_image_{spec.kind}")),
        "title": spec.title,
        "subtitle": spec.subtitle,
        "description": icons.resolve(spec.description),
        "fields": [
            {"label": icons.resolve(label), "value": icons.resolve(value)}
            for label, value in spec.fields
            if value
        ],
        "box_label": spec.box_label,
        "box_text": spec.box_text,
        "footer": spec.footer,
        "link_label": spec.link_label if spec.link_url else "",
    }


def _preview(settings: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    label = str(settings.get("server_label") or context["label"])
    previews: list[dict[str, Any]] = []
    for kind, data in _sample_data(settings, context).items():
        if not module_settings.enabled(settings, kind):
            continue
        previews.append(_serialise(cards.build(kind, settings, label, data), settings))
    return previews


# --------------------------------------------------------------------------- payload
async def _entries(db, limit: int = 25) -> list[dict[str, Any]]:
    async with db.execute(
        "SELECT id, kind, actor_id, target_id, channel_id, forwarded, failures, summary, created_at "
        "FROM module_entries ORDER BY id DESC LIMIT ?",
        (limit,),
    ) as cursor:
        rows = await cursor.fetchall()
    entries = []
    for row in rows:
        entries.append(
            {
                "id": row[0],
                "kind": row[1],
                "label": module_settings.KIND_LABELS.get(row[1], row[1]),
                "actor_id": row[2],
                "target_id": row[3],
                "channel_id": row[4],
                "forwarded": len(json.loads(row[5] or "[]")),
                "failures": len(json.loads(row[6] or "[]")),
                "summary": row[7],
                "created_at": row[8],
            }
        )
    return entries


async def _payload(request: web.Request) -> dict[str, Any]:
    db = request.app["db"]
    settings = await module_settings.load(db)
    context = await _live_context(request)
    return {
        "settings": module_settings.public_view(settings),
        "server_label": context["label"],
        # Five banner renders are CPU-bound Pillow work; keep them off the web loop.
        "preview": await asyncio.to_thread(_preview, settings, context),
        "entries": await _entries(db),
    }


# --------------------------------------------------------------------------- handlers
async def handle_get_modules(request: web.Request) -> web.Response:
    return web.json_response(await _payload(request))


async def handle_save_modules(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except ValueError:
        return web.json_response({"error": "Send a JSON body."}, status=400)
    if not isinstance(payload, dict):
        return web.json_response({"error": "Send a JSON object."}, status=400)
    db = request.app["db"]
    await module_settings.save(db, payload)
    await store.log_activity(db, "modules", "Card modules updated")
    return web.json_response(await _payload(request))


async def handle_banner(request: web.Request) -> web.Response:
    kind = request.match_info["kind"]
    if kind not in module_settings.KINDS:
        raise web.HTTPNotFound(text="Unknown card kind.")
    db = request.app["db"]
    settings = await module_settings.load(db)
    label = str(settings.get("server_label") or "Discord")
    tagline = str(settings.get("banner_tagline") or "")
    color = str(settings.get("brand_color") or module_settings.DEFAULTS["brand_color"])
    title = module_settings.KIND_LABELS[kind]
    image = module_settings.banner_image(settings, kind)
    loop = asyncio.get_running_loop()
    png = await loop.run_in_executor(None, banners.render, title, tagline, label, color, kind, image)
    return web.Response(
        body=png,
        content_type="image/png",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


async def handle_upload_banner(request: web.Request) -> web.Response:
    """Store an owner-uploaded banner image for one card type."""

    kind = request.match_info["kind"]
    if kind not in module_settings.KINDS:
        return web.json_response({"error": "Unknown card type."}, status=404)
    reader = await request.multipart()
    field = await reader.next()
    if field is None or field.name != "file":
        return web.json_response({"error": "Choose an image file to upload."}, status=400)
    data = await field.read(decode=False)
    try:
        extension = image_utils.validate(field.headers.get("Content-Type"), data)
    except image_utils.UploadError as error:
        return web.json_response({"error": str(error)}, status=400)

    db = request.app["db"]
    settings = await module_settings.load(db)
    uploads = module_settings.uploads_dir()
    uploads.mkdir(parents=True, exist_ok=True)
    filename = image_utils.safe_filename(f"banner_{kind}", extension)
    (uploads / filename).write_bytes(data)
    previous = str(settings.get(f"banner_image_{kind}") or "")
    if previous and previous != filename:
        image_utils.remove(uploads, f"{UPLOAD_PREFIX}{previous}")
    await module_settings.save(db, {f"banner_image_{kind}": filename})
    await store.log_activity(db, "modules", f"{module_settings.KIND_LABELS[kind]} banner image updated")
    return web.json_response(await _payload(request))


async def handle_delete_banner(request: web.Request) -> web.Response:
    kind = request.match_info["kind"]
    if kind not in module_settings.KINDS:
        return web.json_response({"error": "Unknown card type."}, status=404)
    db = request.app["db"]
    settings = await module_settings.load(db)
    previous = str(settings.get(f"banner_image_{kind}") or "")
    if previous:
        image_utils.remove(module_settings.uploads_dir(), f"{UPLOAD_PREFIX}{previous}")
    await module_settings.save(db, {f"banner_image_{kind}": ""})
    await store.log_activity(db, "modules", f"{module_settings.KIND_LABELS[kind]} banner image removed")
    return web.json_response(await _payload(request))


def register(app: web.Application) -> None:
    app.add_routes(
        [
            web.get("/api/modules", handle_get_modules),
            web.put("/api/modules", handle_save_modules),
            web.get("/api/modules/banner/{kind}", handle_banner),
            web.post("/api/modules/banner/{kind}", handle_upload_banner),
            web.delete("/api/modules/banner/{kind}", handle_delete_banner),
        ]
    )
