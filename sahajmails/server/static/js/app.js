/* SahajMails UI.
 *
 * Vanilla ES modules, no framework. A local tool that holds mail credentials is
 * better off with no third-party JavaScript in it at all, and the app is small
 * enough that explicit render functions stay clearer than a reactive layer.
 */

import { api, ApiError } from "./api.js";

const $ = (id) => document.getElementById(id);
const el = (sel, root = document) => root.querySelector(sel);
const els = (sel, root = document) => [...root.querySelectorAll(sel)];
const icon = (name) => `<svg class="icon-svg" aria-hidden="true"><use href="#i-${name}"/></svg>`;

/** Escape untrusted text before it goes anywhere near innerHTML. */
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );

const num = (n) => Number(n ?? 0).toLocaleString();

/** Views reachable from the sidebar, and which nav item owns the deeper ones. */
const NAV_OWNER = {
  campaigns: "campaigns",
  editor: "campaigns",
  review: "campaigns",
  send: "campaigns",
  runs: "runs",
  suppression: "suppression",
  settings: "settings",
  ai: "ai",
  plugins: "plugins",
};

const FORMAT_HINT = {
  rich: "Use the toolbar. Placeholders like {{ first_name }} still work.",
  plain: "No formatting at all — exactly what you type is what they read. Best for formal mail.",
  markdown: "**bold**, # heading, [link](url), - list.",
  html: "Paste a complete HTML document. It is sent untouched.",
};

const state = {
  view: "campaigns",
  settings: {},
  providers: [],
  aiProviders: [],
  campaigns: [],
  configured: false,
  campaign: null,
  contacts: null,
  recipient: 0,
  previewMode: "desktop",
  format: "rich",
  jobId: null,
  lastPreflight: null,
  dirty: false,
  suppressedCount: 0,
};

// ═══════════════════════════════════════════════════════════════ toasts ═══

function toast(message, { kind = "ok", hint = "", timeout = 5200 } = {}) {
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.innerHTML =
    icon(kind === "error" ? "alert" : "check") +
    `<div><strong>${esc(message)}</strong>${hint ? `<small>${esc(hint)}</small>` : ""}</div>`;
  $("toasts").append(node);
  setTimeout(() => {
    node.style.opacity = "0";
    setTimeout(() => node.remove(), 200);
  }, timeout);
}

function reportError(error) {
  if (error instanceof ApiError) {
    toast(error.message, { kind: "error", hint: error.hint, timeout: 10000 });
  } else {
    toast(String(error?.message || error), { kind: "error" });
  }
  console.error(error);
}

const guard =
  (fn) =>
  async (...args) => {
    try {
      return await fn(...args);
    } catch (error) {
      reportError(error);
      return undefined;
    }
  };

// ═══════════════════════════════════════════════════════════════ routing ═══

function show(view) {
  state.view = view;
  els(".view").forEach((n) => n.classList.toggle("is-on", n.id === `view-${view}`));

  // The sidebar always reflects where you are. Editor, review and send are all
  // "inside" Campaigns, so that item stays lit rather than the nav going blank.
  const owner = NAV_OWNER[view] ?? view;
  els(".nav-item").forEach((n) => n.classList.toggle("is-on", n.dataset.view === owner));

  if (location.hash.slice(1) !== view) history.replaceState(null, "", `#${view}`);
  $("main").scrollTop = 0;

  if (view === "runs") void loadRuns();
  if (view === "suppression") void loadSuppression();
  if (view === "plugins") void loadPlugins();
}

// ═════════════════════════════════════════════════════════════ bootstrap ═══

async function boot() {
  applyStoredTheme();
  const data = await api.get("/api/state");

  state.settings = data.settings;
  state.providers = data.providers;
  state.aiProviders = data.ai_providers || [];
  state.configured = data.configured;
  $("version").textContent = `v${data.version}`;
  $("plugins-dir").textContent = data.plugins_dir ? `Plugins folder: ${data.plugins_dir}` : "";

  fillSettings(data.settings);
  fillProviders(data.providers, data.settings.provider);
  fillAiProviders(data.settings);
  renderCampaigns(data.campaigns);
  setSuppressedCount(data.suppressed_count ?? 0);

  $("boot").hidden = true;
  $("app").hidden = false;

  const wanted = location.hash.slice(1);
  show(wanted && el(`#view-${wanted}`) && wanted !== "editor" ? wanted : "campaigns");
}

function applyStoredTheme() {
  const stored = localStorage.getItem("sahajmails-theme");
  if (stored) document.documentElement.dataset.theme = stored;
}

function toggleTheme() {
  const current =
    document.documentElement.dataset.theme ||
    (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = current === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("sahajmails-theme", next);
}

// ══════════════════════════════════════════════════════════════ settings ═══

function fillProviders(providers, selected) {
  $("s-provider").innerHTML = providers
    .map(
      (p) =>
        `<option value="${esc(p.key)}"${p.key === selected ? " selected" : ""}>${esc(p.label)}</option>`,
    )
    .join("");
  updateProviderHint();
}

function fillSettings(s) {
  const map = {
    "s-sender-email": s.sender_email,
    "s-sender-name": s.sender_name,
    "s-reply-to": s.reply_to,
    "s-host": s.smtp_host,
    "s-port": s.smtp_port,
    "s-security": s.security,
    "s-unsub-mailto": s.unsubscribe_mailto,
    "s-unsub-url": s.unsubscribe_url,
    "s-rate": s.rate_per_minute,
    "s-concurrency": s.concurrency,
    "ai-model": s.ai_model,
    "ai-base-url": s.ai_base_url,
  };
  for (const [id, value] of Object.entries(map)) if ($(id)) $(id).value = value ?? "";
}

function currentProvider() {
  return state.providers.find((p) => p.key === $("s-provider").value);
}

function updateProviderHint() {
  const p = currentProvider();
  if (!p) return;
  $("custom-smtp").hidden = p.key !== "custom";
  $("provider-hint").textContent = p.setup_hint || "";
  $("quota-hint").textContent = p.daily_limit
    ? `${p.label} allows roughly ${num(p.daily_limit)} messages a day.`
    : `${p.label} does not publish a fixed daily limit.`;
  if (p.key !== "custom") {
    $("s-host").value = p.host;
    $("s-port").value = p.port;
    if (!$("s-rate").value) $("s-rate").value = p.rate_per_minute;
  }
}

function settingsPayload() {
  return {
    sender_email: $("s-sender-email").value.trim(),
    sender_name: $("s-sender-name").value.trim(),
    reply_to: $("s-reply-to").value.trim(),
    provider: $("s-provider").value,
    smtp_host: $("s-host").value.trim(),
    smtp_port: Number($("s-port").value) || 0,
    security: $("s-security").value,
    smtp_username: $("s-sender-email").value.trim(),
    smtp_password: $("s-password").value,
    rate_per_minute: Number($("s-rate").value) || 0,
    concurrency: Number($("s-concurrency").value) || 0,
    unsubscribe_mailto: $("s-unsub-mailto").value.trim(),
    unsubscribe_url: $("s-unsub-url").value.trim(),
    remember_password: $("s-remember").checked,
  };
}

const saveSettings = guard(async () => {
  const result = await api.post("/api/settings", settingsPayload());
  state.settings = result.settings;
  state.configured = true;
  renderCampaigns(state.campaigns);
  toast("Settings saved.");
});

const testConnection = guard(async () => {
  const button = $("test-connection");
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Connecting…";
  try {
    const r = await api.post("/api/settings/test", settingsPayload());
    if (r.ok) toast(r.message);
    else toast(r.error, { kind: "error", hint: r.hint, timeout: 13000 });
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
});

// ── AI settings ───────────────────────────────────────────────────────────

function fillAiProviders(settings) {
  $("ai-provider").innerHTML =
    `<option value="">Off — no AI</option>` +
    state.aiProviders
      .map(
        (p) =>
          `<option value="${esc(p.key)}"${p.key === settings.ai_provider ? " selected" : ""}>${esc(p.label)}</option>`,
      )
      .join("");
  updateAiHint();
}

function updateAiHint() {
  const p = state.aiProviders.find((x) => x.key === $("ai-provider").value);
  $("ai-key-field").hidden = !p || !p.needs_key;
  $("ai-url-field").hidden = !p || !p.needs_base_url;
  $("ai-hint").textContent = p?.hint || "";
  $("ai-models").innerHTML = (p?.models || [])
    .map((m) => `<option value="${esc(m)}"></option>`)
    .join("");
  if (p && !$("ai-model").value && p.models?.length) $("ai-model").value = p.models[0];
}

const saveAi = guard(async () => {
  const r = await api.post("/api/settings", {
    ai_provider: $("ai-provider").value,
    ai_model: $("ai-model").value.trim(),
    ai_api_key: $("ai-key").value,
    ai_base_url: $("ai-base-url").value.trim(),
  });
  state.settings = r.settings;
  toast("AI settings saved.");
});

const testAi = guard(async () => {
  const button = $("ai-test");
  button.disabled = true;
  button.textContent = "Testing…";
  try {
    const r = await api.post("/api/ai/test", {
      provider: $("ai-provider").value,
      model: $("ai-model").value.trim(),
      api_key: $("ai-key").value,
      base_url: $("ai-base-url").value.trim(),
    });
    if (r.ok) toast(r.message, { hint: r.sample ? `Sample: ${r.sample}` : "" });
    else toast(r.error, { kind: "error", hint: r.hint, timeout: 13000 });
  } finally {
    button.disabled = false;
    button.textContent = "Test connection";
  }
});

// ═════════════════════════════════════════════════════════════ campaigns ═══

function setSuppressedCount(n) {
  state.suppressedCount = n;
  const badge = $("count-suppressed");
  badge.textContent = num(n);
  badge.hidden = !n;
}

function renderCampaigns(campaigns) {
  state.campaigns = campaigns;
  const list = $("campaign-list");
  const badge = $("count-campaigns");
  badge.textContent = num(campaigns.length);
  badge.hidden = !campaigns.length;

  // The welcome panel lives inside Campaigns rather than being its own
  // unreachable view — an orphan page with no nav item is how you strand people.
  const fresh = !state.configured && campaigns.length === 0;
  $("welcome-panel").hidden = !fresh;
  $("campaigns-sub").textContent = campaigns.length
    ? `${num(campaigns.length)} campaign${campaigns.length === 1 ? "" : "s"}`
    : "";

  if (!campaigns.length) {
    list.innerHTML = fresh
      ? ""
      : `<div class="empty"><strong>No campaigns yet</strong>Create one to get started.</div>`;
    return;
  }

  list.innerHTML = campaigns
    .map(
      (c) => `
      <div class="campaign-card" data-id="${esc(c.id)}" role="button" tabindex="0"
           aria-label="Open ${esc(c.name)}">
        <div class="grow">
          <h3>${esc(c.name)}</h3>
          <p>${esc(c.subject || "No subject yet")} · edited ${esc(when(c.updated_at))}</p>
        </div>
        <button class="btn small danger" data-delete="${esc(c.id)}" title="Delete">
          ${icon("trash")}
        </button>
      </div>`,
    )
    .join("");
}

function when(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 16);
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 1440) return `${Math.round(mins / 60)} h ago`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

const refreshCampaigns = guard(async () => {
  renderCampaigns(await api.get("/api/campaigns"));
});

function loadEditor(campaign, contacts) {
  state.campaign = campaign;
  state.contacts = contacts || null;
  state.recipient = 0;
  state.dirty = false;

  $("c-name").value = campaign.name || "";
  $("crumb-name").textContent = campaign.name || "Untitled";
  $("c-subject").value = campaign.subject || "";
  $("c-preheader").value = campaign.preheader || "";
  setFormat(campaign.config?.format || "rich", campaign.body || "");
  markSaved(true);
  renderContacts(state.contacts);
  void refreshPreview();
  show("editor");
}

function newCampaign() {
  loadEditor({ id: null, name: "Untitled campaign", subject: "", body: "", preheader: "" });
  setTimeout(() => $("c-name").select(), 60);
}

const openCampaign = guard(async (id) => {
  const campaign = await api.get(`/api/campaigns/${id}`);
  let contacts = null;
  if (campaign.contacts) {
    try {
      contacts = await api.get(`/api/contacts/${campaign.contacts}`);
    } catch {
      toast("That contact file is no longer on disk.", {
        kind: "error",
        hint: "Load it again to keep sending this campaign.",
      });
    }
  }
  loadEditor(campaign, contacts);
});

function campaignPayload() {
  return {
    id: state.campaign?.id ?? null,
    name: $("c-name").value.trim() || "Untitled campaign",
    subject: $("c-subject").value,
    body: bodyValue(),
    preheader: $("c-preheader").value,
    contacts: state.contacts?.token ?? "",
    config: { format: state.format },
  };
}

function markSaved(saved) {
  const node = $("save-state");
  node.textContent = saved ? "Saved" : "Unsaved";
  node.classList.toggle("is-saved", saved);
}

const saveCampaign = guard(async ({ quiet = true } = {}) => {
  const saved = await api.post("/api/campaigns", campaignPayload());
  state.campaign = saved;
  state.dirty = false;
  markSaved(true);
  $("crumb-name").textContent = saved.name;
  // Keep the list in step so a rename shows up without a page refresh.
  await refreshCampaigns();
  if (!quiet) toast("Campaign saved.");
  return saved;
});

// ══════════════════════════════════════════════════════════════ contacts ═══

function renderContacts(contacts) {
  const zone = $("dropzone");
  const loaded = $("contacts-loaded");

  if (!contacts) {
    zone.hidden = false;
    loaded.hidden = true;
    $("change-contacts").hidden = true;
    $("placeholder-field").hidden = true;
    $("contacts-issues").innerHTML = "";
    updateRecipientLabel();
    return;
  }

  zone.hidden = true;
  loaded.hidden = false;
  $("change-contacts").hidden = false;

  $("contacts-count").textContent =
    `${num(contacts.count)} contact${contacts.count === 1 ? "" : "s"} ready`;
  $("contacts-meta").textContent =
    `${contacts.filename || "list"} · ${contacts.columns.length} columns`;

  renderContactsTable(contacts);

  // Chips are strictly the columns in the file, so you can only insert a
  // placeholder that will actually resolve.
  $("placeholder-field").hidden = false;
  $("chips").innerHTML = contacts.columns
    .map(
      (c) =>
        `<button class="chip" data-col="${esc(c.key)}" title="${esc(c.label)}">{{ ${esc(c.key)} }}</button>`,
    )
    .join("");

  const issues = contacts.issues;
  const parts = [];
  if (issues.invalid.length) {
    const sample = issues.invalid
      .slice(0, 3)
      .map((i) => `row ${i.row} (${i.reason})`)
      .join(", ");
    parts.push(`${issues.invalid.length} row(s) skipped: ${sample}`);
  }
  if (issues.duplicates) parts.push(`${issues.duplicates} duplicate(s) removed`);
  if (issues.blank_rows) parts.push(`${issues.blank_rows} blank row(s) ignored`);
  $("contacts-issues").innerHTML = parts.length
    ? `<p class="hint">${esc(parts.join(" · "))}</p>`
    : "";

  updateRecipientLabel();
}

/** Show the actual spreadsheet, so you can confirm you loaded the right file. */
function renderContactsTable(contacts) {
  const cols = contacts.columns;
  $("contacts-table").innerHTML = `
    <table>
      <thead><tr><th>#</th>${cols.map((c) => `<th>${esc(c.label)}</th>`).join("")}</tr></thead>
      <tbody>
        ${contacts.preview
          .map(
            (row) =>
              `<tr><td>${row.row}</td>${cols
                .map((c) => `<td title="${esc(row.fields[c.key] ?? "")}">${esc(row.fields[c.key] ?? "")}</td>`)
                .join("")}</tr>`,
          )
          .join("")}
      </tbody>
    </table>
    ${
      contacts.count > contacts.preview.length
        ? `<p class="hint" style="padding:10px 12px;margin:0">Showing the first
             ${contacts.preview.length} of ${num(contacts.count)}.</p>`
        : ""
    }`;
}

const uploadContacts = guard(async (file) => {
  state.contacts = await api.upload("/api/contacts/upload", file);
  state.recipient = 0;
  renderContacts(state.contacts);
  await refreshPreview();
  if (state.campaign) await saveCampaign();
  toast(`Loaded ${num(state.contacts.count)} contacts from ${state.contacts.filename}.`);
});

const useSample = guard(async () => {
  state.contacts = await api.post("/api/contacts/sample");
  state.recipient = 0;
  renderContacts(state.contacts);
  await refreshPreview();
});

function recipientCount() {
  return Math.min(state.contacts?.count ?? 0, state.contacts?.preview?.length ?? 0);
}

function updateRecipientLabel() {
  const total = state.contacts?.count ?? 0;
  const label = $("recipient-label");
  if (!total) {
    label.textContent = "no contacts yet";
    $("prev-recipient").disabled = true;
    $("next-recipient").disabled = true;
    return;
  }
  const row = state.contacts.preview[state.recipient];
  label.textContent = row ? row.email : `contact ${state.recipient + 1}`;
  label.title = `${state.recipient + 1} of ${num(total)}`;
  $("prev-recipient").disabled = state.recipient === 0;
  $("next-recipient").disabled = state.recipient >= recipientCount() - 1;
}

// ════════════════════════════════════════════════════════════════ editor ═══

function bodyValue() {
  return state.format === "rich" ? $("c-rich").innerHTML.trim() : $("c-body").value;
}

function setFormat(format, body) {
  const previous = state.format;
  state.format = format;
  els("#format-seg .seg-item").forEach((n) =>
    n.classList.toggle("is-on", n.dataset.format === format),
  );
  $("format-hint").textContent = FORMAT_HINT[format] || "";

  const rich = format === "rich";
  $("rich-shell").hidden = !rich;
  $("c-body").hidden = rich;
  $("c-body").classList.toggle("plain-body", format === "plain");
  $("c-body").placeholder =
    format === "plain"
      ? "Dear {{ first_name }},\n\nWrite your letter exactly as you want it read.\n\nRegards,\nYour Name"
      : format === "html"
        ? "<!doctype html><html>…</html>"
        : "Hi {{ first_name | default('there') }},\n\n**Markdown** works here.";

  if (body !== undefined) {
    if (rich) $("c-rich").innerHTML = body;
    else $("c-body").value = body;
    return;
  }

  // Switching modes carries the text across rather than silently dropping it.
  if (rich && previous !== "rich") {
    $("c-rich").innerHTML = textToHtml($("c-body").value);
  } else if (!rich && previous === "rich") {
    $("c-body").value = format === "html" ? $("c-rich").innerHTML : htmlToText($("c-rich"));
  }
}

function textToHtml(text) {
  return text
    .split(/\n\s*\n/)
    .filter((b) => b.trim())
    .map((b) => `<p>${esc(b).replace(/\n/g, "<br>")}</p>`)
    .join("");
}

function htmlToText(node) {
  const clone = node.cloneNode(true);
  clone.querySelectorAll("br").forEach((br) => br.replaceWith("\n"));
  clone.querySelectorAll("p, div, li, h1, h2, h3, blockquote").forEach((b) => {
    b.append("\n\n");
  });
  return (clone.textContent || "").replace(/\n{3,}/g, "\n\n").trim();
}

let previewTimer = null;
let saveTimer = null;

/** Refresh the preview quickly, autosave a beat later. */
function onEdit() {
  state.dirty = true;
  markSaved(false);
  clearTimeout(previewTimer);
  previewTimer = setTimeout(() => void refreshPreview(), 250);
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => state.dirty && void saveCampaign(), 1100);
}

async function refreshPreview() {
  const body = bodyValue();
  const target = $("preview-body");

  if (!body.trim()) {
    target.innerHTML = `<div class="empty">Your email appears here as you type.</div>`;
    $("preview-meta").innerHTML = "";
    return;
  }

  try {
    const r = await api.post("/api/preview", {
      subject: $("c-subject").value,
      body,
      preheader: $("c-preheader").value,
      format: state.format,
      contacts: state.contacts?.token ?? "",
      index: state.recipient,
      missing_policy: "keep",
    });

    $("preview-meta").innerHTML = `
      <div class="to">To: ${esc(r.email)}</div>
      <div class="subject">${esc(r.subject || "(no subject)")}</div>
      ${r.preheader ? `<div class="pre">${esc(r.preheader)}</div>` : ""}`;

    target.className = `preview-body ${state.previewMode === "mobile" ? "mobile" : ""}`;
    if (state.previewMode === "text") {
      target.innerHTML = `<pre>${esc(r.text)}</pre>`;
    } else {
      // srcdoc + sandbox: the rendered email is untrusted content and must not
      // be able to script the app or reach the network.
      const frame = document.createElement("iframe");
      frame.setAttribute("sandbox", "");
      frame.title = "Email preview";
      frame.srcdoc = r.html;
      target.replaceChildren(frame);
    }
  } catch (error) {
    target.innerHTML = `<div class="empty"><strong>Cannot render yet</strong>${esc(error.message)}</div>`;
  }
}

// ════════════════════════════════════════════════════════════════ review ═══

const openReview = guard(async () => {
  if (!state.contacts) return toast("Add a contact list first.", { kind: "error" });
  if (!$("c-subject").value.trim()) return toast("Add a subject first.", { kind: "error" });
  if (!bodyValue().trim()) return toast("Write a message first.", { kind: "error" });

  await saveCampaign();
  show("review");
  $("preflight").innerHTML = `<div class="empty">Checking…</div>`;
  $("go-to-send").disabled = true;

  const report = await api.post("/api/preflight", sendPayload());
  state.lastPreflight = report;
  renderPreflight(report);
  $("go-to-send").disabled = !report.ok;
  if (!report.ok) {
    toast("Fix the blocking problems before sending.", { kind: "error" });
  }
  return undefined;
});

function renderPreflight(report) {
  const marks = { error: "✕", warning: "!", info: "✓" };
  const order = { error: 0, warning: 1, info: 2 };
  const findings = [...report.findings].sort((a, b) => order[a.level] - order[b.level]);
  $("preflight").innerHTML = findings.length
    ? findings
        .map(
          (f) => `
        <div class="finding ${f.level}">
          <span class="mark">${marks[f.level]}</span>
          <div>${esc(f.message)}${f.hint ? `<p class="hint">${esc(f.hint)}</p>` : ""}</div>
        </div>`,
        )
        .join("")
    : `<div class="empty">Nothing to report.</div>`;
}

function openSend() {
  show("send");
  $("console").hidden = true;
  const total = state.contacts?.count ?? 0;
  $("send-sub").textContent = `${num(total)} recipient${total === 1 ? "" : "s"}`;
  $("send-summary").innerHTML = `
    <div><dt>Recipients</dt><b>${num(total)}</b></div>
    <div><dt>From</dt><b>${esc(state.settings.sender_email || "not set")}</b></div>
    <div><dt>Provider</dt><b>${esc(currentProviderLabel())}</b></div>
    <div><dt>Speed</dt><b>${esc(state.settings.rate_per_minute)}/min</b></div>
    <div><dt>Estimated time</dt><b>${esc(estimate(total))}</b></div>`;
}

function currentProviderLabel() {
  return (
    state.providers.find((p) => p.key === state.settings.provider)?.label ||
    state.settings.provider
  );
}

function estimate(total) {
  const rate = Number(state.settings.rate_per_minute) || 30;
  const minutes = total / rate;
  if (minutes < 1) return "under a minute";
  if (minutes < 60) return `about ${Math.ceil(minutes)} min`;
  return `about ${(minutes / 60).toFixed(1)} h`;
}

// ══════════════════════════════════════════════════════════════════ send ═══

function sendPayload(extra = {}) {
  return {
    campaign_id: state.campaign?.id ?? null,
    subject: $("c-subject").value,
    body: bodyValue(),
    preheader: $("c-preheader").value,
    format: state.format,
    contacts: state.contacts?.token ?? "",
    dry_run: $("dry-run").checked,
    ...extra,
  };
}

const sendTest = guard(async () => {
  const address = $("test-to").value.trim();
  if (!address) return toast("Enter an address to test with.", { kind: "error" });

  const button = $("send-test");
  button.disabled = true;
  button.textContent = "Sending…";
  $("test-result").innerHTML = "";
  try {
    const { job_id } = await api.post("/api/send", sendPayload({ test_to: address }));
    api.events(job_id, {
      done: (data) => {
        const failure = data.failed?.[0];
        $("test-result").innerHTML = failure
          ? `<p class="hint" style="color:var(--danger)">Failed: ${esc(failure.error)}</p>`
          : `<p class="hint" style="color:var(--accent)">Sent to ${esc(address)} — check your inbox.</p>`;
        if (!failure) toast(`Test sent to ${address}.`);
        button.disabled = false;
        button.innerHTML = `${icon("send")} Send test`;
      },
    });
  } catch (error) {
    button.disabled = false;
    button.innerHTML = `${icon("send")} Send test`;
    throw error;
  }
  return undefined;
});

/** Show exactly what is about to happen and wait for a decision. */
function confirmSend() {
  const dialog = $("confirm-send");
  const dry = $("dry-run").checked;
  const total = state.contacts.count;
  const rows = state.contacts.preview.slice(0, 3).map((c) => `<li>${esc(c.email)}</li>`);
  const dupes = state.lastPreflight?.findings?.find((f) => f.category === "duplicates");
  const suppressed = state.lastPreflight?.findings?.find((f) => f.category === "suppression");

  $("confirm-title").textContent = dry
    ? `Write ${num(total)} .eml file${total === 1 ? "" : "s"}?`
    : `Send to ${num(total)} contact${total === 1 ? "" : "s"}?`;
  $("confirm-body").innerHTML = `
    <ul class="confirm-list">${rows.join("")}${
      total > 3 ? `<li>… and ${num(total - 3)} more</li>` : ""
    }</ul>
    ${dupes ? `<p class="confirm-warn">${icon("alert")}<span>${esc(dupes.message)}</span></p>` : ""}
    ${suppressed ? `<p class="confirm-warn">${icon("alert")}<span>${esc(suppressed.message)}</span></p>` : ""}
    <p class="muted">${
      dry
        ? "Nothing is sent. Files are written to your SahajMails outbox folder."
        : `Sending as ${esc(state.settings.sender_email)}, ${esc(estimate(total))}. This cannot be undone.`
    }</p>`;
  $("confirm-yes").textContent = dry ? "Write files" : "Send now";

  return new Promise((resolve) => {
    const done = (value) => {
      dialog.close();
      resolve(value);
    };
    $("confirm-yes").onclick = () => done(true);
    $("confirm-no").onclick = () => done(false);
    dialog.addEventListener("cancel", () => resolve(false), { once: true });
    dialog.showModal();
  });
}

const startSend = guard(async () => {
  if (!(await confirmSend())) return;

  const { job_id } = await api.post("/api/send", sendPayload());
  state.jobId = job_id;

  $("console").hidden = false;
  $("send-log").innerHTML = "";
  $("progress-bar").className = "bar";
  $("progress-bar").style.width = "0%";
  $("start-send").disabled = true;
  $("console-title").textContent = "Sending…";
  $("pause-send").dataset.paused = "0";
  $("pause-send").textContent = "Pause";
  $("console").scrollIntoView({ behavior: "smooth", block: "nearest" });

  api.events(job_id, {
    progress: renderProgress,
    result: appendLog,
    status: (data) => {
      if (data.hint) toast(data.error || "Stopped", { kind: "error", hint: data.hint });
    },
    done: (data) => {
      renderProgress(data.progress || {});
      const good = data.status === "completed" && !(data.counts?.failed > 0);
      $("progress-bar").classList.add(good ? "done" : "failed");
      $("console-title").textContent =
        data.status === "completed"
          ? `Done — ${data.summary || ""}`
          : `Stopped — ${data.error || data.status}`;
      (data.failed || []).forEach((f) => appendLog({ ...f, status: "failed" }));
      $("start-send").disabled = false;
      if (good) toast(`All done — ${data.summary}`, { timeout: 9000 });
      void refreshCampaigns();
    },
  });
});

function renderProgress(p) {
  $("progress-bar").style.width = `${Math.round((p.fraction ?? 0) * 100)}%`;
  const eta = p.eta_seconds;
  $("send-stats").innerHTML = `
    <span class="good"><b>${num(p.sent)}</b> sent</span>
    ${p.failed ? `<span class="bad"><b>${num(p.failed)}</b> failed</span>` : ""}
    <span><b>${num(p.remaining)}</b> left</span>
    <span><b>${Math.round(p.rate_per_minute ?? 0)}</b>/min</span>
    ${eta ? `<span>about <b>${duration(eta)}</b> remaining</span>` : ""}`;
}

function appendLog(result) {
  const bad = result.status !== "sent";
  const line = document.createElement("div");
  line.innerHTML =
    `<span class="${bad ? "bad" : "ok"}">${bad ? "✕" : "✓"}</span>` +
    `<span>${esc(result.email)}</span>` +
    (result.error ? `<span class="bad">${esc(result.error)}</span>` : "");
  const log = $("send-log");
  log.append(line);
  log.scrollTop = log.scrollHeight;
}

function duration(seconds) {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

// ═══════════════════════════════════════════════════ runs / suppression ═══

const loadRuns = guard(async () => {
  const runs = await api.get("/api/runs");
  $("runs-list").innerHTML = runs.length
    ? `<div class="card" style="padding:0"><table>
        <thead><tr><th>Started</th><th>Status</th><th>Sent</th><th>Failed</th><th>Run</th></tr></thead>
        <tbody>${runs
          .map(
            (r) => `<tr>
              <td>${esc(when(r.started_at))}</td>
              <td><span class="pill ${esc(r.status)}">${esc(r.status)}</span></td>
              <td>${num(r.sent)}</td><td>${num(r.failed)}</td>
              <td><code>${esc(r.id)}</code></td></tr>`,
          )
          .join("")}</tbody></table></div>`
    : `<div class="empty"><strong>Nothing sent yet</strong>Runs appear here once you send a campaign.</div>`;
});

const loadSuppression = guard(async () => {
  const rows = await api.get("/api/suppression");
  setSuppressedCount(rows.length);
  $("suppression-list").innerHTML = rows.length
    ? `<div class="card" style="padding:0"><table>
        <thead><tr><th>Address</th><th>Reason</th><th>Added</th><th></th></tr></thead>
        <tbody>${rows
          .map(
            (r) => `<tr>
              <td>${esc(r.email)}</td><td>${esc(r.reason)}</td>
              <td>${esc(when(r.added_at))}</td>
              <td><button class="link" data-unsuppress="${esc(r.email)}">remove</button></td>
            </tr>`,
          )
          .join("")}</tbody></table></div>`
    : `<div class="empty"><strong>Nobody is suppressed</strong>Addresses that bounce or unsubscribe land here automatically.</div>`;
});

const loadPlugins = guard(async () => {
  const plugins = await api.get("/api/plugins");
  $("plugins-list").innerHTML = plugins.length
    ? `<div class="cards">${plugins
        .map(
          (p) => `<div class="card" style="margin:0">
            <div class="card-head">
              <div><h2 style="margin:0">${esc(p.name)}</h2>
                <p class="muted" style="margin:3px 0 0">${esc(p.summary || "No description.")}</p></div>
              <label class="check" style="margin:0">
                <input type="checkbox" data-plugin="${esc(p.name)}" ${p.enabled ? "checked" : ""}>
                <span>${p.enabled ? "Enabled" : "Disabled"}</span></label>
            </div>
            <p class="hint">Provides: ${esc(p.hooks.join(", ") || "nothing yet")} · ${esc(p.source)}</p>
          </div>`,
        )
        .join("")}</div>`
    : `<div class="empty"><strong>No plugins installed</strong>Install one with pip, or drop a .py file in your plugins folder.</div>`;
});

// ═══════════════════════════════════════════════════════════════ wiring ═══

function wire() {
  els(".nav-item").forEach((n) => (n.onclick = () => show(n.dataset.view)));
  els("[data-goto]").forEach((n) => (n.onclick = () => show(n.dataset.goto)));
  $("theme-toggle").onclick = toggleTheme;

  // -- campaigns ---------------------------------------------------------
  $("new-campaign").onclick = newCampaign;
  $("try-sample").onclick = guard(async () => {
    newCampaign();
    $("c-name").value = "Demo campaign";
    $("c-subject").value = "Hello {{ first_name }}";
    setFormat(
      "rich",
      "<p>Hi {{ first_name }},</p><p>This is a demo. Everything here is identical for " +
        "everyone except the placeholders, which come from your contact list.</p>" +
        "<p>Best,<br>Your Name</p>",
    );
    await useSample();
    await saveCampaign();
  });

  const cardAction = (event) => {
    const remove = event.target.closest("[data-delete]");
    if (remove) {
      event.stopPropagation();
      return void guard(async () => {
        await api.del(`/api/campaigns/${remove.dataset.delete}`);
        await refreshCampaigns();
        toast("Campaign deleted.");
      })();
    }
    const card = event.target.closest(".campaign-card");
    if (card) void openCampaign(card.dataset.id);
    return undefined;
  };
  $("campaign-list").onclick = cardAction;
  $("campaign-list").onkeydown = (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      cardAction(e);
    }
  };

  // -- editor ------------------------------------------------------------
  ["c-subject", "c-preheader", "c-body"].forEach((id) => $(id).addEventListener("input", onEdit));
  $("c-rich").addEventListener("input", onEdit);
  $("c-name").addEventListener("input", () => {
    $("crumb-name").textContent = $("c-name").value || "Untitled";
    onEdit();
  });
  $("save-campaign").onclick = () => void saveCampaign({ quiet: false });

  els("#format-seg .seg-item").forEach((n) => {
    n.onclick = () => {
      setFormat(n.dataset.format);
      onEdit();
    };
  });

  // rich-text toolbar
  $("toolbar").onclick = (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    event.preventDefault();
    $("c-rich").focus();
    if (button.dataset.cmd) document.execCommand(button.dataset.cmd, false);
    else if (button.dataset.block) document.execCommand("formatBlock", false, button.dataset.block);
    else if (button.dataset.link) {
      const url = prompt("Link address", "https://");
      if (url) document.execCommand("createLink", false, url);
    }
    onEdit();
  };
  $("c-rich").addEventListener("paste", (event) => {
    // Paste as text: pasting from Word otherwise drags in a mountain of markup
    // that renders unpredictably in mail clients.
    event.preventDefault();
    const text = event.clipboardData.getData("text/plain");
    document.execCommand("insertText", false, text);
  });

  // contacts
  const pick = () => $("c-contacts-input").click();
  $("browse").onclick = pick;
  $("change-contacts").onclick = pick;
  $("change-contacts-2").onclick = pick;
  $("use-sample").onclick = () => void useSample();
  $("c-contacts-input").onchange = (event) => {
    const [file] = event.target.files;
    if (file) void uploadContacts(file);
    event.target.value = "";
  };
  $("toggle-table").onclick = () => {
    const table = $("contacts-table");
    table.hidden = !table.hidden;
    $("toggle-table-label").textContent = table.hidden ? "Show table" : "Hide table";
  };

  const zone = $("dropzone");
  ["dragenter", "dragover"].forEach((n) =>
    zone.addEventListener(n, (e) => {
      e.preventDefault();
      zone.classList.add("is-over");
    }),
  );
  ["dragleave", "drop"].forEach((n) =>
    zone.addEventListener(n, () => zone.classList.remove("is-over")),
  );
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    const [file] = e.dataTransfer.files;
    if (file) void uploadContacts(file);
  });

  $("chips").onclick = (event) => {
    const chip = event.target.closest(".chip");
    if (!chip) return;
    insert(`{{ ${chip.dataset.col} }}`);
    onEdit();
  };

  els(".preview-bar .seg-item").forEach((n) => {
    n.onclick = () => {
      els(".preview-bar .seg-item").forEach((m) => m.classList.toggle("is-on", m === n));
      state.previewMode = n.dataset.mode;
      void refreshPreview();
    };
  });
  $("prev-recipient").onclick = () => stepRecipient(-1);
  $("next-recipient").onclick = () => stepRecipient(1);

  // -- review / send ------------------------------------------------------
  $("open-review").onclick = () => void openReview();
  $("go-to-send").onclick = openSend;
  $("send-test").onclick = () => void sendTest();
  $("start-send").onclick = () => void startSend();
  $("dry-run").onchange = openSend;
  $("cancel-send").onclick = guard(async () => {
    if (state.jobId) await api.post(`/api/jobs/${state.jobId}/cancel`);
  });
  $("pause-send").onclick = guard(async () => {
    if (!state.jobId) return;
    const paused = $("pause-send").dataset.paused === "1";
    await api.post(`/api/jobs/${state.jobId}/${paused ? "resume" : "pause"}`);
    $("pause-send").dataset.paused = paused ? "0" : "1";
    $("pause-send").textContent = paused ? "Pause" : "Resume";
  });

  // -- settings -----------------------------------------------------------
  $("s-provider").onchange = updateProviderHint;
  $("save-settings").onclick = () => void saveSettings();
  $("test-connection").onclick = () => void testConnection();
  $("ai-provider").onchange = updateAiHint;
  $("ai-save").onclick = () => void saveAi();
  $("ai-test").onclick = () => void testAi();

  // -- suppression --------------------------------------------------------
  $("suppress-add").onclick = guard(async () => {
    const emails = $("suppress-input")
      .value.split(/[,;\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!emails.length) return;
    const r = await api.post("/api/suppression", { emails });
    $("suppress-input").value = "";
    await loadSuppression();
    toast(`Suppressed ${num(r.added)} address(es).`);
  });
  $("suppress-export").onclick = () => {
    window.location.href = "/api/suppression/export";
  };
  $("suppress-import").onclick = () => $("suppress-file").click();
  $("suppress-file").onchange = guard(async (event) => {
    const [file] = event.target.files;
    if (!file) return;
    const r = await api.upload("/api/suppression/import", file);
    event.target.value = "";
    await loadSuppression();
    toast(`Imported ${num(r.added)} address(es).`);
  });
  $("suppression-list").onclick = (event) => {
    const button = event.target.closest("[data-unsuppress]");
    if (!button) return;
    void guard(async () => {
      await api.del(`/api/suppression/${encodeURIComponent(button.dataset.unsuppress)}`);
      await loadSuppression();
    })();
  };

  $("plugins-list").onchange = guard(async (event) => {
    const box = event.target.closest("[data-plugin]");
    if (!box) return;
    await api.post("/api/plugins/toggle", { name: box.dataset.plugin, enabled: box.checked });
    await loadPlugins();
    toast("Restart SahajMails for the change to take effect.");
  });

  // -- global -------------------------------------------------------------
  window.addEventListener("hashchange", () => {
    const view = location.hash.slice(1);
    if (view && el(`#view-${view}`) && view !== state.view) show(view);
  });
  window.addEventListener("beforeunload", (e) => {
    if (state.dirty) e.preventDefault();
  });
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "s") {
      e.preventDefault();
      if (state.view === "editor") void saveCampaign({ quiet: false });
    }
  });
}

function stepRecipient(delta) {
  state.recipient = Math.max(0, Math.min(recipientCount() - 1, state.recipient + delta));
  updateRecipientLabel();
  void refreshPreview();
}

function insert(text) {
  if (state.format === "rich") {
    $("c-rich").focus();
    document.execCommand("insertText", false, text);
    return;
  }
  const field = $("c-body");
  const start = field.selectionStart ?? field.value.length;
  const end = field.selectionEnd ?? start;
  field.value = field.value.slice(0, start) + text + field.value.slice(end);
  field.focus();
  field.selectionStart = field.selectionEnd = start + text.length;
}

// ════════════════════════════════════════════════════════════════ start ═══

wire();
boot().catch((error) => {
  $("boot").innerHTML = `
    <div class="welcome" style="max-width:520px;text-align:center">
      <h1>${esc(error.message)}</h1>
      <p class="lede">${esc(error.hint || "Something went wrong starting the app.")}</p>
    </div>`;
  console.error(error);
});
