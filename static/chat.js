// MindChart's chat with its psychiatric nurse practitioner assistant (agent.py on the server): the app's home while the
// records features are on hold. Conversations are saved in the encrypted store. Uses helpers from app.js.

// Ways to start, filling in the message box (the clinician adds their own notes or question after each).
const QUICK_TASKS = [
  ["Write a psychiatric evaluation", "Write a psychiatric evaluation from my notes below and any attached files. Use the standard sections, and mark anything missing as [not documented].\n\nMy notes:\n"],
  ["Write a progress note", "Write a SOAP progress note for a medication management visit from these notes:\n"],
  ["Mental status exam", "Write up a mental status exam from these observations:\n"],
  ["Differential diagnosis", "Help me think through the differential diagnosis. What I know so far (age, presentation, history):\n"],
  ["Medication question", "Medication question. Age, weight, diagnoses and current medications:\n\nMy question:\n"],
  ["Safety plan", "Help me build a safety plan with this youth. Warning signs, triggers and supports we know of:\n"],
  ["Summarize a document", "Summarize the attached document for a psychiatric evaluation: history, diagnoses, medications, risk, placement and school, with dates.\n"],
  ["Explain it to a family", "Write a short, plain-language explanation for a foster parent about:\n"],
];

const chat = { id: null, files: [], following: null };

// --- The sidebar: conversations, and whether the AI is on ---------------------------------

async function renderChatList() {
  const box = document.getElementById("chatList");
  if (!box) return;
  let chats = [];
  try { chats = (await api("GET", "chats")).chats; } catch { return; }
  box.innerHTML = chats.length ? chats.map((c) => `<a href="#/chat/${esc(c.id)}" class="chat-link ${c.id === chat.id ? "active" : ""}">
      <span>${esc(c.title)}</span><small>${esc(when(c.updated))}</small></a>`).join("")
    : '<p class="muted small chat-none">Your conversations will show here.</p>';
}

// "Today 2:14 PM", "Yesterday", or a date, for the chat list.
function when(stamp) {
  if (!stamp) return "";
  const date = new Date(stamp), today = new Date();
  const days = Math.round((new Date(today.toDateString()) - new Date(date.toDateString())) / 86400000);
  if (days === 0) return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return days === 1 ? "Yesterday" : date.toLocaleDateString([], { month: "short", day: "numeric", year: days > 300 ? "numeric" : undefined });
}

async function showAiStatus() {
  const box = document.getElementById("aiStatus");
  if (!box || $("#app").hidden) return;
  let ai;
  try { ai = await api("GET", "ai"); } catch { return; }
  box.innerHTML = ai.status === "on"
    ? `<span class="ai-dot on"></span><span>AI on${ai.loaded.length ? ` · ${esc(ai.loaded.join(", "))} loaded` : ""}</span>
       <button class="link small" id="stopAi" title="Frees the memory the AI uses. It starts again when you send a message.">Stop AI</button>`
    : ai.status === "starting" ? '<span class="ai-dot starting"></span><span>Starting the AI…</span>'
    : '<span class="ai-dot"></span><span>AI off · starts when you send a message</span>';
  document.getElementById("stopAi")?.addEventListener("click", async () => {
    if (chat.following && !confirm("The AI is still answering. Stop it anyway?")) return;
    await api("POST", "ai/stop").catch(() => {});
    toast("AI stopped: its memory is free again");
    showAiStatus();
  });
}
setInterval(showAiStatus, 8000);

// --- A conversation ----------------------------------------------------------------------

async function chatView(id) {
  chat.id = id;
  chat.following = null;
  const data = id ? await api("GET", `chats/${encodeURIComponent(id)}`) : { title: "New chat", messages: [], reply: null };
  $("#view").innerHTML = `<div class="chat">
    <div class="chat-head">${id ? `<input class="chat-title" id="chatTitle" value="${esc(data.title)}" aria-label="Chat name" maxlength="80">`
      : '<h1 class="chat-title">New chat</h1>'}
      <span class="grow"></span>
      ${id ? '<button class="secondary small" id="deleteChat">Delete</button>' : ""}
      <button type="button" class="help-btn" id="chatHelp" title="Help" aria-label="Help">?</button></div>
    <div class="chat-log" id="chatLog">${data.messages.length ? data.messages.map(messageHtml).join("") : welcomeHtml()}</div>
    <div class="chat-compose">
      <div class="attach-chips" id="attachChips"></div>
      <form id="chatForm" onsubmit="return false">
        <button type="button" class="secondary attach-btn" id="attachBtn" title="Attach PDFs, Word files, photos or faxes (read on this Mac)" aria-label="Attach files"><span class="i-upload"></span></button>
        <textarea id="chatInput" rows="2" placeholder="Ask MindChart, or paste your notes… (Enter to send, Shift+Enter for a new line)"></textarea>
        <button type="submit" id="sendBtn">Send</button>
        <button type="button" class="secondary" id="stopBtn" hidden>Stop</button>
      </form>
      <p class="chat-note"><span class="i-lock secure-icon"></span> Runs on this Mac and saved encrypted. The AI can be wrong or make things up:
        check everything before you use it. You make the clinical decisions.</p>
    </div></div>`;
  chat.files = [];
  renderAttachments();
  renderChatList();
  scrollChat();
  $("#chatHelp").onclick = () => ($("#help").hidden ? openHelp("chat") : closeHelp());
  $("#attachBtn").onclick = () => {
    $("#fileInput").onchange = () => { const files = [...$("#fileInput").files]; $("#fileInput").value = ""; attachFiles(files); };
    $("#fileInput").click();
  };
  $("#chatInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendChat(); }
  });
  $("#chatForm").onsubmit = () => { sendChat(); return false; };
  $("#stopBtn").onclick = () => chat.id && api("POST", `chats/${chat.id}/stop`).catch(() => {});
  $("#chatLog").addEventListener("click", chatLogClick);
  if (id) bindChatHead(id);
  if (data.reply) followReply(id);  // still being answered (say, the page was reloaded meanwhile)
  else if (id && data.messages.at(-1)?.role === "user") showRetry("This message wasn't answered.");
  $("#chatInput").focus();
}

// A saved chat's header: its name (click to rename) and Delete.
function chatHead(id, title) {
  document.querySelector(".chat-head .chat-title").outerHTML = `<input class="chat-title" id="chatTitle" value="${esc(title)}" aria-label="Chat name" maxlength="80">`;
  if (!document.getElementById("deleteChat")) $("#chatHelp").insertAdjacentHTML("beforebegin", '<button class="secondary small" id="deleteChat">Delete</button>');
  bindChatHead(id);
}

function bindChatHead(id) {
  $("#chatTitle").onchange = async () => {
    try { await api("POST", `chats/${id}`, { title: $("#chatTitle").value }); renderChatList(); }
    catch (err) { toast(err.message, 4000); }
  };
  $("#deleteChat").onclick = async () => {
    if (!confirm("Delete this chat? It's removed from this Mac for good.")) return;
    await api("POST", `chats/${id}/delete`);
    toast("Chat deleted");
    location.hash = "#/";
  };
}

function welcomeHtml() {
  const name = (state.session?.user_name || "").split(" ")[0];
  return `<div class="chat-welcome">
    <div class="logo big"></div>
    <h1>How can I help${name ? `, ${esc(name)}` : ""}?</h1>
    <p class="muted">I'm MindChart, an assistant with the knowledge of an experienced psychiatric nurse practitioner, for children,
      adolescents and their families. I can draft evaluations and notes from your notes or files, think through diagnoses and
      medications with you, and help with safety plans and families. I work only from what you tell me or attach.</p>
    <div class="quick-tasks">${QUICK_TASKS.map(([label], i) => `<button type="button" class="quick-task" data-task="${i}">${esc(label)}</button>`).join("")}</div>
  </div>`;
}

function messageHtml(m) {
  if (m.role === "user") {
    return `<div class="msg user">${(m.files || []).length ? `<div class="msg-files">${m.files.map((f) =>
      `<span class="file-chip"><span class="i-documents"></span>${esc(f.name)}</span>`).join("")}</div>` : ""}${esc(m.content || "")}${
      (m.scored || []).map((s) => `<div class="scored ${s.warning ? "warn" : ""}">Scored by MindChart: ${esc(s.scale)} total ${s.total} (${esc(s.band)})${
        s.warning ? ` · <b>${esc(s.warning)}</b>` : ""}</div>`).join("")}</div>`;
  }
  return `<div class="msg assistant"><div class="md">${markdown(m.content)}</div>
    <div class="msg-foot"><span class="muted small">${esc(m.model || "")}${(m.tools || []).length ? ` · used the ${esc(m.tools.join(", "))}` : ""}${m.stopped ? " · stopped" : ""}</span>
      <button type="button" class="link small" data-copy>Copy</button></div></div>`;
}

function chatLogClick(e) {
  const task = e.target.closest("[data-task]");
  if (task) {
    const [, text] = QUICK_TASKS[+task.dataset.task];
    $("#chatInput").value = text;
    $("#chatInput").focus();
    $("#chatInput").setSelectionRange(text.length, text.length);
    if (/attached/.test(text) && !chat.files.length) $("#attachBtn").click();
    return;
  }
  const copy = e.target.closest("[data-copy]");
  if (copy) {
    const text = copy.closest(".msg").querySelector(".md").innerText;
    navigator.clipboard.writeText(text).then(() => toast("Copied ✓"), () => toast("Couldn't copy; select the text instead"));
  }
  if (e.target.closest("[data-retry]")) retryChat();
}

function scrollChat() {
  const log = document.getElementById("chatLog");
  if (log) log.scrollTop = log.scrollHeight;
}

// --- Attached files: read on this Mac (scans and photos with OCR), and sent as text with the next message ---------

async function attachFiles(files) {
  for (const file of files) {
    const chip = { name: file.name, reading: true };
    chat.files.push(chip);
    renderAttachments();
    try {
      const res = await fetch(`/api/extract?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      Object.assign(chip, data, { reading: false });
    } catch (err) {
      chat.files.splice(chat.files.indexOf(chip), 1);
      toast(`Couldn't read ${file.name}: ${err.message}`, 5000);
    }
    renderAttachments();
  }
}

function renderAttachments() {
  const box = document.getElementById("attachChips");
  if (!box) return;
  const unclear = chat.files.filter((f) => (f.unclear || []).length);
  box.innerHTML = chat.files.map((f, i) => `<span class="file-chip">${f.reading ? '<span class="spinner"></span>' : '<span class="i-documents"></span>'}
      ${esc(f.name)}${f.reading ? " · reading…" : ""}<button type="button" class="link" data-remove="${i}" aria-label="Remove ${esc(f.name)}">✕</button></span>`).join("")
    + (unclear.length ? `<p class="warn small">Parts of ${unclear.map((f) => esc(f.name)).join(", ")} were hard to read (probably handwriting),
        so some words may be misread. Typed or dictated notes work far better.</p>` : "");
  box.querySelectorAll("[data-remove]").forEach((b) => b.onclick = () => { chat.files.splice(+b.dataset.remove, 1); renderAttachments(); });
}

// --- Sending, and following the answer as it's written ------------------------------------

async function sendChat() {
  const text = $("#chatInput").value.trim();
  if (chat.following || chat.files.some((f) => f.reading)) return;
  if (!text && !chat.files.length) return;
  const files = chat.files.map(({ name, text: content, unclear }) => ({ name, text: content, unclear }));
  $("#sendBtn").disabled = true;
  try {
    if (!chat.id) {
      chat.id = (await api("POST", "chats")).id;
      history.replaceState(null, "", `#/chat/${chat.id}`);  // (without opening the page again)
    }
    await api("POST", `chats/${chat.id}/messages`, { text, files, model: modelChoice() });
  } catch (err) {
    toast(err.message, 5000);
    $("#sendBtn").disabled = false;
    return;
  }
  $("#chatLog").querySelector(".chat-welcome")?.remove();
  const saved = await api("GET", `chats/${chat.id}`).catch(() => null);
  const sent = saved?.messages.filter((m) => m.role === "user").at(-1) || { role: "user", content: text, files };
  $("#chatLog").insertAdjacentHTML("beforeend", messageHtml(sent));
  if (saved && !document.getElementById("chatTitle")) chatHead(chat.id, saved.title);
  $("#chatInput").value = "";
  chat.files = [];
  renderAttachments();
  renderChatList();
  followReply(chat.id);
}

async function retryChat() {
  document.querySelector(".msg.failed")?.remove();
  try { await api("POST", `chats/${chat.id}/retry`, { model: modelChoice() }); } catch (err) { return toast(err.message, 5000); }
  followReply(chat.id);
}

// Check on the answer every moment and show it as it grows. Writing carries on on the server whatever happens here, so a
// failed check (the Mac napping, say) is just tried again, and leaving the chat stops only the checking.
async function followReply(id) {
  const token = chat.following = {};
  $("#chatLog").insertAdjacentHTML("beforeend", `<div class="msg assistant pending"><div class="md"></div>
    <div class="msg-foot"><span class="muted small reply-status"><span class="spinner"></span> Thinking…</span></div></div>`);
  const bubble = $("#chatLog").lastElementChild;
  $("#sendBtn").hidden = true;
  $("#stopBtn").hidden = false;
  aiBusy(true);
  let job = null, lostSince = null, shown = "";
  try {
    while (chat.following === token && document.body.contains(bubble)) {
      try {
        job = await api("GET", `chats/${id}/reply`);
        lostSince = null;
      } catch (err) {
        if (!(err instanceof TypeError)) throw err;
        lostSince = lostSince || Date.now();
        if (Date.now() - lostSince > 10 * 60000) throw new Error("Lost touch with MindChart. Open this chat again to see the answer.");
        await new Promise((r) => setTimeout(r, 2000));
        continue;
      }
      if (job.text !== shown) {
        shown = job.text;
        bubble.querySelector(".md").innerHTML = markdown(shown);
        scrollChat();
      }
      bubble.querySelector(".reply-status").innerHTML = job.status ? `<span class="spinner"></span> ${esc(job.status)}`
        : shown ? '<span class="spinner"></span> Writing…' : '<span class="spinner"></span> Thinking…';
      if (job.done) break;
      await new Promise((r) => setTimeout(r, 350));
    }
    if (chat.following !== token || !document.body.contains(bubble)) return;
    if (job.error) throw new Error(job.error);
    bubble.outerHTML = messageHtml({ role: "assistant", content: job.text, model: job.model, tools: job.tools, stopped: job.stopped });
  } catch (err) {
    if (document.body.contains(bubble)) bubble.remove();
    showRetry(err.message);
  } finally {
    if (chat.following === token) {
      chat.following = null;
      if (document.getElementById("sendBtn")) { $("#sendBtn").hidden = false; $("#sendBtn").disabled = false; $("#stopBtn").hidden = true; }
      aiBusy(false);
      renderChatList();
      showAiStatus();
      scrollChat();
    }
  }
}

function showRetry(message) {
  $("#chatLog").insertAdjacentHTML("beforeend", `<div class="msg assistant failed"><p class="error">${esc(message)}</p>
    <button type="button" class="small" data-retry>Try again</button></div>`);
  scrollChat();
}

// --- A small, safe Markdown renderer for the AI's answers (headings, lists, bold, tables) ---------------------------

function markdown(text) {
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<i>$2</i>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  const out = [];
  let list = null, table = null, paragraph = [];
  const flush = () => {
    if (paragraph.length) { out.push(`<p>${paragraph.map(inline).join("<br>")}</p>`); paragraph = []; }
    if (list) { out.push(`<${list.type}>${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.type}>`); list = null; }
    if (table) {
      const [head, ...rows] = table;
      out.push(`<div class="table-wrap"><table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${
        rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      table = null;
    }
  };
  for (const raw of String(text || "").split("\n")) {
    const line = raw.trimEnd();
    const heading = line.match(/^(#{1,4})\s+(.*)$/), bullet = line.match(/^\s*[-*•]\s+(.*)$/), numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (/^\s*\|.*\|\s*$/.test(line)) {
      if (/^\s*\|[\s:|-]+\|\s*$/.test(line)) continue;  // the row under the header
      if (!table) flush();
      (table = table || []).push(line.trim().slice(1, -1).split("|").map((c) => c.trim()));
    } else if (heading) { flush(); out.push(`<h${Math.min(heading[1].length + 2, 5)}>${inline(heading[2])}</h${Math.min(heading[1].length + 2, 5)}>`); }
    else if (bullet || numbered) {
      const type = bullet ? "ul" : "ol";
      if (paragraph.length || table || (list && list.type !== type)) flush();
      (list = list || { type, items: [] }).items.push((bullet || numbered)[1]);
    } else if (!line.trim()) flush();
    else if (/^\s*(---|\*\*\*)\s*$/.test(line)) { flush(); out.push("<hr>"); }
    else { if (list || table) flush(); paragraph.push(line); }
  }
  flush();
  return out.join("");
}
