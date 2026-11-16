/* Florida State Dashboard - client */
(() => {
  "use strict";

  const state = {
    csrf: null,
    oauthConfigured: false,
    authMode: "login",
    view: "overview",
    ticketFilter: "open",
    overview: null,
    tickets: null,
    status: null,
    config: null,
    channels: [],
    categories: [],
    roles: [],
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[char]));

  const EMOJI = "/media/emoji/";
  const icon = (name, cls = "card-icon") => `<img class="${cls}" src="${EMOJI}${name}.png" alt="">`;

  function parseStamp(value) {
    if (!value) return null;
    const text = String(value);
    const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(text) ? `${text.replace(" ", "T")}Z` : text;
    const date = new Date(normalized);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function fmtDate(value) {
    const date = parseStamp(value);
    if (!date) return "—";
    return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function fmtRelative(value) {
    const date = parseStamp(value);
    if (!date) return "—";
    const seconds = Math.round((Date.now() - date.getTime()) / 1000);
    if (seconds < 45) return "just now";
    if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
    return `${Math.round(seconds / 86400)}d ago`;
  }

  function fmtDuration(seconds) {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    if (!total) return "—";
    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    if (days) return `${days}d ${hours}h`;
    if (hours) return `${hours}h ${minutes}m`;
    if (minutes) return `${minutes}m ${secs}s`;
    return `${secs}s`;
  }

  function durationLabel(seconds) {
    const total = Number(seconds) || 0;
    if (total % 3600 === 0) return `${total / 3600} hour${total === 3600 ? "" : "s"}`;
    if (total % 60 === 0) return `${total / 60} minute${total === 60 ? "" : "s"}`;
    return `${total} seconds`;
  }

  function toast(message, tone = "good") {
    const node = document.createElement("div");
    node.className = `toast ${tone}`;
    node.textContent = message;
    $("#toasts").appendChild(node);
    setTimeout(() => node.remove(), 4200);
  }

  async function api(path, options = {}) {
    const { method = "GET", body, form } = options;
    const headers = {};
    if (state.csrf) headers["X-CSRF-Token"] = state.csrf;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let response;
    try {
      response = await fetch(path, {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : form,
        credentials: "same-origin",
      });
    } catch (error) {
      throw new Error("Cannot reach the dashboard service. Is the container running?");
    }
    let data = {};
    try { data = await response.json(); } catch { /* empty body */ }
    if (response.status === 401 && !path.startsWith("/api/auth")) {
      showAuth("login", "Your session expired. Sign in again.");
      throw new Error(data.error || "Signed out");
    }
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  }

  /* ------------------------------------------------------------------ auth */
  function showAuth(mode, message = "") {
    state.authMode = mode;
    $("#app").hidden = true;
    $("#auth").hidden = false;
    const isSetup = mode === "setup";
    $("#auth-title").textContent = isSetup ? "Create the admin password" : "Administrator sign in";
    $("#auth-lead").textContent = isSetup
      ? "Set the password that protects this dashboard. It is stored as a salted PBKDF2 hash."
      : "Enter the dashboard password to manage the bot.";
    $("#auth-password-label").textContent = isSetup ? "New password" : "Password";
    $("#auth-confirm-wrap").hidden = !isSetup;
    $("#auth-submit").textContent = isSetup ? "Create password" : "Sign in";
    $("#auth-discord").hidden = !state.oauthConfigured || isSetup;
    $("#auth-error").textContent = message;
    $("#auth-note").textContent = isSetup
      ? "Use at least 5 characters. You can change it later under Security."
      : "Only authorised administrators of the Discord server should have this password.";
    $("#auth-password").value = "";
    $("#auth-confirm").value = "";
    setTimeout(() => $("#auth-password").focus(), 40);
  }

  async function submitAuth(event) {
    event.preventDefault();
    const password = $("#auth-password").value;
    const confirm = $("#auth-confirm").value;
    const errorBox = $("#auth-error");
    errorBox.textContent = "";
    if (state.authMode === "setup" && password !== confirm) {
      errorBox.textContent = "The two passwords do not match.";
      return;
    }
    try {
      const endpoint = state.authMode === "setup" ? "/api/auth/setup" : "/api/auth/login";
      const data = await api(endpoint, { method: "POST", body: { password } });
      state.csrf = data.csrf;
      await boot();
      toast(state.authMode === "setup" ? "Password created. Welcome." : "Signed in.");
    } catch (error) {
      errorBox.textContent = error.message;
    }
  }

  async function signOut() {
    try { await api("/api/auth/logout", { method: "POST" }); } catch { /* ignore */ }
    state.csrf = null;
    showAuth("login");
  }

  /* ------------------------------------------------------------------ shell */
  const VIEW_META = {
    sleep: ["Sleep mode", "Leave this page open to keep the bot online and the machine awake"],
    overview: ["Overview", "Live status of your Discord server and ticket system"],
    tickets: ["Tickets", "Open and closed support tickets"],
    status: ["Bot status", "Presence, activity and automatic rotations"],
    config: ["Configuration", "Channels, roles, images and bot wiring"],
    security: ["Security", "Access, sessions and privacy"],
    modules: ["Card modules", "Automated infraction, promotion, feedback and session cards"],
    ai: ["AI Control System", "Knowledge, routing and partnership automation"],
  };

  function setView(view) {
    if (view !== "sleep") window.SleepView?.stop();
    state.view = view;
    $$("#nav button").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
    const [title, sub] = VIEW_META[view] || VIEW_META.overview;
    $("#view-title").textContent = title;
    $("#view-sub").textContent = sub;
    render();
  }

  function loading(rows = 3) {
    return `<div class="stack">${Array.from({ length: rows }, () => '<div class="skeleton" style="height:' + (46 + Math.random() * 30) + 'px"></div>').join("")}</div>`;
  }

  function render() {
    const view = $("#view");
    switch (state.view) {
      case "sleep": view.innerHTML = window.SleepView ? window.SleepView.render() : loading(); break;
      case "overview": view.innerHTML = overviewView(); break;
      case "tickets": view.innerHTML = ticketsView(); break;
      case "status": view.innerHTML = statusView(); break;
      case "config": view.innerHTML = configView(); break;
      case "security": view.innerHTML = securityView(); break;
      case "modules": view.innerHTML = window.ModulesView ? window.ModulesView.render() : loading(); break;
      case "ai": view.innerHTML = window.AIView ? window.AIView.render() : loading(); break;
      default: view.innerHTML = "";
    }
    bindView();
  }

  function botChip() {
    const bot = state.overview?.bot;
    const chip = $("#bot-chip");
    const pill = chip.querySelector(".pill");
    const label = chip.querySelector("strong");
    if (!bot) { pill.className = "pill neutral"; label.textContent = "Checking bot…"; return; }
    const tone = bot.state === "online" ? "good" : bot.state === "starting" ? "waiting" : "bad";
    const text = bot.state === "online" ? "Bot online" : bot.state === "starting" ? "Connecting" : bot.state === "missing-token" ? "No token" : "Bot offline";
    pill.className = `pill ${tone}`;
    label.textContent = text;
  }

  function serverChip() {
    const server = state.overview?.server;
    const chip = $("#server-chip");
    if (!server?.name) { chip.innerHTML = "No server connected"; return; }
    chip.innerHTML = `${server.icon ? `<img src="${esc(server.icon)}" alt="">` : ""}<span>${esc(server.name)}</span>`;
  }

  async function loadOverview() {
    state.overview = await api("/api/overview");
    botChip();
    serverChip();
  }

  /* ------------------------------------------------------------------ overview */
  function overviewView() {
    const aiShortcut = `
      <section class="ai-shortcut" aria-label="AI Control System shortcut">
        <button type="button" class="btn primary ai-shortcut-button" id="open-ai-page">
          GO TO THE AI PAGE <span aria-hidden="true">→</span>
        </button>
        <p>Open AI Control System — knowledge, staff routing and partnerships.</p>
      </section>`;
    const data = state.overview;
    if (!data) return aiShortcut + loading();
    const { server, bot, presence, tickets, recent_activity: activity, recent_tickets: recent } = data;
    const tone = bot.state === "online" ? "good" : bot.state === "starting" ? "waiting" : "bad";
    const label = bot.state === "online" ? "Connected to Discord" : bot.state === "starting" ? "Starting up" : bot.state === "missing-token" ? "Waiting for DISCORD_TOKEN" : "Not connected";
    const initial = (server.name || "FS").slice(0, 2).toUpperCase();

    return `
      ${aiShortcut}
      <section class="grid stats">
        <dl class="stat"><dt>Open tickets</dt><dd>${tickets.open}</dd><small>Awaiting staff</small></dl>
        <dl class="stat"><dt>Closed tickets</dt><dd>${tickets.closed}</dd><small>${tickets.total} total recorded</small></dl>
        <dl class="stat"><dt>Bot uptime</dt><dd>${fmtDuration(bot.uptime_seconds)}</dd><small>${bot.restarts} restart${bot.restarts === 1 ? "" : "s"}</small></dl>
        <dl class="stat"><dt>Gateway latency</dt><dd>${bot.latency_ms ? `${bot.latency_ms} ms` : "—"}</dd><small>${bot.guilds ?? 1} server${(bot.guilds ?? 1) === 1 ? "" : "s"}</small></dl>
      </section>

      <section class="grid cols-2" style="margin-top:14px">
        <div class="card">
          <div class="card-head">
            <div>
              <h2>Connected server</h2>
              <p>Discord home of the ticket system</p>
            </div>
            ${icon("support")}
          </div>
          <div class="server-card">
            ${server.icon ? `<img class="server-icon" src="${esc(server.icon)}" alt="">` : `<div class="server-icon fallback">${esc(initial)}</div>`}
            <div>
              <div class="server-name">${esc(server.name || "Not connected")}</div>
              <div class="server-meta">${server.members ?? "—"} members · ${server.channels ?? "—"} channels · ${server.roles ?? "—"} roles</div>
            </div>
          </div>
          <dl class="kv" style="margin-top:16px">
            <dt>Server ID</dt><dd class="mono">${esc(server.id || "—")}</dd>
            <dt>Bot account</dt><dd>${esc(bot.user || "—")}</dd>
            <dt>Status</dt><dd><span class="pill ${tone}"><i></i>${label}</span>${bot.fatal ? `<div class="help">${esc(bot.fatal)}</div>` : ""}</dd>
          </dl>
        </div>

        <div class="card">
          <div class="card-head">
            <div>
              <h2>Bot status</h2>
              <p>Presence reported by the running bot</p>
            </div>
            ${icon("clock")}
          </div>
          <dl class="kv">
            <dt>Activity</dt><dd>${esc(presence.activity || "—")}</dd>
            <dt>Activity type</dt><dd style="text-transform: capitalize">${esc(presence.activity_type || "—")}</dd>
            <dt>Custom status</dt><dd>${esc(presence.custom_status || "—")}</dd>
            <dt>Presence</dt><dd style="text-transform: capitalize">${esc(presence.status || "—")}</dd>
            <dt>Uptime</dt><dd>${fmtDuration(bot.uptime_seconds)}</dd>
            <dt>Last activity</dt><dd>${presence.last_activity ? esc(fmtRelative(String(presence.last_activity).split(" ")[0])) : "—"}</dd>
            <dt>Rotation</dt><dd>${data.rotation.enabled ? `<span class="pill good"><i></i>Active</span>` : `<span class="pill neutral"><i></i>Idle</span>`} <span class="help">${data.rotation.count} entr${data.rotation.count === 1 ? "y" : "ies"}</span></dd>
          </dl>
        </div>
      </section>

      <section class="grid cols-2" style="margin-top:14px">
        <div class="card">
          <div class="card-head">
            <div><h2>Recent activity</h2><p>Dashboard and bot events</p></div>
            ${icon("bell")}
          </div>
          ${activity.length ? `<ul class="feed">${activity.map((item) => `
            <li><span class="dot ${esc(item.kind)}"></span><span>${esc(item.message)}</span><time>${fmtRelative(item.created_at)}</time></li>
          `).join("")}</ul>` : '<div class="empty">No activity recorded yet.</div>'}
        </div>

        <div class="card">
          <div class="card-head">
            <div><h2>Recent tickets</h2><p>Newest tickets in this server</p></div>
            ${icon("ticket")}
          </div>
          ${recent.length ? `<div class="table-wrap"><table><thead><tr><th>Ticket</th><th>Owner</th><th>Status</th><th>Created</th></tr></thead><tbody>
            ${recent.map((ticket) => `<tr>
              <td class="mono">#${String(ticket.ticket_number).padStart(4, "0")}</td>
              <td>${esc(ticket.owner_name || `User ${ticket.creator_id}`)}</td>
              <td><span class="pill ${ticket.status === "open" ? "good" : "neutral"}"><i></i>${esc(ticket.status)}</span></td>
              <td class="num">${fmtRelative(ticket.created_at)}</td>
            </tr>`).join("")}
          </tbody></table></div>` : '<div class="empty">No tickets yet. Publish the panel and open one from Discord.</div>'}
        </div>
      </section>

      <details class="card" style="margin-top:14px">
        <summary style="cursor:pointer; font-size:13px; color:var(--muted)">Bot console</summary>
        <pre id="console-output" class="mono" style="margin:12px 0 0; max-height:260px; overflow:auto; color:var(--muted); font-size:12px; line-height:1.6"></pre>
      </details>
    `;
  }

  async function loadConsole() {
    const output = $("#console-output");
    if (!output) return;
    try {
      const data = await api("/api/console");
      output.textContent = data.lines.slice(-60).join("\n") || "Waiting for the bot to write output…";
      output.scrollTop = output.scrollHeight;
    } catch { /* ignore */ }
  }

  /* ------------------------------------------------------------------ tickets */
  function statusPill(status) {
    return `<span class="pill ${status === "open" ? "good" : "neutral"}"><i></i>${esc(status)}</span>`;
  }

  function ticketsView() {
    const data = state.tickets;
    const filter = state.ticketFilter;
    const rows = data?.tickets || [];
    return `
      <div class="card">
        <div class="card-head">
          <div>
            <h2>Ticket queue</h2>
            <p>${data ? `${data.counts.open} open · ${data.counts.closed} closed` : "Loading tickets…"}</p>
          </div>
          <div class="tabs">
            ${["open", "closed", "all"].map((value) => `<button data-filter="${value}" class="${filter === value ? "active" : ""}">${value === "all" ? "All" : value[0].toUpperCase() + value.slice(1)}</button>`).join("")}
          </div>
        </div>
        ${!data ? loading(4) : rows.length ? `
        <div class="table-wrap">
          <table>
            <thead><tr><th>Ticket</th><th>Owner</th><th>Staff</th><th>Category</th><th>Created</th><th>Status</th><th></th></tr></thead>
            <tbody>
              ${rows.map((ticket) => `
                <tr data-ticket="${ticket.id}">
                  <td class="mono">#${String(ticket.ticket_number).padStart(4, "0")}</td>
                  <td><div class="who">${esc(ticket.owner_name || "Unknown member")}<span class="mono">${esc(ticket.creator_id)}</span></div></td>
                  <td>${ticket.claimed_by ? esc(ticket.staff_name || `ID ${ticket.claimed_by}`) : '<span class="help">Unassigned</span>'}</td>
                  <td>${esc(ticket.category || "—")}</td>
                  <td class="num">${fmtDate(ticket.created_at)}</td>
                  <td>${statusPill(ticket.status)}</td>
                  <td style="text-align:right">
                    <button class="btn sm" data-detail="${ticket.id}">View</button>
                    ${ticket.status === "open" ? `<button class="btn sm danger" data-close="${ticket.id}">Close</button>` : ""}
                  </td>
                </tr>`).join("")}
            </tbody>
          </table>
        </div>` : `<div class="empty">${filter === "open" ? "No open tickets right now." : filter === "closed" ? "No closed tickets yet." : "No tickets recorded for this server."}</div>`}
      </div>`;
  }

  async function loadTickets() {
    state.tickets = await api(`/api/tickets?status=${encodeURIComponent(state.ticketFilter)}`);
    if (state.view === "tickets") render();
  }

  function ticketModal(ticket) {
    const root = $("#modal-root");
    root.innerHTML = `
      <div class="modal-backdrop">
        <div class="modal">
          <header>
            <h3>Ticket #${String(ticket.ticket_number).padStart(4, "0")}</h3>
            <button class="btn icon ghost" data-close-modal>x</button>
          </header>
          <div class="body">
            <dl class="kv">
              <dt>Status</dt><dd>${statusPill(ticket.status)}</dd>
              <dt>Owner</dt><dd>${esc(ticket.owner_name || "Unknown member")} <span class="mono help">${esc(ticket.creator_id)}</span></dd>
              <dt>Handled by</dt><dd>${ticket.claimed_by ? esc(ticket.staff_name || `ID ${ticket.claimed_by}`) : "Unassigned"}</dd>
              <dt>Category</dt><dd>${esc(ticket.category || "—")}</dd>
              <dt>Subject</dt><dd>${esc(ticket.subject || "—")}</dd>
              <dt>Details</dt><dd>${esc(ticket.description || "—")}</dd>
              <dt>Created</dt><dd>${fmtDate(ticket.created_at)}</dd>
              <dt>Closed</dt><dd>${ticket.closed_at ? fmtDate(ticket.closed_at) : "—"}</dd>
              <dt>Channel</dt><dd class="mono">${esc(ticket.channel_id)}</dd>
            </dl>
            ${state.tickets?.guild_id ? `<a href="https://discord.com/channels/${esc(state.tickets.guild_id)}/${esc(ticket.channel_id)}" target="_blank" rel="noreferrer">Open the channel in Discord</a>` : ""}
          </div>
          <footer>
            <button class="btn ghost" data-close-modal>Close window</button>
            ${ticket.status === "open" ? `<button class="btn danger" data-close="${ticket.id}">Close ticket</button>` : ""}
          </footer>
        </div>
      </div>`;
  }

  async function closeTicket(ticketId) {
    const button = $(`[data-close="${ticketId}"]`);
    if (button) button.disabled = true;
    try {
      const data = await api(`/api/tickets/${ticketId}/close`, { method: "POST", body: {} });
      toast(data.message || "Closing the ticket…");
      $("#modal-root").innerHTML = "";
      await loadTickets();
      await loadOverview();
    } catch (error) {
      toast(error.message, "bad");
      if (button) button.disabled = false;
    }
  }

  /* ------------------------------------------------------------------ status */
  function statusView() {
    const data = state.status;
    if (!data) return loading();
    const presence = data.presence;
    const rotation = data.rotation;
    const activityOptions = data.activity_types.map((value) => `<option value="${value}" ${presence.activity_type === value ? "selected" : ""}>${value[0].toUpperCase() + value.slice(1)}</option>`).join("");
    const statusOptions = data.presence_states.map((value) => `<option value="${value}" ${presence.status === value ? "selected" : ""}>${value === "dnd" ? "Do not disturb" : value[0].toUpperCase() + value.slice(1)}</option>`).join("");

    return `
      <section class="grid cols-2">
        <div class="card">
          <div class="card-head"><div><h2>Current presence</h2><p>What Discord shows right now</p></div>${icon("clock")}</div>
          <dl class="kv">
            <dt>Activity</dt><dd>${esc(presence.activity || "—")}</dd>
            <dt>Type</dt><dd style="text-transform: capitalize">${esc(presence.activity_type || "—")}</dd>
            <dt>Status</dt><dd style="text-transform: capitalize">${esc(presence.status || "—")}</dd>
            <dt>Last change</dt><dd>${presence.updated_at ? fmtRelative(presence.updated_at) : "—"}</dd>
            <dt>Bot activity</dt><dd>${esc(presence.last_activity || "—")}</dd>
          </dl>
        </div>

        <div class="card">
          <div class="card-head"><div><h2>Set presence now</h2><p>Applies immediately to the running bot</p></div>${icon("settings")}</div>
          <form id="presence-form" class="stack" style="gap:10px">
            <div class="grid cols-2" style="gap:10px">
              <label class="field"><span>Activity type</span><select name="activity">${activityOptions}</select></label>
              <label class="field"><span>Status</span><select name="status">${statusOptions}</select></label>
            </div>
            <label class="field"><span>Activity text</span><input type="text" name="text" maxlength="128" value="${esc(presence.activity_text || "")}" placeholder="e.g. /help for assistance"></label>
            <button type="submit" class="btn primary">Apply presence</button>
          </form>
        </div>
      </section>

      <section class="card" style="margin-top:14px">
        <div class="card-head">
          <div><h2>Automatic rotation</h2><p>Cycles through the entries below on their own timers</p></div>
          <label class="switch"><input type="checkbox" id="rotation-toggle" ${rotation.enabled ? "checked" : ""}> Rotation ${rotation.enabled ? "enabled" : "disabled"}</label>
        </div>
        ${rotation.entries.length ? `
        <div class="table-wrap">
          <table>
            <thead><tr><th>Activity</th><th>Text</th><th>Shown for</th><th>Enabled</th><th></th></tr></thead>
            <tbody>
              ${rotation.entries.map((entry) => `<tr>
                <td style="text-transform:capitalize">${esc(entry.activity)}</td>
                <td>${esc(entry.text)}</td>
                <td class="num">${esc(durationLabel(entry.duration_seconds))}</td>
                <td><label class="switch"><input type="checkbox" data-toggle-entry="${entry.id}" ${entry.enabled ? "checked" : ""}></label></td>
                <td style="text-align:right"><button class="btn sm danger" data-delete-entry="${entry.id}">Remove</button></td>
              </tr>`).join("")}
            </tbody>
          </table>
        </div>` : '<div class="empty">No rotation entries yet. Add one below.</div>'}

        <form id="rotation-form" class="row" style="margin-top:14px; align-items:flex-end">
          <label class="field" style="flex:0 0 150px"><span>Activity</span><select name="activity">${data.activity_types.map((value) => `<option value="${value}">${value[0].toUpperCase() + value.slice(1)}</option>`).join("")}</select></label>
          <label class="field" style="flex:1 1 240px"><span>Text</span><input type="text" name="text" maxlength="128" placeholder="Assisting the community"></label>
          <label class="field" style="flex:0 0 150px"><span>Duration (minutes)</span><input type="number" name="minutes" min="1" max="1440" value="10"></label>
          <button type="submit" class="btn primary">Add entry</button>
        </form>
      </section>

      <div class="note" style="margin-top:14px">${icon("info", "card-icon")}
        <div>Rotation overrides a manually applied presence while it is enabled. Every change is delivered to the bot within a few seconds.</div>
      </div>

      <section class="card" style="margin-top:14px">
        <div class="card-head"><div><h2>Recent bot commands</h2><p>What the bot did with the last dashboard requests</p></div>${icon("check")}</div>
        ${data.recent_commands.length ? `<ul class="feed">${data.recent_commands.map((command) => `
          <li><span class="dot ${command.state === "failed" ? "" : "ticket"}"></span>
            <span>${esc(command.action.replace(/_/g, " "))} — ${esc(command.result || command.state)}</span>
            <time>${command.processed_at ? fmtRelative(command.processed_at) : "pending"}</time></li>`).join("")}</ul>`
          : '<div class="empty">No dashboard commands have run yet.</div>'}
      </section>`;
  }

  async function loadStatus() {
    state.status = await api("/api/status");
    if (state.view === "status") render();
  }

  /* ------------------------------------------------------------------ configuration */
  function channelSelect(name, label, current, allowEmpty = true) {
    const options = [`<option value="">${allowEmpty ? "Not set" : "Choose a channel"}</option>`]
      .concat(state.channels.map((channel) => {
        const group = channel.category ? `${channel.category} · ` : "";
        return `<option value="${channel.id}" ${String(current) === channel.id ? "selected" : ""}>${esc(group + "#" + channel.name)}</option>`;
      }));
    return `<label class="field"><span>${label}</span>
      <div class="select-row">
        <select name="${name}" data-channel-select>${options.join("")}</select>
        <button type="button" class="btn icon" data-refresh-channels title="Reload channels from Discord">${icon("refresh", "")}</button>
      </div></label>`;
  }

  function categorySelect(name, label, current) {
    const options = ['<option value="">Not set</option>'].concat(
      state.categories.map((category) => `<option value="${category.id}" ${String(current) === category.id ? "selected" : ""}>${esc(category.name)}</option>`)
    );
    return `<label class="field"><span>${label}</span>
      <div class="select-row">
        <select name="${name}" data-channel-select>${options.join("")}</select>
        <button type="button" class="btn icon" data-refresh-channels title="Reload categories from Discord">${icon("refresh", "")}</button>
      </div></label>`;
  }

  function roleSelect(name, label, current) {
    const options = ['<option value="">Not set</option>'].concat(
      state.roles.map((role) => `<option value="${role.id}" ${String(current) === role.id ? "selected" : ""}>${esc(role.name)}</option>`)
    );
    return `<label class="field"><span>${label}</span>
      <div class="select-row">
        <select name="${name}" data-role-select>${options.join("")}</select>
        <button type="button" class="btn icon" data-refresh-roles title="Reload roles from Discord">${icon("refresh", "")}</button>
      </div></label>`;
  }

  function fixedRoleCard(label, group, roles) {
    const matched = (roles && roles[group]) || [];
    const expected = ((roles && roles.names && roles.names[group]) || []).join(" + ");
    const body = matched.length
      ? matched.map((role) => esc(role.name)).join("<br>")
      : '<span style="color: var(--bad)">Not found in this server</span>';
    return `<div class="field"><span>${label}</span>
      <div class="note">${icon("check", "card-icon")}<div>${body}<br><span class="help">${esc(expected)}</span></div></div></div>`;
  }

  function notApplicableRoleCard(label) {
    return `<div class="field"><span>${label}</span>
      <div class="note">${icon("info", "card-icon")}<div>N/A</div></div></div>`;
  }

  function uploadCard(slot, label, path) {
    return `<div class="upload" data-slot="${slot}">
      <div class="preview">${path ? `<img src="${esc(path)}?v=${Date.now()}" alt="${esc(label)}">` : `<span>No image</span>`}</div>
      <div class="meta">
        <strong>${esc(label)}</strong>
        <div class="actions">
          <label class="btn sm" style="margin:0">Upload<input type="file" accept="image/png,image/jpeg,image/webp,image/gif" data-upload="${slot}" hidden></label>
          ${path ? `<button type="button" class="btn sm danger" data-remove-image="${slot}">Remove</button>` : ""}
        </div>
      </div>
    </div>`;
  }

  function configView() {
    const config = state.config;
    if (!config) return loading();
    const settings = config.settings || {};
    const ticket = config.ticket_config || {};
    const images = config.images || {};

    return `
      <form id="config-form" class="stack">
        <div class="card">
          <div class="card-head"><div><h2>Discord server</h2><p>The server this dashboard manages</p></div>${icon("support")}</div>
          <div class="grid cols-2">
            <label class="field"><span>Server ID</span><input type="text" name="guild_id" value="${esc(config.guild_id || "")}" placeholder="123456789012345678"></label>
            <div class="note" style="align-self:end">${icon("info", "card-icon")}<div>Right-click your server icon in Discord → <strong>Copy Server ID</strong> (Developer Mode must be on).</div></div>
          </div>
          ${config.bot_in_guild === false
            ? `<p class="help" style="margin:12px 0 0">The bot is not in this server yet. ${config.invite_url ? `<a href="${esc(config.invite_url)}" target="_blank" rel="noreferrer"><strong>Invite the bot</strong></a> with Administrator access, then reload this page.` : "Invite the bot from the Discord Developer Portal, then reload this page."}</p>`
            : ""}
        </div>

        <div class="card">
          <div class="card-head"><div><h2>Channels</h2><p>Every dropdown is filled from your live Discord server</p></div>${icon("folder")}</div>
          <div class="grid cols-2">
            ${channelSelect("channel_id", "Ticket panel channel", ticket.channel_id)}
            ${categorySelect("category_id", "Ticket category", ticket.category_id)}
            ${channelSelect("panel_channel_id", "Assistance panel channel", settings.panel_channel_id)}
            ${channelSelect("log_channel_id", "Ticket log channel", ticket.log_channel_id)}
            ${channelSelect("staff_channel_id", "Staff notification channel", settings.staff_channel_id)}
            ${channelSelect("transcript_channel_id", "Transcript channel", settings.transcript_channel_id)}
          </div>
          <p class="help" style="margin:12px 0 0">Missing a channel? Create it in Discord, then press the refresh icon next to any dropdown.</p>
        </div>

        <div class="card">
          <div class="card-head"><div><h2>Roles</h2><p>Fixed staff roles, matched by name in your server</p></div>${icon("moderator")}</div>
          <div class="grid cols-2">
            ${fixedRoleCard("Administrator ranks", "administrators", config.roles)}
            ${fixedRoleCard("Management ranks", "moderators", config.roles)}
            ${notApplicableRoleCard("Additional support")}
            ${notApplicableRoleCard("Primary support role")}
          </div>
        </div>

        <div class="card">
          <div class="card-head"><div><h2>Welcome</h2><p>Automatically greets every new member in the channel you pick</p></div>${icon("handshake")}</div>
          <div class="grid cols-2">
            ${channelSelect("welcome_channel_id", "Welcome channel", settings.welcome_channel_id)}
            <label class="field"><span>Welcome message</span><input type="text" name="welcome_message" maxlength="320" value="${esc(settings.welcome_message || "")}" placeholder="{user} and {server} are replaced"></label>
          </div>
          <label class="switch" style="margin-top:12px"><input type="checkbox" name="welcome_enabled" ${settings.welcome_enabled === "0" ? "" : "checked"}> Post the welcome automatically</label>
          <p class="help" style="margin:12px 0 0">The picture is the <strong>Welcome picture</strong> under Banners and images below. Untick the switch and save to stop the welcome entirely.</p>
        </div>

        <div class="card">
          <div class="card-head"><div><h2>Banners and images</h2><p>PNG, JPEG, WebP or GIF up to 4 MB</p></div>${icon("upload")}</div>
          <div class="upload-grid">
            ${Object.entries(images).map(([slot, meta]) => uploadCard(slot, meta.label, meta.path)).join("")}
          </div>
          <p class="help" style="margin:12px 0 0">Uploads are stored on the bot's data volume and served at <span class="mono">${esc(config.public_base_url)}/media/uploads/…</span></p>
        </div>

        <div class="row" style="justify-content:flex-end">
          <label class="switch"><input type="checkbox" name="republish_panel" checked> Republish the panel after saving</label>
          <button type="submit" class="btn primary">Save configuration</button>
        </div>
      </form>

      <section class="card" style="margin-top:14px">
        <div class="card-head"><div><h2>Emoji assets</h2><p>emoji.gg artwork used by the bot and this dashboard${config.emoji_synced ? ` · ${config.emoji_synced} uploaded to the server` : ""}</p></div>${icon("check")}</div>
        <div class="emoji-strip">
          ${["ticket", "support", "moderator", "member", "lock", "unlock", "bell", "settings", "clock", "shield", "upload", "refresh", "home", "chart", "info", "check", "warning", "trash", "folder", "handshake", "media", "search", "calendar", "list", "cross", "staff"].map((name) => `
            <figure><img src="${EMOJI}${name}.png" alt="${name}"><figcaption>${name}</figcaption></figure>`).join("")}
        </div>
      </section>`;
  }

  async function loadConfig() {
    state.config = await api("/api/config");
    const guildReady = state.config.guild_id && state.config.bot_in_guild !== false;
    const loaders = [];
    if (guildReady) {
      loaders.push(api("/api/discord/channels").then((data) => {
        state.channels = data.channels || [];
        state.categories = data.categories || [];
      }).catch((error) => toast(error.message, "bad")));
      loaders.push(api("/api/discord/roles").then((data) => { state.roles = data.roles || []; }).catch(() => {}));
    }
    await Promise.all(loaders);
    if (state.view === "config") render();
  }

  async function refreshChannels() {
    const buttons = $$("[data-refresh-channels]");
    buttons.forEach((button) => { button.disabled = true; });
    try {
      const data = await api("/api/discord/channels?refresh=1");
      state.channels = data.channels || [];
      state.categories = data.categories || [];
      toast(`Channel list refreshed (${state.channels.length} channels).`);
      render();
    } catch (error) {
      toast(error.message, "bad");
      buttons.forEach((button) => { button.disabled = false; });
    }
  }

  async function refreshRoles() {
    const buttons = $$("[data-refresh-roles]");
    buttons.forEach((button) => { button.disabled = true; });
    try {
      const data = await api("/api/discord/roles?refresh=1");
      state.roles = data.roles || [];
      toast(`Role list refreshed (${state.roles.length} roles).`);
      render();
    } catch (error) {
      toast(error.message, "bad");
      buttons.forEach((button) => { button.disabled = false; });
    }
  }

  async function saveConfig(form) {
    const payload = {
      guild_id: form.guild_id.value.trim(),
      channel_id: form.channel_id.value,
      category_id: form.category_id.value,
      panel_channel_id: form.panel_channel_id.value,
      log_channel_id: form.log_channel_id.value,
      staff_channel_id: form.staff_channel_id.value,
      transcript_channel_id: form.transcript_channel_id.value,
      role_id: form.role_id.value,
      welcome_enabled: form.welcome_enabled.checked,
      welcome_channel_id: form.welcome_channel_id.value,
      welcome_message: form.welcome_message.value,
      republish_panel: form.republish_panel.checked,
    };
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const data = await api("/api/config", { method: "PUT", body: payload });
      toast(data.message || "Configuration saved.");
      await loadConfig();
      await loadOverview();
    } catch (error) {
      toast(error.message, "bad");
    } finally {
      button.disabled = false;
    }
  }

  async function uploadImage(slot, file) {
    const body = new FormData();
    body.append("file", file);
    try {
      const data = await api(`/api/images/${slot}`, { method: "POST", form: body });
      toast(data.message || "Image uploaded.");
      await loadConfig();
    } catch (error) {
      toast(error.message, "bad");
    }
  }

  async function removeImage(slot) {
    try {
      const data = await api(`/api/images/${slot}`, { method: "DELETE" });
      toast(data.message || "Image removed.");
      await loadConfig();
    } catch (error) {
      toast(error.message, "bad");
    }
  }

  /* ------------------------------------------------------------------ security */
  function securityView() {
    const bot = state.overview?.bot || {};
    const server = state.overview?.server || {};
    return `
      <section class="grid cols-2">
        <div class="card">
          <div class="card-head"><div><h2>Access</h2><p>Who can open this dashboard</p></div>${icon("shield")}</div>
          <dl class="kv">
            <dt>Signed in as</dt><dd>Dashboard administrator</dd>
            <dt>Method</dt><dd>Password (PBKDF2-SHA256, 240k iterations)</dd>
            <dt>Session</dt><dd>HttpOnly cookie, 12 hour sliding expiry</dd>
            <dt>CSRF</dt><dd>Token required on every write</dd>
            <dt>Login throttling</dt><dd>6 attempts per 5 minutes per address</dd>
            <dt>Server</dt><dd>${esc(server.name || "Not connected")} <span class="mono help">${esc(server.id || "")}</span></dd>
          </dl>
        </div>

        <div class="card">
          <div class="card-head"><div><h2>Change password</h2><p>Rotate the dashboard password</p></div>${icon("lock")}</div>
          <form id="password-form" class="stack" style="gap:10px">
            <label class="field"><span>Current password</span><input type="password" name="current" autocomplete="current-password"></label>
            <label class="field"><span>New password</span><input type="password" name="password" autocomplete="new-password"></label>
            <label class="field"><span>Confirm new password</span><input type="password" name="confirm" autocomplete="new-password"></label>
            <button type="submit" class="btn primary">Update password</button>
          </form>
        </div>
      </section>

      <section class="card" style="margin-top:14px">
        <div class="card-head"><div><h2>Security posture</h2><p>How this dashboard protects your bot</p></div>${icon("moderator")}</div>
        <ul class="feed">
          <li><span class="dot ticket"></span><span>The Discord bot token is only read by the server-side process. It is never sent to the browser or written to a page.</span></li>
          <li><span class="dot ticket"></span><span>Every dashboard action is re-validated on the server: IDs, activity text, durations and image types are all checked again.</span></li>
          <li><span class="dot ticket"></span><span>Uploads must be a real PNG, JPEG, WebP or GIF under 4 MB, verified by magic bytes, and are stored outside the web root.</span></li>
          <li><span class="dot ticket"></span><span>Sessions are held in memory only: restarting the dashboard signs everyone out.</span></li>
          <li><span class="dot ticket"></span><span>Bot status: ${bot.state === "online" ? "connected to Discord" : "not connected"} · token ${bot.token_configured ? "configured" : "missing"}.</span></li>
        </ul>
      </section>

      <section class="card" style="margin-top:14px">
        <div class="card-head"><div><h2>Data and privacy</h2><p>What the dashboard stores</p></div>${icon("list")}</div>
        <dl class="kv">
          <dt>Tickets</dt><dd>Stored in the bot's SQLite database on the server volume.</dd>
          <dt>Configuration</dt><dd>Server, channel and role IDs, plus uploaded image paths.</dd>
          <dt>Activity</dt><dd>The last few dashboard/bot events, kept locally for the overview feed.</dd>
          <dt>Not stored</dt><dd>Message contents, member data beyond ticket owners/staff, or the bot token in any file.</dd>
        </dl>
      </section>`;
  }

  async function loadSecurity() {
    if (!state.overview) {
      try { state.overview = await api("/api/overview"); } catch { /* ignore */ }
    }
    if (state.view === "security") render();
  }

  /* ------------------------------------------------------------------ events */
  function bindView() {
    const view = $("#view");

    if (state.view === "sleep" && window.SleepView) window.SleepView.bind(view);

    if (state.view === "overview") {
      $("#open-ai-page", view)?.addEventListener("click", () => enterView("ai"));
      loadConsole();
    }

    if (state.view === "tickets") {
      $$("[data-filter]", view).forEach((button) => {
        button.addEventListener("click", async () => {
          state.ticketFilter = button.dataset.filter;
          state.tickets = null;
          render();
          try { await loadTickets(); } catch (error) { toast(error.message, "bad"); }
        });
      });
      $$("[data-detail]", view).forEach((button) => {
        button.addEventListener("click", () => {
          const ticket = state.tickets.tickets.find((item) => String(item.id) === button.dataset.detail);
          if (ticket) ticketModal(ticket);
        });
      });
      $$("[data-close]", view).forEach((button) => {
        button.addEventListener("click", () => closeTicket(button.dataset.close));
      });
    }

    if (state.view === "status") {
      const presenceForm = $("#presence-form");
      presenceForm?.addEventListener("submit", async (event) => {
        event.preventDefault();
        const data = new FormData(presenceForm);
        try {
          const result = await api("/api/status/apply", {
            method: "POST",
            body: { activity: data.get("activity"), text: data.get("text"), status: data.get("status") },
          });
          toast(result.message || "Presence sent.");
          await loadStatus();
        } catch (error) { toast(error.message, "bad"); }
      });

      $("#rotation-toggle")?.addEventListener("change", async (event) => {
        try {
          await api("/api/status/rotation", { method: "POST", body: { enabled: event.target.checked } });
          toast(`Rotation ${event.target.checked ? "enabled" : "disabled"}.`);
          await loadStatus();
        } catch (error) { toast(error.message, "bad"); }
      });

      $("#rotation-form")?.addEventListener("submit", async (event) => {
        event.preventDefault();
        const form = event.target;
        const data = new FormData(form);
        try {
          await api("/api/status/entries", {
            method: "POST",
            body: {
              activity: data.get("activity"),
              text: data.get("text"),
              duration_seconds: Math.max(1, Number(data.get("minutes")) || 10) * 60,
            },
          });
          toast("Rotation entry added.");
          await loadStatus();
        } catch (error) { toast(error.message, "bad"); }
      });

      $$("[data-toggle-entry]", view).forEach((input) => {
        input.addEventListener("change", async () => {
          try {
            await api(`/api/status/entries/${input.dataset.toggleEntry}`, {
              method: "PATCH",
              body: { enabled: input.checked },
            });
            await loadStatus();
          } catch (error) { toast(error.message, "bad"); }
        });
      });

      $$("[data-delete-entry]", view).forEach((button) => {
        button.addEventListener("click", async () => {
          try {
            await api(`/api/status/entries/${button.dataset.deleteEntry}`, { method: "DELETE" });
            toast("Rotation entry removed.");
            await loadStatus();
          } catch (error) { toast(error.message, "bad"); }
        });
      });
    }

    if (state.view === "config") {
      $("#config-form")?.addEventListener("submit", (event) => {
        event.preventDefault();
        saveConfig(event.target);
      });
      $$("[data-refresh-channels]", view).forEach((button) => button.addEventListener("click", refreshChannels));
      $$("[data-refresh-roles]", view).forEach((button) => button.addEventListener("click", refreshRoles));
      $$("[data-upload]", view).forEach((input) => {
        input.addEventListener("change", () => {
          if (input.files?.[0]) uploadImage(input.dataset.upload, input.files[0]);
        });
      });
      $$("[data-remove-image]", view).forEach((button) => {
        button.addEventListener("click", () => removeImage(button.dataset.removeImage));
      });
    }

    if (state.view === "modules" && window.ModulesView) window.ModulesView.bind(view);

    if (state.view === "ai" && window.AIView) window.AIView.bind(view);

    if (state.view === "security") {
      $("#password-form")?.addEventListener("submit", async (event) => {
        event.preventDefault();
        const form = event.target;
        if (form.password.value !== form.confirm.value) {
          toast("The two new passwords do not match.", "bad");
          return;
        }
        try {
          await api("/api/auth/password", {
            method: "POST",
            body: { current: form.current.value, password: form.password.value },
          });
          toast("Password updated.");
          form.reset();
        } catch (error) { toast(error.message, "bad"); }
      });
    }

    $("#modal-root").addEventListener("click", (event) => {
      const target = event.target;
      if (target.matches("[data-close-modal]") || target.classList.contains("modal-backdrop")) {
        $("#modal-root").innerHTML = "";
        return;
      }
      const closeButton = target.closest("[data-close]");
      if (closeButton) closeTicket(closeButton.dataset.close);
    });
  }

  async function refreshCurrentView() {
    try {
      switch (state.view) {
        case "sleep": if (window.SleepView) await window.SleepView.load(); break;
        case "overview": await loadOverview(); break;
        case "tickets": await loadTickets(); break;
        case "status": await loadStatus(); break;
        case "config": await loadConfig(); break;
        case "security": await loadSecurity(); break;
        case "modules": await window.ModulesView.load(); break;
        case "ai": await window.AIView.load(); break;
      }
      render();
    } catch (error) {
      toast(error.message, "bad");
    }
  }

  /* ------------------------------------------------------------------ bootstrap */
  async function boot() {
    const info = await api("/api/bootstrap");
    state.oauthConfigured = Boolean(info.oauth_configured);
    if (!info.authenticated) {
      showAuth(info.setup_required ? "setup" : "login");
      return;
    }
    state.csrf = info.csrf;
    $("#auth").hidden = true;
    $("#app").hidden = false;
    try {
      await loadOverview();
    } catch (error) {
      toast(error.message, "bad");
    }
    setView(state.view);
  }

  async function enterView(view) {
    setView(view);
    render();
    try {
      if (view === "sleep" && window.SleepView) await window.SleepView.load();
      if (view === "overview") await loadOverview();
      if (view === "tickets") await loadTickets();
      if (view === "status") await loadStatus();
      if (view === "config") await loadConfig();
      if (view === "security") await loadSecurity();
      if (view === "modules" && window.ModulesView) await window.ModulesView.load();
      if (view === "ai" && window.AIView) await window.AIView.load();
    } catch (error) {
      toast(error.message, "bad");
    }
    render();
  }

  // Small bridge so the AI Control System module shares these helpers.
  window.Dashboard = {
    api,
    esc,
    toast,
    icon,
    fmtDate,
    fmtRelative,
    fmtDuration,
    get view() { return state.view; },
    rerender: render,
  };

  document.addEventListener("DOMContentLoaded", () => {
    $$("#nav button").forEach((button) => {
      button.addEventListener("click", () => enterView(button.dataset.view));
    });
    $("#auth-form").addEventListener("submit", submitAuth);
    $("#signout").addEventListener("click", signOut);
    $("#refresh-view").addEventListener("click", refreshCurrentView);
    boot().catch((error) => showAuth("login", error.message));

    // Keep the overview fresh while it is visible.
    setInterval(async () => {
      if (state.view !== "overview" || $("#app").hidden) return;
      try { await loadOverview(); render(); } catch { /* ignore */ }
    }, 20000);
  });
})();
