// MindChart's page: sign-in, CRM-style lists and record pages, the command bar, and history drafting.
// Everything talks only to MindChart's own server on this computer.

const $ = (selector, root = document) => root.querySelector(selector);
const state = { session: null, objects: null, sectionTitles: {}, models: [], dirtyDraft: false };
const ICONS = { patients: "i-patients", documents: "i-documents", facts: "i-facts", drafts: "i-drafts", activity: "i-activity" };
const PREFIXES = { PT: "patients", DOC: "documents", FCT: "facts", DRF: "drafts", ACT: "activity" };
const HIDDEN_COLUMNS = ["patient_id", "document_id", "notes", "quote", "read_by_model"];
const EXAMPLES = [
  "Which patients have an order of protection?",
  "List current medications for each active patient",
  "Show facts that aren't verified yet",
  "New patient: Maria Lopez, DOB 03/04/2012, placement Cottage 4",
];

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
  const extra = { id: "ID", patient: "Patient", document: "Document", documents: "Documents", facts: "Facts" };
  return fieldSpec(object, column)?.label || extra[column] || column.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

function display(object, column, value) {
  if (value === null || value === undefined || value === "") return '<span class="empty">—</span>';
  const type = fieldSpec(object, column)?.type;
  if (type === "datetime") return esc(String(value).replace("T", " ").slice(0, 16));
  if (type === "checkbox") return value ? '<span class="badge">Yes</span>' : '<span class="badge plain">No</span>';
  if (column === "section") return esc(state.sectionTitles[value] || value);
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
  $("#password").autocomplete = setUp ? "current-password" : "new-password";
  $("#password").placeholder = setUp ? "Password" : `New password (${state.session?.min_password_length || 10}+ characters)`;
  $("#signinBtn").textContent = setUp ? "Unlock" : "Set up MindChart";
  $("#signinFine").textContent = setUp ? "" :
    "Your records are encrypted with this password. Keep it somewhere safe: if it's lost, the records can't be recovered.";
  $("#signinError").textContent = "";
  $("#password").value = $("#password2").value = "";
  $("#password").focus();
}

$("#signinForm").onsubmit = async () => {
  const setUp = state.session.set_up;
  const password = $("#password").value;
  if (!setUp && password !== $("#password2").value) { $("#signinError").textContent = "The passwords don't match."; return false; }
  $("#signinBtn").disabled = true;
  try {
    state.session = { ...state.session, ...(await api("POST", setUp ? "session/unlock" : "session/setup", { password })) };
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
  route();
}

async function loadModels() {
  const data = await api("GET", "models");
  state.models = data.models;
  let saved = null;
  try { saved = localStorage.getItem("model"); } catch {}
  const names = data.models.map((m) => m.name);
  $("#model").innerHTML = data.models.map((m) => `<option value="${esc(m.name)}">${esc(m.name)} · ${m.size_gb} GB${m.fits ? "" : " · slow on this Mac"}</option>`).join("");
  if (state.system) {
    const v = state.system.version || {};
    $("#hardware").innerHTML = `${esc(state.system.chip)} · ${state.system.memory_gb} GB<br>Version ${esc(v.current || "")}` +
      (v.available ? `<br><b class="ok">Version ${esc(v.latest)} is ready: restart ${esc(document.title)} to install it</b>` : "");
  }
  $("#model").value = names.includes(saved) ? saved : names.includes(data.default) ? data.default : names[0];
}
$("#model").onchange = () => { try { localStorage.setItem("model", $("#model").value); } catch {} };

window.addEventListener("beforeunload", (e) => { if (state.dirtyDraft) e.preventDefault(); });

// --- Navigation --------------------------------------------------------------------

function renderNav() {
  const here = location.hash || "#/";
  const items = [["#/", "Home", "i-home"], ...["patients", "documents", "facts", "drafts", "activity"]
    .map((o) => [`#/list/${o}`, state.objects[o].plural, ICONS[o]])];
  $("#nav").innerHTML = items.map(([href, text, icon]) => {
    const object = href.split("/")[2];
    const active = here === href || (object && here.startsWith(`#/r/${object}/`)) ? "active" : "";
    return `<a href="${href}" class="${active}"><span class="${icon}"></span>${esc(text)}</a>`;
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

function toast(text) {
  document.getElementById("toast")?.remove();
  document.body.insertAdjacentHTML("beforeend", `<div id="toast">${esc(text)}</div>`);
  setTimeout(() => document.getElementById("toast")?.remove(), 2400);
}

function aiBusy(on) {
  document.body.classList.toggle("ai-busy", on);
}

// --- Home --------------------------------------------------------------------------

async function homeView() {
  const patients = (await api("GET", "records/patients")).rows;
  $("#view").innerHTML = `
    <div class="page-head"><div><div class="kicker">Welcome back</div><h1>What would you like to do?</h1></div></div>
    <div class="card">
      <h2>Try asking</h2>
      <div class="chips">${EXAMPLES.map((e) => `<button class="chip" data-example="${esc(e)}">${esc(e)}</button>`).join("")}</div>
    </div>
    <div class="card">
      <h2>Patients <span class="count">${patients.length}</span>
        <span class="actions"><button class="small" id="newPatient">+ New patient</button></span></h2>
      <div id="newPatientForm"></div>
      ${patients.length ? recordsTable("patients", patients, ["id", "name", "dob", "status", "placement", "documents"])
        : '<p class="empty">No patients yet. Add one with the button above or the command bar.</p>'}
    </div>`;
  $("#view").querySelectorAll("[data-example]").forEach((b) => b.onclick = () => runCommand(b.dataset.example));
  $("#newPatient").onclick = () => newPatientForm($("#newPatientForm"));
}

function newPatientForm(target) {
  const fields = state.objects.patients.fields.filter((f) => f.edit !== false);
  target.innerHTML = `<form class="card" style="background:var(--panel-2);box-shadow:none" onsubmit="return false">
      <div class="fields">${fields.map((f) => fieldInput("patients", f, f.name === "status" ? "Active" : "")).join("")}</div>
      <div class="actions" style="margin-top:12px"><button type="submit">Save patient</button>
        <button type="button" class="secondary" data-cancel>Cancel</button><span class="error"></span></div></form>`;
  const form = target.querySelector("form");
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
    <div class="page-head"><span class="object-icon"><span class="${ICONS[object]}"></span></span>
      <h1>${esc(spec.plural)}</h1><span class="badge plain">${data.rows.length}</span><span class="grow"></span>
      ${object === "patients" ? '<button id="newPatient">+ New patient</button>' : ""}</div>
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
}

// --- Record pages ------------------------------------------------------------------

function fieldInput(object, field, value) {
  const name = `name="${field.name}"`;
  const wide = field.type === "longtext" ? "wide" : "";
  let input;
  if (field.type === "picklist") {
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
  const values = {};
  form.querySelectorAll("[name]").forEach((el) => { values[el.name] = el.type === "checkbox" ? el.checked : el.value; });
  return values;
}

function detailsCard(object, record, editing) {
  const spec = state.objects[object];
  const fields = spec.fields;
  const body = editing
    ? `<form id="editForm" onsubmit="return false"><div class="fields">${fields.filter((f) => f.edit !== false).map((f) => fieldInput(object, f, record[f.name])).join("")}</div>
        <div class="actions" style="margin-top:14px"><button type="submit">Save</button><button type="button" class="secondary" id="cancelEdit">Cancel</button><span class="error"></span></div></form>`
    : `<div class="fields">${fields.map((f) => `<div class="field ${f.type === "longtext" ? "wide" : ""}"><label>${esc(f.label)}</label>
        <div class="value">${display(object, f.name, record[f.name])}</div></div>`).join("")}
        ${record.patient && object !== "patients" ? `<div class="field"><label>Patient</label><div class="value">${recordLink(record.patient_id, esc(record.patient))}</div></div>` : ""}
        ${record.document && object === "facts" ? `<div class="field"><label>Document</label><div class="value">${recordLink(record.document_id, esc(record.document))}</div></div>` : ""}
      </div>`;
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
    <div class="page-head"><span class="object-icon"><span class="${ICONS[object]}"></span></span>
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
  body.innerHTML = detailsCard(object, record, editing);
  if (editing) {
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
      <div id="uploadStatus" class="muted small"></div>
      ${documents.length ? recordsTable("documents", documents, ["name", "kind", "document_type", "document_date", "author", "facts"])
        : '<p class="empty">Upload PDF or Word records, or add a note.</p>'}</div>
    <div class="card"><h2>Facts <span class="count">${facts.length}</span></h2>${factsBySection(facts, true)}</div>
    <div class="grid2">
      <div class="card"><h2>Drafts <span class="count">${drafts.length}</span></h2>
        ${drafts.length ? recordsTable("drafts", drafts, ["created", "updated", "model"]) : '<p class="empty">No drafts yet.</p>'}</div>
      <div class="card"><h2>Activity</h2>${activity.length ? recordsTable("activity", activity.slice(0, 15), ["time", "action", "detail"]) : '<p class="empty">None.</p>'}</div>
    </div>`);

  $("#uploadBtn").onclick = () => {
    $("#fileInput").onchange = async () => {
      const files = [...$("#fileInput").files];
      $("#fileInput").value = "";
      for (const [i, file] of files.entries()) {
        $("#uploadStatus").textContent = `Reading and saving ${file.name} (${i + 1} of ${files.length})…`;
        try { await api("POST", `patients/${uid}/documents?name=${encodeURIComponent(file.name)}`, file, true); }
        catch (err) { alert(`${file.name}: ${err.message}`); }
      }
      recordView("patients", uid);
    };
    $("#fileInput").click();
  };

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
    <p class="muted small">Documents already read are reused, so only new ones take time. Corrections you've made to facts are used.</p>
    <div class="doc-pick">${documents.map((d) => `<label><input type="checkbox" value="${esc(d.id)}" checked> ${esc(d.name)}
      <span class="muted small">${esc(d.document_type || "not read yet")}</span></label>`).join("")}</div>
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
        ${result.documents.map((d) => `<tr><td><b>${d.label}</b></td><td>${esc(d.name)}${d.scanned_pages.length ? `<br><span class="muted small">OCR on page ${d.scanned_pages.join(", ")}: check scanned text carefully</span>` : ""}</td>
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
    if (state.dirtyDraft) await save();
    const res = await api("POST", "export", { sections: result.sections, documents: result.documents });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await res.blob());
    link.download = `History draft - ${data.record.patient}.docx`;
    link.click();
    URL.revokeObjectURL(link.href);
  };
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
    fields = state.objects.patients.fields.filter((f) => f.edit !== false).map((f) => fieldInput("patients", f, proposal.fields[f.name] || (f.name === "status" ? "Active" : ""))).join("");
  } else if (proposal.action === "add_note") {
    fields = `<div class="field wide"><label>Note</label><textarea name="text" rows="4">${esc(proposal.fields.text)}</textarea></div>
      <div class="field"><label>Date</label><input name="date" value="${esc(proposal.fields.date)}" placeholder="MM/DD/YYYY (today if blank)"></div>`;
  } else {
    fields = Object.keys(proposal.fields).map((name) => {
      const f = fieldSpec("patients", name);
      return f ? fieldInput("patients", f, proposal.fields[name]) : "";
    }).join("");
  }
  showResult(`<form class="proposal" onsubmit="return false">
      <h3 class="kicker">${titles[proposal.action]} · check before saving</h3>
      <p>${esc(proposal.summary)}${proposal.patient ? ` <b>${recordLink(proposal.patient.id, esc(proposal.patient.name))}</b>` : ""}</p>
      <div class="fields">${fields}</div>
      <div class="actions" style="margin-top:14px"><button type="submit">Save</button><button type="button" class="secondary" data-cancel>Cancel</button><span class="error"></span></div>
    </form>`);
  const form = $("#commandResult form");
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
