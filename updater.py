"""Keeps MindChart up to date from its GitHub releases.

Run by "Start MindChart.command" before the app starts. If a newer release exists it is
downloaded, checked, and swapped in over the app's own files. Patient data is never
touched: it lives in the encrypted store under ~/Library/Application Support/MindChart,
not in the app folder. Nothing is ever uploaded; this only downloads.

  python3 updater.py           check and install an update if there is one
  python3 updater.py --check   only report whether an update is available
"""
import hashlib
import io
import json
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
REPOSITORY = "ChamSW93/mindchart"  # where releases are published
ASSET = "MindChart.zip"
KEEP = {"update.json"}  # local files an update must never overwrite


def current_version():
    return (HERE / "VERSION").read_text().strip()


def as_tuple(version):
    return tuple(int(part) for part in version.lstrip("v").split(".") if part.isdigit())


def latest_release():
    """The newest published release, or None if GitHub can't be reached (e.g. offline)."""
    request = urllib.request.Request(f"https://api.github.com/repos/{REPOSITORY}/releases/latest",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "MindChart"})
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            release = json.load(response)
    except Exception:
        return None
    assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
    if ASSET not in assets:
        return None
    return {"version": release["tag_name"].lstrip("v"), "notes": release.get("body") or "", "zip": assets[ASSET],
            "sha256": assets.get(ASSET + ".sha256")}


def status():
    latest = latest_release()
    return {"current": current_version(), "latest": latest and latest["version"],
            "available": bool(latest and as_tuple(latest["version"]) > as_tuple(current_version())),
            "notes": latest and latest["notes"]}


def download(url):
    request = urllib.request.Request(url, headers={"User-Agent": "MindChart"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def install(release):
    """Download a release and replace the app's files with it."""
    data = download(release["zip"])
    if release["sha256"]:  # the checksum published alongside the release must match exactly
        expected = download(release["sha256"]).decode().split()[0]
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError("The download didn't match its checksum, so it wasn't installed")
    with zipfile.ZipFile(io.BytesIO(data)) as archive, tempfile.TemporaryDirectory() as temp:
        names = archive.namelist()
        if "server.py" not in names or "VERSION" not in names or any(n.startswith(("/", "..")) or "/../" in n for n in names):
            raise RuntimeError("The download doesn't look like a MindChart release, so it wasn't installed")
        archive.extractall(temp)
        new_files = {n for n in names if not n.endswith("/")}
        old_manifest = HERE / "MANIFEST"
        old_files = set(old_manifest.read_text().split("\n")) if old_manifest.exists() else set()
        for name in new_files - KEEP:
            target = HERE / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(temp) / name, target)
        for name in old_files - new_files - KEEP:  # files a newer version no longer uses
            if name and (HERE / name).is_file():
                (HERE / name).unlink()
    for executable in ("Start MindChart.command", "tools/pdftext"):
        if (HERE / executable).exists():
            (HERE / executable).chmod(0o755)


def main():
    if REPOSITORY.startswith("OWNER/"):
        return  # a development copy that isn't connected to a release channel
    latest = latest_release()
    if not latest:
        print("Couldn't check for updates (offline?). Starting the current version.")
        return
    if as_tuple(latest["version"]) <= as_tuple(current_version()):
        print(f"MindChart {current_version()} is up to date.")
        return
    if "--check" in sys.argv:
        print(f"Update available: {current_version()} -> {latest['version']}")
        return
    print(f"Updating MindChart {current_version()} -> {latest['version']}...")
    try:
        install(latest)
        print("Updated.")
    except Exception as error:
        print(f"The update didn't install ({error}). Starting the current version.")


if __name__ == "__main__":
    main()
