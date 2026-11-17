"""Starting the local AI (Ollama) only when it's needed, and stopping it again.

MindChart used to open Ollama as soon as it started. Now Ollama starts the first time the clinician asks the AI for
something (sending a chat message, say), quietly in the background with no menu bar icon, and stops when MindChart
quits. Background work never starts it. "Stop AI" frees the memory it uses straight away, which matters on an 8 GB Mac.
If Ollama was already running (opened by hand), it's used as it is and left running; Stop AI just unloads its models.
"""
import json
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

URL = "http://localhost:11434"
MANIFESTS = Path.home() / ".ollama" / "models" / "manifests" / "registry.ollama.ai"
APP_BINARY = "/Applications/Ollama.app/Contents/Resources/ollama"
START_SECONDS = 30  # how long to wait for it to answer after starting it
STATE = {"starting": False}
_process = None  # the Ollama that MindChart started, if it did
_lock = threading.Lock()


class Unavailable(ValueError):
    """The AI couldn't be started (say, Ollama isn't installed). Its message is shown to the clinician."""


def running(timeout=2):
    try:
        urllib.request.urlopen(f"{URL}/api/version", timeout=timeout).close()
        return True
    except Exception:
        return False


def binary():
    """Ollama's program: the command-line one if it's installed, otherwise the one inside the Ollama app."""
    found = shutil.which("ollama")
    return found or (APP_BINARY if Path(APP_BINARY).exists() else None)


def ensure_running():
    """Start Ollama if it isn't running, and wait until it answers. Raises Unavailable if it can't be started."""
    global _process
    if running():
        return
    with _lock:
        if running():  # (another request started it meanwhile)
            return
        STATE["starting"] = True
        try:
            program = binary()
            if not program:
                raise Unavailable("The AI (Ollama) isn't installed. Paste MindChart's install line in Terminal to set it up.")
            _process = subprocess.Popen([program, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.time() + START_SECONDS
            while time.time() < deadline:
                if running(timeout=1):
                    return
                if _process.poll() is not None:  # it stopped straight away (say, another copy holds the port)
                    break
                time.sleep(0.5)
            raise Unavailable("The AI (Ollama) didn't start. Quit MindChart and open it again, and if that doesn't help, "
                              "send the error log from Settings.")
        finally:
            STATE["starting"] = False


def loaded():
    """The models loaded into memory right now."""
    try:
        with urllib.request.urlopen(f"{URL}/api/ps", timeout=3) as response:
            return [m["name"] for m in json.load(response).get("models", [])]
    except Exception:
        return []


def stop():
    """Free the AI's memory: Ollama stops if MindChart started it; otherwise its models are unloaded and it's left running."""
    global _process
    if _process and _process.poll() is None:
        _process.terminate()
        try:
            _process.wait(10)
        except subprocess.TimeoutExpired:
            _process.kill()
        _process = None
        return
    for name in loaded():  # keep_alive 0 unloads a model at once
        request = urllib.request.Request(f"{URL}/api/generate", data=json.dumps({"model": name, "keep_alive": 0}).encode(),
                                         headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(request, timeout=10).close()
        except Exception:
            pass


def shut_down():
    """When MindChart quits: stop the Ollama it started (one opened by hand is left alone)."""
    if _process and _process.poll() is None:
        stop()


def status():
    on = running(timeout=1)
    return {"status": "on" if on else "starting" if STATE["starting"] else "off",
            "started_by_mindchart": bool(_process and _process.poll() is None), "loaded": loaded() if on else []}


def installed_on_disk():
    """The downloaded models, read from Ollama's folder, so they can be listed without starting it:
    [{"name": "bonsai-2:9b", "size": bytes}]."""
    found = []
    for manifest in sorted(MANIFESTS.glob("*/*/*")):  # registry.ollama.ai/library/<model>/<tag>
        try:
            layers = json.loads(manifest.read_text()).get("layers", [])
        except (OSError, ValueError):
            continue
        size = sum(l.get("size", 0) for l in layers if l.get("mediaType", "").endswith((".model", ".projector")))
        if size:
            namespace, model, tag = manifest.parts[-3:]
            found.append({"name": f"{model}:{tag}" if namespace == "library" else f"{namespace}/{model}:{tag}", "size": size})
    return found
