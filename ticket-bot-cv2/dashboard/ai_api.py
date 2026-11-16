"""JSON API for the AI Control System.

Every route sits behind the dashboard's session + CSRF middleware, so only a
signed-in administrator can read or change anything here. Writes are throttled,
audited into ``ai_activity``, and long work is queued for the bot instead of
running inside the request.
"""

from __future__ import annotations

import time
from typing import Any

from aiohttp import web

from dashboard import discord_api
from utils import dashboard_store as dashboard
from utils.ai import engine, jobs, store
from utils.ai import settings as ai_settings
from utils.ai.provider import AIProvider

WRITE_LIMIT = 40
WRITE_WINDOW_SECONDS = 60
SETTING_INPUTS = {
    "enabled",
    "provider",
    "model",
    "base_url",
    "temperature",
    "max_output_tokens",
    "system_instructions",
    "personality",
    "confidence_threshold",
    "human_approval_required",
    "max_processing_seconds",
    "escalation_behavior",
    "retention_days",
    "partnership_channel_id",
    "partnership_ad_channel_id",
    "partnership_role_ids",
    "partnership_requirements",
    "features",
}


async def _body(request: web.Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError as error:
        raise web.HTTPBadRequest(text="Send a JSON body.", content_type="application/json") from error
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="Send a JSON object.", content_type="application/json")
    return payload


def _write_allowed(request: web.Request) -> bool:
    """Simple per-session write budget for AI endpoints."""

    session = request.get("session") or {}
    key = session.get("csrf") or request.remote or "anonymous"
    bucket = request.app["ai_writes"].setdefault(key, [])
    now = time.monotonic()
    bucket[:] = [stamp for stamp in bucket if now - stamp < WRITE_WINDOW_SECONDS]
    if len(bucket) >= WRITE_LIMIT:
        return False
    bucket.append(now)
    return True


async def _audit(request: web.Request, action: str, detail: str = "", *, guild_id: int | None = None) -> None:
    await store.log_activity(
        request.app["db"],
        f"admin.{action}",
        guild_id=guild_id,
        actor="dashboard",
        decision=action,
        status="completed",
        detail=detail[:2000],
    )


def _int_id(value: Any, name: str = "id") -> int:
    text = str(value or "").strip()
    if not text.isdigit():
        raise web.HTTPBadRequest(text=f"{name} must be a numeric Discord ID.", content_type="application/json")
    return int(text)


async def _guild_id(request: web.Request) -> int | None:
    return await dashboard.active_guild_id(request.app["db"])


def _page(request: web.Request) -> tuple[int, int]:
    page = max(1, ai_settings.as_int(request.query.get("page"), default=1))
    size = min(100, max(5, ai_settings.as_int(request.query.get("page_size"), default=25)))
    return (page - 1) * size, size


def _date_range(request: web.Request) -> dict[str, str | None]:
    return {
        "date_from": (request.query.get("from") or "").strip() or None,
        "date_to": (request.query.get("to") or "").strip() or None,
    }


# --------------------------------------------------------------------------- overview
async def handle_overview(request: web.Request) -> web.Response:
    db = request.app["db"]
    guild_id = await _guild_id(request)
    data = await store.overview(db, guild_id)
    data["queue_jobs"] = await jobs.recent(db, 8)
    settings = await ai_settings.load(db)
    provider = AIProvider(settings)
    data["provider"] = {
        "configured": provider.configured,
        "label": ai_settings.provider_label(settings),
        "base_url": provider.base_url,
        "key_configured": ai_settings.key_configured(),
    }
    data["features"] = ai_settings.features(settings)
    data["permissions"] = ai_settings.permissions(settings)
    return web.json_response(data)


# --------------------------------------------------------------------------- settings
async def handle_settings(request: web.Request) -> web.Response:
    db = request.app["db"]
    settings = await ai_settings.load(db)
    response = ai_settings.public_view(settings)
    response["escalation_modes"] = list(ai_settings.ESCALATION_MODES)
    return web.json_response({"settings": response})


async def handle_save_settings(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return web.json_response({"error": "Too many AI changes in a minute. Wait a moment."}, status=429)
    db = request.app["db"]
    payload = await _body(request)
    update = {key: payload[key] for key in SETTING_INPUTS if key in payload}
    if not update:
        return web.json_response({"error": "Nothing to save."}, status=400)
    if "escalation_behavior" in update and update["escalation_behavior"] not in ai_settings.ESCALATION_MODES:
        return web.json_response({"error": "Unknown escalation behavior."}, status=400)
    if "base_url" in update:
        base = str(update["base_url"] or "").strip().rstrip("/")
        if base and not base.startswith(("https://", "http://")):
            return web.json_response({"error": "The base URL must start with https://"}, status=400)
        update["base_url"] = base or ai_settings.DEFAULTS["base_url"]
    if "confidence_threshold" in update:
        update["confidence_threshold"] = min(1.0, max(0.0, ai_settings.as_float(update["confidence_threshold"])))
    if "partnership_role_ids" in update:
        update["partnership_role_ids"] = ai_settings.as_id_list(update["partnership_role_ids"])
    settings = await ai_settings.save(db, update)
    await _audit(request, "settings.save", ", ".join(sorted(update)))
    return web.json_response({"settings": ai_settings.public_view(settings), "message": "AI settings saved."})


async def handle_test_provider(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return web.json_response({"error": "Too many AI changes in a minute. Wait a moment."}, status=429)
    db = request.app["db"]
    settings = await ai_settings.load(db)
    result = await AIProvider(settings).test()
    await _audit(
        request,
        "provider.test",
        f"{'ok' if result.get('ok') else 'failed'}: {result.get('error') or result.get('latency_ms')}",
    )
    return web.json_response({"result": result})


# --------------------------------------------------------------------------- knowledge
async def handle_knowledge(request: web.Request) -> web.Response:
    db = request.app["db"]
    guild_id = await _guild_id(request)
    if guild_id is None:
        return web.json_response({"entries": [], "sources": [], "guild_ready": False})
    entries = await store.list_knowledge(
        db, guild_id, status=request.query.get("status"), query=(request.query.get("q") or "").strip() or None
    )
    sources = await store.list_sources(db, guild_id)
    return web.json_response({"entries": entries, "sources": sources, "guild_ready": True})


async def handle_add_knowledge(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return web.json_response({"error": "Too many AI changes in a minute. Wait a moment."}, status=429)
    db = request.app["db"]
    guild_id = await _guild_id(request)
    if guild_id is None:
        return web.json_response({"error": "Connect a Discord server first."}, status=400)
    payload = await _body(request)
    title = str(payload.get("title") or "").strip()
    body = str(payload.get("body") or "").strip()
    if len(title) < 3 or len(body) < 10:
        return web.json_response({"error": "A knowledge entry needs a title and body text."}, status=400)
    entry_id = await store.add_knowledge(
        db,
        guild_id,
        title=title,
        body=body,
        tags=str(payload.get("tags") or ""),
        source_type="manual",
        source_ref="dashboard",
        status="approved" if ai_settings.as_bool(payload.get("approved"), default=True) else "pending",
        confidence=1.0,
        created_by="dashboard",
    )
    await _audit(request, "knowledge.add", f"entry {entry_id}: {title}", guild_id=guild_id)
    return web.json_response({"id": entry_id, "message": "Knowledge entry saved."})


async def handle_update_knowledge(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return web.json_response({"error": "Too many AI changes in a minute. Wait a moment."}, status=429)
    db = request.app["db"]
    entry_id = _int_id(request.match_info.get("entry_id"), "entry id")
    payload = await _body(request)
    values: dict[str, Any] = {}
    for key in ("title", "body", "tags"):
        if key in payload:
            values[key] = str(payload[key] or "").strip()
    if "status" in payload:
        if payload["status"] not in ("pending", "approved", "rejected"):
            return web.json_response({"error": "Unknown status."}, status=400)
        values["status"] = payload["status"]
        values["reviewed_by"] = "dashboard"
    if not values:
        return web.json_response({"error": "Nothing to update."}, status=400)
    if not await store.update_knowledge(db, entry_id, values):
        return web.json_response({"error": "That knowledge entry no longer exists."}, status=404)
    await _audit(request, "knowledge.update", f"entry {entry_id}: {', '.join(sorted(values))}")
    return web.json_response({"message": "Knowledge entry updated."})


async def handle_delete_knowledge(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return web.json_response({"error": "Too many AI changes in a minute. Wait a moment."}, status=429)
    db = request.app["db"]
    entry_id = _int_id(request.match_info.get("entry_id"), "entry id")
    if not await store.delete_knowledge(db, entry_id):
        return web.json_response({"error": "That knowledge entry no longer exists."}, status=404)
    await _audit(request, "knowledge.delete", f"entry {entry_id}")
    return web.json_response({"message": "Knowledge entry removed."})


async def handle_add_source(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return web.json_response({"error": "Too many AI changes in a minute. Wait a moment."}, status=429)
    db = request.app["db"]
    guild_id = await _guild_id(request)
    if guild_id is None:
        return web.json_response({"error": "Connect a Discord server first."}, status=400)
    payload = await _body(request)
    channel_id = _int_id(payload.get("channel_id"), "channel id")
    source_id = await store.add_source(db, guild_id, channel_id, str(payload.get("label") or ""))
    job_id = await jobs.enqueue(
        db,
        "ingest_knowledge",
        {"channel_id": channel_id, "source_id": source_id, "limit": ai_settings.as_int(payload.get("limit"), default=250)},
        idempotency_key=f"ingest:{guild_id}:{channel_id}:{ai_settings.utc_now()[:13]}",
    )
    await _audit(request, "knowledge.source.add", f"channel {channel_id}", guild_id=guild_id)
    return web.json_response(
        {"id": source_id, "job": job_id, "message": "Channel added. Ingestion is queued for the bot."}
    )


async def handle_scan_source(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return web.json_response({"error": "Too many AI changes in a minute. Wait a moment."}, status=429)
    db = request.app["db"]
    guild_id = await _guild_id(request)
    source_id = _int_id(request.match_info.get("source_id"), "source id")
    sources = await store.list_sources(db, guild_id or 0)
    source = next((item for item in sources if int(item["id"]) == source_id), None)
    if not source:
        return web.json_response({"error": "That knowledge source no longer exists."}, status=404)
    job_id = await jobs.enqueue(
        db,
        "ingest_knowledge",
        {"channel_id": source["channel_id"], "source_id": source_id, "limit": 250},
        idempotency_key=f"ingest:{guild_id}:{source['channel_id']}:{ai_settings.utc_now()[:13]}",
    )
    await _audit(request, "knowledge.source.scan", f"source {source_id}", guild_id=guild_id)
    return web.json_response({"job": job_id, "message": "Scan queued. New entries arrive for review."})


async def handle_delete_source(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return web.json_response({"error": "Too many AI changes in a minute. Wait a moment."}, status=429)
    db = request.app["db"]
    guild_id = await _guild_id(request)
    source_id = _int_id(request.match_info.get("source_id"), "source id")
    if not await store.delete_source(db, guild_id or 0, source_id):
        return web.json_response({"error": "That knowledge source no longer exists."}, status=404)
    await _audit(request, "knowledge.source.delete", f"source {source_id}", guild_id=guild_id)
    return web.json_response({"message": "Knowledge source removed."})


# --------------------------------------------------------------------------- routing
async def handle_routes(request: web.Request) -> web.Response:
    db = request.app["db"]
    guild_id = await _guild_id(request)
    if guild_id is None:
        return web.json_response({"routes": [], "guild_ready": False})
    return web.json_response({"routes": await store.list_routes(db, guild_id), "guild_ready": True})


async def handle_create_route(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return web.json_response({"error": "Too many AI changes in a minute. Wait a moment."}, status=429)
    db = request.app["db"]
    guild_id = await _guild_id(request)
    if guild_id is None:
        return web.json_response({"error": "Connect a Discord server first."}, status=400)
    payload = await _body(request)
    if len(str(payload.get("name") or "").strip()) < 3:
        return web.json_response({"error": "Give the route a name."}, status=400)
    if not ai_settings.as_id_list(payload.get("role_ids")):
        return web.json_response({"error": "Pick at least one staff role to notify."}, status=400)
    route_id = await store.create_route(db, guild_id, payload)
    await _audit(request, "route.create", f"route {route_id}: {payload.get('name')}", guild_id=guild_id)
    return web.json_response({"id": route_id, "message": "Routing rule saved."})


async def handle_update_route(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return web.json_response({"error": "Too many AI changes in a minute. Wait a moment."}, status=429)
    db = request.app["db"]
    route_id = _int_id(request.match_info.get("route_id"), "route id")
    payload = await _body(request)
    if not await store.update_route(db, route_id, payload):
        return web.json_response({"error": "That routing rule no longer exists."}, status=404)
    await _audit(request, "route.update", f"route {route_id}")
    return web.json_response({"message": "Routing rule updated."})


async def handle_delete_route(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return web.json_response({"error": "Too many AI changes in a minute. Wait a moment."}, status=429)
    db = request.app["db"]
    route_id = _int_id(request.match_info.get("route_id"), "route id")
    if not await store.delete_route(db, route_id):
        return web.json_response({"error": "That routing rule no longer exists."}, status=404)
    await _audit(request, "route.delete", f"route {route_id}")
    return web.json_response({"message": "Routing rule removed."})


async def handle_route_preview(request: web.Request) -> web.Response:
    """Show which rule would handle a department/category/severity combination."""

    db = request.app["db"]
    guild_id = await _guild_id(request)
    routes = await store.list_routes(db, guild_id or 0)
    route, explanation = engine.choose_route(
        routes,
        department=(request.query.get("department") or "").strip(),
        report_category=(request.query.get("category") or "general").strip(),
        severity=(request.query.get("severity") or "normal").strip(),
    )
    return web.json_response({"route": route, "explanation": explanation})


# --------------------------------------------------------------------------- cases
async def handle_cases(request: web.Request) -> web.Response:
    db = request.app["db"]
    guild_id = await _guild_id(request)
    offset, limit = _page(request)
    data = await store.list_cases(
        db,
        guild_id=guild_id,
        kind=request.query.get("kind"),
        status=request.query.get("status"),
        query=(request.query.get("q") or "").strip() or None,
        limit=limit,
        offset=offset,
        **_date_range(request),
    )
    return web.json_response({**data, "page": offset // limit + 1, "page_size": limit})


async def handle_case_detail(request: web.Request) -> web.Response:
    db = request.app["db"]
    case_id = _int_id(request.match_info.get("case_id"), "case id")
    case = await store.get_case(db, case_id)
    if not case:
        return web.json_response({"error": "That case no longer exists."}, status=404)
    events = await store.case_events(db, case_id)
    activity = await store.list_activity(db, query=request.query.get("q"), limit=50)
    related = [row for row in activity["activity"] if row.get("case_id") == case_id]
    partnership = await store.find_partnership_by_case(db, case_id)
    return web.json_response({"case": case, "events": events, "activity": related, "partnership": partnership})


async def handle_review_case(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return web.json_response({"error": "Too many AI changes in a minute. Wait a moment."}, status=429)
    db = request.app["db"]
    case_id = _int_id(request.match_info.get("case_id"), "case id")
    payload = await _body(request)
    decision = str(payload.get("decision") or "").lower()
    if decision not in ("approved", "rejected", "pending"):
        return web.json_response({"error": "Decision must be approved or rejected."}, status=400)
    note = str(payload.get("note") or "")[:1000]
    case = await store.get_case(db, case_id)
    if not case:
        return web.json_response({"error": "That case no longer exists."}, status=404)
    await store.update_case(
        db,
        case_id,
        human_review=decision,
        reviewed_by="dashboard",
        review_note=note,
        status="reviewed" if decision != "pending" else case["status"],
    )
    await store.add_case_event(db, case_id, "dashboard", f"review.{decision}", note, status="completed")
    await store.log_activity(
        db,
        "case.review",
        guild_id=case["guild_id"],
        case_id=case_id,
        actor="dashboard",
        decision=decision,
        review_status=decision,
        detail=note or f"Case #{case_id} reviewed by a dashboard administrator.",
    )
    return web.json_response({"message": f"Case #{case_id} marked {decision}."})


# --------------------------------------------------------------------------- partnerships
async def handle_partnerships(request: web.Request) -> web.Response:
    db = request.app["db"]
    guild_id = await _guild_id(request)
    if guild_id is None:
        return web.json_response({"partnerships": [], "guild_ready": False})
    rows = await store.list_partnerships(db, guild_id)
    return web.json_response({"partnerships": rows, "guild_ready": True})


async def handle_partnership_detail(request: web.Request) -> web.Response:
    db = request.app["db"]
    partnership_id = _int_id(request.match_info.get("partnership_id"), "application id")
    record = await store.get_partnership(db, partnership_id)
    if not record:
        return web.json_response({"error": "That partnership application no longer exists."}, status=404)
    case = await store.get_case(db, record.get("case_id") or 0)
    events = await store.case_events(db, record.get("case_id") or 0) if case else []
    return web.json_response({"partnership": record, "case": case, "events": events})


async def handle_verify_partnership(request: web.Request) -> web.Response:
    # Old cached clients must not mistake a dashboard click for passing an
    # external server's verification gate. Confirmation belongs in the ticket.
    return web.json_response(
        {"error": "A server administrator must grant the bot access in the applicant server. Then reply done in the Discord ticket; analysis starts automatically."},
        status=409,
    )


async def handle_analyse_partnership(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return web.json_response({"error": "Too many AI changes in a minute. Wait a moment."}, status=429)
    db = request.app["db"]
    partnership_id = _int_id(request.match_info.get("partnership_id"), "application id")
    record = await store.get_partnership(db, partnership_id)
    if not record:
        return web.json_response({"error": "That partnership application no longer exists."}, status=404)
    if not record.get("human_verified"):
        return web.json_response({"error": "Grant the bot access in the applicant server, then reply done in the Discord ticket first."}, status=400)
    job_id = await jobs.enqueue(
        db,
        "partnership_analyse",
        {"partnership_id": partnership_id, "case_id": record.get("case_id")},
        idempotency_key=f"partnership:{partnership_id}:analyse:{ai_settings.utc_now()[:16]}",
    )
    await _audit(request, "partnership.analyse", f"application {partnership_id}", guild_id=record["guild_id"])
    return web.json_response({"job": job_id, "message": "Analysis queued."})


async def handle_partnership_decision(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return web.json_response({"error": "Too many AI changes in a minute. Wait a moment."}, status=429)
    db = request.app["db"]
    partnership_id = _int_id(request.match_info.get("partnership_id"), "application id")
    payload = await _body(request)
    action = str(payload.get("action") or "").lower()
    note = str(payload.get("note") or "").strip()[:2000]
    record = await store.get_partnership(db, partnership_id)
    if not record:
        return web.json_response({"error": "That partnership application no longer exists."}, status=404)

    if action == "approve":
        if record.get("eligibility") != "eligible":
            return web.json_response(
                {"error": "The application does not meet the configured requirements, so it cannot be approved."},
                status=400,
            )
        await store.update_partnership(db, partnership_id, publish_status="approved", verification_note=note or record.get("verification_note"))
        message = "Publication approved. Publish when you are ready."
    elif action == "publish":
        allowed, reason = engine.can_publish(record)
        if not allowed:
            return web.json_response({"error": reason}, status=400)
        job_id = await jobs.enqueue(
            db,
            "publish_advertisement",
            {"partnership_id": partnership_id, "case_id": record.get("case_id")},
            idempotency_key=f"partnership:{partnership_id}:publish",
            retry_failed=True,
        )
        if job_id is None:
            return web.json_response({"error": "Publishing is already queued for this application."}, status=409)
        await _audit(request, "partnership.publish", f"application {partnership_id}", guild_id=record["guild_id"])
        return web.json_response({"job": job_id, "message": "Publishing queued. The bot posts it to the partnership channel."})
    elif action == "reject":
        await store.update_partnership(db, partnership_id, publish_status="rejected", verification_note=note)
        message = "Application rejected. Nothing will be published."
    elif action == "pause":
        await store.update_partnership(db, partnership_id, publish_status="paused")
        message = "Publication paused."
    else:
        return web.json_response({"error": "Unknown partnership action."}, status=400)

    if record.get("case_id"):
        await store.add_case_event(db, record["case_id"], "dashboard", f"partnership.{action}", note)
    await _audit(request, "partnership." + action, f"application {partnership_id}: {note}", guild_id=record["guild_id"])
    return web.json_response({"message": message})


# --------------------------------------------------------------------------- activity & analytics
async def handle_activity(request: web.Request) -> web.Response:
    db = request.app["db"]
    guild_id = await _guild_id(request)
    offset, limit = _page(request)
    data = await store.list_activity(
        db,
        guild_id=guild_id,
        action=(request.query.get("action") or "").strip() or None,
        status=(request.query.get("status") or "").strip() or None,
        query=(request.query.get("q") or "").strip() or None,
        limit=limit,
        offset=offset,
        **_date_range(request),
    )
    return web.json_response({**data, "page": offset // limit + 1, "page_size": limit})


async def handle_analytics(request: web.Request) -> web.Response:
    db = request.app["db"]
    guild_id = await _guild_id(request)
    stats = await store.analytics(db, guild_id)
    queue = await jobs.stats(db)
    return web.json_response({"analytics": stats, "queue": queue})


# --------------------------------------------------------------------------- permissions
async def handle_permissions(request: web.Request) -> web.Response:
    db = request.app["db"]
    settings = await ai_settings.load(db)
    payload = {"permissions": ai_settings.permissions(settings), "roles": []}
    guild_id = await _guild_id(request)
    if guild_id:
        try:
            payload["roles"] = await request.app["discord"].roles(guild_id)
        except discord_api.DiscordAPIError as error:
            payload["roles_error"] = str(error)
    return web.json_response(payload)


async def handle_save_permissions(request: web.Request) -> web.Response:
    if not _write_allowed(request):
        return web.json_response({"error": "Too many AI changes in a minute. Wait a moment."}, status=429)
    db = request.app["db"]
    payload = await _body(request)
    permissions = {
        "control_role_ids": ai_settings.as_id_list(payload.get("control_role_ids")),
        "sensitive_role_ids": ai_settings.as_id_list(payload.get("sensitive_role_ids")),
    }
    settings = await ai_settings.save(db, {"permissions": permissions})
    await _audit(
        request,
        "permissions.save",
        f"control={permissions['control_role_ids']} sensitive={permissions['sensitive_role_ids']}",
    )
    return web.json_response(
        {
            "permissions": ai_settings.permissions(settings),
            "message": "AI permissions saved. The bot enforces them on its AI commands.",
        }
    )


ROUTES = [
    web.get("/api/ai/overview", handle_overview),
    web.get("/api/ai/settings", handle_settings),
    web.put("/api/ai/settings", handle_save_settings),
    web.post("/api/ai/settings/test", handle_test_provider),
    web.get("/api/ai/knowledge", handle_knowledge),
    web.post("/api/ai/knowledge", handle_add_knowledge),
    web.patch("/api/ai/knowledge/{entry_id}", handle_update_knowledge),
    web.delete("/api/ai/knowledge/{entry_id}", handle_delete_knowledge),
    web.get("/api/ai/sources", handle_knowledge),
    web.post("/api/ai/sources", handle_add_source),
    web.post("/api/ai/sources/{source_id}/scan", handle_scan_source),
    web.delete("/api/ai/sources/{source_id}", handle_delete_source),
    web.get("/api/ai/routes", handle_routes),
    web.post("/api/ai/routes", handle_create_route),
    web.patch("/api/ai/routes/{route_id}", handle_update_route),
    web.delete("/api/ai/routes/{route_id}", handle_delete_route),
    web.get("/api/ai/routes/preview", handle_route_preview),
    web.get("/api/ai/cases", handle_cases),
    web.get("/api/ai/cases/{case_id}", handle_case_detail),
    web.post("/api/ai/cases/{case_id}/review", handle_review_case),
    web.get("/api/ai/partnerships", handle_partnerships),
    web.get("/api/ai/partnerships/{partnership_id}", handle_partnership_detail),
    web.post("/api/ai/partnerships/{partnership_id}/verify", handle_verify_partnership),
    web.post("/api/ai/partnerships/{partnership_id}/analyse", handle_analyse_partnership),
    web.post("/api/ai/partnerships/{partnership_id}/decision", handle_partnership_decision),
    web.get("/api/ai/activity", handle_activity),
    web.get("/api/ai/analytics", handle_analytics),
    web.get("/api/ai/permissions", handle_permissions),
    web.put("/api/ai/permissions", handle_save_permissions),
]


def register(app: web.Application) -> None:
    app.add_routes(ROUTES)
    app["ai_writes"] = {}
