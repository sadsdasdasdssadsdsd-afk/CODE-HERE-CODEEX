/* Sleep mode - the page you leave open. It polls the dashboard for the bot's
   live state and holds a browser wake lock so the machine running the bot does
   not fall asleep while nobody is using it. */
(() => {
  "use strict";

  const POLL_MS = 15000;
  const HISTORY = 8;

  const mod = {
    bot: null,
    error: "",
    checkedAt: null,
    streak: 0,
    history: [],
    timer: null,
    wantAwake: false,
    wakeState: "off",
    wakeError: "",
    listening: false,
  };

  const helpers = () => window.Dashboard;
  const esc = (value) => helpers().esc(value);
  const icon = (name, cls) => helpers().icon(name, cls);
  const fmtDuration = (value) => helpers().fmtDuration(value);

  const TONE = { online: "good", starting: "waiting", "missing-token": "waiting" };
  const LABEL = {
    online: "Bot online",
    starting: "Connecting to Discord",
    "missing-token": "Waiting for DISCORD_TOKEN",
    failed: "Stopped with an error",
    stopped: "Bot process stopped",
  };
  const WAKE_TEXT = {
    off: "Off — the display follows the machine's normal power settings.",
    on: "On — this tab is holding the display awake.",
    hidden: "Paused while the tab was hidden; it resumes as soon as the tab is visible.",
    released: "The browser released it; press the button again to take it back.",
    denied: "The browser refused the wake lock.",
    unsupported: "This browser has no wake-lock support. Use Chrome or Edge.",
  };

  function clockTime(date) {
    if (!date) return "—";
    return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  /* ------------------------------------------------------------------ render */
  function render() {
    const bot = mod.bot;
    const tone = bot ? TONE[bot.state] || "bad" : "neutral";
    const label = bot ? LABEL[bot.state] || "Unknown state" : "Checking…";
    const wakeTone = mod.wakeState === "on" ? "good" : mod.wakeState === "denied" ? "bad" : "neutral";
    const wakeText = mod.wakeState === "denied" && mod.wakeError
      ? `${WAKE_TEXT.denied} ${mod.wakeError}`
      : WAKE_TEXT[mod.wakeState] || "";

    return `
      <section class="grid stats">
        <dl class="stat">
          <dt>Bot</dt>
          <dd><span class="pill ${tone}"><i></i>${esc(label)}</span></dd>
          <small>${bot?.identity ? esc(bot.identity) : "No bot account connected yet"}</small>
        </dl>
        <dl class="stat">
          <dt>Uptime</dt>
          <dd>${bot ? fmtDuration(bot.uptime_seconds) : "—"}</dd>
          <small>${bot ? `${bot.restarts} restart${bot.restarts === 1 ? "" : "s"} since the dashboard started` : "Waiting for the first check"}</small>
        </dl>
        <dl class="stat">
          <dt>Gateway latency</dt>
          <dd>${bot?.latency_ms ? `${bot.latency_ms} ms` : "—"}</dd>
          <small>Reported by the running bot</small>
        </dl>
        <dl class="stat">
          <dt>Keep-alive checks</dt>
          <dd>${mod.streak}</dd>
          <small>Last answer ${clockTime(mod.checkedAt)}</small>
        </dl>
      </section>

      <section class="grid cols-2" style="margin-top:14px">
        <div class="card">
          <div class="card-head">
            <div><h2>Keep this computer awake</h2><p>Stops the display sleeping while this tab stays open</p></div>
            ${icon("refresh")}
          </div>
          <p style="margin:0 0 10px"><span class="pill ${wakeTone}"><i></i>${mod.wantAwake ? "Requested" : "Off"}</span></p>
          <p class="help" style="margin:0 0 14px">${esc(wakeText)}</p>
          <button class="btn ${mod.wantAwake ? "" : "primary"}" id="wake-toggle">${mod.wantAwake ? "Stop keeping awake" : "Keep this computer awake"}</button>
          <p class="help" style="margin:14px 0 0">Wake locks need https or localhost and a visible tab. If the machine still sleeps, turn its sleep timer off in the OS power settings.</p>
        </div>

        <div class="card">
          <div class="card-head">
            <div><h2>Keep-alive</h2><p>This page asks the dashboard for the bot's state every ${POLL_MS / 1000} seconds</p></div>
            ${icon("clock")}
          </div>
          ${mod.error ? `<p class="help" style="color:var(--bad);margin:0 0 12px">${esc(mod.error)}</p>` : ""}
          ${mod.history.length
            ? `<ul class="feed">${mod.history.map((item) => `
                <li>
                  <span class="dot ${item.ok ? "ok" : "fail"}"></span>
                  <span>${item.ok ? `Answered · bot ${esc(item.state || "unknown")}` : "No answer from the dashboard"}</span>
                  <time>${clockTime(item.at)}</time>
                </li>`).join("")}</ul>`
            : '<div class="empty">Waiting for the first check…</div>'}
          <p class="help" style="margin:14px 0 0">The dashboard restarts the bot by itself when the process dies, so keeping this page and the machine running is enough.</p>
        </div>
      </section>`;
  }

  /* ------------------------------------------------------------------ polling */
  function note(ok, bot) {
    mod.checkedAt = new Date();
    if (ok) {
      mod.bot = bot;
      mod.streak += 1;
      mod.error = "";
    } else {
      mod.streak = 0;
    }
    mod.history.unshift({ ok, at: mod.checkedAt, state: bot?.state || "" });
    mod.history.length = Math.min(mod.history.length, HISTORY);
  }

  async function load() {
    try {
      const data = await helpers().api("/api/sleep");
      note(true, data.bot);
    } catch (error) {
      mod.error = error.message;
      note(false, null);
    }
  }

  async function poll() {
    await load();
    if (helpers().view === "sleep") helpers().rerender();
  }

  function bind(view) {
    view.querySelector("#wake-toggle")?.addEventListener("click", toggle);
    if (!mod.listening) {
      mod.listening = true;
      document.addEventListener("visibilitychange", () => {
        if (mod.wantAwake && document.visibilityState === "visible") attach();
      });
    }
    if (!mod.timer) mod.timer = setInterval(poll, POLL_MS);
  }

  function stop() {
    if (mod.timer) clearInterval(mod.timer);
    mod.timer = null;
  }

  /* ------------------------------------------------------------------ wake lock */
  async function toggle() {
    if (mod.wantAwake) {
      mod.wantAwake = false;
      mod.wakeState = "off";
      mod.wakeError = "";
      const lock = mod.wakeLock;
      mod.wakeLock = null;
      if (lock) {
        try { await lock.release(); } catch { /* already released */ }
      }
    } else {
      mod.wantAwake = true;
      await attach();
    }
    helpers().rerender();
  }

  async function attach() {
    if (!("wakeLock" in navigator)) {
      mod.wantAwake = false;
      mod.wakeState = "unsupported";
      return;
    }
    try {
      const lock = await navigator.wakeLock.request("screen");
      mod.wakeLock = lock;
      mod.wakeState = "on";
      mod.wakeError = "";
      lock.addEventListener("release", () => {
        mod.wakeLock = null;
        if (!mod.wantAwake) return;
        mod.wakeState = document.visibilityState === "visible" ? "released" : "hidden";
      });
    } catch (error) {
      mod.wantAwake = false;
      mod.wakeState = "denied";
      mod.wakeError = error?.message || "";
    }
  }

  window.SleepView = { render, bind, load, stop };
})();
