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
import mimetypes
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import assistant
import documents
import history
import updater
import vault

APP_NAME = "MindChart"  # the name shown in the app; change it here to rename the app
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODELS_URL = "http://localhost:11434/api/tags"
MODEL = "gpt-oss:20b"  # used until another model is picked in the app
PORT = 8000
HERE = Path(__file__).parent
CONTEXT_LENGTH = 16384  # how much text the chat model can see at once (in tokens)
PAGES = {"/": "app.html", "/chat.html": "chat.html"}
# Best models for each kind of Mac, in order of preference; the first one installed is the default.
RECOMMENDED_MODELS = {
    "full": ["gpt-oss:20b", "bonsai-2:27b", "mistral-nemo:latest"],      # 24 GB or more
    "standard": ["bonsai-2:27b", "mistral-nemo:latest", "gpt-oss:20b"],  # 16 GB, e.g. MacBook Air M2
    "light": ["bonsai-2:9b", "mistral-nemo:latest"],                     # 8 GB
}


def sysctl(name):
    return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True).stdout.strip()


def hardware():
    """This Mac's chip and memory, how much of it the GPU (and so the AI model) can use, and what to turn on."""
    memory_gb = round(int(sysctl("hw.memsize") or 0) / 2**30)
    # macOS lets the GPU use about two thirds of memory on smaller Macs and three quarters on larger ones.
    gpu_gb = round(memory_gb * (0.75 if memory_gb > 36 else 0.67), 1)
    tier = "full" if memory_gb >= 24 else "standard" if memory_gb >= 16 else "light"
    return {
        "chip": sysctl("machdep.cpu.brand_string"), "memory_gb": memory_gb, "gpu_gb": gpu_gb, "tier": tier,
        "effects": {"full": "rich", "standard": "standard", "light": "minimal"}[tier],
    }


HARDWARE = hardware()
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


def default_model(models):
    names = [m["name"] for m in models]
    return next((n for n in RECOMMENDED_MODELS[HARDWARE["tier"]] if n in names), names[0] if names else MODEL)


def model_choice(payload):
    """The model the page asked for, and whether it can think (reason before answering)."""
    name = payload.get("model") or default_model(list_models())
    try:
        thinking = next((m["thinking"] for m in list_models() if m["name"] == name), False)
    except Exception:
        thinking = False
    return name, thinking


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
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if url.path.startswith("/api/"):
            return self.api("POST", url, body)
        self.send_error(404)

    def api(self, method, url, body):
        parts = url.path.strip("/").split("/")[1:]  # /api/records/patients/PT-123 -> ["records", "patients", "PT-123"]
        query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        if parts[:1] != ["session"]:
            vault.touch()  # any use of the app postpones the automatic lock
        try:
            is_upload = parts[-1:] == ["documents"] or parts == ["extract"]  # these bodies are files, not JSON
            data = json.loads(body) if body and not is_upload else {}
            result = self.route(method, parts, query, data, body)
            if result is not None:
                self.send_json(200, result)
        except PermissionError as error:
            self.send_json(423, {"error": str(error), "locked": True})
        except (ValueError, KeyError) as error:
            self.send_json(400, {"error": str(error)})
        except Exception as error:
            self.send_json(500, {"error": f"Something went wrong: {error}"})

    def route(self, method, parts, query, data, body):
        """Handle one API call. Returns JSON to send, or None if the reply was already sent."""
        match (method, parts):
            # Signing in. The password is passed straight to macOS and never kept.
            case ("GET", ["session"]):
                return {**vault.status(), "app_name": APP_NAME}
            case ("POST", ["session", "setup"]):
                vault.create(data["password"])
                return vault.status()
            case ("POST", ["session", "unlock"]):
                vault.unlock(data["password"])
                return vault.status()
            case ("POST", ["session", "lock"]):
                vault.lock()
                return vault.status()
            case ("GET", ["models"]):
                models = list_models()
                return {"models": models, "default": default_model(models)}
            case ("GET", ["system"]):
                return {**HARDWARE, "version": UPDATE}

        if not vault.is_unlocked():
            raise PermissionError("MindChart is locked")

        match (method, parts):
            case ("GET", ["objects"]):
                return {"objects": vault.describe_objects(), "section_titles": history.SECTION_TITLES}
            case ("GET", ["records", name]):
                return vault.list_records(name, query.get("search", ""), query.get("patient"))
            case ("GET", ["records", name, uid]):
                return vault.get_record(name, uid)
            case ("POST", ["records", name]):
                return {"id": vault.create_record(name, data)}
            case ("POST", ["records", name, uid]):
                vault.update_record(name, uid, data)
                return vault.get_record(name, uid)
            case ("POST", ["records", name, uid, "delete"]):
                vault.delete_record(name, uid)
                return {}
            case ("POST", ["patients", uid, "documents"]):
                name = query.get("name", "document")
                return {"id": vault.add_document(uid, name, body, documents.read_pages(name, body))}
            case ("POST", ["patients", uid, "notes"]):
                return {"id": vault.add_note(uid, data.get("text", ""), data.get("date", ""), data.get("author", ""))}
            case ("POST", ["patients", uid, "history"]):
                return self.build_history(uid, data)
            case ("POST", ["drafts", uid]):
                vault.save_draft_edits(uid, data["result"])
                return {}
            case ("POST", ["export"]):
                docx = history.build_docx(data["sections"], data["documents"])
                self.send_body(200, docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                return None
            case ("POST", ["assistant"]):
                model, thinking = model_choice(data)
                return assistant.handle(data["message"], model, thinking, APP_NAME)
            case ("POST", ["assistant", "confirm"]):
                return assistant.confirm(data["proposal"])
            case ("POST", ["extract"]):  # a chat attachment, as one block of text
                return {"name": query.get("name", "file"), "text": documents.extract_text(query.get("name", "file"), body)}
            case ("POST", ["chat"]):
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
            for update in history.build(docs, model, thinking):
                vault.touch()  # a long build counts as activity, so MindChart stays unlocked
                if "document_read" in update:
                    read = update["document_read"]
                    vault.save_document_facts(docs[read["index"]]["id"], read["info"], read["facts"], model)
                    continue
                if "result" in update:
                    update["draft_id"] = vault.save_draft(patient_uid, model, update["result"])
                self.stream_line(update)
        except Exception as error:
            self.stream_line({"error": str(error)})

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


if __name__ == "__main__":
    print(f"{APP_NAME} running at http://localhost:{PORT}")
    print(f"This Mac: {HARDWARE['chip']}, {HARDWARE['memory_gb']} GB memory ({HARDWARE['tier']} setup)")
    print("Everything stays on this computer. Close this window to stop the app (this also locks your data).")
    threading.Thread(target=vault.auto_lock_forever, daemon=True).start()
    threading.Thread(target=check_for_updates_forever, daemon=True).start()
    # Closing the Terminal window sends SIGHUP; exiting cleanly lets the data lock on the way out.
    signal.signal(signal.SIGHUP, lambda *_: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    finally:
        vault.lock("locked because the app was closed")
