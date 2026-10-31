// MindChart's page: sign-in, CRM-style lists and record pages, the command bar, uploads, and history drafting.
// Everything talks only to MindChart's own server on this computer.

const $ = (selector, root = document) => root.querySelector(selector);
const state = { session: null, objects: null, sectionTitles: {}, models: [], dirtyDraft: false, work: {}, uploads: null };
const ICONS = { patients: "i-patients", documents: "i-documents", facts: "i-facts", drafts: "i-drafts", activity: "i-activity",
  medications: "i-meds", measures: "i-measures", follow_ups: "i-follow" };
const PREFIXES = { PT: "patients", DOC: "documents", FCT: "facts", DRF: "drafts", ACT: "activity", MED: "medications",
  MSR: "measures", FUP: "follow_ups" };
const HIDDEN_COLUMNS = ["patient_id", "document_id", "notes", "quote", "read_by_model", "seq", "details", "read_state"];
const READ_STATES = { queued: "Waiting to be read", fields: "Updating details", read: "Read", failed: "Couldn't read" };
const EXAMPLES = [
  "Which patients have an order of protection?",
  "Who is on sertraline?",
  "Show facts that aren't verified yet",
  "Remind me to renew Jordan's medication consent by 12/01",
  "Jordan's PHQ-A was 11 today",
];

// Every request to MindChart's server carries this header. The server refuses requests without it,
// which stops other web pages open on this Mac from sending it commands.
const plainFetch = window.fetch.bind(window);
window.fetch = (url, options = {}) => String(url).startsWith("/api/")
  ? plainFetch(url, { ...options, headers: { ...(options.headers || {}), "X-MindChart": "1" } })
  : plainFetch(url, options);

// Forms here are handled by code, never by the browser reloading the page.
document.addEventListener("submit", (e) => e.preventDefault(), true);

function esc(value) {
  const div = document.createElement("div");
  div.textContent = value ?? "";
  return div.innerHTML;
}

function fieldSpec(object, name) {
  return state.objects?.[object]?.fields.find((f) => f.name === name);
}

function label(object, column) {
  const extra = { id: "ID", patient: "Patient", document: "Document", documents: "Documents", facts: "Facts", read_state: "Reading" };
  return fieldSpec(object, column)?.label || extra[column] || column.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

function display(object, column, value) {
  if (value === null || value === undefined || value === "") return '<span class="empty">—</span>';
  const type = fieldSpec(object, column)?.type;
  if (type === "datetime") return esc(String(value).replace("T", " ").slice(0, 16));
  if (type === "checkbox") return value ? '<span class="badge">Yes</span>' : '<span class="badge plain">No</span>';
  if (column === "section") return esc(state.sectionTitles[value] || value);
  if (column === "read_state") return `<span class="read-${esc(value)}">${esc(READ_STATES[value] || value)}</span>`;
  if (column === "consent") return consentBadge(value);
  return esc(value);
}

function recordLink(uid, text) {
  const object = PREFIXES[String(uid).split("-")[0]];
  return object ? `<a href="#/r/${object}/${esc(uid)}">${text ?? `<span class="uid">${esc(uid)}</span>`}</a>` : esc(uid);
}

function modelChoice() {
  return $("#model").value;
}

// --- Talking to the server ---------------------------------------------------------

async function api(method, path, body, raw) {
  const res = await fetch("/api/" + path, {
    method,
    headers: raw ? {} : { "Content-Type": "application/json" },
    body: raw ? body : body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.headers.get("Content-Type")?.includes("json")) {
    const data = await res.json();
    if (data.locked) { showSignin("MindChart was locked."); throw new Error("MindChart is locked"); }
    if (data.error) throw new Error(data.error);
    return data;
  }
  if (!res.ok) throw new Error("Request failed");
  return res;
}

// Read a reply that arrives one JSON line at a time (used for history building).
async function readStream(res, onUpdate) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) if (line) onUpdate(JSON.parse(line));
  }
}

// --- Signing in --------------------------------------------------------------------

async function start() {
  try {
    state.system = await (await fetch("/api/system")).json();
    const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    document.documentElement.dataset.effects = reduced ? "minimal" : state.system.effects;
  } catch {}
  state.session = await (await fetch("/api/session")).json();
  if (state.session.unlocked) enterApp(); else showSignin();
}

function showSignin(message = "") {
  state.dirtyDraft = false;
  $("#app").hidden = true;
  $("#view").innerHTML = "";          // take patient data off the page
  $("#commandResult").hidden = true;
  $("#commandResult").innerHTML = "";
  $("#signin").hidden = false;
  const setUp = state.session?.set_up;
  $("#signinIntro").textContent = message || (setUp ? "Enter your password to open your records." :
    "Welcome. Choose a password to protect your records on this computer.");
  $("#password2").hidden = setUp;
  $("#userName").hidden = setUp;
  $("#password").autocomplete = setUp ? "current-password" : "new-password";
  $("#password").placeholder = setUp ? "Password" : `New password (${state.session?.min_password_length || 10}+ characters)`;
  $("#signinBtn").textContent = setUp ? "Unlock" : "Set up MindChart";
  $("#signinFine").textContent = setUp ? "" :
    "Your records are encrypted with this password. Keep it somewhere safe: if it's lost, the records can't be recovered.";
  $("#signinError").textContent = "";
  $("#password").value = $("#password2").value = "";
  (setUp ? $("#password") : $("#userName")).focus();
}

$("#signinForm").onsubmit = async () => {
  const setUp = state.session.set_up;
  const password = $("#password").value;
  if (!setUp && password !== $("#password2").value) { $("#signinError").textContent = "The passwords don't match."; return false; }
  $("#signinBtn").disabled = true;
  try {
    const body = setUp ? { password } : { password, name: $("#userName").value };
    state.session = { ...state.session, ...(await api("POST", setUp ? "session/unlock" : "session/setup", body)) };
    await enterApp();
  } catch (err) {
    $("#signinError").textContent = err.message;
    $("#password").value = "";
  }
  $("#signinBtn").disabled = false;
  return false;
};

$("#lockBtn").onclick = async () => {
  if (state.dirtyDraft && !confirm("You have unsaved changes to a draft. Lock anyway?")) return;
  state.session = { ...state.session, ...(await api("POST", "session/lock")) };
  showSignin("MindChart is locked.");
};

// Notice if MindChart locked itself (after 15 minutes idle) and hide everything.
setInterval(async () => {
  if ($("#app").hidden) return;
  const session = await (await fetch("/api/session")).json();
  if (!session.unlocked) { state.session = { ...state.session, ...session }; showSignin("MindChart was locked. Enter your password to continue."); }
}, 30000);

async function enterApp() {
  $("#signin").hidden = true;
  $("#app").hidden = false;
  const objects = await api("GET", "objects");
  state.objects = objects.objects;
  state.sectionTitles = objects.section_titles;
  await loadModels();
  await refreshWork();
  await route();
  maybeStartTour();  // the first time (and once after updating to a version with the tour)
}

async function loadModels() {
  const data = await api("GET", "models");
  state.models = data.models;
  let saved = null;
  try { saved = localStorage.getItem("model"); } catch {}
  const names = data.models.map((m) => m.name);
  $("#model").innerHTML = data.models.map((m) => `<option value="${esc(m.name)}">${esc(m.name)} · ${m.size_gb} GB${m.fits ? "" : " · slow on this Mac"}</option>`).join("");
  showDownload();
  if (state.system) {
    const v = state.system.version || {};
    $("#hardware").innerHTML = `${esc(state.system.chip)} · ${state.system.memory_gb} GB<br>Version ${esc(v.current || "")}` +
      (v.available ? `<br><b class="ok">Version ${esc(v.latest)} is ready: restart ${esc(document.title)} to install it</b>` : "");
  }
  $("#model").value = names.includes(saved) ? saved : names.includes(data.default) ? data.default : names[0];
  rememberModel();
}

// Background reading uses the model picked here, so the server is told whenever it changes.
function rememberModel() {
  api("POST", "settings/model", { model: $("#model").value }).catch(() => {});
}
// When MindChart is fetching this Mac's recommended model (e.g. after an update), show progress until it's ready.
async function showDownload() {
  const d = state.system?.download || {};
  let box = document.getElementById("download");
  if (d.status !== "downloading" && !box) return;
  if (!box) { $("#hardware").insertAdjacentHTML("beforebegin", '<div class="hardware" id="download"></div>'); box = document.getElementById("download"); }
  if (d.status === "downloading") {
    box.innerHTML = `<b>Downloading the recommended AI model (${esc(d.model)}): ${d.percent}%</b><br>You can keep working meanwhile.`;
    setTimeout(async () => { state.system = await (await fetch("/api/system")).json(); showDownload(); }, 3000);
  } else {
    box.remove();
    if (d.status === "done") { await loadModels(); toast(`${d.model} is ready`); }
  }
}

$("#model").onchange = () => { try { localStorage.setItem("model", $("#model").value); } catch {} rememberModel(); };

window.addEventListener("beforeunload", (e) => { if (state.dirtyDraft) e.preventDefault(); });

// --- Navigation --------------------------------------------------------------------

function renderNav() {
  const here = location.hash || "#/";
  const items = [["#/", "Home", "i-home"], ["#/uploads", "Uploads", "i-upload"],
    ...["patients", "follow_ups", "medications", "documents", "facts", "drafts", "activity"]
    .map((o) => [`#/list/${o}`, state.objects[o].plural, ICONS[o]]), ["#/settings", "Settings", "i-settings"]];
  $("#nav").innerHTML = items.map(([href, text, icon]) => {
    const object = href.split("/")[2];
    const active = here === href || (object && here.startsWith(`#/r/${object}/`)) ? "active" : "";
    const badge = href === "#/uploads" ? `<span class="nav-badge" id="uploadsBadge" ${state.work.uploads ? "" : "hidden"}>${state.work.uploads || ""}</span>` : "";
    return `<a href="${href}" class="${active}"><span class="${icon}"></span>${esc(text)}${badge}</a>`;
  }).join("");
}

let leaving = false;
window.addEventListener("hashchange", () => {
  if (leaving) { leaving = false; return; }
  if (state.dirtyDraft && !confirm("You have unsaved changes to this draft. Leave anyway?")) {
    leaving = true;
    history.back();
    return;
  }
  state.dirtyDraft = false;
  route();
});

async function route() {
  if ($("#app").hidden) return;
  renderNav();
  const parts = (location.hash.slice(1) || "/").split("/").filter(Boolean);
  try {
    if (!parts.length) await homeView();
    else if (parts[0] === "list") await listView(parts[1]);
    else if (parts[0] === "r") await recordView(parts[1], decodeURIComponent(parts[2]));
    else if (parts[0] === "settings") await settingsView();
    else if (parts[0] === "uploads") await uploadsView();
    else await homeView();
  } catch (err) {
    if (!$("#app").hidden) $("#view").innerHTML = `<div class="card"><p class="error">${esc(err.message)}</p></div>`;
  }
  animateIn($("#view"));
  window.scrollTo(0, 0);
}

// Replay the entrance animation: each block (and, on rich setups, each table row) slides in a beat after the last.
function animateIn(view) {
  view.classList.remove("view-enter");
  const blocks = view.id === "view" ? [...view.children, ...view.querySelectorAll("#recordBody > *, .grid2 > *")] : [...view.children];
  blocks.forEach((el, i) => el.style.setProperty("--i", Math.min(i, 10)));
  view.querySelectorAll("tbody").forEach((body) => [...body.rows].slice(0, 25).forEach((row, i) => row.style.setProperty("--r", i)));
  void view.offsetWidth; // restart the animation
  view.classList.add("view-enter");
}

// A short message in the corner. ms = 0 keeps it until the next one (or toast("") clears it).
function toast(text, ms = 2400) {
  document.getElementById("toast")?.remove();
  if (!text) return;
  document.body.insertAdjacentHTML("beforeend", `<div id="toast">${esc(text)}</div>`);
  const shown = document.getElementById("toast");
  if (ms) setTimeout(() => shown.remove(), ms);
}

function aiBusy(on) {
  document.body.classList.toggle("ai-busy", on);
}

// --- Home --------------------------------------------------------------------------

async function homeView() {
  const [patients, settings] = await Promise.all([api("GET", "records/patients").then((d) => d.rows), api("GET", "settings")]);
  $("#view").innerHTML = `
    ${settings.filevault ? "" : `<div class="banner">⚠ This Mac's disk encryption (FileVault) is off. MindChart's own data is encrypted, but exported
      Word files and anything else on this Mac aren't. Turn it on in System Settings → Privacy & Security → FileVault.</div>`}
    <div class="page-head"><div><div class="kicker">Welcome back${settings.user_name ? ", " + esc(settings.user_name.split(" ")[0]) : ""}</div>
      <h1>What would you like to do?</h1></div><span class="grow"></span><button class="secondary" id="homeUpload">Upload documents</button></div>
    ${state.work.uploads ? `<a class="banner inbox" href="#/uploads"><span class="i-upload"></span>
      <span><b>${state.work.uploads} upload${state.work.uploads === 1 ? "" : "s"} waiting for review.</b> Check each one's patient and type to file it →</span></a>` : ""}
    <div id="gettingStarted"></div>
    <div id="comingUp"></div>
    <div class="card">
      <h2>Try asking</h2>
      <div class="chips">${EXAMPLES.map((e) => `<button class="chip" data-example="${esc(e)}">${esc(e)}</button>`).join("")}</div>
    </div>
    <div class="card">
      <h2>Patients <span class="count">${patients.length}</span>
        <span class="actions"><button class="small" id="newPatient">+ New patient</button></span></h2>
      <div id="newPatientForm"></div>
      ${patients.length ? recordsTable("patients", patients, ["id", "name", "record_type", "dob", "status", "documents"])
        : '<p class="empty">No patients yet. Add one with the button above or the command bar.</p>'}
    </div>`;
  $("#view").querySelectorAll("[data-example]").forEach((b) => b.onclick = () => runCommand(b.dataset.example));
  $("#newPatient").onclick = () => newPatientForm($("#newPatientForm"));
  $("#homeUpload").onclick = () => pickFiles();
  renderChecklist();
  renderComingUp();
}

function newPatientForm(target) {
  const fields = state.objects.patients.fields.filter((f) => f.edit !== false);
  target.innerHTML = `<form class="card" style="background:var(--panel-2);box-shadow:none" onsubmit="return false">
      <div class="fields">${fields.map((f) => fieldInput("patients", f, f.name === "status" ? "Active" : f.name === "record_type" ? f.options[0] : "")).join("")}</div>
      <div class="custom-fields"></div>
      <div class="actions" style="margin-top:12px"><button type="submit">Save patient</button>
        <button type="button" class="secondary" data-cancel>Cancel</button><span class="error"></span></div></form>`;
  const form = target.querySelector("form");
  bindRecordType(form, "patients", {}, "custom.");
  form.querySelector("[data-cancel]").onclick = () => target.innerHTML = "";
  form.onsubmit = async () => {
    try {
      const { id } = await api("POST", "records/patients", formValues(form));
      location.hash = `#/r/patients/${id}`;
    } catch (err) { form.querySelector(".error").textContent = err.message; }
    return false;
  };
  form.querySelector("input")?.focus();
}

// --- Lists -------------------------------------------------------------------------

function recordsTable(object, rows, columns) {
  columns = columns || Object.keys(rows[0] || {}).filter((c) => !HIDDEN_COLUMNS.includes(c));
  const cell = (row, column) => {
    const value = row[column];
    if (column === "id") return recordLink(value);
    if (column === "patient" && row.patient_id) return recordLink(row.patient_id, esc(value));
    if (column === "document" && row.document_id) return recordLink(row.document_id, esc(value));
    if (column === state.objects[object]?.title_field && row.id) return recordLink(row.id, display(object, column, value));
    return display(object, column, value);
  };
  return `<div class="table-wrap"><table><thead><tr>${columns.map((c) => `<th>${esc(label(object, c))}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((row) => `<tr>${columns.map((c) => `<td class="${["fact", "detail", "name"].includes(c) ? "clip" : ""}">${cell(row, c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

async function listView(object, search = "") {
  const spec = state.objects[object];
  if (!spec) throw new Error("Unknown page");
  const data = await api("GET", `records/${object}?search=${encodeURIComponent(search)}`);
  const placeholder = object === "documents" ? "Search documents, including their text…" : `Search ${spec.plural.toLowerCase()}…`;
  $("#view").innerHTML = `
    <div class="page-head"><span class="object-icon obj-${object}"><span class="${ICONS[object]}"></span></span>
      <h1>${esc(spec.plural)}</h1><span class="badge plain">${data.rows.length}</span><span class="grow"></span>
      ${object === "patients" ? '<button id="newPatient">+ New patient</button>' : ""}
      ${object === "documents" ? '<button id="listUpload">Upload documents</button>' : ""}</div>
    <div id="newPatientForm"></div>
    <div class="list-tools"><input id="search" placeholder="${esc(placeholder)}" value="${esc(search)}"></div>
    <div id="listBody">${data.rows.length ? recordsTable(object, data.rows) : '<p class="empty">Nothing here yet.</p>'}</div>`;
  let timer;
  $("#search").oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const rows = (await api("GET", `records/${object}?search=${encodeURIComponent($("#search").value)}`)).rows;
      $("#listBody").innerHTML = rows.length ? recordsTable(object, rows) : '<p class="empty">No matches.</p>';
    }, 250);
  };
  if (object === "patients") $("#newPatient").onclick = () => newPatientForm($("#newPatientForm"));
  if (object === "documents") $("#listUpload").onclick = () => pickFiles();
}

// --- Record pages ------------------------------------------------------------------

// --- Record types: each one has its own fields, shown below the shared ones ---------

function recordTypeFields(object, recordType) {
  return state.objects[object]?.record_types?.[recordType] || [];
}

// Show the chosen record type's fields in a form, and swap them when the record type changes.
// prefix "custom." keeps them apart from the shared fields when the form is saved.
function bindRecordType(form, object, values, prefix) {
  const typeField = state.objects[object]?.record_type_field;
  const select = typeField && form.querySelector(`[name="${typeField}"]`);
  const box = form.querySelector(".custom-fields");
  if (!select || !box) return;
  const render = () => {
    const defs = recordTypeFields(object, select.value);
    box.innerHTML = defs.length ? `<h3>${esc(select.value)} details</h3>
      <div class="fields">${defs.map((d) => fieldInput(object, d, values[d.name], prefix)).join("")}</div>` : "";
  };
  select.onchange = render;
  render();
}

function displayValue(field, value) {
  if (value === null || value === undefined || value === "" || (Array.isArray(value) && !value.length)) return '<span class="empty">—</span>';
  if (field.type === "checkbox") return value ? '<span class="badge">Yes</span>' : '<span class="badge plain">No</span>';
  return esc(Array.isArray(value) ? value.join(", ") : value);
}

function fieldInput(object, field, value, prefix = "") {
  const name = `name="${prefix}${field.name}"`;
  const wide = field.type === "longtext" || field.type === "multipicklist" ? "wide" : "";
  let input;
  if (field.type === "multipicklist") {
    const chosen = Array.isArray(value) ? value : [];
    input = `<div class="multi">${field.options.map((o) => `<label><input type="checkbox" ${name} value="${esc(o)}" data-multi
      ${chosen.includes(o) ? "checked" : ""}> ${esc(o)}</label>`).join("")}</div>`;
  } else if (field.type === "number") {
    input = `<input type="number" step="any" ${name} value="${esc(value)}">`;
  } else if (field.type === "picklist") {
    const options = field.options.map((o) => `<option value="${esc(o)}" ${o === value ? "selected" : ""}>${esc(field.name === "section" ? state.sectionTitles[o] || o : o)}</option>`);
    input = `<select ${name}><option value=""></option>${options.join("")}</select>`;
  } else if (field.type === "checkbox") {
    input = `<input type="checkbox" ${name} ${value ? "checked" : ""}>`;
  } else if (field.type === "longtext") {
    input = `<textarea ${name} rows="3">${esc(value)}</textarea>`;
  } else {
    input = `<input ${name} value="${esc(value)}" ${field.type === "date" ? 'placeholder="MM/DD/YYYY"' : ""}>`;
  }
  return `<div class="field ${wide}"><label>${esc(field.label)}${field.required ? " *" : ""}</label>${input}</div>`;
}

function formValues(form) {
  const values = {}, custom = {};
  form.querySelectorAll("[name]").forEach((el) => {
    let target = values, key = el.name;
    if (key.startsWith("custom.")) { target = custom; key = key.slice(7); }
    if (el.dataset.multi !== undefined) {  // a multi-select picklist: one checkbox per choice
      target[key] = target[key] || [];
      if (el.checked) target[key].push(el.value);
      return;
    }
    target[key] = el.type === "checkbox" ? el.checked : el.value;
  });
  if (Object.keys(custom).length) values.custom = custom;
  return values;
}

function detailsCard(object, data, editing) {
  const spec = state.objects[object];
  const fields = spec.fields;
  const record = data.record;
  const recordType = spec.record_type_field ? record[spec.record_type_field] : null;
  const custom = data.custom || {}, sources = data.ai_sources || {};
  const typeDefs = recordType ? recordTypeFields(object, recordType) : [];
  const customView = typeDefs.length ? `<h3>${esc(recordType)} details</h3><div class="fields">${typeDefs.map((d) => {
      const source = sources[d.name];
      return `<div class="field ${d.type === "longtext" || d.type === "multipicklist" ? "wide" : ""}"><label>${esc(d.label)}
        ${source ? '<span class="ai-badge" title="Filled in by the AI. Check it against the quote.">✦ AI</span>' : ""}</label>
        <div class="value">${displayValue(d, custom[d.name])}</div>
        ${source ? `<div class="ai-quote">“${esc(source.quote)}”${source.page ? ` p.${source.page}` : ""}</div>` : ""}</div>`;
    }).join("")}</div>` : "";
  const body = editing
    ? `<form id="editForm" onsubmit="return false"><div class="fields">${fields.filter((f) => f.edit !== false).map((f) => fieldInput(object, f, record[f.name])).join("")}</div>
        <div class="custom-fields"></div>
        <div class="actions" style="margin-top:14px"><button type="submit">Save</button><button type="button" class="secondary" id="cancelEdit">Cancel</button><span class="error"></span></div></form>`
    : `<div class="fields">${fields.map((f) => `<div class="field ${f.type === "longtext" ? "wide" : ""}"><label>${esc(f.label)}</label>
        <div class="value">${display(object, f.name, record[f.name])}</div></div>`).join("")}
        ${record.patient && object !== "patients" ? `<div class="field"><label>Patient</label><div class="value">${recordLink(record.patient_id, esc(record.patient))}</div></div>` : ""}
        ${record.document && object === "facts" ? `<div class="field"><label>Document</label><div class="value">${recordLink(record.document_id, esc(record.document))}</div></div>` : ""}
      </div>${customView}`;
  const canEdit = fields.some((f) => f.edit !== false);
  return `<div class="card"><h2>Details${canEdit && !editing ? '<span class="actions"><button class="secondary small" id="editBtn">Edit</button></span>' : ""}</h2>${body}</div>`;
}

async function recordView(object, uid, editing = false) {
  const data = await api("GET", `records/${object}/${encodeURIComponent(uid)}`);
  const spec = state.objects[object];
  const record = data.record;
  const title = object === "drafts" ? `History draft · ${String(record.created).replace("T", " ").slice(0, 16)}`
    : object === "facts" ? (state.sectionTitles[record.section] || "Fact") : record[spec.title_field];
  $("#view").innerHTML = `
    <div class="page-head"><span class="object-icon obj-${object}"><span class="${ICONS[object]}"></span></span>
      <div><div class="kicker">${esc(spec.label)}</div><h1>${esc(title)}</h1></div>
      <span class="uid" title="Click to copy" id="uidChip">${esc(uid)}</span><span class="grow"></span>
      <div class="actions" id="recordActions"></div></div>
    <div id="recordBody"></div>`;
  $("#uidChip").onclick = () => navigator.clipboard?.writeText(uid);
  if (object !== "activity") {
    $("#recordActions").insertAdjacentHTML("beforeend", `<button class="danger" id="deleteBtn">Delete</button>`);
    $("#deleteBtn").onclick = async () => {
      const what = object === "patients" ? `${record.name} and ALL their documents, facts and drafts` : `this ${spec.label.toLowerCase()}`;
      if (!confirm(`Permanently delete ${what}? This can't be undone.`)) return;
      await api("POST", `records/${object}/${encodeURIComponent(uid)}/delete`);
      state.dirtyDraft = false;
      location.hash = record.patient_id && object !== "patients" ? `#/r/patients/${record.patient_id}` : `#/list/${object}`;
    };
  }

  const body = $("#recordBody");
  if (object === "drafts") return draftView(data, body);
  body.innerHTML = detailsCard(object, data, editing);
  if (editing) {
    bindRecordType($("#editForm"), object, data.custom || {}, "custom.");
    $("#cancelEdit").onclick = () => recordView(object, uid);
    $("#editForm").onsubmit = async () => {
      try {
        await api("POST", `records/${object}/${encodeURIComponent(uid)}`, formValues($("#editForm")));
        await recordView(object, uid);
        toast("Saved ✓");
      } catch (err) { $("#editForm .error").textContent = err.message; }
      return false;
    };
  } else if ($("#editBtn")) {
    $("#editBtn").onclick = async () => { await recordView(object, uid, true); animateIn($("#recordBody")); };
  }
  if (object === "patients") patientExtras(data, body);
  if (object === "documents") documentExtras(data, body);
}

function factsBySection(facts, showDocument) {
  if (!facts.length) return '<p class="empty">No facts yet. Build a history to read the documents.</p>';
  const groups = {};
  facts.forEach((f) => (groups[f.section] = groups[f.section] || []).push(f));
  const order = Object.keys(state.sectionTitles);
  return Object.keys(groups).sort((a, b) => order.indexOf(a) - order.indexOf(b)).map((section) => `
    <details class="section-group"><summary>${esc(state.sectionTitles[section] || section)} <span class="badge plain">${groups[section].length}</span></summary>
      ${groups[section].map((f) => `<div class="fact">${recordLink(f.id, "")}${esc(f.fact)}
        ${f.verified ? "" : ' <span class="badge bad">not verified</span>'}
        <span class="quote">“${esc(f.quote)}”${showDocument ? ` · ${recordLink(f.document_id, esc(f.document))}` : ""}${f.page ? ` p.${f.page}` : ""}
        · ${recordLink(f.id, "open")}</span></div>`).join("")}
    </details>`).join("");
}

function patientExtras(data, body) {
  const uid = data.record.id;
  const { documents, facts, drafts, activity, medications, measures, follow_ups } = data.related;
  $("#recordActions").insertAdjacentHTML("afterbegin", `
    <button class="secondary" id="uploadBtn">Upload documents</button>
    <button class="secondary" id="noteBtn">Add note</button>
    <button class="secondary" id="visitBtn">Write visit note</button>
    <button id="buildBtn" ${documents.length ? "" : "disabled"}>Build history</button>`);
  body.insertAdjacentHTML("beforeend", `
    <div id="noteBox"></div>
    <div id="buildBox"></div>
    <div class="card" id="followUpsCard"></div>
    <div class="card" id="medsCard"></div>
    <div class="card" id="measuresCard"></div>
    <div class="card"><h2>Documents & notes <span class="count">${documents.length}</span></h2>
      ${documents.length ? recordsTable("documents", documents, ["name", "document_type", "document_date", "author", "read_state", "facts"])
        : '<p class="empty">Upload records (PDF, Word, photos or faxes), or drop them onto this page. You can also add a note.</p>'}</div>
    <div class="card"><h2>Facts <span class="count">${facts.length}</span></h2>${factsBySection(facts, true)}</div>
    <div class="card"><details id="timeline"><summary><h2>Timeline <span class="muted small">their history in date order</span></h2></summary>
      <div id="timelineBody"><div class="thinking"><span class="spinner"></span>Putting events in order…</div></div></details></div>
    <div class="grid2">
      <div class="card"><h2>Drafts <span class="count">${drafts.length}</span></h2>
        ${drafts.length ? recordsTable("drafts", drafts, ["created", "updated", "model"]) : '<p class="empty">No drafts yet.</p>'}</div>
      <div class="card"><h2>Activity</h2>${activity.length ? recordsTable("activity", activity.slice(0, 15), ["time", "action", "detail"]) : '<p class="empty">None.</p>'}</div>
    </div>`);
  body.insertAdjacentHTML("afterbegin", glance(data));
  if (!$("#editForm")) $("#measuresCard").after(body.querySelector(".glance + .card"));  // the clinical cards first; details below
  renderFollowUps(uid, follow_ups);
  renderMeds(uid, medications);
  renderMeasures(uid, measures);
  $("#timeline").addEventListener("toggle", () => { if ($("#timeline").open) loadTimeline(uid); });

  $("#uploadBtn").onclick = () => pickFiles(uid);
  $("#visitBtn").onclick = () => visitNoteForm(data);

  $("#noteBtn").onclick = () => {
    $("#noteBox").innerHTML = `<form class="card" onsubmit="return false"><h2>New note</h2>
      <div class="fields"><div class="field wide"><label>Note</label><textarea name="text" rows="4" placeholder="What happened, what was observed, what changed…"></textarea></div>
      <div class="field"><label>Date</label><input name="date" placeholder="MM/DD/YYYY (today if blank)"></div>
      <div class="field"><label>Author</label><input name="author"></div></div>
      <div class="actions" style="margin-top:12px"><button type="submit">Save note</button><button type="button" class="secondary" data-cancel>Cancel</button><span class="error"></span></div></form>`;
    const form = $("#noteBox form");
    form.querySelector("[data-cancel]").onclick = () => $("#noteBox").innerHTML = "";
    form.onsubmit = async () => {
      try { await api("POST", `patients/${uid}/notes`, formValues(form)); await recordView("patients", uid); toast("Note saved ✓"); }
      catch (err) { form.querySelector(".error").textContent = err.message; }
      return false;
    };
    form.querySelector("textarea").focus();
  };

  $("#buildBtn").onclick = () => buildPanel(uid, documents);
}

// --- On a patient's page: follow-ups, medications, rating scales, the timeline ------------

// A strip across the top of a patient's page: age, placement, current medications, latest score, and what's due next.
function glance(data) {
  const r = data.record, custom = data.custom || {};
  const dob = parseDay(r.dob), today = new Date();
  const age = dob ? today.getFullYear() - dob.getFullYear() - (today < new Date(today.getFullYear(), dob.getMonth(), dob.getDate()) ? 1 : 0) : null;
  const where = [r.record_type, custom.cottage || custom.program || custom.foster_home || custom.clinic].filter(Boolean).join(" · ");
  const meds = data.related.medications.filter((m) => m.status === "Active");
  const score = data.related.measures.find((m) => m.measure !== "Vitals" && m.score !== null && m.score !== "");
  const next = data.related.follow_ups.filter((f) => f.status !== "Done").map((f) => ({ ...f, days: daysFromToday(f.due) }))
    .filter((f) => f.days !== null).sort((a, b) => a.days - b.days)[0];
  const item = (label, value, extra = "") => `<div class="glance-item"><span>${label}</span><b>${value}</b>${extra ? `<small>${extra}</small>` : ""}</div>`;
  return `<div class="glance">
    ${item("Age", age !== null ? `${age}` : "—", r.dob ? `born ${esc(r.dob)}` : "")}
    ${item("Placement", esc(where || "—"), custom.admission_date ? `since ${esc(custom.admission_date)}` : "")}
    ${item("Current medications", meds.length ? esc(meds.map((m) => m.name).join(", ")) : "None recorded")}
    ${item("Latest score", score ? `${esc(score.measure.split(" (")[0])} ${esc(score.score)}` : "—", score ? `${esc(bandFor(score.measure, score.score))}${bandFor(score.measure, score.score) ? " · " : ""}${esc(score.date)}` : "")}
    ${item("Next due", next ? esc(next.title) : "Nothing open", next ? `<span class="${next.days < 0 ? "warn" : ""}">${whenLabel(next.days)}</span>` : "")}
  </div>`;
}

// A date as people write it (MM/DD/YYYY, 2024-05-01, May 1, 2024) as a Date, or null.
function parseDay(text) {
  const t = String(text || "").trim();
  let m = t.match(/^(\d{4})-(\d{1,2})-(\d{1,2})/);
  if (m) return new Date(+m[1], m[2] - 1, +m[3]);
  m = t.match(/^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})$/);
  if (m) return new Date(m[3].length === 2 ? 2000 + +m[3] : +m[3], m[1] - 1, +m[2]);
  const parsed = Date.parse(t);
  return isNaN(parsed) ? null : new Date(parsed);
}

function daysFromToday(text) {
  const day = parseDay(text);
  if (!day) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((day - today) / 86400000);
}

function whenLabel(days) {
  if (days === null || days === undefined) return "No date";
  if (days < 0) return `${-days} day${days === -1 ? "" : "s"} overdue`;
  return days === 0 ? "Today" : days === 1 ? "Tomorrow" : `In ${days} days`;
}

const todayText = () => { const d = new Date(); return `${String(d.getMonth() + 1).padStart(2, "0")}/${String(d.getDate()).padStart(2, "0")}/${d.getFullYear()}`; };

// One line in a list of things due: when, what, and (on Home) whose.
function dueRow(item, showPatient) {
  const days = item.days !== undefined ? item.days : daysFromToday(item.due);
  return `<li class="${days !== null && days < 0 ? "overdue" : ""}">
    <span class="due-when">${whenLabel(days)}<small>${esc(item.when ?? item.due ?? "")}</small></span>
    <span class="due-what">${recordLink(item.id, esc(item.text ?? item.title))}
      <small>${showPatient ? recordLink(item.patient_id, esc(item.patient)) + " · " : ""}${esc(item.kind ?? item.category ?? "")}</small></span>
    ${item.follow_up !== false ? `<button class="secondary small" data-done="${esc(item.id)}">Done</button>` : ""}</li>`;
}

function bindDone(box, after) {
  box.querySelectorAll("[data-done]").forEach((b) => b.onclick = async () => {
    await api("POST", `records/follow_ups/${b.dataset.done}`, { status: "Done" });
    toast("Marked done ✓");
    after();
  });
}

async function renderComingUp() {
  const box = document.getElementById("comingUp");
  if (!box) return;
  let items;
  try { items = (await api("GET", "coming-up")).items; } catch { return; }
  box.innerHTML = `<div class="card"><h2>Coming up <span class="count">${items.length}</span>
      <span class="muted small" style="font-family:var(--sans)">the next six weeks, and anything overdue</span></h2>
    ${items.length ? `<ul class="due-list">${items.map((i) => dueRow(i, true)).join("")}</ul>` : `<p class="empty">Nothing due. Add follow-ups on
      a patient's page, or type “Remind me to renew Jordan's consent by 12/01” in the bar at the top. Court order expiry dates,
      IEP reviews and consents still needed show up here by themselves.</p>`}</div>`;
  bindDone(box, renderComingUp);
}

// A form for adding a medication, measure or follow-up to a patient.
function addRecordForm(box, object, patientUid, defaults = {}) {
  const fields = state.objects[object].fields.filter((f) => f.edit !== false);
  box.innerHTML = `<form class="inset add-form" onsubmit="return false">
      <div class="fields">${fields.map((f) => fieldInput(object, f, defaults[f.name] ?? "")).join("")}</div>
      <div class="custom-fields"></div>
      <div class="actions" style="margin-top:12px"><button type="submit" class="small">Save</button>
        <button type="button" class="secondary small" data-cancel>Cancel</button><span class="error"></span></div></form>`;
  const form = box.querySelector("form");
  bindRecordType(form, object, defaults.custom || {}, "custom.");
  form.querySelector("[data-cancel]").onclick = () => box.innerHTML = "";
  form.onsubmit = async () => {
    try {
      await api("POST", `records/${object}`, { ...formValues(form), patient_id: patientUid });
      toast("Saved ✓");
      recordView("patients", patientUid);
    } catch (err) { form.querySelector(".error").textContent = err.message; }
    return false;
  };
  form.querySelector("input, select, textarea")?.focus();
}

function renderFollowUps(uid, rows) {
  const card = $("#followUpsCard");
  const open = rows.filter((r) => r.status !== "Done");
  const done = rows.length - open.length;
  card.innerHTML = `<h2>Follow-ups <span class="count">${open.length}</span>
      <span class="actions"><button class="secondary small" data-add>+ Add follow-up</button></span></h2>
    <div class="add-box"></div>
    ${open.length ? `<ul class="due-list">${open.map((f) => dueRow(f, false)).join("")}</ul>`
      : '<p class="empty">Nothing open. Keep track of court dates, IEP meetings, consents and labs here.</p>'}
    ${done ? `<p class="muted small">${done} done</p>` : ""}`;
  card.querySelector("[data-add]").onclick = () => addRecordForm(card.querySelector(".add-box"), "follow_ups", uid, { status: "Open" });
  bindDone(card, () => recordView("patients", uid));
}

function consentBadge(value) {
  if (!value) return '<span class="empty">—</span>';
  return `<span class="badge ${["On file", "Not needed"].includes(value) ? "" : "warn-badge"}">${esc(value)}</span>`;
}

function renderMeds(uid, rows) {
  const card = $("#medsCard");
  const current = rows.filter((r) => r.status !== "Stopped"), past = rows.filter((r) => r.status === "Stopped");
  const cell = (m, key) => key === "name" ? `${recordLink(m.id, `<b>${esc(m.name)}</b>`)}${m.status === "Planned" ? ' <span class="badge plain">Planned</span>' : ""}`
    : key === "consent" ? consentBadge(m.consent) + (m.consent_by ? ` <span class="muted small">${esc(m.consent_by)}</span>` : "")
    : m[key] ? esc(m[key]) : '<span class="empty">—</span>';
  const table = (list, columns) => `<div class="table-wrap"><table><thead><tr>${columns.map(([, title]) => `<th>${title}</th>`).join("")}</tr></thead>
    <tbody>${list.map((m) => `<tr>${columns.map(([key]) => `<td class="${key === "response" ? "clip" : ""}">${cell(m, key)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  card.innerHTML = `<h2>Medications <span class="count">${current.length}</span><span class="actions">
      <button class="secondary small" data-suggest>Suggest from records</button><button class="secondary small" data-add>+ Add medication</button></span></h2>
    <div class="add-box"></div><div class="suggest-box"></div>
    ${current.length ? table(current, [["name", "Medication"], ["dose", "Dose"], ["frequency", "How often"], ["target", "For"], ["start_date", "Started"], ["consent", "Consent"]])
      : '<p class="empty">No current medications recorded. Add them, or let MindChart suggest them from the records it has read.</p>'}
    ${past.length ? `<details class="past"><summary class="muted small">Past medications (${past.length})</summary>
      ${table(past, [["name", "Medication"], ["dose", "Dose"], ["start_date", "Started"], ["stop_date", "Stopped"], ["stop_reason", "Why stopped"], ["response", "Response"]])}</details>` : ""}`;
  card.querySelector("[data-add]").onclick = () => addRecordForm(card.querySelector(".add-box"), "medications", uid, { status: "Active" });
  card.querySelector("[data-suggest]").onclick = () => suggestMeds(uid, card.querySelector(".suggest-box"));
}

// A medication list drafted by the AI from the facts in the patient's records, to check and add.
async function suggestMeds(uid, box) {
  box.innerHTML = `<div class="inset"><div class="thinking"><span class="spinner"></span>Reading the medication facts in this patient's records…</div></div>`;
  aiBusy(true);
  try {
    const { suggestions } = await api("POST", `patients/${uid}/medications/suggest`, { model: modelChoice() });
    if (!suggestions.length) {
      box.innerHTML = `<div class="inset"><p class="muted small">No medications found that aren't on the list already. Only documents that
        have been read are used; new uploads are read in the background.</p></div>`;
      return;
    }
    box.innerHTML = `<div class="inset"><h3>Found in the records <span class="muted small">check each one, then add the ones that are right</span></h3>
      ${suggestions.map((m, i) => `<div class="suggestion"><label class="check"><input type="checkbox" data-pick="${i}" checked>
          <b>${esc(m.name)}</b> ${esc([m.dose, m.frequency].filter(Boolean).join(" "))}</label>
        <select data-status="${i}" aria-label="Status"><option value="">Status…</option>${["Active", "Stopped", "Planned"].map((o) =>
          `<option ${o === m.status ? "selected" : ""}>${o}</option>`).join("")}</select>
        <div class="muted small">${esc([m.start_date && `started ${m.start_date}`, m.stop_date && `stopped ${m.stop_date}`, m.stop_reason, m.response].filter(Boolean).join(" · "))}</div>
        <details><summary class="muted small">From ${esc(m.source)}</summary>${m.quotes.map((q) => `<div class="ai-quote">${esc(q)}</div>`).join("")}</details></div>`).join("")}
      <div class="actions" style="margin-top:12px"><button class="small" data-add-picked>Add selected</button>
        <button class="secondary small" data-cancel>Cancel</button><span class="error"></span></div></div>`;
    box.querySelector("[data-cancel]").onclick = () => box.innerHTML = "";
    box.querySelector("[data-add-picked]").onclick = async () => {
      const picked = [...box.querySelectorAll("[data-pick]:checked")].map((c) => +c.dataset.pick);
      const missing = picked.find((i) => !box.querySelector(`[data-status="${i}"]`).value);
      if (missing !== undefined) { box.querySelector(".error").textContent = `Choose whether ${suggestions[missing].name} is current or stopped.`; return; }
      for (const i of picked) {
        const { quotes, ...fields } = suggestions[i];
        await api("POST", "records/medications", { ...fields, status: box.querySelector(`[data-status="${i}"]`).value, patient_id: uid });
      }
      toast(`Added ${picked.length} medication${picked.length === 1 ? "" : "s"} ✓`);
      recordView("patients", uid);
    };
  } catch (err) {
    box.innerHTML = `<div class="inset"><p class="error">${esc(err.message)}</p></div>`;
  } finally {
    aiBusy(false);
  }
}

function bandFor(measure, score) {
  if (score === null || score === undefined || score === "") return "";
  return ((state.objects.measures.bands || {})[measure] || []).find(([highest]) => +score <= highest)?.[1] || "";
}

// A small line chart of one measure over time, with the scale's published bands shaded behind it.
function measureChart(measure, points, unit = "") {
  const W = 340, H = 126, L = 30, R = 12, T = 10, B = 22;
  const values = points.map((p) => +p.value);
  const top = (state.objects.measures.max || {})[measure] ?? Math.ceil(Math.max(...values) * 1.15 || 1);
  const bottom = (state.objects.measures.max || {})[measure] !== undefined ? 0 : Math.floor(Math.min(...values) * 0.85);
  const x = (i) => L + (points.length === 1 ? (W - L - R) / 2 : i * (W - L - R) / (points.length - 1));
  const y = (v) => T + (H - T - B) * (1 - (v - bottom) / ((top - bottom) || 1));
  let low = bottom;
  const bands = ((state.objects.measures.bands || {})[measure] || []).map(([highest], i) => {
    const rect = `<rect x="${L}" y="${y(highest)}" width="${W - L - R}" height="${y(low) - y(highest)}" class="band band-${i}"/>`;
    low = highest;
    return rect;
  }).join("");
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(+p.value).toFixed(1)}`).join(" ");
  const labelEvery = Math.ceil(points.length / 5);
  return `<svg viewBox="0 0 ${W} ${H}" class="chart" role="img" aria-label="${esc(measure)} over time">
    ${bands}<line x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}" class="axis"/>
    <text x="${L - 6}" y="${y(top) + 4}" class="tick" text-anchor="end">${top}</text><text x="${L - 6}" y="${y(bottom) + 4}" class="tick" text-anchor="end">${bottom}</text>
    <path d="${line}" class="series"/>
    ${points.map((p, i) => `<circle cx="${x(i)}" cy="${y(+p.value)}" r="3.5" class="dot"><title>${esc(p.date)}: ${esc(p.value)}${unit}</title></circle>
      ${i % labelEvery === 0 || i === points.length - 1 ? `<text x="${x(i)}" y="${H - 6}" class="tick" text-anchor="middle">${esc(shortDate(p.date))}</text>` : ""}`).join("")}
  </svg>`;
}

function shortDate(text) {
  const d = parseDay(text);
  return d ? `${d.getMonth() + 1}/${String(d.getFullYear()).slice(2)}` : text;
}

function renderMeasures(uid, rows) {
  const card = $("#measuresCard");
  const groups = {};
  rows.forEach((r) => (groups[r.measure] = groups[r.measure] || []).push({ ...r, custom: JSON.parse(r.details || "{}") }));
  const blocks = Object.entries(groups).map(([measure, list]) => {
    const ordered = list.sort((a, b) => (parseDay(a.date) || 0) - (parseDay(b.date) || 0));
    const latest = ordered[ordered.length - 1];
    const series = (key, label, unit = "") => {
      const points = ordered.filter((r) => (key ? r.custom[key] : r.score) !== undefined && (key ? r.custom[key] : r.score) !== null && (key ? r.custom[key] : r.score) !== "")
        .map((r) => ({ date: r.date, value: key ? r.custom[key] : r.score }));
      return points.length ? `<div class="measure"><div class="measure-head"><b>${esc(label)}</b>
          <span class="measure-latest">${esc(points[points.length - 1].value)}${unit}${!key && bandFor(measure, points[points.length - 1].value) ? ` · ${esc(bandFor(measure, points[points.length - 1].value))}` : ""}</span>
          <small>${esc(points[points.length - 1].date)} · ${points.length} ${points.length === 1 ? "entry" : "entries"}</small></div>
        ${measure === "PHQ-A (depression)" && +latest.custom.self_harm_item > 0 ? `<div class="flag">⚠ Question 9 (thoughts of self-harm): ${esc(latest.custom.self_harm_item)} on ${esc(latest.date)}</div>` : ""}
        ${measureChart(key ? label : measure, points, unit)}</div>` : "";
    };
    if (measure === "Vitals") return series("weight_lb", "Weight", " lb") + series("bmi", "BMI");
    if (measure === "Vanderbilt ADHD") return series("inattentive", "Vanderbilt: inattentive symptoms") + series("hyperactive", "Vanderbilt: hyperactive symptoms");
    return series(null, measure);
  }).join("");
  card.innerHTML = `<h2>Rating scales and vitals <span class="count">${rows.length}</span>
      <span class="actions"><button class="secondary small" data-add>+ Add a score or vitals</button></span></h2>
    <div class="add-box"></div>
    ${blocks ? `<div class="measures">${blocks}</div>` : `<p class="empty">Track PHQ-A, GAD-7, SCARED, Vanderbilt and CRAFFT scores, or weight
      and BMI, to see change over time. Only totals are kept, not the questionnaires.</p>`}`;
  card.querySelector("[data-add]").onclick = () => addRecordForm(card.querySelector(".add-box"), "measures", uid,
    { measure: "PHQ-A (depression)", date: todayText() });
}

const TIMELINE_KINDS = { birth: "Born", placement: "Placement", document: "Document", medication: "Medication",
  measure: "Score", follow_up: "Follow-up" };

async function loadTimeline(uid) {
  const box = $("#timelineBody");
  if (box.dataset.loaded) return;
  try {
    const { events } = await api("GET", `patients/${uid}/timeline`);
    box.dataset.loaded = "1";
    if (!events.length) { box.innerHTML = '<p class="empty">No dated events yet. They come from documents once they\'re read, and from medications, scores and follow-ups.</p>'; return; }
    const years = {};
    events.forEach((e) => (years[e.key[0]] = years[e.key[0]] || []).push(e));
    box.innerHTML = `<p class="muted small">Dated facts from the documents (verified ones only), plus medications, scores and follow-ups.
        Undated facts aren't shown; they're in the Facts list.</p>` +
      Object.entries(years).map(([year, list]) => `<div class="tl-year"><h3>${year}</h3><ul class="timeline">
        ${list.map((e) => `<li class="tl-${e.kind}"><span class="tl-when">${esc(e.when)}</span>
          <span class="tl-kind">${esc(e.kind === "fact" ? state.sectionTitles[e.section] || "Fact" : TIMELINE_KINDS[e.kind] || e.kind)}</span>
          <span class="tl-text">${recordLink(e.id, esc(e.text))}${e.source ? ` <span class="muted small">· ${esc(e.source)}</span>` : ""}</span></li>`).join("")}
      </ul></div>`).join("");
  } catch (err) {
    box.innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
}

// --- Visit notes: the clinician's notes and a click-through mental status exam, written up by the AI -----

// [domain, choices, the usual findings that "Mark the usual findings" picks, groups of choices that rule each other out]
const MSE = [
  ["Appearance", ["well groomed", "appropriately dressed", "disheveled", "poor hygiene"], ["well groomed", "appropriately dressed"],
    [["well groomed", "disheveled"]]],
  ["Behavior", ["calm", "cooperative", "good eye contact", "guarded", "restless", "agitated", "withdrawn"], ["calm", "cooperative", "good eye contact"],
    [["calm", "agitated"], ["cooperative", "guarded"]]],
  ["Speech", ["normal rate, volume and tone", "pressured", "slowed", "soft", "loud", "minimal"], ["normal rate, volume and tone"],
    [["normal rate, volume and tone", "pressured"], ["normal rate, volume and tone", "slowed"], ["normal rate, volume and tone", "soft"],
     ["normal rate, volume and tone", "loud"], ["normal rate, volume and tone", "minimal"]]],
  ["Mood (in their words)", null],
  ["Affect", ["euthymic", "full range", "constricted", "flat", "anxious", "irritable", "tearful", "labile"], ["euthymic", "full range"],
    [["full range", "constricted", "flat"], ["euthymic", "anxious"], ["euthymic", "irritable"], ["euthymic", "tearful"]]],
  ["Thought process", ["linear and goal-directed", "tangential", "circumstantial", "disorganized"], ["linear and goal-directed"],
    [["linear and goal-directed", "tangential"], ["linear and goal-directed", "circumstantial"], ["linear and goal-directed", "disorganized"]]],
  ["Thought content", ["denies suicidal ideation", "passive death wish", "suicidal ideation without plan", "suicidal ideation with plan",
    "denies homicidal ideation", "homicidal ideation", "no delusions elicited"], ["denies suicidal ideation", "denies homicidal ideation", "no delusions elicited"],
    [["denies suicidal ideation", "passive death wish", "suicidal ideation without plan", "suicidal ideation with plan"], ["denies homicidal ideation", "homicidal ideation"]]],
  ["Perception", ["denies hallucinations", "auditory hallucinations", "visual hallucinations", "not responding to internal stimuli"],
    ["denies hallucinations", "not responding to internal stimuli"], [["denies hallucinations", "auditory hallucinations"], ["denies hallucinations", "visual hallucinations"]]],
  ["Cognition", ["alert", "oriented", "attention intact", "distractible"], ["alert", "oriented", "attention intact"], [["attention intact", "distractible"]]],
  ["Insight", ["good", "fair", "limited", "poor"], [], [["good", "fair", "limited", "poor"]]],
  ["Judgment", ["good", "fair", "limited", "poor"], [], [["good", "fair", "limited", "poor"]]],
];

function visitNoteForm(data) {
  const uid = data.record.id, name = data.record.name;
  const meds = data.related.medications.filter((m) => m.status === "Active");
  const latest = {};
  [...data.related.measures].reverse().forEach((m) => { latest[m.measure] = m; });
  const scores = Object.values(latest).filter((m) => m.measure !== "Vitals" && m.score !== null && m.score !== "");
  $("#noteBox").innerHTML = `<form class="card visit-note" onsubmit="return false">
    <h2>Visit note for ${esc(name)} <span class="actions"><button type="button" class="link small" data-close>Close</button></span></h2>
    <p class="muted small">Write what happened in your own words, pick the mental status findings, and add your assessment and plan. The AI turns
      it into a note without adding anything; you check and edit it before saving or copying.</p>
    <div class="fields"><div class="field"><label>Visit date</label><input name="visit_date" value="${todayText()}"></div>
      <div class="field"><label>Visit type</label><select name="visit_type">${["Medication management", "Follow-up", "Crisis visit", "Initial visit", "Other"].map((t) => `<option>${t}</option>`).join("")}</select></div>
      <div class="field wide"><label>Since the last visit (what the youth, staff and guardian report)</label>
        <textarea name="interval" rows="3" placeholder="Sleeping better since the dose increase. Staff report one argument with a peer, no aggression. Grandmother visited Sunday."></textarea></div></div>
    <h3>Mental status exam <button type="button" class="link small" data-usual>Mark the usual findings</button> <button type="button" class="link small" data-clear>Clear</button></h3>
    <div class="mse">${MSE.map(([domain, choices], d) => `<div class="mse-row"><span class="mse-domain">${esc(domain)}</span>
      ${choices ? `<span class="chips">${choices.map((c) => `<button type="button" class="chip" data-mse="${d}" data-choice="${esc(c)}">${esc(c)}</button>`).join("")}</span>`
        : `<input name="mood" placeholder="“okay”, “bored”, “tired”">`}</div>`).join("")}
      <div class="mse-row"><span class="mse-domain">Other observations</span><input name="mse_other" placeholder="Anything else you noticed"></div></div>
    <h3>Included from the record</h3>
    <label class="check"><input type="checkbox" name="with_meds" ${meds.length ? "checked" : "disabled"}> Current medications
      <span class="muted small">${meds.length ? esc(meds.map((m) => [m.name, m.dose, m.frequency].filter(Boolean).join(" ")).join("; ")) : "(none on the list)"}</span></label>
    <label class="check"><input type="checkbox" name="with_scores" ${scores.length ? "checked" : "disabled"}> Latest scores
      <span class="muted small">${scores.length ? esc(scores.map((m) => `${m.measure} ${m.score} (${m.date})`).join("; ")) : "(none recorded)"}</span></label>
    <div class="field wide" style="margin-top:12px"><label>Assessment and plan (your notes)</label>
      <textarea name="plan" rows="3" placeholder="Depressive symptoms improving. Continue sertraline 50 mg daily. Recheck PHQ-A in 4 weeks. Follow up in 1 month."></textarea></div>
    <div class="actions" style="margin-top:12px"><button type="button" data-draft>Draft the note with AI</button>
      <button type="button" class="secondary" data-assemble>Put it together without AI</button><span class="error"></span></div>
    <div class="note-output" hidden><h3>The note <span class="muted small">edit anything before you save or copy it</span></h3>
      <textarea name="note" rows="14"></textarea>
      <div class="actions" style="margin-top:10px"><button type="button" data-save>Save to ${esc(name.split(" ")[0])}'s record</button>
        <button type="button" class="secondary" data-copy>Copy</button></div></div>
  </form>`;
  const form = $("#noteBox form");
  form.scrollIntoView({ behavior: "smooth" });
  const picked = MSE.map(() => new Set());
  const paint = () => form.querySelectorAll("[data-mse]").forEach((b) => b.classList.toggle("on", picked[+b.dataset.mse].has(b.dataset.choice)));
  form.querySelectorAll("[data-mse]").forEach((b) => b.onclick = () => {
    const d = +b.dataset.mse, choice = b.dataset.choice;
    if (picked[d].has(choice)) picked[d].delete(choice);
    else {
      (MSE[d][3] || []).filter((group) => group.includes(choice)).flat().forEach((other) => picked[d].delete(other));  // e.g. "denies SI" vs "SI with plan"
      picked[d].add(choice);
    }
    paint();
  });
  form.querySelector("[data-usual]").onclick = () => { MSE.forEach(([, , usual], d) => (usual || []).forEach((c) => picked[d].add(c))); paint(); };
  form.querySelector("[data-clear]").onclick = () => { picked.forEach((set) => set.clear()); paint(); };
  form.querySelector("[data-close]").onclick = () => $("#noteBox").innerHTML = "";
  const value = (n) => form.querySelector(`[name=${n}]`).value.trim();
  const mseText = () => MSE.map(([domain, choices], d) => {
    const found = choices ? [...picked[d]].join(", ") : value("mood") && `“${value("mood").replace(/^[“"]|[”"]$/g, "")}”`;
    return found ? `${domain.replace(" (in their words)", "")}: ${found}.` : "";
  }).filter(Boolean).concat(value("mse_other") ? [`Other: ${value("mse_other")}`] : []).join(" ");
  const medsText = () => form.querySelector("[name=with_meds]").checked ? meds.map((m) => [m.name, m.dose, m.frequency].filter(Boolean).join(" ")).join("; ") : "";
  const scoresText = () => form.querySelector("[name=with_scores]").checked ? scores.map((m) => `${m.measure}: ${m.score}${bandFor(m.measure, m.score) ? ` (${bandFor(m.measure, m.score)})` : ""} on ${m.date}`).join("; ") : "";
  const heading = () => `Visit note: ${value("visit_date")} (${value("visit_type")})`;
  const show = (text) => { form.querySelector(".note-output").hidden = false; form.querySelector("[name=note]").value = text; form.querySelector("[name=note]").focus(); };
  const check = () => {
    if (!value("interval") && !mseText() && !value("plan")) throw new Error("Add what happened, the mental status findings, or your plan first.");
  };
  form.querySelector("[data-assemble]").onclick = () => {
    try { check(); } catch (err) { form.querySelector(".error").textContent = err.message; return; }
    show([heading(), "", `Subjective: ${value("interval") || "Not documented."}`, "",
      `Objective: Mental status: ${mseText() || "Not documented."}${medsText() ? ` Current medications: ${medsText()}.` : ""}${scoresText() ? ` Scores: ${scoresText()}.` : ""}`,
      "", `Assessment and plan: ${value("plan") || "Not documented."}`].join("\n"));
  };
  form.querySelector("[data-draft]").onclick = async () => {
    try { check(); } catch (err) { form.querySelector(".error").textContent = err.message; return; }
    const button = form.querySelector("[data-draft]");
    button.disabled = true;
    form.querySelector(".error").innerHTML = '<span class="thinking"><span class="spinner"></span>Writing it up (on this Mac)…</span>';
    aiBusy(true);
    try {
      const inputs = [`Visit: ${value("visit_date")}, ${value("visit_type")}`, `Since the last visit: ${value("interval") || "(nothing entered)"}`,
        `Mental status exam: ${mseText() || "(nothing entered)"}`, `Current medications: ${medsText() || "(not included)"}`,
        `Scores: ${scoresText() || "(not included)"}`, `Clinician's assessment and plan: ${value("plan") || "(nothing entered)"}`].join("\n");
      const note = await api("POST", `patients/${uid}/visit-note`, { inputs, model: modelChoice() });
      show([heading(), "", `Subjective: ${note.subjective}`, "", `Objective: ${note.objective}`, "", `Assessment: ${note.assessment}`, "", `Plan: ${note.plan}`].join("\n"));
      form.querySelector(".error").textContent = "";
    } catch (err) {
      form.querySelector(".error").textContent = err.message;
    } finally {
      button.disabled = false;
      aiBusy(false);
    }
  };
  form.querySelector("[data-copy]").onclick = async () => {
    const box = form.querySelector("[name=note]");
    try { await navigator.clipboard.writeText(box.value); } catch { box.select(); document.execCommand("copy"); }
    toast("Copied. Paste it into your records system.");
  };
  form.querySelector("[data-save]").onclick = async () => {
    await api("POST", `patients/${uid}/notes`, { text: form.querySelector("[name=note]").value, date: value("visit_date"), title: heading() });
    toast("Saved to the record ✓ (it's used in future drafts too)");
    recordView("patients", uid);
  };
}

function buildPanel(uid, documents) {
  $("#buildBox").innerHTML = `<div class="card"><h2>Build a history draft</h2>
    <p class="muted small">Documents already read (in the background, or by an earlier draft) are reused, so only the rest take time. Corrections you've made to facts are used.</p>
    <div class="doc-pick">${documents.map((d) => `<label><input type="checkbox" value="${esc(d.id)}" checked> ${esc(d.name)}
      <span class="muted small">${esc(d.document_type || "type not known yet")} · ${esc(READ_STATES[d.read_state] || "not read yet")}</span></label>`).join("")}</div>
    <label class="small muted"><input type="checkbox" id="reread"> Re-read documents already read</label>
    <div class="actions" style="margin-top:12px"><button id="startBuild">Start</button></div>
    <div id="buildProgress" hidden><progress id="bar" value="0" max="1"></progress><div id="buildStatus" class="muted small"></div><div id="buildWarnings"></div></div></div>`;
  $("#buildBox").scrollIntoView({ behavior: "smooth" });
  $("#startBuild").onclick = async () => {
    const docIds = [...$("#buildBox").querySelectorAll(".doc-pick input:checked")].map((i) => i.value);
    if (!docIds.length) return;
    $("#startBuild").disabled = true;
    $("#buildProgress").hidden = false;
    aiBusy(true);
    const started = Date.now();
    let firstAt = null, firstStep = 0;
    try {
      const res = await fetch(`/api/patients/${uid}/history`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc_ids: docIds, model: modelChoice(), reread: $("#reread").checked }) });
      if (!res.headers.get("Content-Type").includes("ndjson")) throw new Error((await res.json()).error);
      await readStream(res, (u) => {
        if (u.error) throw new Error(u.error);
        if (u.warning) { $("#buildWarnings").insertAdjacentHTML("beforeend", `<div class="error small">${esc(u.warning)}</div>`); return; }
        $("#bar").max = u.total; $("#bar").value = u.step;
        if (firstAt === null && u.step > 0) { firstAt = Date.now(); firstStep = u.step; }
        let eta = "";
        if (firstAt && u.step > firstStep) eta = ` · about ${Math.max(1, Math.round((Date.now() - firstAt) / 60000 / (u.step - firstStep) * (u.total - u.step)))} min left`;
        const s = (Date.now() - started) / 1000;
        $("#buildStatus").textContent = `${u.progress || "Done"} · ${Math.floor(s / 60)}m ${Math.round(s % 60)}s${u.result ? "" : eta}`;
        if (u.result) location.hash = `#/r/drafts/${u.draft_id}`;
      });
    } catch (err) {
      $("#buildStatus").innerHTML = `<span class="error">${esc(err.message)}</span>`;
      $("#startBuild").disabled = false;
    }
    aiBusy(false);
  };
}

function documentExtras(data, body) {
  body.insertAdjacentHTML("beforeend", `
    <div class="card"><h2>Facts found <span class="count">${data.related.facts.length}</span></h2>${factsBySection(data.related.facts, false)}</div>
    <div class="card"><h2>Text</h2><details><summary class="muted">Show the document's text</summary><div class="text-preview">${esc(data.text)}</div></details></div>`);
}

// --- Drafts ------------------------------------------------------------------------

function draftView(data, body) {
  const result = data.result;
  const uid = data.record.id;
  body.innerHTML = `
    <div class="card"><h2>Review the draft <span class="muted small" style="font-family:var(--sans)">for ${recordLink(data.record.patient_id, esc(data.record.patient))}</span>
      <span class="actions"><span id="saveMsg" class="muted small"></span><button class="secondary small" id="saveDraft">Save changes</button><button class="small" id="exportDraft">Export to Word</button></span></h2>
      <p class="muted small">Edit any section. Citations like <b>[D2 p.4]</b> mean document 2, page 4. Red boxes flag where documents disagree.
        Facts whose quote couldn't be found in the source are marked <span class="badge bad">not verified</span> and left out.</p>
      <div class="table-wrap"><table><thead><tr><th></th><th>File</th><th>Type</th><th>Date</th><th>Author</th></tr></thead><tbody>
        ${result.documents.map((d) => `<tr><td><b>${d.label}</b></td><td>${esc(d.name)}${scanNote(d)}</td>
          <td>${esc(d.type)}</td><td>${esc(d.date)}</td><td>${esc(d.author)}</td></tr>`).join("")}</tbody></table></div></div>
    ${result.sections.map((s, i) => {
      const blank = !s.facts.some((f) => f.verified);
      const unverified = s.facts.filter((f) => !f.verified).length;
      return `<div class="card draft-section ${blank ? "blank" : ""}"><h2>${esc(s.title)}</h2>
        <textarea data-section="${i}" rows="3">${esc(s.narrative)}</textarea>
        ${s.conflicts.map((c) => `<div class="conflict">⚠ ${esc(c.description)} ${c.sources ? `[${esc(c.sources)}]` : ""}</div>`).join("")}
        ${s.facts.length ? `<details><summary class="muted small">Facts found (${s.facts.length})${unverified ? ` · ${unverified} not verified` : ""}</summary>
          ${s.facts.map((f) => `<div class="fact"><b class="ok">[${esc(f.source)}]</b> ${f.date ? esc(f.date) + ": " : ""}${esc(f.fact)}
            ${f.verified ? "" : '<span class="badge bad">not verified</span>'}<span class="quote">“${esc(f.quote)}”</span></div>`).join("")}</details>` : ""}
      </div>`;
    }).join("")}`;
  const autosize = (t) => { t.style.height = "auto"; t.style.height = t.scrollHeight + 4 + "px"; };
  body.querySelectorAll("textarea").forEach(autosize);
  body.oninput = (e) => {
    if (e.target.dataset.section === undefined) return;
    result.sections[+e.target.dataset.section].narrative = e.target.value;
    state.dirtyDraft = true;
    $("#saveMsg").textContent = "Unsaved changes";
    autosize(e.target);
  };
  const save = async () => {
    await api("POST", `drafts/${uid}`, { result });
    state.dirtyDraft = false;
    $("#saveMsg").textContent = "";
    toast("Draft saved ✓");
  };
  $("#saveDraft").onclick = () => save().catch((err) => $("#saveMsg").textContent = err.message);
  $("#exportDraft").onclick = async () => {
    if (!state.exportWarned && !confirm("The Word file won't be encrypted once it leaves MindChart. Save it only where your organization allows patient records. Continue?")) return;
    state.exportWarned = true;
    if (state.dirtyDraft) await save();
    const res = await api("POST", "export", { sections: result.sections, documents: result.documents, draft_id: uid });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await res.blob());
    link.download = `History draft - ${data.record.patient}.docx`;
    link.click();
    URL.revokeObjectURL(link.href);
  };
}

// Where text came from OCR (scans, photos, pictures in Word files), so it gets checked carefully.
function scanNote(d) {
  const unclear = (d.unclear_pages || []).filter((p) => p !== null);
  const scanned = (d.scanned_pages || []).filter((p) => p !== null && !unclear.includes(p));
  const notes = [];
  if ((d.scanned_pages || []).includes(null)) notes.push("Some text was read from pictures in the file: check it carefully");
  if (scanned.length) notes.push(`OCR on page ${scanned.join(", ")}: check scanned text carefully`);
  if (unclear.length) notes.push(`<b class="warn">Hard to read (handwriting or a poor copy) on page ${unclear.join(", ")}: check against the original</b>`);
  return notes.map((n) => `<br><span class="muted small">${n}</span>`).join("");
}

// --- Settings ----------------------------------------------------------------------

async function settingsView() {
  const s = await api("GET", "settings");
  const status = (ok, good, bad) => `<span class="${ok ? "status-ok" : "status-bad"}">${ok ? "✓ " + good : "⚠ " + bad}</span>`;
  $("#view").innerHTML = `
    <div class="page-head"><span class="object-icon obj-activity"><span class="i-settings"></span></span><h1>Settings</h1></div>
    <div class="grid2">
      <form class="card" id="nameForm" onsubmit="return false"><h2>Your name</h2>
        <p class="muted small">Shown in the activity log next to everything you do.</p>
        <div class="row"><input name="name" value="${esc(s.user_name)}" style="flex:1"><button type="submit" class="small">Save</button></div>
        <p class="error"></p></form>
      <form class="card" id="passwordForm" onsubmit="return false"><h2>Change password</h2>
        <p class="muted small">Your records are re-protected with the new password. Keep it somewhere safe: a lost password can't be recovered.</p>
        <div class="fields"><div class="field"><label>Current password</label><input type="password" name="current" autocomplete="current-password"></div>
          <div class="field"><label>New password</label><input type="password" name="new" autocomplete="new-password"></div>
          <div class="field"><label>New password again</label><input type="password" name="again" autocomplete="new-password"></div></div>
        <div class="actions" style="margin-top:12px"><button type="submit" class="small">Change password</button><span class="error"></span></div></form>
    </div>
    <div class="card"><h2>Security</h2><div class="fields">
      <div class="field"><label>Records encrypted</label><div class="value"><span class="status-ok"><span class="i-lock secure-icon"></span>
        AES-256, opened only by your password</span></div></div>
      <div class="field"><label>Sent over the internet</label><div class="value"><span class="status-ok"><span class="i-lock secure-icon"></span>
        Never: records stay on this Mac</span></div></div>
      <div class="field"><label>Mac disk encryption (FileVault)</label><div class="value">${status(s.filevault, "On", "Off: turn it on in System Settings → Privacy & Security")}</div></div>
      <div class="field"><label>Automatic lock</label><div class="value">${status(true, `After ${s.auto_lock_minutes} minutes without activity`, "")}</div></div>
      <div class="field"><label>Activity log</label><div class="value">${status(s.audit.ok, `Intact, ${s.audit.entries} entries checked`, `Altered at entry ${esc(s.audit.problem_at || "")}`)}</div></div>
      <div class="field wide"><label>Records are stored in</label><div class="value small">${esc(s.data_location)}</div></div>
      <div class="field wide"><label>Error log (no patient details; send this if something goes wrong)</label><div class="value small">${esc(s.log_file)}</div></div>
      <div class="field"><label>Version</label><div class="value">${esc(s.version.current)}</div></div>
    </div>
    <div class="actions" style="margin-top:14px"><button class="secondary small" id="exportAudit">Export activity log (CSV)</button>
      <button class="link small" id="securityHelp">How your records are protected</button></div></div>
    <div class="card"><h2>Getting to know MindChart</h2>
      <p class="muted small">The tour shows each part of MindChart in about two minutes. Press ? on any page for help with that page.</p>
      <div class="actions"><button class="secondary small" id="startTour">Take the tour</button>
        <button class="secondary small" id="showChecklist">Show the getting-started checklist on Home</button></div></div>`;
  $("#startTour").onclick = () => startTour();
  $("#securityHelp").onclick = () => openHelp("security");
  $("#showChecklist").onclick = async () => { await api("POST", "settings/checklist", { state: "" }); location.hash = "#/"; };

  $("#nameForm").onsubmit = async () => {
    try { await api("POST", "settings/name", { name: $("#nameForm [name=name]").value }); toast("Name saved ✓"); }
    catch (err) { $("#nameForm .error").textContent = err.message; }
  };
  $("#passwordForm").onsubmit = async () => {
    const v = formValues($("#passwordForm"));
    if (v.new !== v.again) { $("#passwordForm .error").textContent = "The new passwords don't match."; return; }
    try { await api("POST", "settings/password", { current: v.current, new: v.new }); $("#passwordForm").reset(); toast("Password changed ✓"); }
    catch (err) { $("#passwordForm .error").textContent = err.message; }
  };
  $("#exportAudit").onclick = async () => {
    const res = await api("GET", "audit.csv");
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await res.blob());
    link.download = "MindChart activity log.csv";
    link.click();
    URL.revokeObjectURL(link.href);
  };
}

// --- Uploads: files wait here until their patient and type are confirmed -----------

// Drop files anywhere in MindChart to upload them; dropped on a patient's page, they're suggested for that patient.
const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
let dragDepth = 0;
document.addEventListener("dragenter", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  if ($("#app").hidden) return;
  dragDepth++;
  document.body.classList.add("dropping");
});
document.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });  // also stops the window opening the file
document.addEventListener("dragleave", (e) => {
  if (!hasFiles(e)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) document.body.classList.remove("dropping");
});
document.addEventListener("drop", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth = 0;
  document.body.classList.remove("dropping");
  if (!$("#app").hidden) uploadFiles(e.dataTransfer.files, currentPatient());
});

function currentPatient() {
  const match = location.hash.match(/^#\/r\/patients\/([^/]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

function pickFiles(patientUid = null) {
  $("#fileInput").onchange = () => {
    const files = [...$("#fileInput").files];
    $("#fileInput").value = "";
    uploadFiles(files, patientUid);
  };
  $("#fileInput").click();
}

// Each file is read straight away (scans and photos with OCR), then waits for review in Uploads.
async function uploadFiles(files, patientUid = null) {
  files = [...files];
  if (!files.length) return;
  let added = 0;
  for (const [i, file] of files.entries()) {
    toast(`Reading ${file.name} (${i + 1} of ${files.length})… scans and photos take a little longer`, 0);
    try {
      await api("POST", `uploads?name=${encodeURIComponent(file.name)}${patientUid ? `&patient=${encodeURIComponent(patientUid)}` : ""}`, file, true);
      added++;
    } catch (err) { alert(`${file.name}: ${err.message}`); }
  }
  toast(added ? `${added} file${added === 1 ? "" : "s"} ready to review` : "");
  await refreshWork();
  if (location.hash === "#/uploads") syncUploadCards(); else location.hash = "#/uploads";
}

// What's happening in the background, shown in the sidebar. Review cards update as the AI finishes with them.
async function refreshWork() {
  if ($("#app").hidden) return;
  let work;
  try { work = await api("GET", "work"); } catch { return; }
  const before = state.work;
  state.work = work;
  const badge = document.getElementById("uploadsBadge");
  if (badge) { badge.textContent = work.uploads || ""; badge.hidden = !work.uploads; }
  const lines = [];
  if (work.current) lines.push(`${work.current.kind === "identify" ? "Identifying" : "Reading"} <b>${esc(work.current.name)}</b>` +
    (work.current.kind === "read" ? `<br>${esc(work.current.progress)}` : ""));
  const more = work.to_read - (work.current?.kind === "read" ? 1 : 0);
  if (more > 0) lines.push(`${more} more document${more === 1 ? "" : "s"} to read`);
  if (work.note) lines.push(esc(work.note));
  $("#work").innerHTML = lines.length ? `<span class="spinner"></span><span>${lines.join("<br>")}</span>` : "";
  $("#work").hidden = !lines.length;
  if (location.hash === "#/uploads" && (work.identifying !== before.identifying || work.uploads !== before.uploads)) syncUploadCards();
}
setInterval(refreshWork, 3000);

async function uploadsView() {
  state.uploads = await api("GET", "uploads");
  $("#view").innerHTML = `
    <div class="page-head"><span class="object-icon obj-documents"><span class="i-upload"></span></span>
      <div><div class="kicker">Inbox</div><h1>Uploads to review</h1></div><span class="badge plain" id="uploadCount"></span>
      <span class="grow"></span><button class="secondary" id="confirmReady" hidden></button><button id="addFiles">Upload documents</button></div>
    <p class="muted small intro">Check each file's type and patient, choose what to take from it, and confirm. Confirmed documents are read in
      the background, ready for drafting, and each confirmation teaches MindChart to recognize that kind of document.</p>
    <p class="secure-note left"><span class="i-lock secure-icon"></span> Uploaded files stay on this Mac, inside your encrypted records.
      Nothing is sent over the internet.</p>
    <div id="uploadCards"></div>`;
  $("#addFiles").onclick = () => pickFiles();
  $("#confirmReady").onclick = confirmReady;
  renderUploadCards();
}

const cardFor = (id) => document.querySelector(`.upload-card[data-upload="${id}"]`);
const documentTypes = () => Object.keys(state.objects.documents.record_types).filter((t) => t !== "Clinician Note");
const capitalize = (text) => text.charAt(0).toUpperCase() + text.slice(1);
// "JORDAN AVERY" or "jordan avery" -> "Jordan Avery"; names already in mixed case are left alone.
const titleCase = (name) => !name || /[a-z]/.test(name) && /[A-Z]/.test(name) ? name || "" : name.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());

// Add cards for new uploads, take away ones that are done, and update the counts.
function renderUploadCards() {
  const box = $("#uploadCards");
  if (!box) return;
  const uploads = state.uploads.uploads;
  box.querySelector(".empty-card")?.remove();
  box.querySelectorAll(".upload-card").forEach((card) => { if (!uploads.some((u) => u.id === card.dataset.upload)) card.remove(); });
  for (const u of uploads) {
    if (cardFor(u.id)) continue;
    box.insertAdjacentHTML("beforeend", uploadCard(u));
    bindUploadCard(cardFor(u.id), u);
  }
  if (!uploads.length) box.innerHTML = `<div class="card empty-card"><p class="empty">Nothing waiting. Drop PDFs, Word files, photos or faxes
    anywhere in MindChart to add them.</p></div>`;
  $("#uploadCount").textContent = uploads.length;
  const ready = readyUploads();
  $("#confirmReady").hidden = !ready.length;
  $("#confirmReady").textContent = `Confirm ${ready.length} that ${ready.length === 1 ? "is" : "are"} ready`;
}

// Fetch the latest suggestions. Cards nobody has touched are redrawn; touched ones just get a note, so no edits are lost.
async function syncUploadCards(redrawAll = false) {
  const old = Object.fromEntries((state.uploads?.uploads || []).map((u) => [u.id, u]));
  state.uploads = await api("GET", "uploads");
  for (const u of state.uploads.uploads) {
    const card = cardFor(u.id);
    if (!card || !old[u.id] || (old[u.id].state === u.state && !redrawAll)) continue;
    if (card.dataset.touched) noteAiResult(card, u); else replaceCard(card, u);
  }
  renderUploadCards();
}

function replaceCard(card, u) {
  card.outerHTML = uploadCard(u);
  bindUploadCard(cardFor(u.id), u);
}

function typeOptions(selected) {
  return documentTypes().map((t) => `<option ${t === selected ? "selected" : ""}>${esc(t)}</option>`).join("") +
    '<option value="__new">+ New type…</option>';
}

function uploadCard(u) {
  const s = u.suggestion;
  const conf = { high: ["Sure", "sure"], medium: ["Likely", "likely"] }[s.type_confidence] || ["Unsure", "unsure"];
  const person = (p) => `${p.name}${p.dob ? ` · born ${p.dob}` : ""}`;
  const suggested = s.matches.map((m) => `<option value="${esc(m.id)}">${esc(person(m))} · ${{ strong: "matches", likely: "matches by name" }[m.strength] || "possible match"}</option>`).join("");
  const others = state.uploads.patients.filter((p) => !s.matches.some((m) => m.id === p.id))
    .map((p) => `<option value="${esc(p.id)}">${esc(person(p))}</option>`).join("");
  const unclear = u.unclear_pages.filter((p) => p !== null);
  const reading = [u.pages ? `${u.pages} page${u.pages === 1 ? "" : "s"}` : "Word or text file",
    u.scanned_pages.length ? "read with OCR" : "",
    unclear.length ? `<b class="warn">hard to read on page ${unclear.join(", ")}</b>` : ""].filter(Boolean).join(" · ");
  return `<form class="card upload-card" data-upload="${esc(u.id)}" onsubmit="return false">
    <div class="upload-head"><span class="object-icon obj-documents"><span class="i-documents"></span></span>
      <div class="grow"><b>${esc(u.name)}</b><div class="muted small">${reading}</div></div>
      <span class="identifying" ${s.ai_done ? "hidden" : ""}><span class="spinner"></span>The AI is reading it…</span></div>
    ${s.warning ? `<div class="conflict">⚠ ${esc(s.warning)}</div>` : ""}
    ${s.ai_error ? `<div class="conflict">${esc(s.ai_error)}</div>` : ""}
    <div class="ai-note" hidden></div>
    <div class="fields">
      <div class="field"><label>Type <span class="conf ${conf[1]}">${conf[0]}</span></label>
        <select name="document_type">${typeOptions(s.document_type)}</select>
        <div class="reason">${esc(s.type_reason || "")}</div></div>
      <div class="field"><label>Patient</label>
        <select name="patient_id"><option value="">Choose a patient…</option>
          ${suggested ? `<optgroup label="Suggested">${suggested}</optgroup>` : ""}
          ${others ? `<optgroup label="${suggested ? "Other patients" : "Patients"}">${others}</optgroup>` : ""}
          <option value="__new">+ New patient from this document</option></select>
        <div class="reason patient-reason"></div></div>
      <div class="field"><label>Date written</label><input name="document_date" value="${esc(s.document_date)}" placeholder="MM/DD/YYYY"></div>
      <div class="field"><label>Author</label><input name="author" value="${esc(s.author)}"></div>
    </div>
    <div class="inset new-type" hidden><h3>New document type</h3>
      <div class="fields"><div class="field"><label>Name</label><input name="type_name" placeholder="Safety Plan"></div>
        <div class="field wide"><label>What it is (helps the AI recognize it)</label>
          <input name="type_description" placeholder="A youth's written plan for staying safe in a crisis"></div></div>
      <div class="actions"><button type="button" class="small" data-add-type>Add type</button>
        <button type="button" class="secondary small" data-cancel-type>Cancel</button></div></div>
    <div class="inset new-patient" hidden><h3>New patient</h3>
      <div class="fields"><div class="field"><label>Name *</label><input name="new_name" value="${esc(titleCase(s.child?.name))}"></div>
        <div class="field"><label>Date of birth</label><input name="new_dob" value="${esc(s.child?.dob)}" placeholder="MM/DD/YYYY"></div>
        <div class="field"><label>Record type</label><select name="new_record_type">
          ${Object.keys(state.objects.patients.record_types).map((t) => `<option>${esc(t)}</option>`).join("")}</select></div></div></div>
    <div class="pull"></div>
    <details><summary class="muted small">Show the text MindChart read</summary><div class="text-preview">${esc(u.preview)}</div></details>
    <div class="actions"><button type="submit">Confirm and file</button><button type="button" class="secondary" data-discard>Discard</button>
      <span class="error"></span></div>
  </form>`;
}

function bindUploadCard(card, u) {
  const s = u.suggestion;
  const type = card.querySelector("[name=document_type]");
  const patient = card.querySelector("[name=patient_id]");
  patient.value = s.patient_id || "";
  const showPatient = () => {
    card.querySelector(".new-patient").hidden = patient.value !== "__new";
    const match = s.matches.find((m) => m.id === patient.value);
    const child = s.child?.name ? `The document is about ${s.child.name}${s.child.dob ? `, born ${s.child.dob}` : ""}` : "";
    card.querySelector(".patient-reason").textContent = match ? capitalize(match.reasons.join(", "))
      : patient.value && patient.value === u.patient_id ? "Uploaded from this patient's page"
      : patient.value === "__new" ? (child ? "Filled in from the document" : "")
      : !patient.value ? child || (s.ai_done ? "Couldn't tell who this is about" : "") : "";
  };
  let lastType = type.value;
  type.onchange = () => {
    if (type.value === "__new") {
      card.querySelector(".new-type").hidden = false;
      card.querySelector("[name=type_name]").focus();
      return;
    }
    lastType = type.value;
    renderPull(card, type.value);
  };
  card.querySelector("[data-cancel-type]").onclick = () => { card.querySelector(".new-type").hidden = true; type.value = lastType; };
  card.querySelector("[data-add-type]").onclick = async () => {
    try {
      const { name } = await api("POST", "record-types", { object: "documents", name: card.querySelector("[name=type_name]").value,
        description: card.querySelector("[name=type_description]").value });
      state.objects = (await api("GET", "objects")).objects;
      document.querySelectorAll(".upload-card [name=document_type]").forEach((select) => {
        const value = select.value;
        select.innerHTML = typeOptions(value);
      });
      type.value = lastType = name;
      card.querySelector(".new-type").hidden = true;
      renderPull(card, name);
      toast(`Added the type “${name}”`);
    } catch (err) { card.querySelector(".error").textContent = err.message; }
  };
  patient.onchange = showPatient;
  card.addEventListener("change", () => { card.dataset.touched = "1"; renderUploadCards(); });
  card.addEventListener("input", () => { card.dataset.touched = "1"; });
  card.querySelector("[data-discard]").onclick = async () => {
    if (!confirm(`Discard ${u.name}? It won't be added to MindChart.`)) return;
    await api("POST", `uploads/${u.id}/discard`);
    removeUpload(u.id);
  };
  card.onsubmit = async () => {
    try { await confirmUpload(card, u); } catch (err) { card.querySelector(".error").textContent = err.message; }
    return false;
  };
  showPatient();
  renderPull(card, type.value);
}

// What to take from the document, ticked the way it was last time for this type (everything, the first time).
function renderPull(card, type) {
  const defs = (state.objects.documents.record_types[type] || []).filter((d) => d.ai);
  const pref = state.uploads.pull_preferences[type];
  const fieldOn = (name) => !pref || pref.fields === null || pref.fields.includes(name);
  card.querySelector(".pull").innerHTML = `<h3>What to take from it</h3>
    <label class="check"><input type="checkbox" name="pull_facts" ${!pref || pref.facts ? "checked" : ""}> Facts for history drafts
      <span class="muted small">(every section: medications, risk, placements, school and more)</span></label>
    ${defs.length ? `<div class="muted small pull-label">${esc(type)} details to fill in</div>
      <div class="multi">${defs.map((d) => `<label><input type="checkbox" data-field="${esc(d.name)}" ${fieldOn(d.name) ? "checked" : ""}> ${esc(d.label)}</label>`).join("")}</div>` : ""}
    ${pref ? '<div class="muted small pull-label">Ticked the way you chose last time for this type.</div>' : ""}`;
}

function noteAiResult(card, u) {
  card.querySelector(".identifying").hidden = true;
  const s = u.suggestion, note = card.querySelector(".ai-note");
  const match = s.matches.find((m) => m.id === s.patient_id);
  note.innerHTML = `The AI has finished: it suggests <b>${esc(s.document_type)}</b>${match ? ` for <b>${esc(match.name)}</b>` : ""}.
    ${s.warning ? `⚠ ${esc(s.warning)}` : ""} <button type="button" class="link small" data-use-ai>Use its suggestion</button>`;
  note.hidden = false;
  note.querySelector("[data-use-ai]").onclick = () => replaceCard(card, u);
}

function cardChoice(card) {
  const get = (name) => card.querySelector(`[name=${name}]`).value.trim();
  const choice = { document_type: get("document_type"), document_date: get("document_date"), author: get("author") };
  if (!choice.document_type || choice.document_type === "__new") throw new Error("Choose the document's type");
  const patient = get("patient_id");
  if (patient === "__new") {
    if (!get("new_name")) throw new Error("Enter the new patient's name");
    choice.new_patient = { name: get("new_name"), dob: get("new_dob"), record_type: get("new_record_type") };
  } else if (patient) {
    choice.patient_id = patient;
  } else {
    throw new Error("Choose which patient this belongs to");
  }
  const boxes = [...card.querySelectorAll("[data-field]")];
  choice.pull = { facts: card.querySelector("[name=pull_facts]").checked,
    fields: boxes.every((b) => b.checked) ? null : boxes.filter((b) => b.checked).map((b) => b.dataset.field) };
  return choice;
}

async function confirmUpload(card, u) {
  const choice = cardChoice(card);
  const who = choice.new_patient ? choice.new_patient.name : card.querySelector("[name=patient_id]").selectedOptions[0].textContent.split(" · ")[0];
  const button = card.querySelector("[type=submit]");
  button.disabled = true;
  try {
    await api("POST", `uploads/${u.id}/confirm`, choice);
    state.uploads.pull_preferences[choice.document_type] = choice.pull;
    removeUpload(u.id);
    toast(`Filed under ${who} · it'll be read in the background`);
    refreshWork();
    if (choice.new_patient && state.uploads.uploads.length) syncUploadCards(true);  // other files may be about the new patient
  } finally {
    button.disabled = false;
  }
}

function removeUpload(id) {
  state.uploads.uploads = state.uploads.uploads.filter((u) => u.id !== id);
  state.work.uploads = state.uploads.uploads.length;
  const badge = document.getElementById("uploadsBadge");
  if (badge) { badge.textContent = state.work.uploads || ""; badge.hidden = !state.work.uploads; }
  renderUploadCards();
}

// Files MindChart is sure about (type, and patient by a strong match or the page they were dropped on), with no warnings.
function readyUploads() {
  return (state.uploads?.uploads || []).filter((u) => {
    const s = u.suggestion, card = cardFor(u.id);
    if (!card || !s.ai_done || s.warning || s.type_confidence !== "high") return false;
    const chosen = card.querySelector("[name=patient_id]").value;
    const match = s.matches.find((m) => m.id === chosen);
    return chosen && chosen !== "__new" && (match?.strength === "strong" || chosen === u.patient_id);
  });
}

async function confirmReady() {
  for (const u of readyUploads()) {
    const card = cardFor(u.id);
    try { await confirmUpload(card, u); } catch (err) { card.querySelector(".error").textContent = err.message; }
  }
}

// --- Command bar -------------------------------------------------------------------

function showResult(html) {
  $("#commandResult").hidden = false;
  $("#commandResult").innerHTML = `<button class="link close" title="Close">✕</button>${html}`;
  $("#commandResult .close").onclick = () => { $("#commandResult").hidden = true; };
}

async function runCommand(message) {
  message = (message ?? $("#commandInput").value).trim();
  if (!message) return;
  $("#commandInput").value = message;
  $("#commandBtn").disabled = true;
  showResult(`<div class="thinking"><span class="spinner"></span>Thinking… (the AI runs on this computer, so this can take a little while)</div><div class="shimmer"></div>`);
  aiBusy(true);
  try {
    const reply = await api("POST", "assistant", { message, model: modelChoice() });
    renderReply(reply);
  } catch (err) {
    showResult(`<p class="error">${esc(err.message)}</p>`);
  }
  aiBusy(false);
  $("#commandBtn").disabled = false;
}

$("#command").onsubmit = () => { runCommand(); return false; };

function renderReply(reply) {
  if (reply.kind === "answer") {
    const linkify = (v) => typeof v === "string" && /^(PT|DOC|FCT|DRF|ACT|MED|MSR|FUP)-[A-Z0-9]{6}$/.test(v) ? recordLink(v) : esc(v ?? "");
    showResult(`<h3 class="kicker">Answer</h3><p>${esc(reply.explanation)}</p>
      ${reply.rows.length ? `<div class="table-wrap"><table><thead><tr>${reply.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
        <tbody>${reply.rows.map((r) => `<tr>${r.map((v) => `<td>${linkify(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
        <p class="muted small">${reply.rows.length} row${reply.rows.length === 1 ? "" : "s"}</p>` : '<p class="empty">No matching records.</p>'}
      <details><summary class="muted small">How this was looked up</summary><pre class="text-preview" style="font-family:var(--mono)">${esc(reply.sql)}</pre></details>`);
  } else if (reply.kind === "proposal") {
    renderProposal(reply);
  } else if (reply.kind === "done") {
    showResult(`<p class="ok">✓ ${esc(reply.text)} ${recordLink(reply.id, "Open")}</p>`);
  } else {
    showResult(`<p>${esc(reply.text)}</p>`);
  }
}

function renderProposal(proposal) {
  const titles = { new_patient: "New patient", add_note: "New note", update_patient: "Update patient",
    add_medication: "New medication", add_measure: "New score", add_follow_up: "New follow-up" };
  let fields = "";
  if (proposal.object) {  // a medication, measure or follow-up
    fields = state.objects[proposal.object].fields.filter((f) => f.edit !== false).map((f) => fieldInput(proposal.object, f,
      proposal.fields[f.name] ?? ({ medications: { status: "Active" }, follow_ups: { status: "Open" } }[proposal.object] || {})[f.name] ?? "")).join("");
  } else if (proposal.action === "new_patient") {
    fields = state.objects.patients.fields.filter((f) => f.edit !== false).map((f) => fieldInput("patients", f,
      proposal.fields[f.name] || (f.name === "status" ? "Active" : f.name === "record_type" ? f.options[0] : ""))).join("");
  } else if (proposal.action === "add_note") {
    fields = `<div class="field wide"><label>Note</label><textarea name="text" rows="4">${esc(proposal.fields.text)}</textarea></div>
      <div class="field"><label>Date</label><input name="date" value="${esc(proposal.fields.date)}" placeholder="MM/DD/YYYY (today if blank)"></div>`;
  } else {
    const typeDefs = Object.values(state.objects.patients.record_types || {}).flat();
    fields = Object.keys(proposal.fields).map((name) => {
      const f = fieldSpec("patients", name) || typeDefs.find((d) => d.name === name);
      return f ? fieldInput("patients", f, proposal.fields[name]) : "";
    }).join("");
  }
  showResult(`<form class="proposal" onsubmit="return false">
      <h3 class="kicker">${titles[proposal.action]} · check before saving</h3>
      <p>${esc(proposal.summary)}${proposal.patient ? ` <b>${recordLink(proposal.patient.id, esc(proposal.patient.name))}</b>` : ""}</p>
      <div class="fields">${fields}</div>
      ${proposal.action === "new_patient" || proposal.object === "measures" ? '<div class="custom-fields"></div>' : ""}
      <div class="actions" style="margin-top:14px"><button type="submit">Save</button><button type="button" class="secondary" data-cancel>Cancel</button><span class="error"></span></div>
    </form>`);
  const form = $("#commandResult form");
  if (proposal.action === "new_patient") bindRecordType(form, "patients", proposal.fields, "");
  if (proposal.object === "measures") bindRecordType(form, "measures", {}, "custom.");
  form.querySelector("[data-cancel]").onclick = () => { $("#commandResult").hidden = true; };
  form.onsubmit = async () => {
    const values = formValues(form);
    if (proposal.action === "update_patient") Object.keys(values).forEach((k) => { if (!(k in proposal.fields)) delete values[k]; });
    try {
      const done = await api("POST", "assistant/confirm", { proposal: { ...proposal, fields: values } });
      renderReply(done);
      $("#commandInput").value = "";
      toast("Saved ✓");
      route();
    } catch (err) { form.querySelector(".error").textContent = err.message; }
    return false;
  };
}

start();
