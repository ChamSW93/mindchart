"""Unlocking with Touch ID: optional, and off until it's turned on in Settings.

Turning it on keeps the password sealed in touchid.json, next to the encrypted store, by a key that never leaves
this Mac's Secure Enclave and only works after a fingerprint (tools/touchid.swift does that part). If fingerprints
are added or removed, the key stops working and MindChart asks for the password, as before. Turning it off
deletes the file.
"""
import os
import subprocess
from pathlib import Path

import vault

HELPER = Path(__file__).parent / "tools" / "touchid"
CANCELLED, KEY_CHANGED = 2, 3  # the helper's exit codes


class Cancelled(ValueError):
    pass


def sealed_file():
    return vault.HOME / "touchid.json"


def helper():
    """The compiled helper. Releases include it; a working copy builds it the first time it's needed."""
    if not HELPER.exists():
        subprocess.run(["swiftc", "-O", str(HELPER) + ".swift", "-o", str(HELPER)], check=True, capture_output=True)
    if not os.access(HELPER, os.X_OK):  # updaters from before Touch ID don't know to make it runnable
        HELPER.chmod(0o755)
    return str(HELPER)


def available():
    try:
        return subprocess.run([helper(), "available"], capture_output=True, text=True, timeout=30).stdout.strip() == "yes"
    except Exception:
        return False


def is_on():
    return sealed_file().exists()


def turn_on(password):
    """Seal the password for Touch ID. Doesn't ask for a fingerprint (opening it does)."""
    result = subprocess.run([helper(), "enroll", str(sealed_file())], input=password.encode(), capture_output=True, timeout=30)
    if result.returncode != 0:
        raise ValueError(result.stderr.decode().strip() or "Couldn't set up Touch ID")


def turn_off():
    sealed_file().unlink(missing_ok=True)


def get_password():
    """Ask for Touch ID and return the password."""
    try:
        result = subprocess.run([helper(), "unlock", str(sealed_file())], capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise Cancelled("Touch ID timed out. Try again, or sign in with your password.")
    if result.returncode == CANCELLED:
        raise Cancelled("Touch ID was cancelled")
    if result.returncode == KEY_CHANGED:
        turn_off()
        raise ValueError("Touch ID was turned off because this Mac's fingerprints changed. Sign in with your password, "
                         "then turn it back on in Settings.")
    if result.returncode != 0:
        raise ValueError(result.stderr.decode().strip() or "Touch ID didn't work")
    return result.stdout.decode()
