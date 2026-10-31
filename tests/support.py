"""Shared setup for the tests: a throwaway encrypted store and a free port, created once per test run.

Importing this before vault or server points them at a temporary folder, so tests never touch real data.
"""
import atexit
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = Path(tempfile.mkdtemp(prefix="mindchart-test-"))
os.environ["MINDCHART_DATA"] = str(DATA)
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    os.environ["MINDCHART_PORT"] = str(probe.getsockname()[1])

import vault  # noqa: E402  (must come after the environment is set)

PASSWORD = "test-password-123"
SAMPLES = ROOT / "sample-records"


def unlocked_vault():
    """The shared test store, created on first use and unlocked."""
    if not vault.IMAGE.exists():
        vault.create(PASSWORD, "Test Clinician")
    vault._wait_until = 0
    vault.unlock(PASSWORD)
    return vault


def ensure_pdf_reader():
    """Build the PDF reader if this copy doesn't have it yet (it isn't kept in git)."""
    reader = ROOT / "tools" / "pdftext"
    if not reader.exists():
        subprocess.run(["swiftc", "-O", str(reader) + ".swift", "-o", str(reader)], check=True)


def fake_touch_id(exit_code=0):
    """Point touchid at a stand-in helper that "seals" by saving the password as is and never asks for a finger.
    exit_code: what unlocking returns (2 cancelled, 3 fingerprints changed). Returns a function that undoes it."""
    import touchid
    script = DATA / "fake-touchid"
    script.write_text(f"""#!/bin/sh
case "$1" in
  available) echo yes ;;
  enroll) cat > "$2" ;;
  unlock) [ {exit_code} -ne 0 ] && {{ echo "fake failure" >&2; exit {exit_code}; }}; cat "$2" ;;
esac
""")
    script.chmod(0o755)
    real, touchid.HELPER = touchid.HELPER, script

    def undo():
        touchid.HELPER = real
        touchid.turn_off()
    return undo


@atexit.register
def clean_up():
    vault.lock("tests finished")
    subprocess.run(["hdiutil", "detach", "-force", str(vault.MOUNT)], capture_output=True)
    shutil.rmtree(DATA, ignore_errors=True)
