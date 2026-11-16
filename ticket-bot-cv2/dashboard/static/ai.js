/* AI Control System - dashboard module. */
(() => {
  "use strict";

  const ai = {
    section: "overview",
    data: {},
    channels: [],
    roles: [],
    guildLoaded: false,
    guildError: "",
    filters: {
      knowledge: "all",
      knowledgeQuery: "",
      activity: { q: "", action: "", status: "", from: "", to: "", page: 1 },
      cases: { kind: "all", status: "all", q: "", from: "", to: "", page: 1 },
    },
  };

  const SECTIONS = [
    ["overview", "AI Overview"],
    ["configuration", "AI Configuration"],
    ["knowledge", "Knowledge Base"],
    ["routing", "Staff Routing"],
    ["partnership", "Partnership Automation"],
    ["activity", "AI Activity Logs"],
    ["analytics", "Analytics"],
    ["permissions", "AI Permissions"],
  ];

  const api = (path, options) => window.Dashboard.api(path, options);
  const esc = (value) => window.Dashboard.esc(value);
  const icon = (name, cls) => window.Dashboard.icon(name, cls);
  const toast = (message, tone) => window.Dashboard.toast(message, tone);
  const fmtDate = (value) => window.Dashboard.fmtDate(value);
  const fmtRelative = (value) => window.Dashboard.fmtRelative(value);

  const pad = (value) => String(value ?? "").padStart(2, "0");
  const num = (value) => Number(value || 0).toLocaleString();
  const pill = (tone, label) => `<span class="pill ${tone}"><i></i>${esc(label)}</span>`;
  const statusTone = (status) => {
    if (["approved", "eligible", "published", "complete", "joined", "granted", "answered", "verified", "granted"].includes(status)) return "good";
    if (["pending", "manual_review", "queued", "running", "awaiting_verification", "waiting", "paused", "degraded"].includes(status)) return "waiting";
    if (["rejected", "failed", "not_eligible", "invalid", "blocked", "missing"].includes(status)) return "bad";
    return "neutral";
  };
  const options = (items, current, { valueKey = "id", labelKey = "name", blank = "" } = {}) => {
    const rows = items.map((item) => {
      const value = item[valueKey];
      const label = item[labelKey];
      return `<option value="${esc(value)}" ${String(current) === String(value) ? "selected" : ""}>${esc(label)}</option>`;
    });
    return (blank ? `<option value="">${esc(blank)}</option>` : "") + rows.join("");
  };
  const channelOptions = (current, blank = "Not set") => options(ai.channels, current, { blank });
  const roleOptions = (current, blank = "Not set") => options(ai.roles, current, { blank });
  const roleMulti = (name, selected, size = 5) => {
    const chosen = new Set((selected || []).map(String));
    return `<select name="${name}" multiple size="${size}" class="multi">
      ${ai.roles.map((role) => `<option value="${esc(role.id)}" ${chosen.has(String(role.id)) ? "selected" : ""}>${esc(role.name)}</option>`).join("")}
    </select>`;
  };
  const roleNames = (ids) => {
    if (!ids || !ids.length) return "—";
    return ids.map((id) => {
      const role = ai.roles.find((item) => String(item.id) === String(id));
      return role ? role.name : `ID ${id}`;
    }).join(", ");
  };
  const multiValues = (select) => Array.from(select.selectedOptions || []).map((option) => option.value);
  const check = (name, checked, label) =>
    `<label class="switch"><input type="checkbox" name="${name}" ${checked ? "checked" : ""}> ${esc(label)}</label>`;

  function loading() {
    return `<div class="stack">${Array.from({ length: 4 }, () => '<div class="skeleton" style="height:52px"></div>').join("")}</div>`;
  }

  function tabs() {
    return `<nav class="ai-tabs">${SECTIONS.map(([key, label]) =>
      `<button class="${key === ai.section ? "active" : ""}" data-ai-section="${key}">${esc(label)}</button>`).join("")}</nav>`;
  }

  /* ------------------------------------------------------------------ overview */
  function overviewBody() {
    const overview = ai.data.overview;
    if (!overview) return loading();
    const stats = overview.analytics || {};
    const review = stats.review || {};
    const status = overview.status || {};
    const provider = overview.provider || {};
    const queue = stats.jobs || {};
    const avg = stats.avg_response_ms ? `${(stats.avg_response_ms / 1000).toFixed(2)}s` : "—";
    const enabled = status.enabled;
    const activity = overview.recent_activity || [];

    return `
      <section class="grid stats">
        <dl class="stat"><dt>AI status</dt><dd>${enabled ? "Live" : "Off"}</dd><small>${enabled ? "Partnership automation active" : "Paused by an administrator"}</small></dl>
        <dl class="stat"><dt>Requests processed</dt><dd>${num(stats.total_requests)}</dd><small>Support tickets and partnerships</small></dl>
        <dl class="stat"><dt>Auto-resolved</dt><dd>${num(stats.auto_resolved)}</dd><small>Answered without staff</small></dl>
        <dl class="stat"><dt>Forwarded to staff</dt><dd>${num(stats.forwarded)}</dd><small>Human escalation</small></dl>
        <dl class="stat"><dt>Partnerships</dt><dd>${num(stats.partnerships_processed)}</dd><small>Automated analyses</small></dl>
        <dl class="stat"><dt>Avg response</dt><dd>${avg}</dd><small>Model turnaround</small></dl>
        <dl class="stat"><dt>Review accuracy</dt><dd>${review.accuracy === null || review.accuracy === undefined ? "—" : `${review.accuracy}%`}</dd><small>${num(review.approved)} approved · ${num(review.rejected)} rejected</small></dl>
      </section>

      <section class="grid cols-2" style="margin-top:14px">
        <div class="card">
          <div class="card-head"><div><h2>Model connection</h2><p>Server-side provider used for every decision</p></div>${icon("staff")}</div>
          <dl class="kv">
            <dt>Operational state</dt><dd>${enabled ? pill("good", "Enabled") : pill("neutral", "Disabled")}</dd>
            <dt>Connection</dt><dd>${provider.key_configured
              ? pill("good", "API key present")
              : pill("waiting", "No API key - human escalation")}</dd>
            <dt>Provider</dt><dd>${esc(provider.label || status.provider || "—")}</dd>
            <dt>Endpoint</dt><dd class="mono">${esc(provider.base_url || status.base_url || "—")}</dd>
            <dt>Confidence threshold</dt><dd>${esc((overview.settings || {}).confidence_threshold ?? "—")}</dd>
            <dt>Partnerships</dt><dd>Automatic after server access is confirmed in the ticket</dd>
            <dt>Queue</dt><dd>${num(queue.queued)} queued · ${num(queue.failed)} failed</dd>
            <dt>Knowledge</dt><dd>${num((stats.knowledge || {}).approved)} approved · ${num((stats.knowledge || {}).pending)} awaiting review</dd>
          </dl>
        </div>
        <div class="card">
          <div class="card-head"><div><h2>Recent AI activity</h2><p>Every decision is recorded</p></div>${icon("list")}</div>
          ${activity.length ? `<div class="table-wrap"><table><thead><tr><th>When</th><th>Action</th><th>Decision</th><th>Confidence</th></tr></thead><tbody>
            ${activity.map((row) => `<tr>
              <td class="num">${fmtRelative(row.created_at)}</td>
              <td class="mono">${esc(row.action)}</td>
              <td>${esc(row.decision || "—")}</td>
              <td class="num">${row.confidence ? Math.round(row.confidence * 100) + "%" : "—"}</td>
            </tr>`).join("")}
          </tbody></table></div>` : '<div class="empty">No AI activity yet. Enable the system and it will start logging decisions.</div>'}
        </div>
      </section>

      <section class="card" style="margin-top:14px">
        <div class="card-head"><div><h2>Enabled features</h2><p>Configured under AI Configuration</p></div>${icon("settings")}</div>
        <div class="checks">
          ${Object.entries(overview.features || {}).map(([key, value]) =>
            `<span class="pill ${value ? "good" : "neutral"}"><i></i>${esc(key.replace(/_/g, " "))}</span>`).join("")}
        </div>
      </section>`;
  }

  /* ------------------------------------------------------------------ configuration */
  function configurationBody() {
    const overview = ai.data.overview;
    if (!overview) return loading();
    const s = overview.settings || {};
    const features = s.features || {};
    const escalation = overview.escalation_modes || ["notify_staff", "assign_ticket"];
    return `
      <form id="ai-config-form" class="stack">
        <div class="card">
          <div class="card-head"><div><h2>Provider and model</h2><p>The API key lives in Secrets, never in this page</p></div>${icon("settings")}</div>
          <div class="grid cols-2">
            <label class="field"><span>Provider</span><input type="text" name="provider" value="${esc(s.provider || "groq")}" placeholder="groq"></label>
            <label class="field"><span>Model</span><input type="text" name="model" value="${esc(s.model || "openai/gpt-oss-120b")}" placeholder="openai/gpt-oss-120b"></label>
            <label class="field"><span>Base URL</span><input type="text" name="base_url" value="${esc(s.base_url || "")}" placeholder="https://api.groq.com/openai/v1"></label>
            <label class="field"><span>Temperature</span><input type="number" step="0.05" min="0" max="2" name="temperature" value="${esc(s.temperature ?? 0.2)}"></label>
            <label class="field"><span>Max output tokens</span><input type="number" min="64" max="4000" name="max_output_tokens" value="${esc(s.max_output_tokens ?? 700)}"></label>
            <label class="field"><span>Max processing seconds</span><input type="number" min="10" max="300" name="max_processing_seconds" value="${esc(s.max_processing_seconds ?? 90)}"></label>
          </div>
          <div class="row" style="margin-top:12px">
            ${overview.provider?.key_configured
              ? pill("good", "AI_API_KEY configured")
              : pill("waiting", "AI_API_KEY missing - answers escalate to staff")}
            <button type="button" class="btn sm" id="ai-test-provider">Test connection</button>
            <span class="help" id="ai-test-result"></span>
          </div>
        </div>

        <div class="card">
          <div class="card-head"><div><h2>Instructions and behaviour</h2><p>Applied to every model call</p></div>${icon("info")}</div>
          <div class="grid cols-2">
            <label class="field"><span>System instructions</span><textarea name="system_instructions" rows="6">${esc(s.system_instructions || "")}</textarea></label>
            <label class="field"><span>Response personality</span><textarea name="personality" rows="6">${esc(s.personality || "")}</textarea></label>
            <label class="field"><span>Confidence threshold (0-1)</span><input type="number" step="0.01" min="0" max="1" name="confidence_threshold" value="${esc(s.confidence_threshold ?? 0.62)}"></label>
            <label class="field"><span>Escalation behaviour</span><select name="escalation_behavior">${options(escalation.map((value) => ({ id: value, name: value.replace(/_/g, " ") })), s.escalation_behavior)}</select></label>
            <label class="field"><span>Data retention (days)</span><input type="number" min="7" max="3650" name="retention_days" value="${esc(s.retention_days ?? 180)}"></label>
            <div class="field"><span>Human oversight</span>
              <p class="help">Partnership access is confirmed by replying done in the Discord ticket, not here.</p>
              ${check("enabled", s.enabled, "AI Control System enabled")}
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-head"><div><h2>Features</h2><p>Turn individual capabilities on or off</p></div>${icon("check")}</div>
          <div class="grid cols-2">
            ${Object.keys(features).map((key) => check(`feature_${key}`, features[key], key.replace(/_/g, " "))).join("")}
          </div>
        </div>

        <div class="row" style="justify-content:flex-end"><button type="submit" class="btn primary">Save AI configuration</button></div>
      </form>`;
  }

  /* ------------------------------------------------------------------ knowledge */
  function knowledgeBody() {
    const data = ai.data.knowledge;
    if (!data) return loading();
    if (!data.guild_ready) return `<div class="card"><div class="empty">Connect a Discord server under Configuration first.</div></div>`;
    const entries = data.entries || [];
    const sources = data.sources || [];
    return `
      <section class="grid cols-2">
        <div class="card">
          <div class="card-head"><div><h2>Knowledge sources</h2><p>Channels the AI may read for official information</p></div>${icon("folder")}</div>
          <div class="row" style="align-items:flex-end">
            <label class="field" style="flex:1"><span>Discord channel</span><select name="source_channel">${channelOptions("", "Pick a channel")}</select></label>
            <button type="button" class="btn primary sm" id="ai-add-source">Add &amp; scan</button>
          </div>
          ${sources.length ? `<div class="table-wrap" style="margin-top:12px"><table><thead><tr><th>Channel</th><th>Last scan</th><th>Found</th><th></th></tr></thead><tbody>
            ${sources.map((source) => `<tr>
              <td class="mono">&lt;#${esc(source.channel_id)}&gt;</td>
              <td class="num">${source.last_scanned_at ? fmtRelative(source.last_scanned_at) : "never"}</td>
              <td class="num">${num(source.entries_found)}</td>
              <td class="row">
                <button class="btn sm" data-ai-scan-source="${source.id}">Scan now</button>
                <button class="btn sm danger" data-ai-delete-source="${source.id}">Remove</button>
              </td>
            </tr>`).join("")}
          </tbody></table></div>` : '<div class="empty" style="margin-top:12px">No channel selected yet. Ingestion extracts policy text and files it for your review - chatter is ignored.</div>'}
        </div>

        <div class="card">
          <div class="card-head"><div><h2>Add knowledge manually</h2><p>Approved entries are used for answers immediately</p></div>${icon("document")}</div>
          <form id="ai-knowledge-form" class="stack" style="gap:10px">
            <label class="field"><span>Title</span><input type="text" name="title" placeholder="How to apply for staff" required></label>
            <label class="field"><span>Content</span><textarea name="body" rows="4" placeholder="State the policy or procedure exactly." required></textarea></label>
            <label class="field"><span>Tags</span><input type="text" name="tags" placeholder="staff, application, requirements"></label>
            ${check("approved", true, "Approve immediately")}
            <button type="submit" class="btn primary">Save entry</button>
          </form>
        </div>
      </section>

      <section class="card" style="margin-top:14px">
        <div class="card-head"><div><h2>Knowledge entries</h2><p>${num(entries.length)} entr${entries.length === 1 ? "y" : "ies"} shown</p></div>${icon("list")}</div>
        <div class="row" style="margin-bottom:12px">
          ${["all", "pending", "approved", "rejected"].map((status) =>
            `<button class="btn sm ${ai.filters.knowledge === status ? "primary" : ""}" data-ai-knowledge-filter="${status}">${status}</button>`).join("")}
          <input type="search" id="ai-knowledge-search" placeholder="Search knowledge…" value="${esc(ai.filters.knowledgeQuery)}" style="max-width:240px">
        </div>
        ${entries.length ? `<div class="table-wrap"><table><thead><tr><th>Title</th><th>Status</th><th>Source</th><th>Updated</th><th></th></tr></thead><tbody>
          ${entries.map((entry) => `<tr>
            <td><strong>${esc(entry.title)}</strong><div class="help">${esc((entry.body || "").slice(0, 150))}</div></td>
            <td>${pill(statusTone(entry.status), entry.status)}</td>
            <td class="help">${esc(entry.source_type)}${entry.source_ref ? `<div class="mono">${esc(entry.source_ref)}</div>` : ""}</td>
            <td class="num">${fmtRelative(entry.updated_at)}</td>
            <td class="row">
              ${entry.status !== "approved" ? `<button class="btn sm" data-ai-knowledge-approve="${entry.id}">Approve</button>` : `<button class="btn sm" data-ai-knowledge-reject="${entry.id}">Unpublish</button>`}
              <button class="btn sm danger" data-ai-knowledge-delete="${entry.id}">Delete</button>
            </td>
          </tr>`).join("")}
        </tbody></table></div>` : '<div class="empty">No knowledge entries yet. Add one, or scan a channel to extract them.</div>'}
      </section>`;
  }

  /* ------------------------------------------------------------------ routing */
  function routingBody() {
    const data = ai.data.routes;
    if (!data) return loading();
    if (!data.guild_ready) return `<div class="card"><div class="empty">Connect a Discord server under Configuration first.</div></div>`;
    const routes = data.routes || [];
    const categories = ["any", "staff_report", "moderator_complaint", "administrator_complaint", "partnership", "technical", "general"];
    const severities = ["any", "low", "normal", "high", "critical"];
    return `
      <section class="grid cols-2">
        <div class="card">
          <div class="card-head"><div><h2>Add a routing rule</h2><p>Who is notified for each kind of case</p></div>${icon("moderator")}</div>
          <form id="ai-route-form" class="stack" style="gap:10px">
            <label class="field"><span>Rule name</span><input type="text" name="name" placeholder="Staff Report → Internal Affairs" required></label>
            <div class="grid cols-2">
              <label class="field"><span>Department</span><input type="text" name="department" placeholder="Internal Affairs"></label>
              <label class="field"><span>Report category</span><select name="report_category">${options(categories.map((value) => ({ id: value, name: value })), "any")}</select></label>
              <label class="field"><span>Severity</span><select name="severity">${options(severities.map((value) => ({ id: value, name: value })), "any")}</select></label>
              <label class="field"><span>Destination channel</span><select name="destination_channel_id">${channelOptions("", "Not set")}</select></label>
              <label class="field"><span>Escalate after (minutes)</span><input type="number" min="0" max="1440" name="escalate_after_minutes" value="60"></label>
              <label class="field"><span>Priority (lower wins)</span><input type="number" min="1" max="999" name="priority" value="100"></label>
            </div>
            <label class="field"><span>Staff roles notified</span>${roleMulti("role_ids", [])}</label>
            <label class="field"><span>Backup roles</span>${roleMulti("backup_role_ids", [], 3)}</label>
            ${check("enabled", true, "Enabled")}
            <button type="submit" class="btn primary">Save routing rule</button>
          </form>
        </div>

        <div class="card">
          <div class="card-head"><div><h2>Test a route</h2><p>See which rule the AI would pick</p></div>${icon("search")}</div>
          <div class="stack" style="gap:10px">
            <label class="field"><span>Department</span><input type="text" id="preview-department" placeholder="Internal Affairs"></label>
            <label class="field"><span>Category</span><select id="preview-category">${options(categories.map((value) => ({ id: value, name: value })), "staff_report")}</select></label>
            <label class="field"><span>Severity</span><select id="preview-severity">${options(severities.filter((v) => v !== "any").map((value) => ({ id: value, name: value })), "normal")}</select></label>
            <button type="button" class="btn" id="ai-route-preview">Check routing</button>
            <div class="note" id="ai-route-result"><div class="help">Pick a combination to see the explanation the log records.</div></div>
          </div>
        </div>
      </section>

      <section class="card" style="margin-top:14px">
        <div class="card-head"><div><h2>Routing rules</h2><p>${num(routes.length)} configured</p></div>${icon("list")}</div>
        ${routes.length ? `<div class="table-wrap"><table><thead><tr><th>Rule</th><th>Department</th><th>Category</th><th>Severity</th><th>Roles</th><th>Channel</th><th>Priority</th><th></th></tr></thead><tbody>
          ${routes.map((route) => `<tr>
            <td><strong>${esc(route.name)}</strong><div class="help">${route.enabled ? "enabled" : "disabled"}</div></td>
            <td>${esc(route.department || "any")}</td>
            <td class="mono">${esc(route.report_category)}</td>
            <td>${esc(route.severity)}</td>
            <td class="help">${esc(roleNames(route.role_ids))}${(route.backup_role_ids || []).length ? `<div>backup: ${esc(roleNames(route.backup_role_ids))}</div>` : ""}</td>
            <td class="mono">${route.destination_channel_id ? `&lt;#${esc(route.destination_channel_id)}&gt;` : "—"}</td>
            <td class="num">${route.priority}</td>
            <td class="row">
              <button class="btn sm" data-ai-route-toggle="${route.id}" data-current="${route.enabled ? 1 : 0}">${route.enabled ? "Disable" : "Enable"}</button>
              <button class="btn sm danger" data-ai-route-delete="${route.id}">Delete</button>
            </td>
          </tr>`).join("")}
        </tbody></table></div>` : '<div class="empty">No routing rules yet. Without one, escalations fall back to the staff channel.</div>'}
      </section>`;
  }

  /* ------------------------------------------------------------------ partnerships */
  function partnershipBody() {
    const data = ai.data.partnerships;
    const overview = ai.data.overview;
    if (!data || !overview) return loading();
    if (!data.guild_ready) return `<div class="card"><div class="empty">Connect a Discord server under Configuration first.</div></div>`;
    const s = overview.settings || {};
    const rows = data.partnerships || [];
    return `
      <section class="grid cols-2">
        <div class="card">
          <div class="card-head"><div><h2>Partnership settings</h2><p>Channel, staff roles and hard requirements</p></div>${icon("handshake")}</div>
          <form id="ai-partnership-form" class="stack" style="gap:10px">
            <label class="field"><span>Partnership channel</span><select name="partnership_channel_id">${channelOptions(s.partnership_channel_id, "Automatically find #partnerships")}</select></label>
            <label class="field"><span>Our advertisement channel</span><select name="partnership_ad_channel_id">${channelOptions(s.partnership_ad_channel_id, "Same as the partnership channel")}</select></label>
            <p class="help">The channel in this server that holds our own advertisement. Applicants must repost it in their server before anything is published.</p>
            ${check("partnership_automation", s.features?.partnership_automation, "Automatic partnership processing enabled")}
            <label class="field"><span>Partnership staff roles</span>${roleMulti("partnership_role_ids", s.partnership_role_ids || [])}</label>
            <p class="help">Only requirement: <strong>at least 15 members</strong>. Channel names, emojis, duplicate entries, separators, activity and server layout do not affect eligibility.</p>
            <button type="submit" class="btn primary">Save partnership settings</button>
          </form>
        </div>
        <div class="card">
          <div class="card-head"><div><h2>Workflow</h2><p>What happens after a partnership ticket opens</p></div>${icon("info")}</div>
          <ol class="steps">
            <li>AI welcomes the applicant and collects the advertisement and a Discord invite.</li>
            <li>The invite identifies the server. If the bot is not installed, the ticket supplies an installation link for a server administrator.</li>
            <li>An administrator of that server verifies the bot through any access gate and grants channel-reading access.</li>
            <li>The applicant replies <strong>done</strong> in the ticket. The bot checks actual access and automatically sends its review report there.</li>
            <li>A server with at least 15 members is approved automatically. The bot checks public channels for the distinctive wording of <strong>our advertisement</strong>; unrelated ads and messages do not count.</li>
            <li>After the ad is found, it posts the partnership in the home server, announces that it will stay to monitor the required ad, keeps monitoring that ad, and closes the ticket without leaving the applicant server.</li>
          </ol>
          <p class="help" style="margin-top:10px">No application entry or verification click is required here. These records show progress and the audit trail; they do not start the workflow.</p>
        </div>
      </section>

      <section class="card" style="margin-top:14px">
        <div class="card-head"><div><h2>Applications</h2><p>${num(rows.length)} recorded</p></div>${icon("list")}</div>
        ${rows.length ? `<div class="table-wrap"><table><thead><tr><th>Server</th><th>Invite</th><th>Verification</th><th>Analysis</th><th>Eligibility</th><th>Publish</th><th></th></tr></thead><tbody>
          ${rows.map((row) => `<tr>
            <td><strong>${esc(row.server_name || "Unnamed server")}</strong><div class="help">${esc(row.joined_guild_name || "")}</div></td>
            <td>${pill(statusTone(row.invite_status), row.invite_status)}${row.bot_joined ? `<div class="help">bot joined</div>` : ""}</td>
            <td>${pill(row.human_verified ? "good" : "waiting", row.human_verified ? "verified" : "pending")}</td>
            <td>${pill(statusTone(row.analysis_status), row.analysis_status)}</td>
            <td>${pill(statusTone(row.eligibility), row.eligibility.replace("_", " "))}</td>
            <td>${pill(statusTone(row.publish_status), row.publish_status)}</td>
            <td><button class="btn sm" data-ai-partnership="${row.id}">Open</button></td>
          </tr>`).join("")}
        </tbody></table></div>` : '<div class="empty">No partnership applications yet. They arrive from the Partnership Support ticket category.</div>'}
      </section>`;
  }

  /* ------------------------------------------------------------------ activity */
  function activityBody() {
    const data = ai.data.activity;
    if (!data) return loading();
    const filters = ai.filters.activity;
    const rows = data.activity || [];
    const pages = Math.max(1, Math.ceil((data.total || 0) / (data.page_size || 25)));
    return `
      <section class="card">
        <div class="card-head"><div><h2>AI activity log</h2><p>${num(data.total || 0)} events recorded</p></div>${icon("list")}</div>
        <div class="row" style="margin-bottom:12px">
          <input type="search" id="ai-activity-q" placeholder="Search decisions and detail…" value="${esc(filters.q)}" style="max-width:220px">
          <select id="ai-activity-action">${options([
            { id: "", name: "All actions" }, { id: "partnership", name: "Partnerships" }, { id: "knowledge", name: "Knowledge" },
            { id: "staff", name: "Staff notifications" }, { id: "admin", name: "Administrator actions" },
            { id: "job", name: "Background jobs" },
          ], filters.action)}</select>
          <select id="ai-activity-status">${options([
            { id: "", name: "All statuses" }, { id: "completed", name: "Completed" }, { id: "degraded", name: "Degraded" },
            { id: "failed", name: "Failed" }, { id: "pending", name: "Pending" },
          ], filters.status)}</select>
          <input type="date" id="ai-activity-from" value="${esc(filters.from)}">
          <input type="date" id="ai-activity-to" value="${esc(filters.to)}">
          <button class="btn sm primary" id="ai-activity-apply">Apply filters</button>
        </div>
        ${rows.length ? `<div class="table-wrap"><table><thead><tr><th>Event</th><th>When</th><th>Actor</th><th>Action</th><th>Decision</th><th>Confidence</th><th>Destination</th><th>Status</th><th>Review</th></tr></thead><tbody>
          ${rows.map((row) => `<tr>
            <td class="mono">#${row.id}${row.case_id ? ` <button class="btn sm ghost" data-ai-case="${row.case_id}">case</button>` : ""}</td>
            <td class="num">${fmtDate(row.created_at)}</td>
            <td>${esc(row.actor)}${row.user_id ? `<div class="help mono">${esc(row.user_id)}</div>` : ""}</td>
            <td class="mono">${esc(row.action)}</td>
            <td>${esc(row.decision || "—")}<div class="help">${esc((row.detail || "").slice(0, 120))}</div></td>
            <td class="num">${row.confidence ? Math.round(row.confidence * 100) + "%" : "—"}</td>
            <td class="help">${row.destination_channel ? `<span class="mono">#${esc(row.destination_channel)}</span>` : "—"}${row.destination_role ? `<div class="mono">${esc(row.destination_role)}</div>` : ""}</td>
            <td>${pill(statusTone(row.status), row.status)}</td>
            <td>${pill(statusTone(row.review_status), row.review_status)}</td>
          </tr>`).join("")}
        </tbody></table></div>
        <div class="row" style="margin-top:12px;justify-content:flex-end">
          <button class="btn sm" id="ai-activity-prev" ${filters.page <= 1 ? "disabled" : ""}>Previous</button>
          <span class="help">Page ${filters.page} of ${pages}</span>
          <button class="btn sm" id="ai-activity-next" ${filters.page >= pages ? "disabled" : ""}>Next</button>
        </div>` : '<div class="empty">No matching activity.</div>'}
      </section>`;
  }

  /* ------------------------------------------------------------------ analytics */
  function analyticsBody() {
    const data = ai.data.analytics;
    if (!data) return loading();
    const stats = data.analytics || {};
    const review = stats.review || {};
    const queue = data.queue || {};
    const bar = (value, total, tone = "good") => {
      const pct = total ? Math.round((value / total) * 100) : 0;
      return `<div class="bar"><span class="${tone}" style="width:${pct}%"></span></div><span class="help">${pct}%</span>`;
    };
    const total = Math.max(1, stats.total_requests || 0);
    return `
      <section class="grid stats">
        <dl class="stat"><dt>Requests</dt><dd>${num(stats.total_requests)}</dd><small>All AI cases</small></dl>
        <dl class="stat"><dt>Auto-resolved</dt><dd>${num(stats.auto_resolved)}</dd><small>No staff needed</small></dl>
        <dl class="stat"><dt>Escalated</dt><dd>${num(stats.forwarded)}</dd><small>Human intervention</small></dl>
        <dl class="stat"><dt>Partnerships</dt><dd>${num(stats.partnerships_processed)}</dd><small>Completed analyses</small></dl>
        <dl class="stat"><dt>Accuracy</dt><dd>${review.accuracy === null || review.accuracy === undefined ? "—" : `${review.accuracy}%`}</dd><small>Human-reviewed decisions</small></dl>
      </section>

      <section class="grid cols-2" style="margin-top:14px">
        <div class="card">
          <div class="card-head"><div><h2>Resolution mix</h2><p>Share of cases answered automatically</p></div>${icon("chart")}</div>
          <div class="stack" style="gap:10px">
            <div class="spread"><span>Auto-resolved</span><div class="row">${bar(stats.auto_resolved || 0, total)}</div></div>
            <div class="spread"><span>Escalated to staff</span><div class="row">${bar(stats.forwarded || 0, total, "waiting")}</div></div>
          </div>
          <dl class="kv" style="margin-top:14px">
            <dt>Avg response</dt><dd>${stats.avg_response_ms ? `${(stats.avg_response_ms / 1000).toFixed(2)}s` : "—"}</dd>
            <dt>Approved knowledge</dt><dd>${num((stats.knowledge || {}).approved)}</dd>
            <dt>Pending knowledge</dt><dd>${num((stats.knowledge || {}).pending)}</dd>
            <dt>Routing rules</dt><dd>${num(stats.routes)}</dd>
          </dl>
        </div>
        <div class="card">
          <div class="card-head"><div><h2>Human review</h2><p>Staff verdicts on AI decisions</p></div>${icon("check")}</div>
          <dl class="kv">
            <dt>Approved</dt><dd>${num(review.approved)}</dd>
            <dt>Rejected</dt><dd>${num(review.rejected)}</dd>
            <dt>Awaiting review</dt><dd>${num(review.pending)}</dd>
            <dt>Queue</dt><dd>${num(queue.queued)} queued · ${num(queue.running)} running · ${num(queue.failed)} failed</dd>
          </dl>
          <p class="help" style="margin-top:10px">Accuracy is approved ÷ reviewed. It stays empty until staff review cases, because the AI never grades itself.</p>
        </div>
      </section>`;
  }

  /* ------------------------------------------------------------------ permissions */
  function permissionsBody() {
    const data = ai.data.permissions;
    if (!data) return loading();
    const permissions = data.permissions || {};
    return `
      <section class="grid cols-2">
        <div class="card">
          <div class="card-head"><div><h2>AI permissions</h2><p>Enforced by the bot on its AI commands</p></div>${icon("shield")}</div>
          <form id="ai-permissions-form" class="stack" style="gap:10px">
            <label class="field"><span>Control roles — may use AI controls</span>${roleMulti("control_role_ids", permissions.control_role_ids || [])}</label>
            <label class="field"><span>Sensitive roles — may read AI case details</span>${roleMulti("sensitive_role_ids", permissions.sensitive_role_ids || [])}</label>
            <button type="submit" class="btn primary">Save permissions</button>
          </form>
          ${data.roles_error ? `<p class="help" style="color:var(--bad);margin-top:10px">${esc(data.roles_error)}</p>` : ""}
        </div>
        <div class="card">
          <div class="card-head"><div><h2>Security model</h2><p>How the AI system protects data</p></div>${icon("lock")}</div>
          <ul class="feed">
            <li><span class="dot ticket"></span><span>Every AI endpoint requires an authenticated dashboard session and a CSRF token on writes.</span></li>
            <li><span class="dot ticket"></span><span>The provider API key lives only in the server environment; it is never returned to the browser.</span></li>
            <li><span class="dot ticket"></span><span>Administrator changes are written to the AI activity log with the action and detail.</span></li>
            <li><span class="dot ticket"></span><span>AI writes are limited to ${40} actions per minute per session to resist runaway scripts.</span></li>
            <li><span class="dot ticket"></span><span>Case details are only readable with the sensitive roles above; the bot refuses /ai-case otherwise.</span></li>
            <li><span class="dot ticket"></span><span>The AI never issues punishments. Partnerships publish automatically only after confirmed server access and a passing review.</span></li>
          </ul>
        </div>
      </section>`;
  }

  const BODIES = {
    overview: overviewBody,
    configuration: configurationBody,
    knowledge: knowledgeBody,
    routing: routingBody,
    partnership: partnershipBody,
    activity: activityBody,
    analytics: analyticsBody,
    permissions: permissionsBody,
  };

  function render() {
    if (!ai.data.overview) return loading();
    const body = (BODIES[ai.section] || overviewBody)();
    return `${tabs()}<div class="ai-body">${body}</div>`;
  }

  /* ------------------------------------------------------------------ loading */
  async function loadGuildData() {
    if (ai.guildLoaded) return;
    try {
      const data = await api("/api/discord/channels");
      ai.channels = data.channels || [];
      ai.guildError = "";
    } catch (error) {
      ai.guildError = error.message;
    }
    try {
      const data = await api("/api/discord/roles");
      ai.roles = data.roles || [];
    } catch { /* roles are optional for read-only sections */ }
    ai.guildLoaded = true;
  }

  async function loadSection(section) {
    const filters = ai.filters;
    switch (section) {
      case "knowledge": {
        const query = new URLSearchParams({ status: filters.knowledge, q: filters.knowledgeQuery });
        ai.data.knowledge = await api(`/api/ai/knowledge?${query}`);
        break;
      }
      case "routing":
        ai.data.routes = await api("/api/ai/routes");
        break;
      case "partnership":
        ai.data.partnerships = await api("/api/ai/partnerships");
        break;
      case "activity": {
        const f = filters.activity;
        const query = new URLSearchParams({ q: f.q, action: f.action, status: f.status, from: f.from, to: f.to, page: String(f.page) });
        ai.data.activity = await api(`/api/ai/activity?${query}`);
        break;
      }
      case "analytics":
        ai.data.analytics = await api("/api/ai/analytics");
        break;
      case "permissions":
        ai.data.permissions = await api("/api/ai/permissions");
        break;
      default:
        break;
    }
  }

  async function load() {
    ai.data.overview = await api("/api/ai/overview");
    await loadGuildData();
    await loadSection(ai.section);
  }

  async function refresh(section = ai.section) {
    ai.data.overview = await api("/api/ai/overview");
    await loadSection(section);
    window.Dashboard.rerender();
  }

  /* ------------------------------------------------------------------ modals */
  function openModal(html) {
    document.querySelector("#modal-root").innerHTML = `<div class="modal-backdrop"><div class="modal wide">${html}</div></div>`;
  }

  function closeModal() {
    document.querySelector("#modal-root").innerHTML = "";
  }

  async function openCase(caseId) {
    try {
      const data = await api(`/api/ai/cases/${caseId}`);
      const row = data.case;
      const events = data.events || [];
      const details = row.details || {};
      const analysis = details.analysis || null;
      openModal(`
        <header><h3>AI case #${row.id} · ${esc(row.kind)}</h3><button class="btn icon ghost" data-ai-close>x</button></header>
        <div class="body">
          <dl class="kv">
            <dt>Status</dt><dd>${pill(statusTone(row.status), row.status)}</dd>
            <dt>Ticket</dt><dd class="mono">${row.ticket_channel_id ? `&lt;#${esc(row.ticket_channel_id)}&gt;` : "—"}</dd>
            <dt>Requester</dt><dd class="mono">${esc(row.requester_id || "—")}</dd>
            <dt>Department</dt><dd>${esc(row.department || "—")}</dd>
            <dt>Severity</dt><dd>${esc(row.severity)}</dd>
            <dt>Confidence</dt><dd>${row.confidence ? Math.round(row.confidence * 100) + "%" : "—"}</dd>
            <dt>AI handled</dt><dd>${row.ai_handled ? "Yes, answered automatically" : "No"}</dd>
            <dt>Escalated</dt><dd>${row.escalated ? "Yes, sent to staff" : "No"}</dd>
            <dt>Destination</dt><dd>${row.destination_channel_id ? `<span class="mono">&lt;#${esc(row.destination_channel_id)}&gt;</span>` : "—"} ${esc(roleNames(row.destination_roles || []))}</dd>
            <dt>Summary</dt><dd>${esc(row.summary || "—")}</dd>
            ${details.escalation_reason ? `<dt>Escalation reason</dt><dd>${esc(details.escalation_reason)}</dd>` : ""}
            ${details.route_reason ? `<dt>Routing</dt><dd>${esc(details.route_reason)}</dd>` : ""}
          </dl>
          ${analysis ? `<div class="note"><div><strong>Form analysis</strong><div class="help">${esc(analysis.summary || "—")}</div>
            <div class="help">Reported staff: ${esc(analysis.reported_staff || "—")} · Allegations: ${esc(analysis.allegations || "—")}<br>
            Evidence: ${esc(analysis.evidence || "—")} · Missing: ${esc((analysis.missing_information || []).join(", ") || "none")}</div></div></div>` : ""}
          <div><strong>History</strong>
            <ul class="feed">${events.map((event) => `<li><span class="dot ticket"></span><span>${esc(event.actor)}: ${esc(event.action)}${event.detail ? ` — ${esc(event.detail)}` : ""}</span><time>${fmtRelative(event.created_at)}</time></li>`).join("") || '<li class="help">No events yet.</li>'}</ul>
          </div>
          <label class="field"><span>Review note</span><textarea id="ai-review-note" rows="2" placeholder="Why you approve or reject the AI decision"></textarea></label>
        </div>
        <footer>
          <button class="btn ghost" data-ai-close>Close</button>
          <button class="btn danger" data-ai-review="rejected">Reject decision</button>
          <button class="btn primary" data-ai-review="approved">Approve decision</button>
        </footer>`);
      document.querySelectorAll("[data-ai-close]").forEach((button) => button.addEventListener("click", closeModal));
      document.querySelectorAll("[data-ai-review]").forEach((button) => {
        button.addEventListener("click", async () => {
          const note = document.querySelector("#ai-review-note")?.value || "";
          try {
            const result = await api(`/api/ai/cases/${row.id}/review`, {
              method: "POST",
              body: { decision: button.dataset.aiReview, note },
            });
            toast(result.message);
            closeModal();
            await refresh();
          } catch (error) { toast(error.message, "bad"); }
        });
      });
    } catch (error) {
      toast(error.message, "bad");
    }
  }

  async function openPartnership(partnershipId) {
    try {
      const data = await api(`/api/ai/partnerships/${partnershipId}`);
      const row = data.partnership;
      const analysis = row.analysis || {};
      const findings = analysis.findings || {};
      const checklist = row.requirements || [];
      const events = data.events || [];
      const statusRow = (label, status) => `<dt>${label}</dt><dd>${pill(statusTone(String(status)), String(status))}</dd>`;
      openModal(`
        <header><h3>Partnership · ${esc(row.server_name || "Unnamed server")}</h3><button class="btn icon ghost" data-ai-close>x</button></header>
        <div class="body">
          <dl class="kv">
            <dt>Applicant</dt><dd class="mono">${esc(row.applicant_id || "—")}</dd>
            <dt>Invitation</dt><dd>${pill(statusTone(row.invite_status), row.invite_status)} <span class="mono">${esc(row.invite_code || "")}</span></dd>
            ${statusRow("Bot membership", row.bot_joined ? "joined" : "not joined")}
            ${statusRow("Server access (ticket confirmation)", row.human_verified ? "verified" : "pending")}
            ${statusRow("Permissions", row.permissions_status)}
            ${statusRow("Inspection", row.analysis_status)}
            <dt>Eligibility</dt><dd>${pill(statusTone(row.eligibility), row.eligibility.replace("_", " "))}</dd>
            <dt>Our ad in their server</dt><dd>${pill(statusTone(row.ad_status), (row.ad_status || "pending").replace("_", " "))}</dd>
            <dt>Publish</dt><dd>${pill(statusTone(row.publish_status), row.publish_status)}</dd>
            <dt>Joined server</dt><dd>${esc(row.joined_guild_name || "—")} ${row.member_count ? `<span class="help">${num(row.member_count)} members</span>` : ""}</dd>
            <dt>Verification note</dt><dd>${esc(row.verification_note || "—")}</dd>
          </dl>
          <div><strong>Advertisement</strong><div class="help" style="white-space:pre-wrap">${esc(row.advertisement || "Not supplied yet.")}</div></div>
          <div><strong>Requirement checklist</strong>
            <div class="checklist">
              ${checklist.length ? checklist.map((item) => `<div class="ai-check ${item.passed === true ? "ok" : item.passed === false ? "bad" : "unknown"}">
                <span>${item.passed === true ? "✔" : item.passed === false ? "✖" : "?"}</span>
                <div><strong>${esc(item.label)}</strong><div class="help">${esc(item.actual)} · target ${esc(item.target)}</div></div>
              </div>`).join("") : '<div class="help">Not analysed yet.</div>'}
            </div>
          </div>
          <div><strong>AI explanation</strong><div class="help" style="white-space:pre-wrap">${esc(findings.summary || analysis.error || "No analysis yet.")}</div>
            ${(findings.limitations || []).length ? `<div class="help">Limitations: ${esc(findings.limitations.join("; "))}</div>` : ""}
            <div class="help"><strong>Recommendation:</strong> ${esc(row.recommendation || "—")}</div>
          </div>
          <div><strong>History</strong>
            <ul class="feed">${events.map((event) => `<li><span class="dot ticket"></span><span>${esc(event.actor)}: ${esc(event.action)}${event.detail ? ` — ${esc(event.detail)}` : ""}</span><time>${fmtRelative(event.created_at)}</time></li>`).join("") || '<li class="help">No history yet.</li>'}</ul>
          </div>
          <label class="field"><span>Staff decision note (optional)</span><textarea id="ai-partnership-note" rows="2" placeholder="Confirmed legitimate server with public channels."></textarea></label>
        </div>
        <footer class="wrap">
          <button class="btn ghost" data-ai-close>Close</button>
          <button class="btn" data-ai-partnership-action="analyse" ${row.human_verified ? "" : "disabled"}>Re-run analysis</button>
          <button class="btn danger" data-ai-partnership-action="reject">Reject</button>
          <button class="btn" data-ai-partnership-action="pause">Pause</button>
          <button class="btn primary" data-ai-partnership-action="publish" ${["approved", "blocked"].includes(row.publish_status) && row.eligibility === "eligible" && !row.published_at ? "" : "disabled"}>Retry publication</button>
        </footer>`);
      document.querySelectorAll("[data-ai-close]").forEach((button) => button.addEventListener("click", closeModal));
      document.querySelectorAll("[data-ai-partnership-action]").forEach((button) => {
        button.addEventListener("click", async () => {
          const action = button.dataset.aiPartnershipAction;
          const note = document.querySelector("#ai-partnership-note")?.value || "";
          button.disabled = true;
          try {
            const result = action === "analyse"
                ? await api(`/api/ai/partnerships/${row.id}/analyse`, { method: "POST", body: {} })
                : await api(`/api/ai/partnerships/${row.id}/decision`, { method: "POST", body: { action, note } });
            toast(result.message || "Done.");
            closeModal();
            await refresh();
          } catch (error) {
            toast(error.message, "bad");
            button.disabled = false;
          }
        });
      });
    } catch (error) {
      toast(error.message, "bad");
    }
  }

  /* ------------------------------------------------------------------ binding */
  function bind(view) {
    view.querySelectorAll("[data-ai-section]").forEach((button) => {
      button.addEventListener("click", async () => {
        ai.section = button.dataset.aiSection;
        window.Dashboard.rerender();
        try {
          await loadSection(ai.section);
          window.Dashboard.rerender();
        } catch (error) { toast(error.message, "bad"); }
      });
    });

    view.querySelectorAll("[data-ai-case]").forEach((button) => {
      button.addEventListener("click", () => openCase(button.dataset.aiCase));
    });
    view.querySelectorAll("[data-ai-partnership]").forEach((button) => {
      button.addEventListener("click", () => openPartnership(button.dataset.aiPartnership));
    });

    /* configuration */
    view.querySelector("#ai-config-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.target;
      const body = {
        provider: form.provider.value.trim(),
        model: form.model.value.trim(),
        base_url: form.base_url.value.trim(),
        temperature: form.temperature.value,
        max_output_tokens: form.max_output_tokens.value,
        max_processing_seconds: form.max_processing_seconds.value,
        system_instructions: form.system_instructions.value,
        personality: form.personality.value,
        confidence_threshold: form.confidence_threshold.value,
        escalation_behavior: form.escalation_behavior.value,
        retention_days: form.retention_days.value,
        enabled: form.enabled.checked,
        features: {},
      };
      Object.keys(ai.data.overview.features || {}).forEach((key) => {
        const input = form.elements[`feature_${key}`];
        if (input) body.features[key] = input.checked;
      });
      try {
        const result = await api("/api/ai/settings", { method: "PUT", body });
        toast(result.message || "Saved.");
        await refresh("configuration");
      } catch (error) { toast(error.message, "bad"); }
    });

    view.querySelector("#ai-test-provider")?.addEventListener("click", async (event) => {
      const button = event.target;
      button.disabled = true;
      const target = view.querySelector("#ai-test-result");
      if (target) target.textContent = "Testing…";
      try {
        const result = await api("/api/ai/settings/test", { method: "POST", body: {} });
        const info = result.result || {};
        if (target) target.textContent = info.ok ? `Connected in ${info.latency_ms} ms` : info.error;
        toast(info.ok ? "Provider reachable." : info.error, info.ok ? "good" : "bad");
      } catch (error) {
        if (target) target.textContent = error.message;
        toast(error.message, "bad");
      } finally { button.disabled = false; }
    });

    /* knowledge */
    view.querySelector("#ai-knowledge-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.target;
      try {
        const result = await api("/api/ai/knowledge", {
          method: "POST",
          body: {
            title: form.title.value,
            body: form.body.value,
            tags: form.tags.value,
            approved: form.approved.checked,
          },
        });
        toast(result.message || "Saved.");
        await refresh("knowledge");
      } catch (error) { toast(error.message, "bad"); }
    });
    view.querySelector("#ai-add-source")?.addEventListener("click", async (event) => {
      const select = view.querySelector('[name="source_channel"]');
      if (!select?.value) { toast("Pick a channel first.", "bad"); return; }
      event.target.disabled = true;
      try {
        const result = await api("/api/ai/sources", { method: "POST", body: { channel_id: select.value } });
        toast(result.message || "Queued.");
        setTimeout(() => refresh("knowledge").catch(() => {}), 4000);
        await refresh("knowledge");
      } catch (error) { toast(error.message, "bad"); }
      finally { event.target.disabled = false; }
    });
    view.querySelectorAll("[data-ai-scan-source]").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          const result = await api(`/api/ai/sources/${button.dataset.aiScanSource}/scan`, { method: "POST", body: {} });
          toast(result.message || "Scan queued.");
        } catch (error) { toast(error.message, "bad"); }
      });
    });
    view.querySelectorAll("[data-ai-delete-source]").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          const result = await api(`/api/ai/sources/${button.dataset.aiDeleteSource}`, { method: "DELETE" });
          toast(result.message || "Removed.");
          await refresh("knowledge");
        } catch (error) { toast(error.message, "bad"); }
      });
    });
    view.querySelectorAll("[data-ai-knowledge-filter]").forEach((button) => {
      button.addEventListener("click", async () => {
        ai.filters.knowledge = button.dataset.aiKnowledgeFilter;
        try { await refresh("knowledge"); } catch (error) { toast(error.message, "bad"); }
      });
    });
    view.querySelector("#ai-knowledge-search")?.addEventListener("change", async (event) => {
      ai.filters.knowledgeQuery = event.target.value.trim();
      try { await refresh("knowledge"); } catch (error) { toast(error.message, "bad"); }
    });
    const quickEdit = (selector, body) => {
      view.querySelectorAll(selector).forEach((button) => {
        button.addEventListener("click", async () => {
          try {
            const result = await api(body.url(button), { method: body.method, body: body.payload ? body.payload(button) : {} });
            toast(result.message || "Updated.");
            await refresh(ai.section);
          } catch (error) { toast(error.message, "bad"); }
        });
      });
    };
    quickEdit("[data-ai-knowledge-approve]", {
      url: (button) => `/api/ai/knowledge/${button.dataset.aiKnowledgeApprove}`,
      method: "PATCH",
      payload: () => ({ status: "approved" }),
    });
    quickEdit("[data-ai-knowledge-reject]", {
      url: (button) => `/api/ai/knowledge/${button.dataset.aiKnowledgeReject}`,
      method: "PATCH",
      payload: () => ({ status: "pending" }),
    });
    quickEdit("[data-ai-knowledge-delete]", {
      url: (button) => `/api/ai/knowledge/${button.dataset.aiKnowledgeDelete}`,
      method: "DELETE",
    });

    /* routing */
    view.querySelector("#ai-route-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.target;
      try {
        const result = await api("/api/ai/routes", {
          method: "POST",
          body: {
            name: form.name.value,
            department: form.department.value,
            report_category: form.report_category.value,
            severity: form.severity.value,
            destination_channel_id: form.destination_channel_id.value,
            escalate_after_minutes: form.escalate_after_minutes.value,
            priority: form.priority.value,
            role_ids: multiValues(form.role_ids),
            backup_role_ids: multiValues(form.backup_role_ids),
            enabled: form.enabled.checked,
          },
        });
        toast(result.message || "Saved.");
        await refresh("routing");
      } catch (error) { toast(error.message, "bad"); }
    });
    view.querySelector("#ai-route-preview")?.addEventListener("click", async () => {
      const query = new URLSearchParams({
        department: view.querySelector("#preview-department").value,
        category: view.querySelector("#preview-category").value,
        severity: view.querySelector("#preview-severity").value,
      });
      const target = view.querySelector("#ai-route-result");
      try {
        const data = await api(`/api/ai/routes/preview?${query}`);
        target.innerHTML = `<div><strong>${data.route ? esc(data.route.name) : "No matching rule"}</strong><div class="help">${esc(data.explanation)}</div></div>`;
      } catch (error) { toast(error.message, "bad"); }
    });
    quickEdit("[data-ai-route-toggle]", {
      url: (button) => `/api/ai/routes/${button.dataset.aiRouteToggle}`,
      method: "PATCH",
      payload: (button) => ({ enabled: button.dataset.current !== "1" }),
    });
    quickEdit("[data-ai-route-delete]", {
      url: (button) => `/api/ai/routes/${button.dataset.aiRouteDelete}`,
      method: "DELETE",
    });

    /* partnerships */
    view.querySelector("#ai-partnership-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.target;
      try {
        const result = await api("/api/ai/settings", {
          method: "PUT",
          body: {
            partnership_channel_id: form.partnership_channel_id.value,
            partnership_ad_channel_id: form.partnership_ad_channel_id.value,
            partnership_role_ids: multiValues(form.partnership_role_ids),
            enabled: true,
            features: { ...ai.data.overview.features, partnership_automation: form.partnership_automation.checked },
            partnership_requirements: { min_members: 15 },
          },
        });
        toast(result.message || "Saved.");
        await refresh("partnership");
      } catch (error) { toast(error.message, "bad"); }
    });

    /* activity */
    view.querySelector("#ai-activity-apply")?.addEventListener("click", async () => {
      ai.filters.activity = {
        q: view.querySelector("#ai-activity-q").value.trim(),
        action: view.querySelector("#ai-activity-action").value,
        status: view.querySelector("#ai-activity-status").value,
        from: view.querySelector("#ai-activity-from").value,
        to: view.querySelector("#ai-activity-to").value,
        page: 1,
      };
      try { await refresh("activity"); } catch (error) { toast(error.message, "bad"); }
    });
    view.querySelector("#ai-activity-prev")?.addEventListener("click", async () => {
      ai.filters.activity.page = Math.max(1, ai.filters.activity.page - 1);
      try { await refresh("activity"); } catch (error) { toast(error.message, "bad"); }
    });
    view.querySelector("#ai-activity-next")?.addEventListener("click", async () => {
      ai.filters.activity.page += 1;
      try { await refresh("activity"); } catch (error) { toast(error.message, "bad"); }
    });

    /* permissions */
    view.querySelector("#ai-permissions-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.target;
      try {
        const result = await api("/api/ai/permissions", {
          method: "PUT",
          body: {
            control_role_ids: multiValues(form.control_role_ids),
            sensitive_role_ids: multiValues(form.sensitive_role_ids),
          },
        });
        toast(result.message || "Saved.");
        await refresh("permissions");
      } catch (error) { toast(error.message, "bad"); }
    });
  }

  window.AIView = { render, bind, load, refresh };
})();
