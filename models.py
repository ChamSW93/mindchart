"""Which AI model suits this Mac, and downloading it through Ollama when it's missing.

This is the one place the recommendations live: the app uses it to pick its default model, and
install.sh asks it (python3 models.py --recommended) which model to download.
"""
import json
import subprocess
import sys
import urllib.request

OLLAMA = "http://localhost:11434"

# Best models for each kind of Mac, in order of preference. The first is downloaded if missing;
# the first one installed is the default. Chosen by testing on the fictional sample records (see README).
RECOMMENDED_MODELS = {
    "full": ["gpt-oss:20b", "ornith-1.5:9b", "bonsai-2:27b"],      # 24 GB or more
    "standard": ["ornith-1.5:9b", "bonsai-2:27b", "bonsai-2:9b"],  # 16 GB, e.g. MacBook Air M2
    "light": ["bonsai-2:9b"],                                      # 8 GB
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


def recommended(tier=None):
    """The model this Mac should have."""
    return RECOMMENDED_MODELS[tier or hardware()["tier"]][0]


def installed():
    """Names of the models downloaded with Ollama, or [] if Ollama isn't running."""
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=5) as response:
            return [m["name"] for m in json.load(response)["models"]]
    except Exception:
        return []


def pull(name, progress):
    """Download a model through Ollama, calling progress(percent) as it goes."""
    request = urllib.request.Request(f"{OLLAMA}/api/pull", data=json.dumps({"model": name}).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=3600) as response:
        for line in response:
            update = json.loads(line)
            if update.get("error"):
                raise RuntimeError(update["error"])
            if update.get("total"):
                progress(round(100 * update.get("completed", 0) / update["total"]))


if __name__ == "__main__":
    if "--recommended" in sys.argv:
        print(recommended())
