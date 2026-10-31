// MindChart's guidance: the first-run tour, the getting-started checklist on Home, help for each page (the ? button),
// and the plain-language explanation of how records are protected (the green padlock). Uses helpers from app.js.

// --- Help for each page ---------------------------------------------------------------

const SECURITY_HELP = {
  title: "How your records are protected",
  intro: "In plain terms, here's what keeps your records private, and what's up to you.",
  items: [
    ["Encrypted on this Mac", "Everything in MindChart is kept in an encrypted vault on this Mac, using the encryption built into macOS. Only your password opens it, and the password isn't saved anywhere (unless you turn on Touch ID), so no one can open your records without it. Not even the developer."],
    ["Touch ID, if you turn it on", "Your password is then kept locked by your Mac's Secure Enclave, the chip that handles Touch ID, and opens only with one of your fingerprints, on this Mac. If fingerprints are added or removed, MindChart asks for your password again. It's off unless you turn it on in Settings."],
    ["Never sent over the internet", "The AI runs on this Mac, so records are never sent to an online service. Files you upload go into your encrypted vault, not to the internet. Updates only download new versions of the app."],
    ["Locks itself", "MindChart locks after 15 minutes without use, when you click Lock, and when you quit. Locking takes everything off the screen."],
    ["Every access is recorded", "The Activity log notes who opened, changed, exported or deleted what, and when. It can't be edited, and Settings checks that it hasn't been tampered with."],
    ["What's up to you", "Word files you export are ordinary files, not encrypted, so save them only where your organization allows. Keep this Mac's own protections on: FileVault (Settings shows whether it is), a login password and screen lock. Check with your organization before using real patient records."],
  ],
};

const HELP = {
  home: { title: "Home", intro: "Your starting point.", items: [
    ["Ask or log in plain English", "Type in the bar at the top: questions such as “Which patients have an order of protection?”, or things to record, such as “Add a note for Jordan: calm week”."],
    ["Upload documents", "Drop files anywhere in MindChart, or use Upload documents. You'll check each one before it's filed."],
    ["Patients", "Open a patient, or add one with + New patient."],
    ["Getting started", "The checklist ticks itself off as you try each feature. Hide it whenever you like."],
  ] },
  uploads: { title: "Uploads", intro: "Files wait here until you've checked them. Nothing is filed until you confirm.", items: [
    ["Type", "MindChart suggests what kind of document it is, and says how sure it is: Sure, Likely or Unsure. Change it if it's wrong, or add a new type with + New type…"],
    ["Patient", "Matched by name, date of birth and MRN. If the document names someone other than the patient whose page you dropped it on, you'll see a warning. For a child who isn't in MindChart yet, choose + New patient."],
    ["What to take from it", "Facts for history drafts, and which details to fill in. MindChart remembers your choice for that type."],
    ["Confirm", "Files it into the patient's records and reads it in the background. Confirm all that are ready files the ones MindChart is sure of."],
    ["It learns", "Each confirmation, and each later correction, teaches MindChart to recognize that kind of document."],
  ] },
  "list:patients": { title: "Patients", intro: "Everyone you keep records for.", items: [
    ["Search", "Type any part of a name, ID or other detail."],
    ["+ New patient", "Add someone. Their placement (such as Residential treatment or Foster care) decides which extra details they have, and you can pick their legal guardian."],
    ["Open a patient", "Click a name to see their documents, facts, drafts and activity."],
    ["Dates", "Type a date, or click the calendar in the box to pick one."],
  ] },
  "list:guardians": { title: "Guardians", intro: "Parents, relatives, foster parents and agencies legally responsible for your patients.", items: [
    ["+ New guardian", "Add their name, relationship and contact details. You can also add one while editing a patient: under Legal guardian, choose New guardian."],
    ["One guardian, several patients", "Siblings can share a guardian. Each guardian's page lists the patients they're responsible for."],
    ["Patients and guardians", "Both are people in MindChart, with a record type of Patient or Legal Guardian. Guardians have IDs starting LG-, patients PT-."],
  ] },
  "list:cottages": { title: "Cottages", intro: "The Children's Village cottages and who lives in each.", items: [
    ["Residents", "Each cottage lists the patients whose Cottage is set to it (on a patient's page: Edit, then Residential treatment details)."],
    ["+ New cottage", "Add one, or rename one from its page; patients' Cottage choices follow, and renaming moves its residents with it."],
    ["Dynamics across cottages (experimental)", "The AI compares the cottages and points out what the records say about residents of different cottages together, citing each record. Check it against the records, and tell it what helped or didn't: your feedback guides the next analyses."],
  ] },
  "record:cottages": { title: "A cottage's page", intro: "One cottage, its residents, and how they're doing together.", items: [
    ["Residents", "Current residents, with past ones underneath."],
    ["Cottage dynamics (experimental)", "The AI reads the residents' records (risk, social, trauma and substance use facts, recent notes, medications and scores) and writes a short briefing: shared themes, what's recorded between residents, and what to watch for. Every point links to the records it comes from. It's not a clinical assessment."],
    ["Feedback", "Mark an analysis Helpful or Not helpful and say what was right, wrong or missing. MindChart passes your recent comments to the AI next time."],
  ] },
  "record:guardians": { title: "A guardian's page", intro: "One legal guardian and the patients they're responsible for.", items: [
    ["Edit", "Change their name, relationship or contact details. A new name shows on their patients' pages too."],
    ["Guardian of", "The patients linked to them. To link another, open that patient, click Edit, and choose this guardian under Legal guardian."],
    ["Delete", "Removes the guardian. Their patients stay, without a guardian linked."],
  ] },
  "record:patients": { title: "A patient's page", intro: "Everything about one patient.", items: [
    ["Upload documents", "Add their records. Files dropped on this page are suggested for this patient, and you'll be warned if one names someone else."],
    ["Write visit note", "Type what happened, click through the mental status exam, and add your plan. The AI writes it up without adding anything; edit it, then save it or copy it into your records system."],
    ["Follow-ups", "Court dates, IEP meetings, consents and labs. Mark them done as you go; Home lists what's coming up for everyone."],
    ["Medications", "Current and past medications, with consent. Suggest from records drafts the list from what the documents say, for you to check."],
    ["Rating scales and vitals", "Add PHQ-A, GAD-7, SCARED, Vanderbilt or CRAFFT totals, or weight and BMI, and see them charted over time with the published score bands."],
    ["Timeline", "Their history in date order, from the documents' dated facts, medications, scores and follow-ups."],
    ["Add note", "Write a quick note. Notes are used in future drafts too."],
    ["Build history", "Drafts the history section of an evaluation from their documents. Documents already read are reused, so it's quicker each time."],
    ["Documents", "Each one shows whether it's been read yet. Reading happens in the background; the bottom left of the sidebar shows progress."],
    ["Edit and Delete", "Edit changes their details, and every change is logged. Delete removes them and all their records for good."],
  ] },
  "list:follow_ups": { title: "Follow-ups", intro: "Everything due, across all patients.", items: [
    ["Open first", "Open follow-ups come first, soonest due at the top."],
    ["Add one", "From a patient's page, or the bar at the top: “Remind me to renew Jordan's consent by 12/01”."],
    ["Done", "Open a follow-up and set its status to Done, or click Done on Home or the patient's page."],
  ] },
  "record:follow_ups": { title: "A follow-up", intro: "Something due for a patient.", items: [["Edit", "Change the date, or set the status to Done."]] },
  "list:medications": { title: "Medications", intro: "Every patient's medications in one list: current ones first.", items: [
    ["Search", "Find who is on a medication, or whose consent is still needed."],
    ["Add or suggest", "From a patient's page: add one, or let MindChart suggest them from the records."],
    ["Ask", "The bar at the top answers questions like “Who is on sertraline?”"],
  ] },
  "record:medications": { title: "A medication", intro: "One medication trial.", items: [
    ["Edit", "Change the dose, or stop it: set the status to Stopped with the date and reason. It moves to Past medications."],
    ["Consent", "Record whether consent is on file, who gave it and when. Medications still needing consent show on Home."],
    ["Found in", "For medications suggested from the records, the documents they came from."],
  ] },
  "record:measures": { title: "A score", intro: "One rating scale total or set of vitals.", items: [
    ["Bands", "PHQ-A, GAD-7, SCARED and CRAFFT totals are shown with their published score bands. They support your judgment; they don't replace it."],
    ["Only totals", "MindChart keeps the scores, not the questionnaires."],
  ] },
  "list:measures": { title: "Measures", intro: "Rating scale scores and vitals, newest first.", items: [] },
  "list:documents": { title: "Documents", intro: "Every file and note, across all patients.", items: [
    ["Search", "Find a document by name, type, date or author, or by any words in its text."],
    ["Upload documents", "Add more files. You'll check each one in Uploads."],
    ["Open a document", "See its details, the facts found in it, and its text."],
  ] },
  "record:documents": { title: "A document", intro: "One file or note, and what MindChart found in it.", items: [
    ["✦ AI details", "Filled in by the AI, each with the quote it came from. Check them against the quote; anything you edit is never overwritten."],
    ["Change its type", "Edit and pick another type. MindChart fills in the new type's details and learns from the correction."],
    ["Facts found", "Each fact keeps the exact words it came from. “Not verified” means the quote couldn't be found, so it's left out of drafts."],
  ] },
  "list:facts": { title: "Facts", intro: "Every fact found in every document, with its source.", items: [
    ["Quotes", "Each fact keeps the exact words it came from and the page they're on."],
    ["Correct a fact", "Open it and click Edit. Future drafts use your correction."],
    ["Not verified", "The AI's quote couldn't be found in the document, so the fact is kept out of drafts."],
  ] },
  "record:facts": { title: "A fact", intro: "One fact and where it came from.", items: [
    ["Edit", "Correct the wording, section or date. Future drafts use your version."],
    ["Source", "The quote and page show exactly where it came from."],
  ] },
  "list:drafts": { title: "Drafts", intro: "History drafts you've built.", items: [
    ["Open a draft", "Review, edit and export it."],
    ["New drafts", "Build them from a patient's page."],
  ] },
  "record:drafts": { title: "A draft", intro: "Review it against the source records before using it.", items: [
    ["Citations", "Every sentence names its sources, such as [D2 p.4]: document 2, page 4. The table at the top lists the documents."],
    ["Disagreements", "Red boxes show where documents disagree. Check those against the originals."],
    ["Scans", "Text read from scans or photos is noted, so you can check it carefully."],
    ["Edit, save and export", "Edit any section, Save changes, then Export to Word. Exported Word files aren't encrypted."],
  ] },
  "list:activity": { title: "Activity", intro: "A record of who did what, and when.", items: [
    ["Tamper-evident", "Entries can't be changed or deleted. Settings checks the whole log."],
    ["Export", "Settings can export the log as a spreadsheet for compliance reviews."],
  ] },
  "record:activity": { title: "An activity entry", intro: "One entry in the log: who did what, and when.", items: [] },
  settings: { title: "Settings", intro: "Your name, password and security.", items: [
    ["Your name", "Shown in the activity log next to everything you do."],
    ["Change password", "Your records are re-protected with the new one. A lost password can't be recovered."],
    ["Touch ID", "Open MindChart with your fingerprint instead of typing your password. It's off until you turn it on, and your password still works."],
    ["Security", "Shows that your records are encrypted, whether this Mac's FileVault is on, and that the activity log is intact."],
    ["Tour and checklist", "Take the tour again, or bring back the getting-started checklist."],
  ] },
};

function currentScreen() {
  const parts = (location.hash.slice(1) || "/").split("/").filter(Boolean);
  if (!parts.length) return "home";
  if (parts[0] === "list") return `list:${parts[1]}`;
  if (parts[0] === "r") return parts[1] === "patients" && parts[2]?.startsWith("LG-") ? "record:guardians" : `record:${parts[1]}`;
  return HELP[parts[0]] ? parts[0] : "home";
}

function openHelp(topic = currentScreen()) {
  const help = topic === "security" ? SECURITY_HELP : HELP[topic] || HELP.home;
  const panel = $("#help");
  panel.innerHTML = `<button class="link close" data-close aria-label="Close help">✕</button>
    <div class="kicker">${topic === "security" ? '<span class="i-lock secure-icon"></span> Your data' : "Help"}</div>
    <h2>${esc(help.title)}</h2><p class="muted">${esc(help.intro)}</p>
    ${help.items.map(([title, text]) => `<div class="help-item"><b>${esc(title)}</b><p>${esc(text)}</p></div>`).join("")}
    <div class="help-foot">
      ${topic === "security" ? '<button class="secondary small" data-topic>Help for this page</button>'
        : '<button class="link small secure-link" data-security><span class="i-lock secure-icon"></span> How your records are protected</button>'}
      <button class="secondary small" data-tour>Take the tour</button></div>`;
  panel.hidden = false;
  panel.querySelector("[data-close]").onclick = closeHelp;
  panel.querySelector("[data-tour]").onclick = () => { closeHelp(); startTour(); };
  panel.querySelector("[data-security]")?.addEventListener("click", () => openHelp("security"));
  panel.querySelector("[data-topic]")?.addEventListener("click", () => openHelp());
  panel.querySelector("[data-close]").focus();
}

function closeHelp() {
  $("#help").hidden = true;
}

$("#helpBtn").onclick = () => ($("#help").hidden ? openHelp() : closeHelp());
$("#securePill").onclick = () => openHelp("security");
window.addEventListener("hashchange", () => { if (!$("#help").hidden) openHelp(); });  // follow the page you go to
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#help").hidden && !document.getElementById("tour")) closeHelp(); });

// --- The tour --------------------------------------------------------------------------

const TOUR = [
  { title: "Welcome to MindChart", welcome: true,
    body: "Here's a quick look at what MindChart can do. It takes about two minutes. You can skip it and press ? on any page for help instead." },
  { route: "#/", target: "#nav", title: "Your records",
    body: "Patients, Documents, Facts, Drafts and Activity, organized like a CRM. Every record has its own ID, such as PT-7K3M9Q." },
  { route: "#/", target: "#command", title: "Ask or log in plain English",
    body: "Ask a question, such as “Which patients have an order of protection?”, or record something: “Add a note for Jordan: calm week”. Questions never change anything, and anything you record is shown to you to check before it's saved.",
    tip: "Try it after the tour with one of the examples on Home." },
  { route: "#/uploads", target: "#addFiles", title: "Add records",
    body: "Drop PDFs, Word files, photos or faxes anywhere in MindChart. It reads them, scans included, and suggests who each one is about and what type it is.",
    tip: "The fictional sample records are in your Documents folder." },
  { route: "#/uploads", target: "#uploadCards", title: "Check, then confirm",
    body: "Each file gets a card with its type (and how sure MindChart is), the patient it matched, and what to take from it. Nothing is filed until you confirm, and each confirmation teaches MindChart to recognize that kind of document." },
  { route: "#/list/patients", target: "#listBody", title: "Patients",
    body: "Open a patient to see their documents, facts and drafts. Build history drafts the history section of an evaluation from their documents, with a source for every sentence." },
  { route: "#/list/follow_ups", target: "#listBody", title: "Follow-ups, medications and scores",
    body: "Each patient's page keeps their follow-ups (court dates, IEP meetings, consents, labs), medications with consent, and rating scales charted over time. Home shows what's coming up for everyone." },
  { route: "#/list/facts", target: "#listBody", title: "Facts, with their sources",
    body: "Every fact keeps the exact words it came from, checked against the document. Correct a fact and future drafts use your correction." },
  { target: "#work, #model", title: "Working in the background",
    body: "The AI runs on this Mac. Confirmed documents are read in the background, and progress shows here, so you can keep working." },
  { target: "#securePill", title: "Your records stay private",
    body: "Everything is encrypted on this Mac, and only your password opens it. Click the padlock any time for a plain-language explanation." },
  { target: "#lockBtn", title: "Lock when you step away",
    body: "MindChart also locks itself after 15 minutes without use, and when you quit." },
  { target: "#helpBtn", title: "Help on every page",
    body: "Press ? to see what you can do on the page you're on. You can take this tour again from Settings." },
  { route: "#/", target: "#gettingStarted", title: "Your first steps", last: true,
    body: "This checklist ticks itself off as you try each feature. Start by adding a patient and uploading the fictional sample records." },
];
let tourStep = -1;

async function maybeStartTour() {
  try {
    const onboarding = await api("GET", "onboarding");
    if (!onboarding.tour) startTour();
  } catch {}
}

function startTour() {
  closeHelp();
  document.body.insertAdjacentHTML("beforeend", `<div id="tour" role="dialog" aria-modal="true" aria-labelledby="tourTitle">
    <div class="tour-spot"></div><div class="tour-card"></div></div>`);
  document.addEventListener("keydown", tourKeys);
  window.addEventListener("resize", placeTour);
  showStep(0);
}

async function showStep(index) {
  tourStep = index;
  const step = TOUR[index];
  const tour = document.getElementById("tour");
  if (!tour) return;
  tour.classList.add("moving");
  if (step.route && (location.hash || "#/") !== step.route) {
    const oldPage = $("#view").firstElementChild;
    location.hash = step.route;
    await until(() => !oldPage || !oldPage.isConnected);  // so the old page's copy of the target isn't picked
  }
  const target = step.target ? await waitFor(step.target) : null;
  if (tourStep !== index || !document.getElementById("tour")) return;  // moved on, or closed, while waiting
  if (target) target.scrollIntoView({ block: "nearest" });
  const card = tour.querySelector(".tour-card");
  card.innerHTML = `
    ${step.welcome ? '<div class="logo big"></div>' : `<div class="kicker">${index} of ${TOUR.length - 1}</div>`}
    <h2 id="tourTitle">${esc(step.title)}</h2><p>${esc(step.body)}</p>
    ${step.tip ? `<p class="tour-tip">${esc(step.tip)}</p>` : ""}
    ${step.welcome ? '<p class="secure-note"><span class="i-lock secure-icon"></span> Your records are encrypted and stay on this Mac.</p>' : ""}
    <div class="actions">
      ${step.welcome ? '<button data-next>Show me around</button><button class="secondary" data-skip>Skip the tour</button>'
        : `${index > 1 ? '<button class="secondary small" data-back>Back</button>' : ""}
           <button class="small" data-next>${step.last ? "Finish" : "Next"}</button>
           ${step.last ? "" : '<button class="link small" data-skip>Skip the tour</button>'}`}
    </div>`;
  card.querySelector("[data-next]").onclick = () => (step.last ? endTour("done") : showStep(index + 1));
  card.querySelector("[data-back]")?.addEventListener("click", () => showStep(index - 1));
  card.querySelector("[data-skip]")?.addEventListener("click", () => endTour("skipped"));
  tour.dataset.target = target ? "yes" : "";
  tour.targetElement = target;
  placeTour();
  tour.classList.remove("moving");
  card.querySelector("[data-next]").focus();
}

async function until(test, ms = 3000) {
  const started = Date.now();
  while (!test() && Date.now() - started < ms) await new Promise((resolve) => setTimeout(resolve, 50));
}

// Wait (briefly) for an element to appear and finish sliding in after a page change.
async function waitFor(selector, ms = 2500) {
  const started = Date.now();
  while (Date.now() - started < ms) {
    const found = [...document.querySelectorAll(selector)].find((el) => el.offsetParent !== null);
    if (found) {
      await new Promise((resolve) => setTimeout(resolve, 380));
      return found;
    }
    await new Promise((resolve) => setTimeout(resolve, 80));
  }
  return null;
}

// Light up the step's target and put the card beside it (or in the middle when there's nothing to point at).
function placeTour() {
  const tour = document.getElementById("tour");
  if (!tour) return;
  const spot = tour.querySelector(".tour-spot"), card = tour.querySelector(".tour-card");
  const target = tour.targetElement;
  const vw = window.innerWidth, vh = window.innerHeight, pad = 8, gap = 16;
  const w = card.offsetWidth, h = card.offsetHeight;
  if (!target) {
    spot.style.cssText = `left:${vw / 2}px;top:${vh / 2}px;width:0;height:0`;
    card.style.left = `${Math.max(16, (vw - w) / 2)}px`;
    card.style.top = `${Math.max(16, (vh - h) / 2)}px`;
    return;
  }
  const r = target.getBoundingClientRect();
  const box = { left: r.left - pad, top: Math.max(8, r.top - pad), right: r.right + pad, bottom: Math.min(vh - 8, r.bottom + pad) };
  spot.style.cssText = `left:${box.left}px;top:${box.top}px;width:${box.right - box.left}px;height:${box.bottom - box.top}px`;
  const clamp = (value, low, high) => Math.min(Math.max(value, low), high);
  let left, top;
  if (box.right + gap + w < vw && r.left < vw / 3) {  // things in the sidebar: to their right
    left = box.right + gap;
    top = clamp(box.top, 16, vh - h - 16);
  } else if (box.bottom + gap + h < vh) {  // below
    left = clamp(box.left, 16, vw - w - 16);
    top = box.bottom + gap;
  } else if (box.top - gap - h > 0) {  // above
    left = clamp(box.left, 16, vw - w - 16);
    top = box.top - gap - h;
  } else {  // a large area: inside it, at the top right
    left = clamp(box.right - w - gap, 16, vw - w - 16);
    top = clamp(box.top + gap, 16, vh - h - 16);
  }
  card.style.left = `${left}px`;
  card.style.top = `${top}px`;
}

function tourKeys(e) {
  if (!document.getElementById("tour")) return;
  if (e.key === "Escape") endTour("skipped");
  else if (e.key === "ArrowRight" && tourStep < TOUR.length - 1) showStep(tourStep + 1);
  else if (e.key === "ArrowLeft" && tourStep > 1) showStep(tourStep - 1);
}

function endTour(how) {
  document.getElementById("tour")?.remove();
  document.removeEventListener("keydown", tourKeys);
  window.removeEventListener("resize", placeTour);
  tourStep = -1;
  api("POST", "settings/tour", { state: how }).catch(() => {});
  if (how === "skipped") toast("Press ? on any page for help. The tour is in Settings whenever you want it.", 4500);
  else toast("You're all set. Press ? on any page for help.", 3500);
}

// --- Getting started (on Home) -----------------------------------------------------------

const CHECKLIST = [
  ["patient", "Add a patient", "Use + New patient below, or type “New patient: …” in the bar at the top.", () => $("#newPatient")?.click()],
  ["upload", "Upload a record", "Drop a file onto MindChart. The fictional samples are in your Documents folder.", () => pickFiles()],
  ["question", "Ask a question", "Try “Which patients have an order of protection?” in the bar at the top.",
    () => { $("#commandInput").value = "Which patients have an order of protection?"; $("#commandInput").focus(); }],
  ["note", "Write a visit note", "On a patient's page, click Write visit note (or Add note for a quick one).", null],
  ["draft", "Build a history draft", "On a patient's page with documents, click Build history.", null],
  ["edit", "Correct something", "Open any record and click Edit. Every change is logged.", null],
  ["export", "Export a draft to Word", "Open a draft and click Export to Word.", null],
  ["lock", "Lock MindChart", "Click Lock at the bottom left, then sign back in.", null],
];

async function renderChecklist() {
  const box = document.getElementById("gettingStarted");
  if (!box) return;
  let onboarding;
  try { onboarding = await api("GET", "onboarding"); } catch { return; }
  const done = CHECKLIST.filter(([key]) => onboarding.steps[key]).length;
  if (onboarding.hidden || done === CHECKLIST.length) { box.innerHTML = ""; return; }
  box.innerHTML = `<div class="card checklist"><h2>Getting started <span class="count">${done} of ${CHECKLIST.length}</span>
      <span class="actions"><button class="link small" id="hideChecklist">Hide</button></span></h2>
    <ul>${CHECKLIST.map(([key, title, hint, action], i) => `<li class="${onboarding.steps[key] ? "done" : ""}">
      <span class="tick" aria-label="${onboarding.steps[key] ? "Done" : "Not yet"}"></span>
      <span><b>${esc(title)}</b><span class="muted small">${esc(hint)}</span></span>
      ${action && !onboarding.steps[key] ? `<button class="secondary small" data-do="${i}">Do it</button>` : ""}</li>`).join("")}</ul></div>`;
  box.querySelectorAll("[data-do]").forEach((b) => b.onclick = () => CHECKLIST[+b.dataset.do][3]());
  $("#hideChecklist").onclick = async () => {
    await api("POST", "settings/checklist", { state: "hidden" });
    box.innerHTML = "";
    toast("Hidden. You can bring it back from Settings.");
  };
}
