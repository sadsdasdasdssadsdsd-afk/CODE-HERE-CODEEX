/* Card modules - dashboard module for the automated infraction, promotion,
   feedback, session shutdown and welcome cards. */
(() => {
  "use strict";

  const mod = { data: null, channels: [], roles: [], saving: false, uploading: "" };

  const api = (path, options) => window.Dashboard.api(path, options);
  const esc = (value) => window.Dashboard.esc(value);
  const icon = (name, cls) => window.Dashboard.icon(name, cls);
  const toast = (message, tone) => window.Dashboard.toast(message, tone);
  const fmtDate = (value) => window.Dashboard.fmtDate(value);

  const packEmoji = (name) => `<img class="dc-inline-icon" src="/media/emoji/${name}.png" alt="">`;

  const md = (text) =>
    esc(text)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|\s)(@[A-Za-z0-9_.\-]+)/g, '$1<span class="dc-mention">$2</span>')
      .replace(/\n/g, "<br>");

  const channelName = (id) => {
    const channel = mod.channels.find((item) => String(item.id) === String(id));
    return channel ? `#${channel.name}` : `#${id}`;
  };
  const channelSelect = (name, current, blank) => {
    const rows = mod.channels
      .filter((item) => item.type === 0 || item.type === 5)
      .map((channel) => `<option value="${esc(channel.id)}" ${String(current) === String(channel.id) ? "selected" : ""}>${esc(channel.name)}</option>`);
    return `<select name="${name}">${(blank ? `<option value="">${esc(blank)}</option>` : "") + rows.join("")}</select>`;
  };
  const channelMulti = (name, selected, size = 4) => {
    const chosen = new Set((selected || []).map(String));
    const rows = mod.channels
      .filter((item) => item.type === 0 || item.type === 5)
      .map((channel) => `<option value="${esc(channel.id)}" ${chosen.has(String(channel.id)) ? "selected" : ""}>${esc(channel.name)}</option>`);
    return `<select name="${name}" multiple size="${size}" class="multi">${rows.join("") || '<option value="">No channels found</option>'}</select>`;
  };
  const roleMulti = (name, selected, size = 4) => {
    const chosen = new Set((selected || []).map(String));
    const rows = mod.roles.map((role) => `<option value="${esc(role.id)}" ${chosen.has(String(role.id)) ? "selected" : ""}>${esc(role.name)}</option>`);
    return `<select name="${name}" multiple size="${size}" class="multi">${rows.join("") || '<option value="">No roles found</option>'}</select>`;
  };
  const multiValues = (select) => Array.from(select.selectedOptions || []).map((option) => option.value).filter(Boolean);

  function loading() {
    return `<div class="stack">${Array.from({ length: 3 }, () => '<div class="skeleton" style="height:64px"></div>').join("")}</div>`;
  }

  /* ------------------------------------------------------------------ banners */
  function bannerGrid() {
    const settings = (mod.data && mod.data.settings) || {};
    const kinds = settings.kinds || [];
    return `<div class="banner-grid">${kinds
      .map((kind) => {
        const version = kind.banner_image || "artwork";
        const busy = mod.uploading === kind.kind;
        return `<figure class="banner-tile">
          <a class="banner-shot" href="/api/modules/banner/${esc(kind.kind)}" target="_blank" rel="noopener" title="Open the full-size ${esc(kind.label)} banner">
            <img src="/api/modules/banner/${esc(kind.kind)}?v=${encodeURIComponent(version)}" alt="${esc(kind.label)} banner" loading="lazy">
          </a>
          <figcaption>
            <div class="banner-text">
              <strong>${esc(kind.label)}</strong>
              <span class="help">${esc(kind.banner_image ? "Custom image" : "Generated from your branding")}</span>
            </div>
            <div class="banner-actions">
              <label class="btn sm ghost${busy ? " busy" : ""}">${busy ? "Uploading…" : "Replace image"}
                <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" data-banner-upload="${esc(kind.kind)}" hidden>
              </label>
              ${kind.banner_image ? `<button type="button" class="btn sm ghost" data-banner-reset="${esc(kind.kind)}">Use artwork</button>` : ""}
            </div>
          </figcaption>
        </figure>`;
      })
      .join("")}</div>`;
  }

  /* ------------------------------------------------------------------ cards */
  function discordCard(card) {
    const fields = (card.fields || [])
      .map((field) => `<div class="dc-field"><span>${md(`${field.label}:`)}</span> <span>${md(field.value)}</span></div>`)
      .join("");
    const box = card.box_text
      ? `<p class="dc-box-label">${packEmoji("folder")} <strong>${esc(card.box_label)}</strong></p><blockquote class="dc-box">${md(card.box_text)}</blockquote>`
      : "";
    const link = card.link_label
      ? `<div class="dc-action"><span class="dc-button">${esc(card.link_label)} <span aria-hidden="true">↗</span></span></div>`
      : "";
    return `<article class="dc-card" style="--dc-accent: ${esc(card.accent)}">
      <img class="dc-banner" src="${esc(card.banner_url)}" alt="${esc(card.label)} banner" loading="lazy">
      <div class="dc-body">
        <h3 class="dc-title">${esc(card.title)}</h3>
        ${card.subtitle ? `<p class="dc-subtitle">${esc(card.subtitle)}</p>` : ""}
        <p class="dc-description">${md(card.description)}</p>
        ${fields ? `<div class="dc-fields">${fields}</div>` : ""}
        ${box}
        ${link}
        <p class="dc-footer">${esc(card.footer)}</p>
      </div>
      <footer class="dc-meta"><code>${esc(card.command)}</code><span>${esc(card.label)}</span></footer>
    </article>`;
  }

  function gallery() {
    const cards = (mod.data && mod.data.preview) || [];
    if (!cards.length) {
      return `<p class="help">Every module is switched off below — turn one on to see its card.</p>`;
    }
    return `<div class="dc-grid">${cards.map(discordCard).join("")}</div>`;
  }

  /* ------------------------------------------------------------------ settings */
  function brandingForm() {
    const settings = (mod.data && mod.data.settings) || {};
    return `<div class="stack">
      <div class="grid cols-2">
        <label class="field"><span>Server label on the banner</span><input type="text" name="server_label" maxlength="48" value="${esc(settings.server_label || "")}" placeholder="Uses the Discord server name"></label>
        <label class="field"><span>Banner tagline</span><input type="text" name="banner_tagline" maxlength="60" value="${esc(settings.banner_tagline || "")}" placeholder="Experience Cali Like Never Before"></label>
        <label class="field"><span>Brand colour</span><input type="text" name="brand_color" maxlength="7" value="${esc(settings.brand_color || "#FCC200")}" placeholder="#FCC200"></label>
        <label class="field"><span>Dashboard link (welcome button)</span><input type="text" name="dashboard_url" maxlength="400" value="${esc(settings.dashboard_url || "")}" placeholder="https://…"></label>
      </div>
      <p class="help" style="margin:0">Every banner is generated from these four values — the colour sets the gradient and the ink, the label becomes the mark. Upload artwork to replace any single banner; cards keep the same title, tagline and layout either way.</p>
    </div>`;
  }

  function deliveryForm() {
    const settings = (mod.data && mod.data.settings) || {};
    return `<div class="grid cols-2">
      <label class="field"><span>Welcome channel</span>${channelSelect("welcome_channel_id", settings.welcome_channel_id, "Not set")}</label>
      <label class="field"><span>Welcome message</span><input type="text" name="welcome_message" maxlength="320" value="${esc(settings.welcome_message || "")}" placeholder="{user} and {server} are replaced"></label>
      <label class="field"><span>Forward to (up to 10 channels)</span>${channelMulti("forward_channel_ids", settings.forward_channel_ids)}</label>
      <label class="field"><span>Extra staff roles</span>${roleMulti("staff_role_ids", settings.staff_role_ids)}</label>
    </div>
    <p class="help" style="margin:12px 0 0">Founder, Co-Founder, Assistant Founder, Board of Directors, and the Management ranks always keep access; Discord administrators and Manage Server do too. Forwarded cards keep the same banner and text in every channel.</p>`;
  }

  function settingsForm() {
    const settings = (mod.data && mod.data.settings) || {};
    const kinds = settings.kinds || [];
    return `<form id="modules-form" class="stack">
      <section class="card">
        <div class="card-head"><div><h2>Banners</h2><p>Artwork for the five cards — generated, or replaced with your own image</p></div>${icon("media")}</div>
        ${brandingForm()}
        ${bannerGrid()}
      </section>

      <section class="card">
        <div class="card-head"><div><h2>Delivery and permissions</h2><p>Where cards land and who may publish them</p></div>${icon("upload")}</div>
        ${deliveryForm()}
      </section>

      <section class="card">
        <div class="card-head"><div><h2>Modules</h2><p>Switch a card type off to stop publishing it</p></div>${icon("list")}</div>
        <div class="module-toggles">
          ${kinds
            .map(
              (kind) =>
                `<label class="switch"><input type="checkbox" data-kind-toggle="${esc(kind.kind)}" ${kind.enabled ? "checked" : ""}> <strong>${esc(kind.label)}</strong> <span class="help">${esc(kind.command)}</span></label>`
            )
            .join("")}
        </div>
      </section>

      <div class="row-actions">
        <button type="submit" class="btn primary" ${mod.saving ? "disabled" : ""}>${mod.saving ? "Saving…" : "Save card settings"}</button>
        <span class="help">Publish from Discord with <code>/infraction issue</code> — <code>/cards preview</code> shows all five privately.</span>
      </div>
    </form>`;
  }

  /* ------------------------------------------------------------------ entries */
  function entriesTable() {
    const entries = (mod.data && mod.data.entries) || [];
    if (!entries.length) return `<p class="help">No cards published yet. They appear here the moment staff use a card command.</p>`;
    return `<table class="table">
      <thead><tr><th>Card</th><th>Summary</th><th>Channel</th><th>Forwarded</th><th>Published</th></tr></thead>
      <tbody>${entries
        .map(
          (entry) => `<tr>
          <td><span class="pill neutral"><i></i>${esc(entry.label)}</span></td>
          <td>${esc(entry.summary)}</td>
          <td>${esc(channelName(entry.channel_id))}</td>
          <td>${entry.forwarded}${entry.failures ? ` <span class="pill bad"><i></i>${entry.failures} failed</span>` : ""}</td>
          <td class="num">${esc(fmtDate(entry.created_at))}</td>
        </tr>`
        )
        .join("")}</tbody></table>`;
  }

  /* ------------------------------------------------------------------ view */
  function render() {
    if (!mod.data) return loading();
    const settings = mod.data.settings || {};
    const enabledCount = (settings.kinds || []).filter((kind) => kind.enabled).length;
    const forwarding = (settings.forward_channel_ids || []).length;
    const custom = (settings.kinds || []).filter((kind) => kind.banner_image).length;
    return `<div class="stack">
      <section class="card">
        <div class="card-head">
          <div><h2>Five automated cards</h2><p>Generated by the bot from your branding and forwarded everywhere you choose</p></div>
          <span class="pill ${enabledCount ? "good" : "waiting"}"><i></i>${enabledCount} of 5 live</span>
        </div>
        <div class="dc-chips">
          <span class="chip">Server: ${esc(settings.server_label || mod.data.server_label || "—")}</span>
          <span class="chip">Tagline: ${esc(settings.banner_tagline || "—")}</span>
          <span class="chip">Brand: ${esc(settings.brand_color || "—")}</span>
          <span class="chip">Banners: ${custom ? `${custom} custom` : "generated"}</span>
          <span class="chip">Forwarding: ${forwarding ? `${forwarding} channel${forwarding === 1 ? "" : "s"}` : "off"}</span>
          <span class="chip">Welcome: ${settings.welcome_channel_id ? esc(channelName(settings.welcome_channel_id)) : "not set"}</span>
        </div>
      </section>

      ${settingsForm()}

      <section class="card">
        <div class="card-head"><div><h2>Live preview</h2><p>Rendered from your settings with this server's own members and roles</p></div>${icon("list")}</div>
        ${gallery()}
      </section>

      <section class="card">
        <div class="card-head"><div><h2>Recently published</h2><p>Every card is logged for auditing</p></div>${icon("clock")}</div>
        ${entriesTable()}
      </section>
    </div>`;
  }

  async function load() {
    const data = await api("/api/modules");
    mod.data = data;
    try {
      const result = await api("/api/discord/channels");
      mod.channels = result.channels || [];
    } catch { mod.channels = []; }
    try {
      const result = await api("/api/discord/roles");
      mod.roles = result.roles || [];
    } catch { mod.roles = []; }
    window.Dashboard.rerender();
  }

  async function uploadBanner(view, kind, file) {
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    mod.uploading = kind;
    window.Dashboard.rerender();
    try {
      mod.data = await api(`/api/modules/banner/${kind}`, { method: "POST", form: body });
      toast("Banner image uploaded.");
    } catch (error) {
      toast(error.message, "bad");
    } finally {
      mod.uploading = "";
      window.Dashboard.rerender();
    }
  }

  function bind(view) {
    view.querySelectorAll("[data-banner-upload]").forEach((input) => {
      input.addEventListener("change", () => {
        uploadBanner(view, input.dataset.bannerUpload, input.files && input.files[0]);
      });
    });
    view.querySelectorAll("[data-banner-reset]").forEach((button) => {
      button.addEventListener("click", async () => {
        const kind = button.dataset.bannerReset;
        try {
          mod.data = await api(`/api/modules/banner/${kind}`, { method: "DELETE" });
          toast("Back to the generated artwork.");
        } catch (error) {
          toast(error.message, "bad");
        }
        window.Dashboard.rerender();
      });
    });

    const form = view.querySelector("#modules-form");
    if (!form) return;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (mod.saving) return;
      mod.saving = true;
      window.Dashboard.rerender();
      const enabled = {};
      view.querySelectorAll("[data-kind-toggle]").forEach((input) => {
        enabled[input.dataset.kindToggle] = input.checked;
      });
      const payload = {
        banner_tagline: form.banner_tagline.value,
        server_label: form.server_label.value,
        brand_color: form.brand_color.value,
        dashboard_url: form.dashboard_url.value,
        welcome_channel_id: form.welcome_channel_id.value,
        welcome_message: form.welcome_message.value,
        forward_channel_ids: multiValues(form.forward_channel_ids),
        staff_role_ids: multiValues(form.staff_role_ids),
        enabled,
      };
      try {
        mod.data = await api("/api/modules", { method: "PUT", body: payload });
        toast("Card settings saved.");
      } catch (error) {
        toast(error.message, "bad");
      } finally {
        mod.saving = false;
        window.Dashboard.rerender();
      }
    });
  }

  window.ModulesView = { render, load, bind };
})();
