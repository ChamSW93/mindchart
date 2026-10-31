"""MindChart: a private, offline app for keeping patient records and drafting psychiatric evaluation histories.

Everything runs on this computer: the web page, this server, and the AI model (through
Ollama). The server only listens to this computer (127.0.0.1). All data is kept in the
encrypted store described in vault.py, and nothing can be read until MindChart is
unlocked with its password.

Run:  python3 server.py
Open: http://localhost:8000
"""
import http.server
import json
import logging
import logging.handlers
import mimetypes
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import assistant
import documents
import history
import intake
import models
import updater
import vault
import worker

APP_NAME = "MindChart"  # the name shown in the app; change it here to rename the app
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODELS_URL = "http://localhost:11434/api/tags"
MODEL = "gpt-oss:20b"  # used until another model is picked in the app
PORT = int(os.environ.get("MINDCHART_PORT", 8000))  # the Mac app picks a free port and passes it here
MAX_UPLOAD_BYTES = 200 * 2**20  # 200 MB; larger uploads are refused rather than filling memory
HERE = Path(__file__).parent
CONTEXT_LENGTH = 16384  # how much text the chat model can see at once (in tokens)
PAGES = {"/": "app.html", "/chat.html": "chat.html"}
# Errors go to a log file the tester can send when something goes wrong. It records what failed and
# where in the code, not record contents (database queries pass values separately, so they don't appear).
LOG_FILE = Path.home() / "Library" / "Logs" / "MindChart" / "mindchart.log"
log = logging.getLogger("mindchart")


def setup_logging():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
HARDWARE = models.hardware()
DOWNLOAD = {}  # progress of downloading the recommended model, if it was missing


def ollama_running():
    try:
        urllib.request.urlopen(f"{models.OLLAMA}/api/version", timeout=3).close()
        return True
    except Exception:
        return False


def download_recommended_model():
    """If this Mac doesn't have its recommended model yet (say, after an update changed it), fetch it."""
    name = models.recommended(HARDWARE["tier"])
    if not ollama_running() or name in models.installed():
        return
    DOWNLOAD.update({"model": name, "percent": 0, "status": "downloading"})
    log.info("Downloading recommended model %s", name)
    try:
        models.pull(name, lambda percent: DOWNLOAD.update({"percent": percent}))
        DOWNLOAD["status"] = "done"
    except Exception as error:
        log.warning("Couldn't download %s: %s", name, error)
        DOWNLOAD.update({"status": "error", "error": str(error)})


def filevault_on():
    """Whether the Mac's own disk encryption is on. HIPAA expects devices holding patient data to be encrypted."""
    return "FileVault is On" in subprocess.run(["fdesetup", "status"], capture_output=True, text=True).stdout
UPDATE = {"current": updater.current_version(), "available": False}


def check_for_updates_forever():
    """Every few hours, note whether a newer version has been published (installed on the next start)."""
    while True:
        try:
            UPDATE.update(updater.status())
        except Exception:
            pass
        time.sleep(6 * 3600)


def list_models():
    """The models downloaded with `ollama pull`, smallest first."""
    with urllib.request.urlopen(OLLAMA_MODELS_URL) as response:
        models = json.load(response)["models"]
    return [
        {"name": m["name"], "size_gb": round(m["size"] / 1e9, 1), "thinking": "thinking" in m.get("capabilities", []),
         # Models bigger than the GPU's share of memory spill onto the slower processor.
         "fits": m["size"] / 1e9 < HARDWARE["gpu_gb"] - 1.5}
        for m in sorted(models, key=lambda m: m["size"])
        if "embedding" not in m.get("capabilities", [])  # embedding models can't chat
    ]


def default_model(available):
    names = [m["name"] for m in available]
    return next((n for n in models.RECOMMENDED_MODELS[HARDWARE["tier"]] if n in names), names[0] if names else MODEL)


def model_choice(payload):
    """The model the page asked for, and whether it can think (reason before answering)."""
    name = payload.get("model") or default_model(list_models())
    return name, can_think(name)


def background_model():
    """The model picked in the app (remembered in settings), for work done in the background."""
    try:
        name = vault.get_setting("model") or default_model(list_models())
    except urllib.error.URLError:
        raise history.AIUnavailable()
    return name, can_think(name)


def can_think(name):
    try:
        return next((m["thinking"] for m in list_models() if m["name"] == name), False)
    except Exception:
        return False


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # keep the Terminal window free of request details

    # --- Replies -----------------------------------------------------------------

    def send_body(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, payload):
        self.send_body(status, json.dumps(payload).encode(), "application/json")

    def start_stream(self):
        """Begin a reply that's sent a line at a time, so the page can show progress."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def stream_line(self, payload):
        self.wfile.write((json.dumps(payload) + "\n").encode())
        self.wfile.flush()

    # --- Requests ----------------------------------------------------------------

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path in PAGES:
            body = (HERE / PAGES[url.path]).read_text().replace("{{APP_NAME}}", APP_NAME).encode()
            return self.send_body(200, body, "text/html; charset=utf-8")
        if url.path.startswith("/static/") and "/.." not in url.path:
            path = HERE / url.path.lstrip("/")
            if path.is_file():
                return self.send_body(200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "text/plain")
        if url.path.startswith("/api/"):
            return self.api("GET", url, None)
        self.send_error(404)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_UPLOAD_BYTES:
            return self.send_json(413, {"error": f"That file is too large (over {MAX_UPLOAD_BYTES // 2**20} MB)"})
        body = self.rfile.read(length)
        if url.path.startswith("/api/"):
            return self.api("POST", url, body)
        self.send_error(404)

    def from_mindchart(self):
        """True if a request really comes from MindChart's own page.

        The server only listens to this Mac, but web pages open in a browser on this Mac could
        still try to send it requests. Three checks stop that:
        - Host must be this server, which defeats "DNS rebinding" (a website pretending to be localhost).
        - Origin, when the browser sends one, must be this server, so other sites' pages are refused.
        - A custom header must be present; other sites can't add one without permission we never give.
        """
        allowed = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
        origin = self.headers.get("Origin")
        return (self.headers.get("Host") in allowed
                and (origin is None or origin.split("://", 1)[-1] in allowed)
                and self.headers.get("X-MindChart") == "1")

    def api(self, method, url, body):
        if not self.from_mindchart():
            return self.send_json(403, {"error": "Requests are only accepted from MindChart itself"})
        parts = url.path.strip("/").split("/")[1:]  # /api/records/patients/PT-123 -> ["records", "patients", "PT-123"]
        query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        if parts[:1] not in (["session"], ["work"]):  # the page checks these by itself, so they aren't activity
            vault.touch()  # any use of the app postpones the automatic lock
        try:
            is_upload = parts in (["uploads"], ["extract"])  # these bodies are files, not JSON
            data = json.loads(body) if body and not is_upload else {}
            result = self.route(method, parts, query, data, body)
            if result is not None:
                self.send_json(200, result)
        except PermissionError as error:
            self.send_json(423, {"error": str(error), "locked": True})
        except (ValueError, KeyError) as error:
            self.send_json(400, {"error": str(error)})
        except Exception as error:
            log.exception("Request failed: %s %s", method, "/".join(parts[:2]))  # no patient details in the log
            self.send_json(500, {"error": f"Something went wrong: {error}"})

    def route(self, method, parts, query, data, body):
        """Handle one API call. Returns JSON to send, or None if the reply was already sent."""
        args = []

        def is_(expected_method, *pattern):
            """True if this request is expected_method on this path. "*" parts are collected into args."""
            if method != expected_method or len(parts) != len(pattern):
                return False
            if any(want != "*" and want != part for want, part in zip(pattern, parts)):
                return False
            args[:] = [part for want, part in zip(pattern, parts) if want == "*"]
            return True

        # Signing in. The password is passed straight to macOS and never kept.
        if is_("GET", "session"):
            return {**vault.status(), "app_name": APP_NAME}
        if is_("POST", "session", "setup"):
            vault.create(data["password"], data.get("name", ""))
            return vault.status()
        if is_("POST", "session", "unlock"):
            vault.unlock(data["password"])
            return vault.status()
        if is_("POST", "session", "lock"):
            vault.lock()
            return vault.status()
        if is_("GET", "models"):
            available = list_models()
            return {"models": available, "default": default_model(available)}
        if is_("GET", "system"):
            return {**HARDWARE, "version": UPDATE, "download": DOWNLOAD}

        if not vault.is_unlocked():
            raise PermissionError("MindChart is locked")

        if is_("GET", "settings"):
            return {"user_name": vault.get_setting("user_name"), "filevault": filevault_on(), "data_location": str(vault.IMAGE),
                    "log_file": str(LOG_FILE), "version": UPDATE, "auto_lock_minutes": vault.AUTO_LOCK_MINUTES,
                    "audit": vault.verify_audit()}
        if is_("POST", "settings", "name"):
            if not data.get("name", "").strip():
                raise ValueError("Enter your name")
            vault.set_setting("user_name", data["name"].strip())
            vault.log("changed the user name")
            return {}
        if is_("POST", "settings", "tour"):  # "done" or "skipped"; empty shows the tour again
            vault.set_setting("tour", str(data.get("state", "")))
            return {}
        if is_("POST", "settings", "checklist"):  # "hidden" or empty
            vault.set_setting("checklist", str(data.get("state", "")))
            return {}
        if is_("GET", "onboarding"):
            return {**vault.onboarding(), "tour": vault.get_setting("tour")}
        if is_("POST", "settings", "model"):  # so background work uses the model picked in the app
            vault.set_setting("model", str(data.get("model", "")))
            return {}
        if is_("POST", "settings", "password"):
            vault.change_password(data.get("current", ""), data.get("new", ""))
            return {}
        if is_("GET", "audit.csv"):
            vault.log("exported the activity log")
            self.send_body(200, vault.export_audit().encode(), "text/csv; charset=utf-8")
            return None
        if is_("GET", "objects"):
            return {"objects": vault.describe_objects(), "section_titles": history.SECTION_TITLES}
        if is_("GET", "records", "*"):
            return vault.list_records(args[0], query.get("search", ""), query.get("patient"))
        if is_("GET", "records", "*", "*"):
            return vault.get_record(*args)
        if is_("POST", "records", "*"):  # medications, measures and follow-ups name their patient in "patient_id"
            return {"id": vault.create_record(args[0], data, data.get("patient_id"))}
        if is_("POST", "records", "*", "*"):
            vault.update_record(args[0], args[1], data)
            return vault.get_record(*args)
        if is_("POST", "records", "*", "*", "delete"):
            vault.delete_record(*args)
            return {}
        if is_("POST", "record-types"):
            return {"name": vault.add_record_type(data.get("object", ""), data.get("name", ""), data.get("description", ""))}
        if is_("POST", "uploads"):  # a file to review; it's read now (with OCR if needed) and identified in the background
            name, target = query.get("name", "document"), query.get("patient") or None
            pages = documents.read_pages(name, body)
            suggestion, _ = intake.suggest(name, pages, target)
            return {"id": vault.add_upload(name, body, pages, target, suggestion)}
        if is_("GET", "uploads"):
            return {"uploads": [intake.refresh(u) for u in vault.list_uploads()], "pull_preferences": vault.pull_preferences(),
                    "patients": vault.patients_for_matching()}
        if is_("POST", "uploads", "*", "confirm"):
            document_id, patient_id = vault.confirm_upload(args[0], data)
            return {"document_id": document_id, "patient_id": patient_id}
        if is_("POST", "uploads", "*", "discard"):
            vault.discard_upload(args[0])
            return {}
        if is_("GET", "work"):
            return worker.status()
        if is_("POST", "patients", "*", "notes"):
            return {"id": vault.add_note(args[0], data.get("text", ""), data.get("date", ""), data.get("author", ""),
                                         data.get("title", ""))}
        if is_("GET", "patients", "*", "timeline"):
            return {"events": vault.timeline(args[0])}
        if is_("POST", "patients", "*", "medications", "suggest"):  # a medication list drafted from the records
            model, thinking = model_choice(data)
            have = {m["name"].lower() for m in vault.list_records("medications", patient_uid=args[0])["rows"]}
            suggestions = history.suggest_medications(model, thinking, vault.medication_facts(args[0]))
            return {"suggestions": [m for m in suggestions if m["name"].lower() not in have]}
        if is_("POST", "patients", "*", "visit-note"):  # a visit note drafted from the clinician's own notes
            model, thinking = model_choice(data)
            vault.internal("patients", args[0])  # the patient must exist
            return history.draft_visit_note(model, thinking, data.get("inputs", ""))
        if is_("GET", "coming-up"):
            return {"items": vault.coming_up()}
        if is_("POST", "patients", "*", "history"):
            return self.build_history(args[0], data)
        if is_("POST", "drafts", "*"):
            vault.save_draft_edits(args[0], data["result"])
            return {}
        if is_("POST", "export"):
            docx = history.build_docx(data["sections"], data["documents"])
            if data.get("draft_id"):  # leaving the encrypted store is recorded, since the file isn't encrypted
                vault.log("exported a draft to Word", vault.internal_patient("drafts", data["draft_id"]), data["draft_id"])
            self.send_body(200, docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            return None
        if is_("POST", "assistant"):
            model, thinking = model_choice(data)
            return assistant.handle(data["message"], model, thinking, APP_NAME)
        if is_("POST", "assistant", "confirm"):
            return assistant.confirm(data["proposal"])
        if is_("POST", "extract"):  # a chat attachment, as one block of text
            return {"name": query.get("name", "file"), "text": documents.extract_text(query.get("name", "file"), body)}
        if is_("POST", "chat"):
            return self.chat(data)
        raise KeyError("Unknown request")

    def build_history(self, patient_uid, data):
        """Draft a history from a patient's documents, saving what's read and the draft itself."""
        model, thinking = model_choice(data)
        docs = vault.documents_for_history(patient_uid, data.get("doc_ids") or [], data.get("reread"))
        if not docs:
            raise ValueError("Choose at least one document")
        vault.log("built a history", vault.internal("patients", patient_uid), model)
        self.start_stream()
        try:
            if worker.STATUS["current"]:
                self.stream_line({"progress": "Waiting for background reading to finish its current step", "step": 0, "total": 1})
            with worker.paused():
                self.stream_history(patient_uid, docs, model, thinking, data.get("reread"))
        except Exception as error:
            self.stream_line({"error": str(error)})

    def stream_history(self, patient_uid, docs, model, thinking, reread):
        if not reread:  # documents read in the background while this waited are picked up, not read twice
            read_now = {d["uid"]: d for d in vault.documents_for_history(patient_uid, [d["uid"] for d in docs], False)}
            docs = [read_now[d["uid"]] if "facts" in read_now.get(d["uid"], {}) and "facts" not in d else d for d in docs]
        for update in history.build(docs, model, thinking, vault.document_field_definitions(), intake.document_types()):
            vault.touch()  # a long build counts as activity, so MindChart stays unlocked
            if "document_read" in update:
                read = update["document_read"]
                vault.save_document_facts(docs[read["index"]]["id"], read["info"], read["facts"], model, read.get("filled"))
                continue
            if "result" in update:
                update["draft_id"] = vault.save_draft(patient_uid, model, update["result"])
            self.stream_line(update)

    def chat(self, payload):
        """Forward a chat to Ollama and stream the reply back, one JSON line per chunk."""
        model, _ = model_choice(payload)
        options = {"model": model, "messages": payload["messages"], "stream": True, "options": {"num_ctx": CONTEXT_LENGTH}}
        if payload.get("think"):
            options["think"] = True  # the model's reasoning arrives separately from its answer
        request = urllib.request.Request(OLLAMA_URL, data=json.dumps(options).encode(),
                                         headers={"Content-Type": "application/json"})
        self.start_stream()
        try:
            with urllib.request.urlopen(request) as response:
                for line in response:
                    message = json.loads(line).get("message", {})
                    for key in ("thinking", "content"):
                        if message.get(key):
                            self.stream_line({"thinking": message[key]} if key == "thinking" else {"text": message[key]})
        except Exception as error:
            self.stream_line({"error": str(error)})


def stop_when_app_quits():
    """When started by the Mac app, shut down (locking the data) if the app goes away, even if it crashed."""
    parent = os.getppid()
    while True:
        time.sleep(2)
        if os.getppid() != parent:  # the app is gone, so this server now belongs to nobody
            log.info("The MindChart app went away; stopping and locking")
            os.kill(os.getpid(), signal.SIGTERM)


if __name__ == "__main__":
    setup_logging()
    log.info("Starting %s %s on %s (%s GB, %s setup), Python %s", APP_NAME, UPDATE["current"], HARDWARE["chip"],
             HARDWARE["memory_gb"], HARDWARE["tier"], sys.version.split()[0])
    print(f"{APP_NAME} running at http://localhost:{PORT}")
    print(f"This Mac: {HARDWARE['chip']}, {HARDWARE['memory_gb']} GB memory ({HARDWARE['tier']} setup)")
    print("Everything stays on this computer. Close this window to stop the app (this also locks your data).")
    threading.Thread(target=vault.auto_lock_forever, daemon=True).start()
    threading.Thread(target=check_for_updates_forever, daemon=True).start()
    threading.Thread(target=download_recommended_model, daemon=True).start()
    threading.Thread(target=worker.run_forever, args=(background_model,), daemon=True).start()
    if os.environ.get("MINDCHART_APP"):
        threading.Thread(target=stop_when_app_quits, daemon=True).start()
    # Closing the Terminal window sends SIGHUP; exiting cleanly lets the data lock on the way out.
    signal.signal(signal.SIGHUP, lambda *_: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    finally:
        vault.lock("locked because the app was closed")
