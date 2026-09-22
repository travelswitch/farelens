/* FareLens admin UI. Vanilla JS, no build step. */
(() => {
  "use strict";

  // ------------------------------------------------------------------ utils
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtInt = (n) => (n == null ? "–" : Number(n).toLocaleString());
  const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString() : "–");
  const fmtMs = (n) => (n == null ? "–" : `${fmtInt(n)} ms`);
  const RTL = new Set(["ar", "ur", "fa", "he"]);

  function toast(message, kind = "info", ms = 3800) {
    const el = document.createElement("div");
    el.className = `toast toast-${kind}`;
    el.textContent = message;
    $("#toasts").appendChild(el);
    setTimeout(() => el.remove(), ms);
  }

  function modal(html) {
    const root = $("#modal-root");
    root.innerHTML = `<div class="modal-backdrop"><div class="modal" role="dialog" aria-modal="true">${html}</div></div>`;
    const close = () => (root.innerHTML = "");
    root.querySelector(".modal-backdrop").addEventListener("click", (e) => {
      if (e.target.classList.contains("modal-backdrop")) close();
    });
    $$("[data-close]", root).forEach((b) => b.addEventListener("click", close));
    return { close, root };
  }

  async function confirmDialog(title, body, okLabel = "Confirm", danger = false) {
    return new Promise((resolve) => {
      const m = modal(`
        <h3>${esc(title)}</h3>
        <p>${body}</p>
        <div class="form-actions">
          <button class="btn ${danger ? "btn-danger" : "btn-primary"}" data-ok>${esc(okLabel)}</button>
          <button class="btn" data-close>Cancel</button>
        </div>`);
      $("[data-ok]", m.root).addEventListener("click", () => { m.close(); resolve(true); });
      $("[data-close]", m.root).addEventListener("click", () => resolve(false));
    });
  }

  // ------------------------------------------------------------------- api
  class ApiError extends Error {
    constructor(status, payload) {
      super((payload && payload.error && payload.error.message) || `HTTP ${status}`);
      this.status = status;
      this.payload = payload;
      this.code = payload && payload.error && payload.error.code;
    }
  }

  async function request(method, url, body) {
    const res = await fetch(url, {
      method,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
    if (!res.ok) {
      if (res.status === 401 && state.user && !url.includes("/auth/login")) {
        state.user = null;
        showLogin();
      }
      throw new ApiError(res.status, data);
    }
    return data;
  }
  const api = {
    get: (u) => request("GET", u),
    post: (u, b) => request("POST", u, b ?? {}),
    put: (u, b) => request("PUT", u, b ?? {}),
    del: (u) => request("DELETE", u),
  };

  // ----------------------------------------------------------------- state
  const state = { user: null, overview: null, providers: null };

  // ------------------------------------------------------------- markdown
  function renderMarkdown(src) {
    const lines = String(src || "").replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let i = 0;
    const inline = (t) =>
      esc(t)
        .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
        .replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, "$1<em>$2</em>")
        .replace(/`([^`]+)`/g, "<code>$1</code>");
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      const h = line.match(/^(#{1,4})\s+(.*)$/);
      if (h) { out.push(`<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`); i++; continue; }
      const isRow = (l) => l.includes("|") && !/^\s*([-*•]|\d+\.)\s+/.test(l);
      const isSep = (l) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
      if (isRow(line) && i + 1 < lines.length && isSep(lines[i + 1])) {
        const rows = [];
        while (i < lines.length && isRow(lines[i])) { rows.push(lines[i]); i++; }
        const cells = (r) => r.trim().replace(/^\|/, "").replace(/\|$/, "").trim().replace(/^\*\*(.*)\*\*$/, "$1").split("|").map((c) => inline(c.trim()));
        const head = cells(rows[0]);
        const body = rows.slice(2).map(cells);
        out.push(`<table><thead><tr>${head.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${body
          .map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
        continue;
      }
      if (/^\s*([-*•]|\d+\.)\s+/.test(line)) {
        const items = [];
        while (i < lines.length && /^\s*([-*•]|\d+\.)\s+/.test(lines[i])) {
          items.push(lines[i].replace(/^\s*([-*•]|\d+\.)\s+/, ""));
          i++;
        }
        out.push(`<ul>${items.map((t) => `<li>${inline(t)}</li>`).join("")}</ul>`);
        continue;
      }
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|\s*([-*•]|\d+\.)\s)/.test(lines[i]) && !(isRow(lines[i]) && i + 1 < lines.length && isSep(lines[i + 1]))) { para.push(lines[i]); i++; }
      if (para.length) out.push(`<p>${inline(para.join(" "))}</p>`);
      else i++;
    }
    return out.join("\n");
  }

  // ---------------------------------------------------------------- chart
  function barChart(container, days, series) {
    // series: [{key, label, color}] stacked bars; days: [{day, ...}]
    const W = 800, H = 240, padL = 44, padR = 10, padT = 14, padB = 34;
    const n = days.length || 1;
    const max = Math.max(1, ...days.map((d) => series.reduce((s, sr) => s + Number(d[sr.key] || 0), 0)));
    const step = (W - padL - padR) / n;
    const bw = Math.max(2, step * 0.62);
    const y = (v) => padT + (H - padT - padB) * (1 - v / max);
    const ticks = 4;
    let svg = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Daily requests">`;
    for (let t = 0; t <= ticks; t++) {
      const v = (max / ticks) * t;
      svg += `<line x1="${padL}" x2="${W - padR}" y1="${y(v)}" y2="${y(v)}" style="stroke:var(--border);stroke-width:1"/>`;
      svg += `<text x="${padL - 6}" y="${y(v) + 4}" text-anchor="end" style="font-size:11px;fill:var(--text-muted)">${Math.round(v)}</text>`;
    }
    days.forEach((d, idx) => {
      let acc = 0;
      const x = padL + idx * step + (step - bw) / 2;
      series.forEach((sr) => {
        const v = Number(d[sr.key] || 0);
        if (!v) return;
        const y1 = y(acc + v), y0 = y(acc);
        svg += `<rect x="${x}" y="${y1}" width="${bw}" height="${Math.max(0, y0 - y1)}" style="fill:${sr.color}" rx="2" data-i="${idx}"/>`;
        acc += v;
      });
      svg += `<rect x="${padL + idx * step}" y="${padT}" width="${step}" height="${H - padT - padB}" fill="transparent" data-i="${idx}"/>`;
      const every = n <= 10 ? 1 : Math.ceil(n / 10);
      if (idx % every === 0 || idx === n - 1) {
        const label = d.day.slice(5);
        svg += `<text x="${x + bw / 2}" y="${H - 12}" text-anchor="middle" style="font-size:10.5px;fill:var(--text-muted)">${label}</text>`;
      }
    });
    svg += "</svg>";
    container.innerHTML = svg + `<div class="chart-legend">${series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.label)}</span>`).join("")}</div>`;
    let tip = $(".chart-tooltip");
    if (!tip) { tip = document.createElement("div"); tip.className = "chart-tooltip"; document.body.appendChild(tip); }
    container.querySelectorAll("[data-i]").forEach((el) => {
      el.addEventListener("mousemove", (e) => {
        const d = days[Number(el.dataset.i)];
        tip.style.display = "block";
        tip.style.left = `${e.clientX + 12}px`;
        tip.style.top = `${e.clientY + 12}px`;
        tip.innerHTML = `<strong>${esc(d.day)}</strong><br>${series.map((s) => `${esc(s.label)}: ${fmtInt(d[s.key])}`).join("<br>")}<br>tokens: ${fmtInt(Number(d.prompt_tokens || 0) + Number(d.completion_tokens || 0))}`;
      });
      el.addEventListener("mouseleave", () => (tip.style.display = "none"));
    });
  }

  // ------------------------------------------------------------- routing
  const routes = {
    overview: { title: "Overview", render: renderOverview },
    usage: { title: "Usage", render: renderUsage },
    llm: { title: "LLM provider", render: renderLLM },
    "api-keys": { title: "API keys", render: renderApiKeys },
    datastores: { title: "Data stores", render: renderDatastores },
    prompts: { title: "Prompts", render: renderPrompts },
    playground: { title: "Playground", render: renderPlayground },
    account: { title: "Account", render: renderAccount },
  };

  function currentRoute() {
    const name = (location.hash || "#/overview").replace(/^#\/?/, "").split("?")[0];
    return routes[name] ? name : "overview";
  }

  async function navigate() {
    if (!state.user) return;
    const name = currentRoute();
    $$(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === name));
    $("#page-title").textContent = routes[name].title;
    const page = $("#page");
    page.innerHTML = `<p class="muted"><span class="spinner"></span> Loading…</p>`;
    try {
      await routes[name].render(page);
    } catch (err) {
      page.innerHTML = `<div class="card"><p class="form-error">${esc(err.message)}</p></div>`;
    }
  }

  // ------------------------------------------------------------- auth flow
  function showLogin() {
    $("#view-app").hidden = true;
    $("#view-login").hidden = false;
    $("#login-form").querySelector("input[name=username]").focus();
  }

  async function showApp() {
    $("#view-login").hidden = true;
    $("#view-app").hidden = false;
    $("#topbar-user").textContent = state.user.username;
    $("#password-banner").hidden = !state.user.must_change_password;
    await refreshOverview();
    await navigate();
  }

  async function refreshOverview() {
    try {
      state.overview = await api.get("/api/admin/overview");
      $("#app-version").textContent = `v${state.overview.version}`;
      $("#llm-banner").hidden = !!(state.overview.llm && state.overview.llm.configured);
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 401)) toast(err.message, "error");
    }
  }

  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const errEl = $("#login-error");
    errEl.hidden = true;
    const btn = form.querySelector("button");
    btn.disabled = true;
    try {
      const data = await api.post("/api/admin/auth/login", { username: form.username.value.trim(), password: form.password.value });
      state.user = data.user;
      form.password.value = "";
      await showApp();
    } catch (err) {
      errEl.textContent = err.message;
      errEl.hidden = false;
    } finally {
      btn.disabled = false;
    }
  });

  $("#logout-btn").addEventListener("click", async () => {
    try { await api.post("/api/admin/auth/logout"); } catch { /* ignore */ }
    state.user = null;
    showLogin();
  });

  window.addEventListener("hashchange", navigate);

  // ------------------------------------------------------------- overview
  const PROVIDER_META = {
    openai: { short: "OA", label: "OpenAI" },
    azure_openai: { short: "AZ", label: "Azure AI" },
    anthropic: { short: "CL", label: "Anthropic" },
    google: { short: "GG", label: "Google Gemini" },
    groq: { short: "GQ", label: "Groq" },
    bedrock: { short: "AWS", label: "AWS Bedrock" },
  };

  function sparkline(days, key, color) {
    const vals = days.map((d) => Number(d[key] || 0));
    const max = Math.max(1, ...vals);
    const w = 120, h = 34, gap = 3, bw = (w - gap * (vals.length - 1)) / Math.max(1, vals.length);
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" aria-hidden="true">${vals
      .map((v, i) => { const bh = Math.max(2, (h - 2) * (v / max)); return `<rect x="${i * (bw + gap)}" y="${h - bh}" width="${bw}" height="${bh}" rx="2" style="fill:${color};opacity:${v ? 1 : 0.25}"/>`; })
      .join("")}</svg>`;
  }

  function storeRow({ id, name, mode, host, enabled, connected, error }) {
    const status = !enabled ? `<span class="pill pill-muted">disabled</span>` : connected ? `<span class="pill pill-ok">connected</span>` : `<span class="pill pill-bad">down</span>`;
    return `
      <div class="store-row">
        <div class="store-icon store-icon-${id}">${id === "pg" ? "PG" : "RD"}</div>
        <div class="store-body">
          <div class="store-title">${esc(name)} ${status} <span class="pill pill-muted">${esc(mode)}</span></div>
          <div class="mono small muted store-host">${esc(host || "–")}</div>
          ${error ? `<div class="form-error small">${esc(error)}</div>` : ""}
        </div>
        <div class="store-latency" data-latency="${id}"><span class="muted small">checking…</span></div>
      </div>`;
  }

  async function renderOverview(page) {
    await refreshOverview();
    const o = state.overview;
    const [usage, daily, events] = await Promise.all([
      api.get("/api/admin/usage/summary?days=7"),
      api.get("/api/admin/usage/daily?days=7"),
      api.get("/api/admin/usage/events?limit=8"),
    ]);
    const llm = o.llm || {};
    const ds = o.datastores;
    const t = usage.totals || {};
    const days = daily.days || [];
    const meta = PROVIDER_META[llm.provider] || { short: "AI", label: llm.provider_label || "" };
    const hitRate = t.cache_hits + t.llm_calls ? Math.round((100 * t.cache_hits) / (t.cache_hits + t.llm_calls)) : 0;
    const base = location.origin;

    page.innerHTML = `
      <div class="grid grid-4">
        <div class="card stat">
          <div class="stat-row"><div><span class="label">Requests · 7 days</span><span class="value">${fmtInt(t.requests)}</span></div>${sparkline(days, "requests", "var(--chart-1)")}</div>
          <span class="sub">${fmtInt(usage.today.requests)} today · ${fmtInt(t.error_requests)} errors</span>
        </div>
        <div class="card stat">
          <div class="stat-row"><div><span class="label">Tokens · 7 days</span><span class="value">${fmtInt(t.total_tokens)}</span></div>${sparkline(days.map((d) => ({ v: Number(d.prompt_tokens || 0) + Number(d.completion_tokens || 0) })), "v", "var(--chart-2)")}</div>
          <span class="sub">${fmtInt(t.prompt_tokens)} in · ${fmtInt(t.completion_tokens)} out</span>
        </div>
        <div class="card stat">
          <div class="stat-row"><div><span class="label">Cache hit rate</span><span class="value">${hitRate}%</span></div>${sparkline(days, "cache_hits", "var(--success)")}</div>
          <span class="sub">${fmtInt(t.cache_hits)} hits · ${fmtInt(t.llm_calls)} LLM generations</span>
        </div>
        <div class="card stat">
          <div class="stat-row"><div><span class="label">Avg latency</span><span class="value">${fmtMs(t.avg_latency_ms)}</span></div></div>
          <span class="sub">p95 ${fmtMs(t.p95_latency_ms)}</span>
        </div>
      </div>

      <div class="grid grid-2">
        <div class="card">
          <div class="card-head">
            <div class="head-with-avatar">
              <span class="provider-logo lg">${providerIcon(llm.provider)}</span>
              <div><h3>LLM provider</h3><span class="muted small">${llm.configured ? esc(meta.label) : "Not configured"}</span></div>
            </div>
            <div class="inline">${llm.configured ? `<span class="pill pill-ok" id="llm-health">configured</span>` : `<span class="pill pill-warn">action needed</span>`}<a href="#/llm" class="btn btn-sm">Configure</a></div>
          </div>
          ${llm.configured ? `
            <div class="spec-grid">
              <div class="spec"><span class="spec-label">Model</span><span class="spec-value mono">${esc(llm.model)}</span></div>
              <div class="spec"><span class="spec-label">Credentials</span><span class="spec-value mono">${esc(Object.values(llm.secrets_masked).filter(Boolean)[0] || "IAM role / env")}</span></div>
              <div class="spec"><span class="spec-label">Temperature</span><span class="spec-value">${llm.supports_temperature ? esc(llm.options.temperature) : "model default"}</span></div>
              <div class="spec"><span class="spec-label">Max output</span><span class="spec-value">${fmtInt(llm.options.max_tokens)} tokens</span></div>
              ${Object.entries(llm.settings).filter(([, v]) => v).map(([k, v]) => `<div class="spec"><span class="spec-label">${esc(k.replace(/_/g, " "))}</span><span class="spec-value mono">${esc(v)}</span></div>`).join("")}
              <div class="spec"><span class="spec-label">Last change</span><span class="spec-value">${fmtDate(llm.updated_at)}${llm.updated_by ? ` · ${esc(llm.updated_by)}` : ""}</span></div>
            </div>
            <div class="card-foot">
              <button class="btn btn-sm" id="ov-llm-test">Test connection</button>
              <span id="ov-llm-test-result" class="muted small"></span>
            </div>` : `
            <p class="muted">Pick a provider, paste a key and test it. Until then the public API returns <code>503 LLM_NOT_CONFIGURED</code>.</p>
            <div class="provider-chips">${Object.values(PROVIDER_META).map((m) => `<a href="#/llm" class="chip">${esc(m.label)}</a>`).join("")}</div>`}
        </div>

        <div class="card">
          <div class="card-head">
            <div><h3>Data stores</h3><span class="muted small">Bundled by default · switch to your own any time</span></div>
            <div class="inline"><a href="#/datastores" class="btn btn-sm">Manage</a></div>
          </div>
          ${storeRow({ id: "pg", name: "PostgreSQL", mode: ds.postgres.mode, host: ds.postgres.dsn_masked, enabled: true, connected: ds.postgres.connected, error: ds.postgres.fallback_reason })}
          ${storeRow({ id: "rd", name: "Redis", mode: ds.redis.mode, host: ds.redis.url_masked || ds.redis.bundled_url_masked, enabled: ds.redis.enabled, connected: ds.redis.connected, error: ds.redis.error })}
          <div class="mini-stats">
            <div><span class="spec-label">Summary cache</span><span class="spec-value">${fmtInt(o.summary_cache.entries)} <span class="muted small">entries</span></span></div>
            <div><span class="spec-label">Cache hits</span><span class="spec-value">${fmtInt(o.summary_cache.hits)}</span></div>
            <div><span class="spec-label">Chat context</span><span class="spec-value">${esc(o.conversations.backend)} <span class="muted small">· ${Math.round(o.conversations.ttl_seconds / 60)} min TTL</span></span></div>
          </div>
        </div>
      </div>

      <div class="grid grid-3-1">
        <div class="card">
          <div class="card-head"><h3>Requests · last 7 days</h3><a href="#/usage" class="btn btn-sm btn-ghost">Full usage →</a></div>
          <div id="ov-chart"></div>
        </div>
        <div class="card">
          <div class="card-head"><h3>Recent activity</h3></div>
          ${events.events.length ? `<ul class="activity">${events.events.map((e) => `
            <li>
              <span class="dot ${e.status === "ok" ? "dot-ok" : "dot-bad"}"></span>
              <span class="activity-main"><strong>${esc(e.feature)}</strong> <span class="muted">${esc(e.cache_status && e.cache_status !== "n/a" ? e.cache_status : (e.model || ""))}</span></span>
              <span class="muted small activity-meta">${e.total_tokens ? fmtInt(e.total_tokens) + " tok · " : ""}${fmtInt(e.latency_ms)} ms</span>
            </li>`).join("")}</ul>` : `<p class="empty">No requests yet — try the <a href="#/playground">Playground</a>.</p>`}
        </div>
      </div>

      <div class="card">
        <div class="card-head"><h3>Integrate</h3><a href="/docs" target="_blank" rel="noopener" class="btn btn-sm btn-ghost">API docs ↗</a></div>
        <div class="grid grid-2">
          <div>
            <ol class="steps">
              <li>Configure an <a href="#/llm">LLM provider</a> and test the connection.</li>
              <li>Create an <a href="#/api-keys">API key</a> for your application.</li>
              <li>Call the summary or chat endpoint with <code>X-API-Key</code>.</li>
              <li>Watch <a href="#/usage">Usage</a> for tokens, latency and cache hits.</li>
            </ol>
            <p class="muted small">Limits: ${fmtInt(o.limits.max_fare_rules_chars)} chars per request · ${esc(o.limits.rate_limit)} per key · summary cache ${Math.round(o.limits.summary_cache_ttl_seconds / 86400)} days.</p>
          </div>
          <pre class="code-block"><code>curl -X POST ${esc(base)}/api/v1/fare-rules/summary \\
  -H "X-API-Key: fl_..." \\
  -H "Content-Type: application/json" \\
  -d '{"fare_rules_text": "CANCELLATIONS ...", "lang": "en"}'</code></pre>
        </div>
      </div>`;

    barChart($("#ov-chart"), days, [
      { key: "summary_requests", label: "Summary", color: "var(--chart-1)" },
      { key: "chat_requests", label: "Chat", color: "var(--chart-2)" },
      { key: "errors", label: "Errors", color: "var(--chart-3)" },
    ]);

    // Live latency checks for the datastore rows (non-blocking).
    const check = async (id, kind, mode) => {
      const el = page.querySelector(`[data-latency="${id}"]`);
      if (!el) return;
      try {
        const r = await api.post("/api/admin/datastores/test", { kind, mode, dsn: "" });
        el.innerHTML = r.ok ? `<span class="latency-ok">${r.latency_ms} ms</span><span class="muted small">${esc(r.server_version || "")}</span>` : `<span class="pill pill-bad">${esc(r.message)}</span>`;
      } catch (err) { el.innerHTML = `<span class="muted small">${esc(err.message)}</span>`; }
    };
    check("pg", "postgres", ds.postgres.mode);
    if (ds.redis.enabled) check("rd", "redis", ds.redis.mode); else { const el = page.querySelector('[data-latency="rd"]'); if (el) el.innerHTML = ""; }

    const testBtn = $("#ov-llm-test");
    if (testBtn) testBtn.addEventListener("click", async () => {
      const out = $("#ov-llm-test-result");
      testBtn.disabled = true; out.innerHTML = `<span class="spinner"></span>`;
      try {
        const r = await api.post("/api/admin/llm/config/test", { provider: llm.provider, model: llm.model, settings: llm.settings, secrets: {}, options: llm.options });
        out.innerHTML = r.ok ? `<span class="latency-ok">OK · ${r.latency_ms} ms</span>` : `<span class="form-error">${esc(r.message)}</span>`;
        const health = $("#llm-health"); if (health) { health.className = `pill ${r.ok ? "pill-ok" : "pill-bad"}`; health.textContent = r.ok ? "healthy" : "failing"; }
      } catch (err) { out.innerHTML = `<span class="form-error">${esc(err.message)}</span>`; } finally { testBtn.disabled = false; }
    });
  }

  const pill = (ok) => (ok ? `<span class="pill pill-ok">connected</span>` : `<span class="pill pill-bad">down</span>`);

  // ---------------------------------------------------------------- usage
  async function renderUsage(page) {
    let days = 30;
    let feature = "";
    page.innerHTML = `
      <div class="card">
        <div class="card-head">
          <h3>Requests per day</h3>
          <div class="inline">
            <select id="usage-days"><option value="7">Last 7 days</option><option value="30" selected>Last 30 days</option><option value="90">Last 90 days</option></select>
          </div>
        </div>
        <div id="usage-chart"></div>
      </div>
      <div class="grid grid-4" id="usage-stats"></div>
      <div class="grid grid-3">
        <div class="card"><div class="card-head"><h3>By feature</h3></div><div id="usage-feature"></div></div>
        <div class="card"><div class="card-head"><h3>By model</h3></div><div id="usage-model"></div></div>
        <div class="card"><div class="card-head"><h3>By API key</h3></div><div id="usage-key"></div></div>
      </div>
      <div class="card">
        <div class="card-head"><h3>Recent requests</h3>
          <div class="inline">
            <select id="usage-feature-filter"><option value="">All features</option><option value="summary">summary</option><option value="chat">chat</option></select>
            <button id="usage-refresh" class="btn btn-sm">Refresh</button>
          </div>
        </div>
        <div class="table-wrap" id="usage-events"></div>
      </div>`;

    async function load() {
      const [summary, daily, events] = await Promise.all([
        api.get(`/api/admin/usage/summary?days=${days}`),
        api.get(`/api/admin/usage/daily?days=${days}`),
        api.get(`/api/admin/usage/events?limit=50${feature ? `&feature=${feature}` : ""}`),
      ]);
      barChart($("#usage-chart"), daily.days, [
        { key: "summary_requests", label: "Summary", color: "var(--chart-1)" },
        { key: "chat_requests", label: "Chat", color: "var(--chart-2)" },
        { key: "errors", label: "Errors", color: "var(--chart-3)" },
      ]);
      const t = summary.totals || {};
      $("#usage-stats").innerHTML = `
        <div class="card stat"><span class="label">Requests</span><span class="value">${fmtInt(t.requests)}</span><span class="sub">${fmtInt(summary.today.requests)} today</span></div>
        <div class="card stat"><span class="label">Tokens</span><span class="value">${fmtInt(t.total_tokens)}</span><span class="sub">${fmtInt(t.prompt_tokens)} in / ${fmtInt(t.completion_tokens)} out</span></div>
        <div class="card stat"><span class="label">Cache hit rate</span><span class="value">${t.cache_hits + t.llm_calls ? Math.round((100 * t.cache_hits) / (t.cache_hits + t.llm_calls)) : 0}%</span><span class="sub">${fmtInt(t.cache_hits)} hits · ${fmtInt(t.llm_calls)} generations</span></div>
        <div class="card stat"><span class="label">Errors</span><span class="value">${fmtInt(t.error_requests)}</span><span class="sub">avg ${fmtMs(t.avg_latency_ms)} · p95 ${fmtMs(t.p95_latency_ms)}</span></div>`;
      $("#usage-feature").innerHTML = tableOrEmpty(summary.by_feature, ["feature", "requests", "errors", "prompt_tokens", "completion_tokens", "avg_latency_ms"], ["Feature", "Req", "Err", "In", "Out", "Avg ms"]);
      $("#usage-model").innerHTML = tableOrEmpty(summary.by_model, ["provider", "model", "requests", "total_tokens"], ["Provider", "Model", "Req", "Tokens"]);
      $("#usage-key").innerHTML = tableOrEmpty(summary.by_api_key, ["api_key_name", "requests", "total_tokens"], ["Key", "Req", "Tokens"]);
      $("#usage-events").innerHTML = events.events.length
        ? `<table class="table"><thead><tr><th>Time</th><th>Feature</th><th>Status</th><th>Cache</th><th>Model</th><th class="num">In</th><th class="num">Out</th><th class="num">ms</th><th>Lang</th><th>Key</th></tr></thead><tbody>${events.events
            .map((e) => `<tr><td>${fmtDate(e.occurred_at)}</td><td>${esc(e.feature)}</td><td>${e.status === "ok" ? `<span class="pill pill-ok">ok</span>` : `<span class="pill pill-bad" title="${esc(e.error_code || "")}">${esc(e.error_code || "error")}</span>`}</td><td>${esc(e.cache_status || "")}</td><td class="mono small">${esc(e.model || "")}</td><td class="num">${fmtInt(e.prompt_tokens)}</td><td class="num">${fmtInt(e.completion_tokens)}</td><td class="num">${fmtInt(e.latency_ms)}</td><td>${esc(e.lang || "")}</td><td>${esc(e.api_key_name || (e.api_key_id == null ? "admin" : "deleted"))}</td></tr>`)
            .join("")}</tbody></table>`
        : `<p class="empty">No requests yet.</p>`;
    }
    $("#usage-days").addEventListener("change", (e) => { days = Number(e.target.value); load().catch((err) => toast(err.message, "error")); });
    $("#usage-feature-filter").addEventListener("change", (e) => { feature = e.target.value; load().catch((err) => toast(err.message, "error")); });
    $("#usage-refresh").addEventListener("click", () => load().catch((err) => toast(err.message, "error")));
    await load();
  }

  function tableOrEmpty(rows, keys, labels) {
    if (!rows || !rows.length) return `<p class="empty">No data.</p>`;
    return `<div class="table-wrap"><table class="table"><thead><tr>${labels.map((l, i) => `<th class="${typeof rows[0][keys[i]] === "number" ? "num" : ""}">${esc(l)}</th>`).join("")}</tr></thead><tbody>${rows
      .map((r) => `<tr>${keys.map((k) => `<td class="${typeof r[k] === "number" ? "num" : ""}">${typeof r[k] === "number" ? fmtInt(r[k]) : esc(r[k] ?? "")}</td>`).join("")}</tr>`)
      .join("")}</tbody></table></div>`;
  }

  // ------------------------------------------------------------------ llm
  // Simplified, brand-coloured marks (not official logos) so the picker is scannable.
  // Provider icons from lobe-icons (MIT) - see ui/assets/icons/LICENSE.md
  const PROVIDER_ICONS = {
    openai: `<svg fill="#000" fill-rule="evenodd"   viewBox="0 0 24 24"  xmlns="http://www.w3.org/2000/svg"><path d="M9.205 8.658v-2.26c0-.19.072-.333.238-.428l4.543-2.616c.619-.357 1.356-.523 2.117-.523 2.854 0 4.662 2.212 4.662 4.566 0 .167 0 .357-.024.547l-4.71-2.759a.797.797 0 00-.856 0l-5.97 3.473zm10.609 8.8V12.06c0-.333-.143-.57-.429-.737l-5.97-3.473 1.95-1.118a.433.433 0 01.476 0l4.543 2.617c1.309.76 2.189 2.378 2.189 3.948 0 1.808-1.07 3.473-2.76 4.163zM7.802 12.703l-1.95-1.142c-.167-.095-.239-.238-.239-.428V5.899c0-2.545 1.95-4.472 4.591-4.472 1 0 1.927.333 2.712.928L8.23 5.067c-.285.166-.428.404-.428.737v6.898zM12 15.128l-2.795-1.57v-3.33L12 8.658l2.795 1.57v3.33L12 15.128zm1.796 7.23c-1 0-1.927-.332-2.712-.927l4.686-2.712c.285-.166.428-.404.428-.737v-6.898l1.974 1.142c.167.095.238.238.238.428v5.233c0 2.545-1.974 4.472-4.614 4.472zm-5.637-5.303l-4.544-2.617c-1.308-.761-2.188-2.378-2.188-3.948A4.482 4.482 0 014.21 6.327v5.423c0 .333.143.571.428.738l5.947 3.449-1.95 1.118a.432.432 0 01-.476 0zm-.262 3.9c-2.688 0-4.662-2.021-4.662-4.519 0-.19.024-.38.047-.57l4.686 2.71c.286.167.571.167.856 0l5.97-3.448v2.26c0 .19-.07.333-.237.428l-4.543 2.616c-.619.357-1.356.523-2.117.523zm5.899 2.83a5.947 5.947 0 005.827-4.756C22.287 18.339 24 15.84 24 13.296c0-1.665-.713-3.282-1.998-4.448.119-.5.19-.999.19-1.498 0-3.401-2.759-5.947-5.946-5.947-.642 0-1.26.095-1.88.31A5.962 5.962 0 0010.205 0a5.947 5.947 0 00-5.827 4.757C1.713 5.447 0 7.945 0 10.49c0 1.666.713 3.283 1.998 4.448-.119.5-.19 1-.19 1.499 0 3.401 2.759 5.946 5.946 5.946.642 0 1.26-.095 1.88-.309a5.96 5.96 0 004.162 1.713z"></path></svg>`,
    azure_openai: `<img src="/ui/assets/icons/azureai-color.svg" alt="" />`,
    anthropic: `<img src="/ui/assets/icons/claude-color.svg" alt="" />`,
    google: `<img src="/ui/assets/icons/gemini-color.svg" alt="" />`,
    groq: `<svg fill="#f55036" fill-rule="evenodd"   viewBox="0 0 24 24"  xmlns="http://www.w3.org/2000/svg"><path d="M12.036 2c-3.853-.035-7 3-7.036 6.781-.035 3.782 3.055 6.872 6.908 6.907h2.42v-2.566h-2.292c-2.407.028-4.38-1.866-4.408-4.23-.029-2.362 1.901-4.298 4.308-4.326h.1c2.407 0 4.358 1.915 4.365 4.278v6.305c0 2.342-1.944 4.25-4.323 4.279a4.375 4.375 0 01-3.033-1.252l-1.851 1.818A7 7 0 0012.029 22h.092c3.803-.056 6.858-3.083 6.879-6.816v-6.5C18.907 4.963 15.817 2 12.036 2z"></path></svg>`,
    bedrock: `<img src="/ui/assets/icons/bedrock-color.svg" alt="" />`,
  };
  const providerIcon = (id) => `<span class="logo-tile">${PROVIDER_ICONS[id] || `<span class="logo-fallback">AI</span>`}</span>`;

  const EYE = `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"/><circle cx="12" cy="12" r="3"/></svg>`;
  const EYE_OFF = `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.9 17.9A10.9 10.9 0 0 1 12 19c-7 0-11-7-11-7a20 20 0 0 1 5.1-5.9M9.9 4.2A10.9 10.9 0 0 1 12 4c7 0 11 7 11 7a20 20 0 0 1-3.2 4.3M14.1 14.1a3 3 0 1 1-4.2-4.2"/><path d="M1 1l22 22"/></svg>`;
  const secretInput = (name, value = "", placeholder = "", attrs = "") =>
    `<div class="secret-wrap"><input name="${name}" type="password" value="${esc(value)}" placeholder="${esc(placeholder)}" autocomplete="off" spellcheck="false" ${attrs}/><button type="button" class="eye" data-eye aria-label="Show or hide" title="Show / hide">${EYE}</button></div>`;
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-eye]");
    if (!btn) return;
    const input = btn.parentElement.querySelector("input");
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    btn.innerHTML = show ? EYE_OFF : EYE;
  });

  async function renderLLM(page) {
    if (!state.providers) state.providers = (await api.get("/api/admin/llm/providers")).providers;
    const current = await api.get("/api/admin/llm/config?reveal=true");
    const providers = state.providers;
    let selected = current.configured ? current.provider : providers[0].id;

    page.innerHTML = `
      <div class="card">
        <div class="card-head">
          <div><h3>Choose an inference provider</h3><span class="muted small">All summaries and chat answers are generated by this provider. Credentials are encrypted at rest.</span></div>
          ${current.configured ? `<span class="pill pill-ok">configured · ${esc(current.provider_label)}</span>` : `<span class="pill pill-warn">not configured</span>`}
        </div>
        <div class="provider-grid" id="provider-grid">
          ${providers.map((p) => `
            <button type="button" class="provider-card ${p.id === selected ? "selected" : ""}" data-provider="${p.id}">
              <span class="provider-logo">${providerIcon(p.id)}</span>
              <span class="provider-name">${esc(p.label)}</span>
              ${current.configured && current.provider === p.id ? `<span class="pill pill-ok provider-badge">active</span>` : ""}
            </button>`).join("")}
        </div>
      </div>

      <div class="grid grid-2" style="grid-template-columns: minmax(0, 3fr) minmax(0, 2fr)">
        <div class="card">
          <form id="llm-form">
            <input type="hidden" name="provider" value="${esc(selected)}" />
            <div class="card-head"><div class="head-with-avatar"><span class="provider-logo lg" id="llm-form-logo">${providerIcon(selected)}</span><div><h3 id="llm-form-title"></h3><span class="muted small" id="llm-provider-desc"></span></div></div></div>
            <div id="llm-fields"></div>
            <div class="form-row">
              <label class="field" id="llm-temp-field"><span>Temperature</span><input name="temperature" type="number" min="0" max="2" step="0.1" value="${current.configured ? current.options.temperature : 0}" /><span class="help">0 = deterministic (recommended for fare rules).</span></label>
              <label class="field"><span>Max output tokens</span><input name="max_tokens" type="number" min="256" max="32768" step="1" value="${current.configured ? current.options.max_tokens : 4096}" /></label>
            </div>
            <p class="form-error" id="llm-error" hidden></p>
            <div class="form-actions">
              <button type="button" class="btn" id="llm-test">Test connection</button>
              <button type="submit" class="btn btn-primary">Save &amp; activate</button>
              ${current.configured ? `<button type="button" class="btn btn-danger" id="llm-remove">Remove</button>` : ""}
              <span id="llm-status" class="muted small"></span>
            </div>
          </form>
        </div>
        <div class="card">
          <div class="card-head"><h3>Active configuration</h3></div>
          ${current.configured ? `
            <div class="spec-grid">
              <div class="spec"><span class="spec-label">Provider</span><span class="spec-value">${esc(current.provider_label)}</span></div>
              <div class="spec"><span class="spec-label">Model</span><span class="spec-value mono">${esc(current.model)}</span></div>
              ${Object.entries(current.settings).filter(([, v]) => v).map(([k, v]) => `<div class="spec"><span class="spec-label">${esc(k.replace(/_/g, " "))}</span><span class="spec-value mono">${esc(v)}</span></div>`).join("")}
              ${Object.entries(current.secrets_masked).map(([k, v]) => `<div class="spec"><span class="spec-label">${esc(k.replace(/_/g, " "))}</span><span class="spec-value mono">${esc(v || "(not set)")}</span></div>`).join("")}
              <div class="spec"><span class="spec-label">Updated</span><span class="spec-value">${fmtDate(current.updated_at)}${current.updated_by ? ` · ${esc(current.updated_by)}` : ""}</span></div>
            </div>` : `<p class="muted">Nothing saved yet. Pick a provider on the left, paste your credentials and click <em>Test connection</em>, then <em>Save &amp; activate</em>.</p>`}
          <h3 style="margin-top:18px">Good to know</h3>
          <ul class="muted small">
            <li>Stored secrets are pre-filled; use the eye icon to reveal them.</li>
            <li>Switching provider keeps the other provider's saved key until you overwrite it.</li>
            <li>Cached summaries are not regenerated when you change models — clear the cache under <a href="#/datastores">Data stores</a> if you want fresh output.</li>
          </ul>
        </div>
      </div>`;

    const form = $("#llm-form");
    const fieldsEl = $("#llm-fields");

    function renderFields() {
      const spec = providers.find((p) => p.id === form.provider.value);
      const same = current.configured && current.provider === spec.id;
      $("#llm-form-title").textContent = spec.label;
      $("#llm-form-logo").innerHTML = providerIcon(spec.id);
      $("#llm-provider-desc").innerHTML = `${esc(spec.description)} <a href="${esc(spec.docs_url)}" target="_blank" rel="noopener">Docs ↗</a>`;
      const modelValue = same ? current.model : spec.default_model;
      const listId = `models-${spec.id}`;
      fieldsEl.innerHTML = `
        <label class="field"><span>${esc(spec.model_label)}</span>
          <input name="model" type="text" list="${listId}" value="${esc(modelValue)}" placeholder="${esc(spec.default_model || "model id")}" required />
          <datalist id="${listId}">${spec.suggested_models.map((m) => `<option value="${esc(m)}"></option>`).join("")}</datalist>
          <span class="help">${esc(spec.model_help || "Suggestions are listed; any model id accepted by the provider works.")}</span>
        </label>
        ${spec.fields.map((f) => `
          <label class="field"><span>${esc(f.label)}${f.required ? "" : " <span class='muted'>(optional)</span>"}</span>
            ${f.secret
              ? secretInput(`secret:${f.key}`, same && current.secrets ? current.secrets[f.key] || "" : "", f.placeholder || "")
              : `<input name="setting:${f.key}" type="text" autocomplete="off" value="${esc(same ? current.settings[f.key] || f.default || "" : f.default || "")}" placeholder="${esc(f.placeholder || "")}" />`}
            ${f.help ? `<span class="help">${esc(f.help)}</span>` : ""}
          </label>`).join("")}`;
      $("#llm-temp-field").hidden = !spec.supports_temperature;
    }

    function collect() {
      const fd = new FormData(form);
      const body = { provider: fd.get("provider"), model: fd.get("model") || "", settings: {}, secrets: {}, options: { temperature: Number(fd.get("temperature")), max_tokens: Number(fd.get("max_tokens")) } };
      for (const [k, v] of fd.entries()) {
        if (k.startsWith("setting:")) body.settings[k.slice(8)] = v;
        if (k.startsWith("secret:")) body.secrets[k.slice(7)] = v;
      }
      return body;
    }

    $$(".provider-card", page).forEach((card) => card.addEventListener("click", () => {
      $$(".provider-card", page).forEach((c) => c.classList.toggle("selected", c === card));
      form.provider.value = card.dataset.provider;
      renderFields();
    }));
    renderFields();

    $("#llm-test").addEventListener("click", async () => {
      const status = $("#llm-status");
      status.innerHTML = `<span class="spinner"></span> Testing…`;
      try {
        const res = await api.post("/api/admin/llm/config/test", collect());
        status.innerHTML = res.ok ? `<span class="latency-ok">Connected · ${res.latency_ms} ms</span>` : `<span class="form-error">${esc(res.message)}</span>`;
        if (res.ok) toast(`Connected to ${res.model} in ${res.latency_ms} ms`, "success", 5000);
      } catch (err) { status.innerHTML = `<span class="form-error">${esc(err.message)}</span>`; }
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const errEl = $("#llm-error");
      errEl.hidden = true;
      try {
        await api.put("/api/admin/llm/config", collect());
        toast("LLM configuration saved and activated", "success");
        await refreshOverview();
        await renderLLM(page);
      } catch (err) { errEl.textContent = err.message; errEl.hidden = false; }
    });

    const remove = $("#llm-remove");
    if (remove) remove.addEventListener("click", async () => {
      if (!(await confirmDialog("Remove LLM configuration?", "The API will return 503 until a provider is configured again.", "Remove", true))) return;
      await api.del("/api/admin/llm/config");
      toast("LLM configuration removed");
      await refreshOverview();
      await renderLLM(page);
    });
  }

  // ------------------------------------------------------------- api keys
  async function renderApiKeys(page) {
    const data = await api.get("/api/admin/api-keys");
    page.innerHTML = `
      <div class="card">
        <div class="card-head"><h3>Create a key</h3></div>
        <form id="key-form" class="inline">
          <input name="name" type="text" placeholder="Name, e.g. booking-web" required maxlength="100" style="width:420px;max-width:100%" />
          <button class="btn btn-primary" type="submit">Create API key</button>
        </form>
        <p class="muted small" style="margin-top:8px">Send it as <code>X-API-Key: fl_…</code> (or <code>Authorization: Bearer fl_…</code>). The full key is shown once.</p>
      </div>
      <div class="card">
        <div class="card-head"><h3>Keys</h3></div>
        <div class="table-wrap">${data.keys.length ? `<table class="table"><thead><tr><th>Name</th><th>Prefix</th><th>Created</th><th>Last used</th><th class="num">Requests</th><th>Status</th><th></th></tr></thead><tbody>${data.keys
          .map((k) => `<tr><td>${esc(k.name)}</td><td class="mono">${esc(k.key_prefix)}…</td><td>${fmtDate(k.created_at)}<div class="muted small">${esc(k.created_by || "")}</div></td><td>${fmtDate(k.last_used_at)}</td><td class="num">${fmtInt(k.requests)}</td><td>${k.revoked_at ? `<span class="pill pill-bad">revoked</span>` : `<span class="pill pill-ok">active</span>`}</td><td>${k.revoked_at ? "" : `<button class="btn btn-sm btn-danger" data-revoke="${k.id}">Revoke</button>`}</td></tr>`)
          .join("")}</tbody></table>` : `<p class="empty">No API keys yet.</p>`}</div>
      </div>`;

    $("#key-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const name = e.target.name.value.trim();
      try {
        const created = await api.post("/api/admin/api-keys", { name });
        const m = modal(`
          <h3>API key created</h3>
          <p>Copy it now — it will not be shown again.</p>
          <div class="copy-box"><input type="text" readonly value="${esc(created.key)}" id="new-key" /><button class="btn" id="copy-key">Copy</button></div>
          <p class="muted small" style="margin-top:10px">Example:<br><code>curl -H "X-API-Key: ${esc(created.key)}" -H "Content-Type: application/json" -d '{"fare_rules_text":"..."}' ${esc(location.origin)}/api/v1/fare-rules/summary</code></p>
          <div class="form-actions"><button class="btn btn-primary" data-close>Done</button></div>`);
        $("#copy-key", m.root).addEventListener("click", async () => {
          try { await navigator.clipboard.writeText(created.key); toast("Copied", "success"); } catch { $("#new-key", m.root).select(); }
        });
        $("[data-close]", m.root).addEventListener("click", () => renderApiKeys(page));
      } catch (err) { toast(err.message, "error"); }
    });

    $$("[data-revoke]", page).forEach((btn) => btn.addEventListener("click", async () => {
      if (!(await confirmDialog("Revoke this API key?", "Requests using it will be rejected immediately.", "Revoke", true))) return;
      try { await api.del(`/api/admin/api-keys/${btn.dataset.revoke}`); toast("Key revoked"); await renderApiKeys(page); } catch (err) { toast(err.message, "error"); }
    }));
  }

  // ----------------------------------------------------------- datastores
  const encPart = (v) => encodeURIComponent(v || "");
  function buildPgDsn(f) {
    if (f.dsn_override && f.dsn_override.trim()) return f.dsn_override.trim();
    const auth = f.username ? `${encPart(f.username)}${f.password ? ":" + encPart(f.password) : ""}@` : "";
    const q = f.sslmode ? `?sslmode=${encPart(f.sslmode)}` : "";
    return `postgresql://${auth}${f.host}:${f.port || 5432}/${f.database}${q}`;
  }
  function buildRedisUrl(f) {
    if (f.url_override && f.url_override.trim()) return f.url_override.trim();
    const auth = f.password || f.username ? `${encPart(f.username)}:${encPart(f.password)}@` : "";
    return `${f.tls ? "rediss" : "redis"}://${auth}${f.host}:${f.port || 6379}/${f.db || 0}`;
  }
  const readForm = (form) => Object.fromEntries(Array.from(new FormData(form).entries()));

  async function renderDatastores(page) {
    const s = await api.get("/api/admin/datastores?reveal=true");
    const pg = s.postgres, rd = s.redis;
    const pgx = pg.external, rdx = rd.external;
    page.innerHTML = `
      <div class="grid grid-2">
        <div class="card">
          <div class="card-head">
            <div class="head-with-avatar"><span class="store-icon store-icon-pg">PG</span><div><h3>PostgreSQL</h3><span class="muted small">Settings, users, API keys, summary cache, usage</span></div></div>
            ${pill(pg.connected)}
          </div>
          <div class="store-current"><span class="muted small">In use</span><span class="mono small">${esc(pg.dsn_masked)}</span></div>
          ${pg.fallback_reason ? `<p class="form-error">${esc(pg.fallback_reason)}</p>` : ""}
          <form id="pg-form">
            <div class="segmented" role="radiogroup">
              <label><input type="radio" name="postgres_mode" value="bundled" ${pg.mode === "bundled" ? "checked" : ""}/><span>Bundled (docker compose)</span></label>
              <label><input type="radio" name="postgres_mode" value="external" ${pg.mode === "external" ? "checked" : ""}/><span>My own PostgreSQL</span></label>
            </div>
            <div id="pg-external">
              <div class="form-row form-row-3-1">
                <label class="field"><span>Host</span><input name="host" type="text" value="${esc(pgx.host)}" placeholder="db.example.com" /></label>
                <label class="field"><span>Port</span><input name="port" type="number" value="${esc(pgx.port || 5432)}" min="1" max="65535" /></label>
              </div>
              <div class="form-row">
                <label class="field"><span>Database</span><input name="database" type="text" value="${esc(pgx.database)}" placeholder="farelens" /></label>
                <label class="field"><span>SSL mode</span>
                  <select name="sslmode">${["", "prefer", "require", "verify-ca", "verify-full", "disable"].map((m) => `<option value="${m}" ${pgx.sslmode === m ? "selected" : ""}>${m || "default"}</option>`).join("")}</select>
                </label>
              </div>
              <div class="form-row">
                <label class="field"><span>Username</span><input name="username" type="text" value="${esc(pgx.username)}" autocomplete="off" /></label>
                <label class="field"><span>Password</span>${secretInput("password", pgx.password, "")}</label>
              </div>
              <details class="advanced"><summary>Advanced: paste a connection string instead</summary>
                <label class="field"><span>Connection string</span>${secretInput("dsn_override", "", "postgresql://user:password@host:5432/dbname?sslmode=require")}<span class="help">If filled, this overrides the fields above.</span></label>
              </details>
              <p class="help">The schema is created automatically. On the first switch to an empty database your users, LLM configuration and API keys are copied over.</p>
            </div>
            <div class="form-actions">
              <button type="button" class="btn" data-test="postgres">Test connection</button>
              <button type="submit" class="btn btn-primary">Apply</button>
              <span class="muted small" id="pg-status"></span>
            </div>
          </form>
        </div>

        <div class="card">
          <div class="card-head">
            <div class="head-with-avatar"><span class="store-icon store-icon-rd">RD</span><div><h3>Redis</h3><span class="muted small">Optional · fast summary cache and shared chat context</span></div></div>
            ${rd.enabled ? pill(rd.connected) : `<span class="pill pill-muted">disabled</span>`}
          </div>
          <div class="store-current"><span class="muted small">In use</span><span class="mono small">${esc(rd.url_masked || (rd.enabled ? "not connected" : "—"))}</span></div>
          ${rd.error ? `<p class="form-error small">${esc(rd.error)}</p>` : ""}
          <form id="redis-form">
            <label class="checkbox" style="margin-bottom:12px"><input type="checkbox" name="redis_enabled" ${rd.enabled ? "checked" : ""}/> Use Redis <span class="muted small">(without it: Postgres-only cache, in-memory chat context)</span></label>
            <div class="segmented" role="radiogroup">
              <label><input type="radio" name="redis_mode" value="bundled" ${rd.mode === "bundled" ? "checked" : ""}/><span>Bundled (docker compose)</span></label>
              <label><input type="radio" name="redis_mode" value="external" ${rd.mode === "external" ? "checked" : ""}/><span>My own Redis</span></label>
            </div>
            <div id="redis-external">
              <div class="form-row form-row-3-1">
                <label class="field"><span>Host</span><input name="host" type="text" value="${esc(rdx.host)}" placeholder="cache.example.com" /></label>
                <label class="field"><span>Port</span><input name="port" type="number" value="${esc(rdx.port || 6379)}" min="1" max="65535" /></label>
              </div>
              <div class="form-row">
                <label class="field"><span>Username <span class="muted">(optional, ACL)</span></span><input name="username" type="text" value="${esc(rdx.username)}" autocomplete="off" /></label>
                <label class="field"><span>Password <span class="muted">(optional)</span></span>${secretInput("password", rdx.password, "")}</label>
              </div>
              <div class="form-row">
                <label class="field"><span>Database index</span><input name="db" type="number" value="${esc(rdx.db || 0)}" min="0" max="15" /></label>
                <label class="checkbox" style="align-self:end;margin-bottom:14px"><input type="checkbox" name="tls" ${rdx.tls ? "checked" : ""}/> Use TLS (rediss://)</label>
              </div>
              <details class="advanced"><summary>Advanced: paste a Redis URL instead</summary>
                <label class="field"><span>Redis URL</span>${secretInput("url_override", "", "redis://:password@host:6379/0")}</label>
              </details>
            </div>
            <div class="form-actions">
              <button type="button" class="btn" data-test="redis">Test connection</button>
              <button type="submit" class="btn btn-primary">Apply</button>
              <span class="muted small" id="redis-status"></span>
            </div>
          </form>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><h3>Maintenance</h3></div>
        <div class="inline">
          <button class="btn btn-danger" id="clear-cache">Clear summary cache</button>
          <span class="muted small">Removes every cached summary from Postgres and Redis. Use after editing prompts or moving to a better model.</span>
        </div>
      </div>`;

    const pgForm = $("#pg-form"), redisForm = $("#redis-form");
    const syncPg = () => { $("#pg-external").hidden = pgForm.postgres_mode.value !== "external"; };
    const syncRedis = () => {
      const on = redisForm.redis_enabled.checked;
      $$("input[name=redis_mode]", redisForm).forEach((r) => (r.disabled = !on));
      $("#redis-external").hidden = !on || redisForm.redis_mode.value !== "external";
    };
    pgForm.addEventListener("change", syncPg); redisForm.addEventListener("change", syncRedis); syncPg(); syncRedis();

    const pgDsn = () => (pgForm.postgres_mode.value === "external" ? buildPgDsn(readForm(pgForm)) : "");
    const redisUrl = () => (redisForm.redis_mode.value === "external" ? buildRedisUrl({ ...readForm(redisForm), tls: redisForm.tls.checked }) : "");

    $$("[data-test]", page).forEach((btn) => btn.addEventListener("click", async () => {
      const kind = btn.dataset.test;
      const status = kind === "postgres" ? $("#pg-status") : $("#redis-status");
      const body = kind === "postgres" ? { kind, mode: pgForm.postgres_mode.value, dsn: pgDsn() } : { kind, mode: redisForm.redis_mode.value, dsn: redisUrl() };
      btn.disabled = true; status.innerHTML = `<span class="spinner"></span>`;
      try {
        const res = await api.post("/api/admin/datastores/test", body);
        status.innerHTML = res.ok
          ? `<span class="latency-ok">Connected · ${res.latency_ms} ms</span> <span class="muted">${esc(res.server_version || "")}${kind === "postgres" && res.can_create_tables === false ? " · ⚠ user cannot create tables" : ""}</span>`
          : `<span class="form-error">${esc(res.message)}</span>`;
      } catch (err) { status.innerHTML = `<span class="form-error">${esc(err.message)}</span>`; } finally { btn.disabled = false; }
    }));

    const apply = async (e) => {
      e.preventDefault();
      const body = {
        postgres_mode: pgForm.postgres_mode.value, postgres_dsn: pgDsn(),
        redis_mode: redisForm.redis_mode.value, redis_url: redisUrl(),
        redis_enabled: redisForm.redis_enabled.checked, carry_over: true,
      };
      if (body.postgres_mode === "external" && (!readForm(pgForm).host && !readForm(pgForm).dsn_override)) { toast("Postgres host is required", "error"); return; }
      if (body.postgres_mode !== pg.mode || (body.postgres_mode === "external" && body.postgres_dsn !== pg.external_dsn_full)) {
        const ok = await confirmDialog("Switch PostgreSQL?", "FareLens will connect to the target, create the schema and copy users, LLM configuration and API keys if it is empty. Usage history and cached summaries stay in the old database.", "Switch");
        if (!ok) return;
      }
      try {
        const res = await api.put("/api/admin/datastores", body);
        toast(`Applied — Postgres: ${res.report.postgres}, Redis: ${res.report.redis}${res.report.carried_over.length ? `, copied: ${res.report.carried_over.join(", ")}` : ""}`, "success", 7000);
        await refreshOverview();
        await renderDatastores(page);
      } catch (err) { toast(err.message, "error", 9000); }
    };
    pgForm.addEventListener("submit", apply);
    redisForm.addEventListener("submit", apply);

    $("#clear-cache").addEventListener("click", async () => {
      if (!(await confirmDialog("Clear the summary cache?", "Every cached summary will be regenerated on next request (LLM cost).", "Clear", true))) return;
      try { const r = await api.post("/api/admin/cache/summary/clear"); toast(`Cleared ${r.postgres} Postgres rows and ${r.redis} Redis keys`, "success"); await renderDatastores(page); } catch (err) { toast(err.message, "error"); }
    });
  }

  // ----------------------------------------------------------- playground
  const SAMPLES = {
    saver: { label: "Saver economy (SAR)", text: `CANCELLATIONS
BEFORE DEPARTURE
  CHARGE SAR 150 FOR CANCEL/REFUND.
  CHILD/INFANT DISCOUNTS APPLY.
  WAIVED FOR DEATH OF PASSENGER OR FAMILY MEMBER.
AFTER DEPARTURE
  TICKET IS NON-REFUNDABLE.
  REFUND OF UNUSED TAXES PERMITTED.
NO-SHOW
  TICKET IS NON-REFUNDABLE.

CHANGES
BEFORE DEPARTURE
  CHARGE SAR 100 FOR REISSUE/REVALIDATION. FARE DIFFERENCE APPLIES.
  CHANGES PERMITTED UP TO 4 HOURS BEFORE DEPARTURE.
AFTER DEPARTURE
  CHARGE SAR 200 FOR REISSUE. FARE DIFFERENCE APPLIES.
NO-SHOW
  CHARGE SAR 250 FOR REISSUE. FARE DIFFERENCE APPLIES.

TICKET VALID FOR 1 YEAR FROM DATE OF ISSUE.` },
    flex: { label: "Flex business (USD)", text: `CANCELLATIONS
ANY TIME
  CHARGE USD 50 FOR CANCEL/REFUND.
  REFUND PERMITTED TO ORIGINAL FORM OF PAYMENT.
NO-SHOW
  CHARGE USD 150 FOR CANCEL/REFUND.

CHANGES
ANY TIME
  CHANGES PERMITTED FREE OF CHARGE. FARE DIFFERENCE APPLIES.
NO-SHOW
  CHARGE USD 100 FOR REISSUE. FARE DIFFERENCE APPLIES.

MIN STAY NONE. MAX STAY 12 MONTHS.` },
    nonref: { label: "Non-refundable promo (INR)", text: `CANCELLATIONS
  TICKET IS NON-REFUNDABLE.
  REFUND OF UNUSED TAXES PERMITTED EXCEPT YQ/YR.
CHANGES
BEFORE DEPARTURE
  CHARGE INR 3500 PER PASSENGER FOR REISSUE UP TO 72 HOURS BEFORE DEPARTURE. FARE DIFFERENCE APPLIES.
  CHARGE INR 5000 PER PASSENGER FOR REISSUE WITHIN 72 HOURS OF DEPARTURE. FARE DIFFERENCE APPLIES.
AFTER DEPARTURE / NO-SHOW
  CHANGES NOT PERMITTED.` },
  };
  const LANGS = [["en", "English"], ["ar", "Arabic"], ["fr", "French"], ["de", "German"], ["es", "Spanish"], ["ur", "Urdu"], ["hi", "Hindi"], ["ru", "Russian"], ["tr", "Turkish"], ["zh", "Chinese"]];
  const langOptions = () => LANGS.map(([c, n]) => `<option value="${c}">${n}</option>`).join("");
  const cachePill = (c) => ({ "redis-hit": `<span class="pill pill-ok">cache · redis</span>`, "postgres-hit": `<span class="pill pill-ok">cache · postgres</span>`, generated: `<span class="pill pill-warn">generated by model</span>` }[c] || "");
  const SUGGESTIONS = ["What does it cost to cancel the outbound flight?", "Can I change the return flight the day before departure?", "What happens if I miss the flight?", "Are taxes refundable if I cancel?"];

  async function renderPlayground(page) {
    const today = new Date();
    const plus = (d) => new Date(today.getTime() + d * 86400000).toISOString().slice(0, 10);
    const tab = (location.hash.split("?")[1] || "").includes("chat") ? "chat" : "summary";

    page.innerHTML = `
      <div class="pg-toolbar">
        <div class="tabs" role="tablist">
          <button class="tab ${tab === "summary" ? "active" : ""}" data-tab="summary" role="tab">Summary</button>
          <button class="tab ${tab === "chat" ? "active" : ""}" data-tab="chat" role="tab">Chat</button>
        </div>
        <span class="muted small">Runs with your admin session · recorded in <a href="#/usage">Usage</a></span>
      </div>

      <section id="tab-summary" class="pg-tab" ${tab !== "summary" ? "hidden" : ""}>
        <div class="pg-split">
          <div class="card pg-pane">
            <div class="card-head">
              <div><h3>Fare rules</h3><span class="muted small">Paste raw airline text — HTML and uppercase are fine</span></div>
              <div class="inline"><select id="sum-sample" class="btn btn-sm select-btn"><option value="">Load sample…</option>${Object.entries(SAMPLES).map(([k, v]) => `<option value="${k}">${esc(v.label)}</option>`).join("")}</select></div>
            </div>
            <form id="sum-form" class="pg-form">
              <textarea name="fare_rules_text" class="rules-input" spellcheck="false">${esc(SAMPLES.saver.text)}</textarea>
              <div class="pg-options">
                <label class="opt"><span>Language</span><select name="lang">${langOptions()}</select></label>
                <label class="opt"><span>Layout</span>
                  <span class="segmented small"><label><input type="radio" name="layout" value="desktop" checked/><span>Desktop</span></label><label><input type="radio" name="layout" value="mobile"/><span>Mobile</span></label></span>
                </label>
                <label class="checkbox"><input type="checkbox" name="bypass_cache"/> Bypass cache</label>
                <span class="muted small" id="sum-count"></span>
              </div>
              <div class="form-actions"><button class="btn btn-primary" type="submit">Summarise →</button></div>
            </form>
          </div>
          <div class="card pg-pane">
            <div class="card-head">
              <div><h3>Result</h3><span class="muted small" id="sum-meta">Nothing generated yet</span></div>
              <div class="inline" id="sum-actions" hidden><button class="btn btn-sm" id="sum-copy">Copy Markdown</button><button class="btn btn-sm btn-ghost" id="sum-raw">Raw</button></div>
            </div>
            <div id="sum-out" class="result-body md">
              <div class="empty-state"><div class="empty-art">📄</div><p>Your summary will appear here — a table on desktop, compact sections on mobile.</p></div>
            </div>
          </div>
        </div>
      </section>

      <section id="tab-chat" class="pg-tab" ${tab !== "chat" ? "hidden" : ""}>
        <div class="pg-split pg-split-chat">
          <div class="card pg-pane">
            <div class="card-head">
              <div><h3>Itinerary</h3><span class="muted small">One segment per flight, each with its own fare rules</span></div>
              <button class="btn btn-sm" id="seg-add" type="button">+ Segment</button>
            </div>
            <div id="segments" class="segments-list"></div>
            <div class="pg-options" style="margin-top:12px">
              <label class="opt"><span>Language</span><select id="chat-lang">${langOptions()}</select></label>
              <label class="opt"><span>Layout</span>
                <span class="segmented small"><label><input type="radio" name="chat-layout" value="desktop" checked/><span>Desktop</span></label><label><input type="radio" name="chat-layout" value="mobile"/><span>Mobile</span></label></span>
              </label>
            </div>
          </div>
          <div class="card pg-pane chat-pane">
            <div class="card-head">
              <div><h3>Conversation</h3><span class="muted small" id="chat-meta">New conversation</span></div>
              <button class="btn btn-sm btn-ghost" id="chat-reset" type="button">Reset</button>
            </div>
            <div id="chat-log" class="chat-log">
              <div class="empty-state" id="chat-empty">
                <div class="empty-art">💬</div>
                <p>Ask anything about the itinerary's fare rules.</p>
                <div class="suggestions">${SUGGESTIONS.map((q) => `<button type="button" class="chip" data-suggest>${esc(q)}</button>`).join("")}</div>
              </div>
            </div>
            <form id="chat-form" class="composer">
              <input name="message" type="text" placeholder="e.g. Can I cancel the return flight on ${plus(9)}?" autocomplete="off" required />
              <button class="btn btn-primary" type="submit" aria-label="Send">Send</button>
            </form>
          </div>
        </div>
      </section>`;

    // ---- tabs
    $$(".tab", page).forEach((b) => b.addEventListener("click", () => {
      $$(".tab", page).forEach((x) => x.classList.toggle("active", x === b));
      $("#tab-summary").hidden = b.dataset.tab !== "summary";
      $("#tab-chat").hidden = b.dataset.tab !== "chat";
      history.replaceState(null, "", `#/playground?${b.dataset.tab}`);
    }));

    // ---- summary
    const sumForm = $("#sum-form");
    const rulesInput = sumForm.fare_rules_text;
    const updateCount = () => { $("#sum-count").textContent = `${fmtInt(rulesInput.value.length)} chars`; };
    rulesInput.addEventListener("input", updateCount); updateCount();
    $("#sum-sample").addEventListener("change", (e) => { if (SAMPLES[e.target.value]) { rulesInput.value = SAMPLES[e.target.value].text; updateCount(); } e.target.value = ""; });

    let lastMarkdown = "", showRaw = false;
    const paint = () => { const out = $("#sum-out"); out.innerHTML = showRaw ? `<pre class="raw">${esc(lastMarkdown)}</pre>` : renderMarkdown(lastMarkdown); };
    $("#sum-copy").addEventListener("click", async () => { try { await navigator.clipboard.writeText(lastMarkdown); toast("Copied", "success"); } catch { toast("Copy failed", "error"); } });
    $("#sum-raw").addEventListener("click", (e) => { showRaw = !showRaw; e.target.textContent = showRaw ? "Rendered" : "Raw"; paint(); });

    sumForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const out = $("#sum-out"), meta = $("#sum-meta"), btn = sumForm.querySelector("button[type=submit]");
      out.innerHTML = `<div class="empty-state"><span class="spinner"></span><p>Asking the model…</p></div>`;
      meta.textContent = "Working…"; btn.disabled = true; $("#sum-actions").hidden = true;
      const mobile = sumForm.layout.value === "mobile";
      const body = { fare_rules_text: rulesInput.value, lang: sumForm.lang.value, is_mobile_view: mobile, bypass_cache: sumForm.bypass_cache.checked };
      try {
        const res = await fetch("/api/v1/fare-rules/summary", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        const data = await res.json();
        if (!res.ok) throw new ApiError(res.status, data);
        lastMarkdown = data.summary_markdown; showRaw = false; $("#sum-raw").textContent = "Raw";
        out.setAttribute("dir", RTL.has(data.lang) ? "rtl" : "ltr");
        out.classList.toggle("mobile-preview", mobile);
        paint();
        meta.innerHTML = `${cachePill(data.cache)} <span class="mono">${esc(data.provider || "")} · ${esc(data.model || "")}</span> · ${fmtInt(data.latency_ms)} ms`;
        $("#sum-actions").hidden = false;
      } catch (err) {
        out.innerHTML = `<div class="empty-state"><div class="empty-art">⚠️</div><p class="form-error">${esc(err.message)}</p></div>`;
        meta.textContent = "Failed";
      } finally { btn.disabled = false; }
    });

    // ---- chat
    const segmentsEl = $("#segments");
    let convoId = null, chatHistory = [];
    const addSegment = (src = "", dst = "", date = "", rules = "") => {
      const div = document.createElement("div");
      div.className = "segment";
      div.innerHTML = `
        <div class="segment-head">
          <div class="route"><input name="source_airport" maxlength="3" value="${esc(src)}" placeholder="DEL" required /><span class="arrow">→</span><input name="destination_airport" maxlength="3" value="${esc(dst)}" placeholder="DXB" required /></div>
          <input name="departure_date" type="date" value="${esc(date)}" required />
          <button class="btn btn-sm btn-ghost" type="button" data-remove title="Remove segment">✕</button>
        </div>
        <details open><summary><span class="seg-label">Fare rules for this segment</span> <span class="seg-count muted small"></span> <span class="muted small seg-hint">(click to collapse)</span></summary>
          <textarea name="fare_rules_text" rows="7" class="rules-input small" spellcheck="false" placeholder="Paste the raw fare rules / penalties text for this flight here (HTML and uppercase are fine)">${esc(rules)}</textarea>
        </details>`;
      const count = () => { div.querySelector(".seg-count").textContent = `· ${fmtInt(div.querySelector("textarea").value.length)} chars`; };
      div.querySelector("textarea").addEventListener("input", count); count();
      div.querySelector("[data-remove]").addEventListener("click", () => { div.remove(); convoId = null; });
      div.addEventListener("input", () => { convoId = null; $("#chat-meta").textContent = "Itinerary changed — next message starts a new conversation"; });
      segmentsEl.appendChild(div);
    };
    addSegment("DEL", "DXB", plus(7), SAMPLES.saver.text);
    addSegment("DXB", "DEL", plus(14), SAMPLES.saver.text.replace(/SAR 150/, "SAR 300").replace(/SAR 100/, "SAR 200"));
    $("#seg-add").addEventListener("click", () => addSegment("", "", plus(21), ""));
    const resetChat = () => { convoId = null; chatHistory = []; $("#chat-meta").textContent = "New conversation"; $$("#chat-log .msg").forEach((m) => m.remove()); $("#chat-empty").hidden = false; };
    $("#chat-reset").addEventListener("click", resetChat);
    $$("[data-suggest]", page).forEach((b) => b.addEventListener("click", () => { $("#chat-form").message.value = b.textContent; $("#chat-form").requestSubmit(); }));

    const collectSegments = () => $$(".segment", segmentsEl).map((seg) => ({
      source_airport: seg.querySelector("[name=source_airport]").value.trim().toUpperCase(),
      destination_airport: seg.querySelector("[name=destination_airport]").value.trim().toUpperCase(),
      departure_date: seg.querySelector("[name=departure_date]").value,
      fare_rules_text: seg.querySelector("[name=fare_rules_text]").value,
    }));
    const addMsg = (role, html, dir) => {
      const log = $("#chat-log");
      $("#chat-empty").hidden = true;
      const el = document.createElement("div");
      el.className = `msg msg-${role}`;
      el.innerHTML = `<div class="msg-avatar">${role === "user" ? "You" : `<span class="brand-mark sm"></span>`}</div><div class="msg-body md" ${dir ? `dir="${dir}"` : ""}>${html}</div>`;
      log.appendChild(el); log.scrollTop = log.scrollHeight;
      return el.querySelector(".msg-body");
    };

    $("#chat-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = e.target.message, sendBtn = e.target.querySelector("button");
      const message = input.value.trim();
      if (!message) return;
      const lang = $("#chat-lang").value;
      const mobile = page.querySelector("input[name=chat-layout]:checked").value === "mobile";
      addMsg("user", esc(message));
      const bubble = addMsg("assistant", `<span class="typing"><i></i><i></i><i></i></span>`, RTL.has(lang) ? "rtl" : "ltr");
      input.value = ""; sendBtn.disabled = true;
      const body = { user_message: message, history: chatHistory, lang, is_mobile_view: mobile };
      if (convoId) body.convo_id = convoId; else body.segments = collectSegments();
      let answer = "";
      try {
        const res = await fetch("/api/v1/fare-rules/chat/stream", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        if (!res.ok) { const data = await res.json(); throw new ApiError(res.status, data); }
        const reader = res.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
        const handle = (evt, data) => {
          if (evt === "start") { convoId = data.convo_id; $("#chat-meta").textContent = `Conversation ${data.convo_id.slice(0, 8)}… · ${data.segments.length} segment${data.segments.length > 1 ? "s" : ""}`; }
          else if (evt === "token") { answer += data.content; bubble.innerHTML = renderMarkdown(answer); $("#chat-log").scrollTop = $("#chat-log").scrollHeight; }
          else if (evt === "end") { $("#chat-meta").innerHTML = `Conversation ${data.convo_id.slice(0, 8)}… · <span class="mono">${esc(data.model)}</span> · ${fmtInt(data.usage.total_tokens)} tokens · ${fmtInt(data.latency_ms)} ms`; }
          else if (evt === "error") throw new Error(data.error.message);
          else if (evt === "info") toast(data.message);
        };
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let idx;
          while ((idx = buffer.indexOf("\n\n")) !== -1) {
            const chunk = buffer.slice(0, idx); buffer = buffer.slice(idx + 2);
            let evt = "message", data = "";
            chunk.split("\n").forEach((l) => { if (l.startsWith("event:")) evt = l.slice(6).trim(); else if (l.startsWith("data:")) data += l.slice(5).trim(); });
            if (data) handle(evt, JSON.parse(data));
          }
        }
        chatHistory.push({ role: "user", content: message }, { role: "assistant", content: answer || "(no answer)" });
      } catch (err) {
        bubble.innerHTML = `<span class="form-error">${esc(err.message)}</span>`;
      } finally { sendBtn.disabled = false; input.focus(); }
    });
  }

  // -------------------------------------------------------------- prompts
  async function renderPrompts(page) {
    const data = await api.get("/api/admin/prompts");
    let prompts = data.prompts;
    let selectedId = (location.hash.split("?")[1] || "").replace("id=", "") || prompts[0].id;

    page.innerHTML = `
      <div class="prompts-layout">
        <div class="card prompt-list">
          <div class="card-head"><h3>Templates</h3></div>
          <div id="prompt-nav"></div>
          <p class="muted small" style="margin-top:12px">Files under <code>prompts/</code> are the defaults. Edits made here are stored in the database and take effect immediately on every instance.</p>
        </div>
        <div class="card prompt-editor">
          <div id="prompt-editor"></div>
        </div>
      </div>`;

    const navEl = $("#prompt-nav");
    const editorEl = $("#prompt-editor");

    function renderNav() {
      const groups = [["summary", "Summary"], ["chat", "Chat"]];
      navEl.innerHTML = groups.map(([f, label]) => `
        <div class="prompt-group"><span class="spec-label">${label}</span>
          ${prompts.filter((p) => p.feature === f).map((p) => `
            <button type="button" class="prompt-item ${p.id === selectedId ? "active" : ""}" data-id="${p.id}">
              <span>${esc(p.label.replace(/^.*·\s*/, ""))}</span>
              ${p.is_overridden ? `<span class="pill pill-warn">customised</span>` : `<span class="pill pill-muted">default</span>`}
            </button>`).join("")}
        </div>`).join("");
      $$(".prompt-item", navEl).forEach((b) => b.addEventListener("click", () => { selectedId = b.dataset.id; history.replaceState(null, "", `#/prompts?id=${selectedId}`); renderNav(); renderEditor(); }));
    }

    function renderEditor() {
      const p = prompts.find((x) => x.id === selectedId);
      editorEl.innerHTML = `
        <div class="card-head">
          <div><h3>${esc(p.label)}</h3><span class="muted small">${esc(p.description)}</span></div>
          ${p.is_overridden ? `<span class="pill pill-warn">customised${p.updated_by ? ` · ${esc(p.updated_by)}` : ""}</span>` : `<span class="pill pill-muted">default</span>`}
        </div>
        <div class="placeholders">
          ${p.allowed_placeholders.length ? p.allowed_placeholders.map((ph) => `<code class="ph ${p.required_placeholders.includes(ph) ? "ph-req" : ""}" title="${p.required_placeholders.includes(ph) ? "required" : "optional"}">[[${ph}]]</code>`).join("") : `<span class="muted small">No placeholders — plain instructions inserted into the system prompt.</span>`}
        </div>
        <textarea id="prompt-text" class="rules-input prompt-text" spellcheck="false">${esc(p.content)}</textarea>
        <div class="pg-options">
          <span class="muted small" id="prompt-count"></span>
          <label class="opt"><span>Preview language</span><select id="prompt-lang">${langOptions()}</select></label>
          <label class="checkbox"><input type="checkbox" id="prompt-mobile"/> Mobile layout</label>
          ${p.feature === "summary" ? `<label class="checkbox"><input type="checkbox" id="prompt-clear" checked/> Clear summary cache on save</label>` : ""}
        </div>
        <p class="form-error" id="prompt-error" hidden></p>
        <div class="form-actions">
          <button class="btn" id="prompt-preview">Preview with sample</button>
          <button class="btn btn-primary" id="prompt-save">Save</button>
          <button class="btn btn-ghost" id="prompt-default">Show default</button>
          ${p.is_overridden ? `<button class="btn btn-danger" id="prompt-reset">Reset to default</button>` : ""}
          <span class="muted small" id="prompt-status"></span>
        </div>
        <div id="prompt-preview-out" hidden>
          <div class="card-head" style="margin-top:16px"><h3>Preview</h3><span class="muted small" id="prompt-preview-meta"></span></div>
          <div class="md result-body" id="prompt-preview-md"></div>
          <details style="margin-top:10px"><summary class="muted small">Rendered prompt sent to the model</summary><pre class="raw" id="prompt-preview-raw"></pre></details>
        </div>`;

      const ta = $("#prompt-text");
      const count = () => { $("#prompt-count").textContent = `${fmtInt(ta.value.length)} chars${ta.value.trim() !== p.content.trim() ? " · unsaved changes" : ""}`; };
      ta.addEventListener("input", count); count();
      const showErr = (m) => { const e = $("#prompt-error"); e.textContent = m; e.hidden = !m; };

      $("#prompt-default").addEventListener("click", () => {
        const m = modal(`<h3>Default prompt</h3><pre class="raw" style="max-height:60vh;overflow:auto">${esc(p.default)}</pre><div class="form-actions"><button class="btn btn-primary" data-use>Use default text</button><button class="btn" data-close>Close</button></div>`);
        $("[data-use]", m.root).addEventListener("click", () => { ta.value = p.default; count(); m.close(); });
      });

      $("#prompt-preview").addEventListener("click", async () => {
        showErr(""); const btn = $("#prompt-preview"); btn.disabled = true; $("#prompt-status").innerHTML = `<span class="spinner"></span> Asking the model…`;
        try {
          const r = await api.post(`/api/admin/prompts/${p.id}/preview`, { content: ta.value, lang: $("#prompt-lang").value, is_mobile_view: $("#prompt-mobile").checked });
          $("#prompt-preview-out").hidden = false;
          $("#prompt-preview-md").setAttribute("dir", RTL.has($("#prompt-lang").value) ? "rtl" : "ltr");
          $("#prompt-preview-md").innerHTML = renderMarkdown(r.output_markdown);
          $("#prompt-preview-raw").textContent = r.rendered_prompt;
          $("#prompt-preview-meta").textContent = `${r.provider} · ${r.model} · ${fmtInt((r.usage.prompt_tokens || 0) + (r.usage.completion_tokens || 0))} tokens · ${r.latency_ms} ms`;
          $("#prompt-status").textContent = "";
        } catch (err) { showErr(err.message); $("#prompt-status").textContent = ""; } finally { btn.disabled = false; }
      });

      $("#prompt-save").addEventListener("click", async () => {
        showErr("");
        try {
          const clear = $("#prompt-clear"); const r = await api.put(`/api/admin/prompts/${p.id}`, { content: ta.value, clear_summary_cache: clear ? clear.checked : false });
          prompts = prompts.map((x) => (x.id === p.id ? r.prompt : x));
          toast(`Saved${r.cache_cleared ? ` · cleared ${r.cache_cleared.postgres} cached summaries` : ""}`, "success");
          renderNav(); renderEditor();
        } catch (err) { showErr(err.message); }
      });

      const reset = $("#prompt-reset");
      if (reset) reset.addEventListener("click", async () => {
        if (!(await confirmDialog("Reset to the default prompt?", "Your customised text will be discarded.", "Reset", true))) return;
        const r = await api.del(`/api/admin/prompts/${p.id}`);
        prompts = prompts.map((x) => (x.id === p.id ? r.prompt : x));
        toast("Reset to default"); renderNav(); renderEditor();
      });
    }

    renderNav(); renderEditor();
  }

  // -------------------------------------------------------------- account
  async function renderAccount(page) {
    page.innerHTML = `
      <div class="grid grid-2">
        <div class="card">
          <div class="card-head"><h3>Change password</h3></div>
          <form id="pw-form">
            <label class="field"><span>Current password</span><input name="current_password" type="password" autocomplete="current-password" required /></label>
            <label class="field"><span>New password</span><input name="new_password" type="password" autocomplete="new-password" minlength="8" required /><span class="help">At least 8 characters.</span></label>
            <label class="field"><span>Confirm new password</span><input name="confirm" type="password" autocomplete="new-password" minlength="8" required /></label>
            <p class="form-error" id="pw-error" hidden></p>
            <div class="form-actions"><button class="btn btn-primary" type="submit">Update password</button></div>
          </form>
        </div>
        <div class="card">
          <div class="card-head"><h3>Username</h3></div>
          <form id="name-form">
            <label class="field"><span>Username</span><input name="username" type="text" value="${esc(state.user.username)}" minlength="3" pattern="[A-Za-z0-9._@-]+" required /></label>
            <div class="form-actions"><button class="btn" type="submit">Rename</button></div>
          </form>
          <h3 style="margin-top:20px">Session</h3>
          <p class="muted small">Sessions are signed cookies valid for a limited time; sign out to end one early.</p>
        </div>
      </div>`;
    $("#pw-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.target, errEl = $("#pw-error");
      errEl.hidden = true;
      if (f.new_password.value !== f.confirm.value) { errEl.textContent = "Passwords do not match."; errEl.hidden = false; return; }
      try {
        await api.post("/api/admin/auth/change-password", { current_password: f.current_password.value, new_password: f.new_password.value });
        toast("Password updated", "success");
        state.user.must_change_password = false;
        $("#password-banner").hidden = true;
        f.reset();
      } catch (err) { errEl.textContent = err.message; errEl.hidden = false; }
    });
    $("#name-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const r = await api.post("/api/admin/auth/rename", { username: e.target.username.value.trim() });
        state.user.username = r.username;
        $("#topbar-user").textContent = r.username;
        toast("Username updated", "success");
      } catch (err) { toast(err.message, "error"); }
    });
  }

  // ----------------------------------------------------------------- boot
  (async () => {
    try {
      const me = await api.get("/api/admin/auth/me");
      state.user = me.user;
      await showApp();
    } catch {
      showLogin();
    }
  })();
})();
