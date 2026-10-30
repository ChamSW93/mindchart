// MindChart's page: sign-in, CRM-style lists and record pages, the command bar, uploads, and history drafting.
// Everything talks only to MindChart's own server on this computer.

const $ = (selector, root = document) => root.querySelector(selector);
const state = { session: null, objects: null, sectionTitles: {}, models: [], dirtyDraft: false, work: {}, uploads: null };
const ICONS = { patients: "i-patients", documents: "i-documents", facts: "i-facts", drafts: "i-drafts", activity: "i-activity" };
const PREFIXES = { PT: "patients", DOC: "documents", FCT: "facts", DRF: "drafts", ACT: "activity" };
const HIDDEN_COLUMNS = ["patient_id", "document_id", "notes", "quote", "read_by_model", "seq", "details", "read_state"];
const READ_STATES = { queued: "Waiting to be read", fields: "Updating details", read: "Read", failed: "Couldn't read" };
const EXAMPLES = [
  "Which patients have an order of protection?",
  "List current medications for each active patient",
  "Show facts that aren't verified yet",
  "New patient: Maria Lopez, DOB 03/04/2012, placement Cottage 4",
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
  const items = [["#/", "Home", "i-home"], ["#/uploads", "Uploads", "i-upload"], ...["patients", "documents", "facts", "drafts", "activity"]
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
  $("#view").innerHTML = `
    <div class="page-head"><span class="object-icon obj-${object}"><span class="${ICONS[object]}"></span></span>
      <h1>${esc(spec.plural)}</h1><span class="badge plain">${data.rows.length}</span><span class="grow"></span>
      ${object === "patients" ? '<button id="newPatient">+ New patient</button>' : ""}
      ${object === "documents" ? '<button id="listUpload">Upload documents</button>' : ""}</div>
    <div id="newPatientForm"></div>
    <div class="list-tools"><input id="search" placeholder="Search ${esc(spec.plural.toLowerCase())}…" value="${esc(search)}"></div>
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
  const { documents, facts, drafts, activity } = data.related;
  $("#recordActions").insertAdjacentHTML("afterbegin", `
    <button class="secondary" id="uploadBtn">Upload documents</button>
    <button class="secondary" id="noteBtn">Add note</button>
    <button id="buildBtn" ${documents.length ? "" : "disabled"}>Build history</button>`);
  body.insertAdjacentHTML("beforeend", `
    <div id="noteBox"></div>
    <div id="buildBox"></div>
    <div class="card"><h2>Documents & notes <span class="count">${documents.length}</span></h2>
      ${documents.length ? recordsTable("documents", documents, ["name", "document_type", "document_date", "author", "read_state", "facts"])
        : '<p class="empty">Upload records (PDF, Word, photos or faxes), or drop them onto this page. You can also add a note.</p>'}</div>
    <div class="card"><h2>Facts <span class="count">${facts.length}</span></h2>${factsBySection(facts, true)}</div>
    <div class="grid2">
      <div class="card"><h2>Drafts <span class="count">${drafts.length}</span></h2>
        ${drafts.length ? recordsTable("drafts", drafts, ["created", "updated", "model"]) : '<p class="empty">No drafts yet.</p>'}</div>
      <div class="card"><h2>Activity</h2>${activity.length ? recordsTable("activity", activity.slice(0, 15), ["time", "action", "detail"]) : '<p class="empty">None.</p>'}</div>
    </div>`);

  $("#uploadBtn").onclick = () => pickFiles(uid);

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
    const linkify = (v) => typeof v === "string" && /^(PT|DOC|FCT|DRF|ACT)-[A-Z0-9]{6}$/.test(v) ? recordLink(v) : esc(v ?? "");
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
  const titles = { new_patient: "New patient", add_note: "New note", update_patient: "Update patient" };
  let fields = "";
  if (proposal.action === "new_patient") {
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
      ${proposal.action === "new_patient" ? '<div class="custom-fields"></div>' : ""}
      <div class="actions" style="margin-top:14px"><button type="submit">Save</button><button type="button" class="secondary" data-cancel>Cancel</button><span class="error"></span></div>
    </form>`);
  const form = $("#commandResult form");
  if (proposal.action === "new_patient") bindRecordType(form, "patients", proposal.fields, "");
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
