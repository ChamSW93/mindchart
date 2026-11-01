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
import fields as fieldtypes
import documents
import history
import intake
import models
import touchid
import updater
import vault
import worker
import zipcodes

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
UPDATE = {"current": updater.current_version(), "available": False, "last_check": updater.last_check()}


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

        # Signing in. The password is passed straight to macOS and never kept (Touch ID keeps it sealed: touchid.py).
        if is_("GET", "session"):
            return {**vault.status(), "app_name": APP_NAME, "touch_id": touchid.is_on()}
        if is_("POST", "session", "setup"):
            vault.create(data["password"], data.get("name", ""))
            return vault.status()
        if is_("POST", "session", "unlock"):
            vault.unlock(data["password"])
            return vault.status()
        if is_("POST", "session", "touch-id"):  # Touch ID opens the sealed password, which then goes to macOS like a typed one
            try:
                vault.unlock(touchid.get_password(), method="Touch ID")
            except touchid.Cancelled:
                raise
            except ValueError as error:
                if str(error) == "Wrong password":  # the password was changed some other way
                    touchid.turn_off()
                    raise ValueError("Touch ID was turned off because the password changed. Sign in with your password.")
                raise
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
            if touchid.is_on():
                touchid.turn_on(data["new"])  # so Touch ID opens the new password
            return {}
        if is_("GET", "settings", "touch-id"):
            return {"available": touchid.available(), "on": touchid.is_on()}
        if is_("POST", "settings", "touch-id"):
            if not data.get("on"):
                touchid.turn_off()
                vault.log("turned off Touch ID")
                return {"on": False}
            vault.check_password(data.get("password", ""))
            touchid.turn_on(data["password"])
            try:  # one fingerprint now, to be sure it works before relying on it
                if touchid.get_password() != data["password"]:
                    raise ValueError("Touch ID didn't give back the right password, so it wasn't turned on")
            except ValueError:
                touchid.turn_off()
                raise
            vault.log("turned on Touch ID")
            return {"on": True}
        if is_("GET", "audit.csv"):
            vault.log("exported the activity log")
            self.send_body(200, vault.export_audit().encode(), "text/csv; charset=utf-8")
            return None
        if is_("GET", "objects"):
            return {"objects": vault.describe_objects(), "section_titles": history.SECTION_TITLES}
        if is_("GET", "records", "*"):
            return vault.list_records(args[0], query.get("search", ""), query.get("patient"), record_type=query.get("type"))
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
        if is_("POST", "uploads", "combine"):  # separate files that are pages of one document, in this order
            uids = [str(u) for u in data.get("ids") or []]
            files = [vault.get_upload(uid) for uid in uids]  # (each must still be waiting)
            name = f"{Path(files[0]['name']).stem} + {len(files) - 1} more.pdf" if files else ""
            pages, target = vault.combine_pages(files), next((f["patient_uid"] for f in files if f["patient_uid"]), None)
            suggestion, _ = intake.suggest(name, pages, target)
            original = documents.combine([(f["name"], f["original"]) for f in files])
            return {"id": vault.combine_uploads(uids, name, original, pages, target, suggestion)}
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
            have = {history.medication_key(m["name"]) for m in vault.list_records("medications", patient_uid=args[0])["rows"]}
            suggestions = history.suggest_medications(model, thinking, vault.medication_facts(args[0]))
            return {"suggestions": [m for m in suggestions if history.medication_key(m["name"]) not in have]}
        if is_("POST", "patients", "*", "visit-note"):  # a visit note drafted from the clinician's own notes
            model, thinking = model_choice(data)
            vault.internal("patients", args[0])  # the patient must exist
            return history.draft_visit_note(model, thinking, data.get("inputs", ""))
        if is_("POST", "cottages", "*", "analyze"):  # the AI's (experimental) reading of how a cottage's residents get along
            model, thinking = model_choice(data)
            with worker.paused():  # the model is busy with this
                result = history.analyze_cottage(model, thinking, vault.cottage_inputs(args[0]), vault.analysis_feedback())
            number = vault.save_cottage_analysis(args[0], result, model)
            return {**result, "id": number, "created": vault.now(), "model": model, "feedback": None}
        if is_("POST", "cottages", "analyze"):  # the same across all the cottages
            model, thinking = model_choice(data)
            with worker.paused():
                result = history.analyze_cottages(model, thinking, vault.intercottage_inputs(), vault.analysis_feedback())
            number = vault.save_cottage_analysis(None, result, model)
            return {**result, "id": number, "created": vault.now(), "model": model, "feedback": None}
        if is_("GET", "cottages", "analysis"):
            return {"analysis": vault.intercottage_analysis()}
        if is_("POST", "cottage-analyses", "*", "feedback"):
            vault.save_analysis_feedback(int(args[0]), data.get("rating"), data.get("comment", ""))
            return {}
        if is_("POST", "update", "check"):  # from Settings: look for a newer version now
            UPDATE.update(updater.status())
            return UPDATE
        if is_("POST", "update", "install"):  # from Settings: install it now; it takes over when MindChart reopens
            if (updater.HERE / ".git").exists():
                raise ValueError("This is the development copy of MindChart, which isn't updated from releases")
            latest = updater.latest_release()
            if not latest:
                raise ValueError(f"Couldn't reach GitHub: {updater.last_error}")
            if updater.as_tuple(latest["version"]) <= updater.as_tuple(UPDATE["current"]):
                raise ValueError("MindChart is already up to date")
            before = UPDATE["current"]
            try:
                updater.install(latest)
            except Exception as error:
                updater.record(f"The update to {latest['version']} didn't install ({error})")
                raise ValueError(f"The update didn't install: {error}")
            updater.record(f"Updated from {before} to {latest['version']} (from Settings)")
            UPDATE.update(installed=latest["version"], available=False, last_check=updater.last_check())
            return UPDATE
        if is_("GET", "medication-names"):  # offered when adding a medication
            return {"medications": vault.medication_names()}
        if is_("GET", "zip"):  # ZIP codes and towns, worked out on this Mac
            return {"results": zipcodes.search(query.get("q", "")), "home": zipcodes.HOME_NAME}
        if is_("GET", "coming-up"):
            return {"items": vault.coming_up()}
        if is_("POST", "patients", "*", "history"):
            return self.build_history(args[0], data)
        if is_("POST", "drafts", "*"):
            vault.save_draft_edits(args[0], data["result"])
            return {}
        if is_("POST", "export"):
            docx = history.build_docx(data["sections"], data["documents"], data.get("kind") or "history", data.get("header"))
            if data.get("draft_id"):  # leaving the encrypted store is recorded, since the file isn't encrypted
                vault.log("exported a draft to Word", vault.internal_patient("drafts", data["draft_id"]), data["draft_id"])
            self.send_body(200, docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            return None
        if is_("POST", "assistant"):
            model, thinking = model_choice(data)
            return assistant.handle(data["message"], model, thinking, APP_NAME)
        if is_("POST", "assistant", "confirm"):
            return assistant.confirm(data["proposal"])
        if is_("POST", "extract"):  # a chat attachment, as one block of text, noting pages the OCR couldn't read well
            pages = documents.read_pages(query.get("name", "file"), body)
            return {"name": query.get("name", "file"), "text": documents.pages_text(pages),
                    "unclear": [p["page"] for p in pages if p.get("unclear")]}
        if is_("POST", "chat"):
            return self.chat(data)
        raise KeyError("Unknown request")

    def build_history(self, patient_uid, data):
        """Draft a psychiatric evaluation (or the history alone) from a patient's documents, saving what's read and the
        draft. Evaluation notes typed into the form are saved first, as a document of their own."""
        model, thinking = model_choice(data)
        evaluation = data.get("kind", "evaluation") == "evaluation"
        doc_ids = list(data.get("doc_ids") or [])
        if evaluation and str(data.get("notes") or "").strip():
            doc_ids.append(vault.add_note(patient_uid, data["notes"], title=f"Evaluation notes {time.strftime('%m/%d/%Y')}",
                                          document_type=fieldtypes.EVALUATION_NOTES))
        docs = vault.documents_for_history(patient_uid, doc_ids, data.get("reread"))
        if not docs:
            raise ValueError("Choose at least one source")
        header = vault.evaluation_header(patient_uid) if evaluation else None
        vault.log("wrote an evaluation" if evaluation else "built a history", vault.internal("patients", patient_uid), model)
        self.start_stream()
        try:
            if worker.STATUS["current"]:
                self.stream_line({"progress": "Waiting for background reading to finish its current step", "step": 0, "total": 1})
            with worker.paused():
                self.stream_history(patient_uid, docs, model, thinking, data.get("reread"), evaluation, header)
        except Exception as error:
            self.stream_line({"error": str(error)})

    def stream_history(self, patient_uid, docs, model, thinking, reread, evaluation=False, header=None):
        if not reread:  # documents read in the background while this waited are picked up, not read twice
            read_now = {d["uid"]: d for d in vault.documents_for_history(patient_uid, [d["uid"] for d in docs], False)}
            docs = [read_now[d["uid"]] if "facts" in read_now.get(d["uid"], {}) and "facts" not in d else d for d in docs]
        for update in history.build(docs, model, thinking, vault.document_field_definitions(), intake.document_types(), evaluation, header):
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
