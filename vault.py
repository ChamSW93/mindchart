"""Encrypted storage for all of MindChart's data, using macOS's built-in encrypted disk images.

Everything MindChart saves lives in an AES-256 encrypted disk image that opens only with
the password chosen the first time MindChart starts. While it's unlocked it appears as a
hidden drive that only MindChart uses; when it's locked (Lock button, 15 minutes of
inactivity, closing the app, or restarting the computer) the data can't be read without
the password. The password itself is never stored.

The data is organized as objects, like a CRM. Each record has its own ID with a prefix
for its type (PT-7K3M9Q is a patient), and each type has its own fields, defined once in
OBJECTS below and used everywhere: the database, the app's pages, and the assistant.

  Patient    who the records are about
  Document   a source file (PDF/Word) or a note typed or dictated into MindChart
  Fact       one fact found in a document, with its quote, page and whether it was verified
  Draft      a history drafted for a patient
  Activity   the access log: who did what, when

The data lives outside the app's folder, so tools used to work on the app's code never see it.
"""
import contextlib
import csv
import datetime
import getpass
import hashlib
import io
import json
import os
import secrets
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import fields as fieldtypes

# Where the data lives. This stays the same if the app is renamed, so saved records are always found.
HOME = Path(os.environ.get("MINDCHART_DATA", Path.home() / "Library" / "Application Support" / "MindChart"))
IMAGE = HOME / "Patient Records.sparsebundle"
MOUNT = HOME / ".unlocked"
DATABASE = MOUNT / "records.db"
AUTO_LOCK_MINUTES = 15
MIN_PASSWORD_LENGTH = 10
SCHEMA_VERSION = 3
BACKUPS_KEPT = 5
MAX_ATTEMPTS = 5  # wrong passwords allowed before MindChart makes you wait

# How to upgrade saved data from one layout to the next: {new version: function(db)}.
# When a future version changes the tables, add a step here and raise SCHEMA_VERSION. Before any
# step runs, the database is backed up (inside the encrypted store), and each step runs in a
# transaction, so a failed upgrade leaves the data exactly as it was.
MIGRATIONS = {}  # filled in below, after the functions the steps use
SETTINGS_TABLE = "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);"
# Each record type's own fields (see fields.py). Kept as data so a field's type can change without a new version.
FIELD_DEFS_TABLE = """CREATE TABLE IF NOT EXISTS field_defs (
    object TEXT, record_type TEXT, name TEXT, label TEXT, type TEXT, options TEXT, ai INTEGER, position INTEGER,
    PRIMARY KEY (object, record_type, name));"""

_last_activity = time.time()
_lock = threading.Lock()

SECTIONS = ["identifying", "referral", "hpi", "past_psych", "medications", "risk", "trauma", "substance",
            "medical", "developmental", "educational", "testing", "family", "social", "legal", "strengths"]
DOCUMENT_TYPES = list(fieldtypes.DEFAULT_RECORD_TYPES["documents"])
DOCUMENT_TYPES_BEFORE_V3 = ["Psychiatric Evaluation", "Psychological Evaluation", "Progress or Clinical Note",
                  "Individualized Education Program (IEP)", "Court Order", "Discharge Summary", "Clinician Note", "Other"]

# Every object type and its fields. "edit": False fields are filled in by MindChart, not people.
OBJECTS = {
    "patients": {
        "label": "Patient", "plural": "Patients", "prefix": "PT", "title_field": "name",
        "fields": [
            {"name": "name", "label": "Name", "type": "text", "required": True},
            {"name": "dob", "label": "Date of birth", "type": "date"},
            {"name": "gender", "label": "Gender", "type": "text"},
            {"name": "mrn", "label": "MRN / ID", "type": "text"},
            {"name": "record_type", "label": "Record type", "type": "picklist",
             "options": list(fieldtypes.DEFAULT_RECORD_TYPES["patients"])},
            {"name": "status", "label": "Status", "type": "picklist", "options": ["Active", "Pending", "Discharged"]},
            {"name": "guardian", "label": "Legal guardian", "type": "text"},
            {"name": "notes", "label": "Notes", "type": "longtext"},
            {"name": "created", "label": "Created", "type": "datetime", "edit": False},
        ],
    },
    "documents": {
        "label": "Document", "plural": "Documents", "prefix": "DOC", "title_field": "name", "parent": "patients",
        "fields": [
            {"name": "name", "label": "Title", "type": "text", "required": True},
            {"name": "kind", "label": "Kind", "type": "picklist", "options": ["File", "Note"], "edit": False},
            {"name": "document_type", "label": "Record type", "type": "picklist", "options": DOCUMENT_TYPES},
            {"name": "document_date", "label": "Date", "type": "date"},
            {"name": "author", "label": "Author", "type": "text"},
            {"name": "added", "label": "Added", "type": "datetime", "edit": False},
            {"name": "read_by_model", "label": "Read by", "type": "text", "edit": False},
        ],
    },
    "facts": {
        "label": "Fact", "plural": "Facts", "prefix": "FCT", "title_field": "fact", "parent": "documents",
        "fields": [
            {"name": "section", "label": "Section", "type": "picklist", "options": SECTIONS},
            {"name": "fact", "label": "Fact", "type": "longtext", "required": True},
            {"name": "date", "label": "Date", "type": "date"},
            {"name": "quote", "label": "Quote from source", "type": "longtext", "edit": False},
            {"name": "page", "label": "Page", "type": "number", "edit": False},
            {"name": "verified", "label": "Verified", "type": "checkbox"},
            {"name": "edited", "label": "Last edited", "type": "datetime", "edit": False},
        ],
    },
    "drafts": {
        "label": "Draft", "plural": "Drafts", "prefix": "DRF", "title_field": "created", "parent": "patients",
        "fields": [
            {"name": "created", "label": "Created", "type": "datetime", "edit": False},
            {"name": "updated", "label": "Last saved", "type": "datetime", "edit": False},
            {"name": "model", "label": "Model", "type": "text", "edit": False},
        ],
    },
    "activity": {
        "label": "Activity", "plural": "Activity", "prefix": "ACT", "title_field": "action",
        "fields": [
            {"name": "time", "label": "Time", "type": "datetime", "edit": False},
            {"name": "actor", "label": "By", "type": "text", "edit": False},
            {"name": "action", "label": "Action", "type": "text", "edit": False},
            {"name": "detail", "label": "Detail", "type": "text", "edit": False},
        ],
    },
}
PARENT_COLUMN = {"patients": "patient_id", "documents": "document_id"}


def column_type(field):
    return "INTEGER" if field["type"] in ("number", "checkbox") else "TEXT"


def table_sql(name):
    spec = OBJECTS[name]
    columns = ["id INTEGER PRIMARY KEY", "uid TEXT UNIQUE NOT NULL"]
    if spec.get("parent"):
        on_delete = "SET NULL" if name == "activity" else "CASCADE"  # keep the log when a patient is deleted
        columns.append(f"{PARENT_COLUMN[spec['parent']]} INTEGER REFERENCES {spec['parent']}(id) ON DELETE {on_delete}")
    columns += [f"{f['name']} {column_type(f)}" for f in spec["fields"]]
    # "custom" holds the record type's own fields as JSON (see fields.py).
    columns += {"patients": ["custom TEXT"], "documents": ["original BLOB", "pages TEXT", "read_at TEXT", "custom TEXT"],
                "drafts": ["result TEXT"],
                # The log names its patient by record ID rather than a link that could change or be reused,
                # and each entry carries a hash chained to the one before (see chain_hash).
                "activity": ["patient_uid TEXT", "hash TEXT"]}.get(name, [])
    return f"CREATE TABLE IF NOT EXISTS {name} ({', '.join(columns)});"


# Views with patient and document details joined in, used by lists, search and the assistant.
VIEWS = """
DROP VIEW IF EXISTS patients_view; DROP VIEW IF EXISTS documents_view; DROP VIEW IF EXISTS facts_view;
DROP VIEW IF EXISTS drafts_view; DROP VIEW IF EXISTS activity_view;
CREATE VIEW patients_view AS
    SELECT p.uid AS id, p.name, p.record_type, p.dob, p.gender, p.mrn, p.status, p.guardian, p.notes, p.created,
           (SELECT COUNT(*) FROM documents d WHERE d.patient_id = p.id) AS documents,
           p.custom AS details  -- the record type's own fields, as JSON
    FROM patients p;
CREATE VIEW documents_view AS
    SELECT d.uid AS id, p.uid AS patient_id, p.name AS patient, d.name, d.kind, d.document_type, d.document_date,
           d.author, d.added, d.read_by_model, (SELECT COUNT(*) FROM facts f WHERE f.document_id = d.id) AS facts,
           d.custom AS details
    FROM documents d JOIN patients p ON p.id = d.patient_id;
CREATE VIEW facts_view AS
    SELECT f.uid AS id, p.uid AS patient_id, p.name AS patient, d.uid AS document_id, d.name AS document,
           d.document_type, d.document_date, f.section, f.fact, f.date, f.quote, f.page, f.verified, f.edited
    FROM facts f JOIN documents d ON d.id = f.document_id JOIN patients p ON p.id = d.patient_id;
CREATE VIEW drafts_view AS
    SELECT r.uid AS id, p.uid AS patient_id, p.name AS patient, r.created, r.updated, r.model
    FROM drafts r JOIN patients p ON p.id = r.patient_id;
CREATE VIEW activity_view AS
    SELECT a.uid AS id, a.patient_uid AS patient_id, p.name AS patient, a.time, a.actor, a.action, a.detail,
           a.id AS seq  -- the order entries were written, for sorting entries made in the same second
    FROM activity a LEFT JOIN patients p ON p.uid = a.patient_uid;
"""


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def new_uid(prefix):
    """A record ID like PT-7K3M9Q: the type's prefix plus six random letters and digits (no look-alikes like 0/O)."""
    alphabet = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
    return f"{prefix}-{''.join(secrets.choice(alphabet) for _ in range(6))}"


# --- Locking and unlocking ------------------------------------------------------

def status():
    return {"set_up": IMAGE.exists(), "unlocked": is_unlocked(), "auto_lock_minutes": AUTO_LOCK_MINUTES,
            "min_password_length": MIN_PASSWORD_LENGTH}


def is_unlocked():
    return os.path.ismount(MOUNT)


def create(password, user_name):
    if IMAGE.exists():
        raise ValueError("MindChart is already set up")
    if not user_name.strip():
        raise ValueError("Enter your name, so the activity log shows who did what")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Use a password of at least {MIN_PASSWORD_LENGTH} characters")
    HOME.mkdir(parents=True, exist_ok=True)
    # A sparse bundle only takes up as much disk space as the data inside it, up to 50 GB.
    result = subprocess.run(
        ["hdiutil", "create", "-size", "50g", "-type", "SPARSEBUNDLE", "-fs", "APFS",
         "-encryption", "AES-256", "-stdinpass", "-volname", "Patient Records", str(IMAGE)],
        input=password.encode(), capture_output=True)
    if result.returncode != 0:
        raise ValueError("Couldn't set up encrypted storage: " + result.stderr.decode().strip())
    unlock(password)
    set_setting("user_name", user_name.strip())
    log("set up MindChart")


_failed_attempts = 0
_wait_until = 0.0


def unlock(password):
    global _failed_attempts, _wait_until
    with _lock:
        if is_unlocked():
            touch()
            return
        if time.time() < _wait_until:  # slows down anyone guessing passwords
            raise ValueError(f"Too many wrong passwords. Try again in {int(_wait_until - time.time()) + 1} seconds.")
        MOUNT.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["hdiutil", "attach", "-stdinpass", "-nobrowse", "-mountpoint", str(MOUNT), str(IMAGE)],
            input=password.encode(), capture_output=True)
        if result.returncode != 0:
            if b"Authentication" not in result.stderr:
                raise ValueError(result.stderr.decode().strip())
            _failed_attempts += 1
            if _failed_attempts >= MAX_ATTEMPTS:  # 30 seconds, then doubling, up to 15 minutes
                _wait_until = time.time() + min(30 * 2 ** (_failed_attempts - MAX_ATTEMPTS), 900)
            raise ValueError("Wrong password")
        try:
            with connect() as db:
                prepare_database(db)
        except Exception:
            subprocess.run(["hdiutil", "detach", "-force", str(MOUNT)], capture_output=True)  # never leave it half-open
            raise
        touch()
        failures, _failed_attempts = _failed_attempts, 0
    log("signed in" + (f" after {failures} wrong password attempt{'s' if failures > 1 else ''}" if failures else ""))


def prepare_database(db):
    """Create the tables on first use, or upgrade saved data from an older version of MindChart."""
    db.execute("PRAGMA journal_mode = WAL")  # lets reads carry on while a long history build is writing
    version = db.execute("PRAGMA user_version").fetchone()[0]
    has_tables = db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'patients'").fetchone()[0]
    if has_tables and version > SCHEMA_VERSION:
        raise ValueError("These records were saved by a newer version of MindChart. Update MindChart to open them.")
    if has_tables and version < SCHEMA_VERSION:
        back_up(db, f"before-upgrade-to-v{SCHEMA_VERSION}")
        db.isolation_level = None  # handle transactions here, so changes to tables are undone too on failure
        for target in range(version + 1, SCHEMA_VERSION + 1):
            db.execute("BEGIN")  # one transaction per step: all of it happens, or none of it
            try:
                MIGRATIONS[target](db)
                db.execute(f"PRAGMA user_version = {target}")
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        db.isolation_level = ""
    db.executescript("\n".join(table_sql(name) for name in OBJECTS) + SETTINGS_TABLE + FIELD_DEFS_TABLE + VIEWS)
    seed_field_defs(db)
    db.commit()
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def back_up(db, reason):
    """Copy the database to backups/ inside the encrypted store, keeping the most recent few."""
    folder = MOUNT / "backups"
    folder.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = sqlite3.connect(folder / f"records-{stamp}-{reason}.db")
    with target:
        db.backup(target)
    target.close()
    for old in sorted(folder.glob("records-*.db"))[:-BACKUPS_KEPT]:
        old.unlink()


def lock(reason="locked"):
    with _lock:
        if not is_unlocked():
            return
        log(reason)
        subprocess.run(["hdiutil", "detach", str(MOUNT)], capture_output=True)
        if is_unlocked():  # something still had a file open
            subprocess.run(["hdiutil", "detach", "-force", str(MOUNT)], capture_output=True)


def touch():
    """Note activity, which postpones the automatic lock."""
    global _last_activity
    _last_activity = time.time()


def auto_lock_forever():
    """Run in the background: lock after AUTO_LOCK_MINUTES with no activity."""
    while True:
        time.sleep(30)
        if is_unlocked() and time.time() - _last_activity > AUTO_LOCK_MINUTES * 60:
            lock("locked automatically after inactivity")


# --- Basics ---------------------------------------------------------------------

@contextlib.contextmanager
def connect(read_only=False):
    """Open the database, save changes when done, and always close it (so locking never finds it in use)."""
    if not is_unlocked():
        raise PermissionError("MindChart is locked")
    # timeout: if another part of MindChart is writing, wait for it rather than failing with "database is locked".
    db = (sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True, timeout=15) if read_only
          else sqlite3.connect(DATABASE, timeout=15))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    finally:
        db.close()


def insert(db, name, values):
    """Add a record with a fresh ID; returns (internal id, record ID)."""
    for _ in range(10):
        uid = new_uid(OBJECTS[name]["prefix"])
        try:
            columns = ["uid", *values]
            cursor = db.execute(f"INSERT INTO {name} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                                (uid, *values.values()))
            return cursor.lastrowid, uid
        except sqlite3.IntegrityError as error:
            if "uid" not in str(error):
                raise
    raise RuntimeError("Couldn't make a unique ID")


def internal_id(db, name, uid):
    row = db.execute(f"SELECT id FROM {name} WHERE uid = ?", (uid,)).fetchone()
    if not row:
        raise ValueError(f"No {OBJECTS[name]['label'].lower()} with ID {uid}")
    return row["id"]


def patient_of(db, name, row_id):
    """The patient a record belongs to (internal id), for the activity log."""
    query = {
        "patients": "SELECT id FROM patients WHERE id = ?",
        "documents": "SELECT patient_id FROM documents WHERE id = ?",
        "facts": "SELECT d.patient_id FROM facts f JOIN documents d ON d.id = f.document_id WHERE f.id = ?",
        "drafts": "SELECT patient_id FROM drafts WHERE id = ?",
    }[name]
    row = db.execute(query, (row_id,)).fetchone()
    return row[0] if row else None


_log_lock = threading.Lock()


def chain_hash(previous, uid, time_, actor, action, patient_uid, detail):
    """A fingerprint of one log entry plus the entry before it. Changing, removing or reordering any
    past entry breaks every fingerprint after it, which verify_audit() detects."""
    text = "|".join(str(v or "") for v in (previous, uid, time_, actor, action, patient_uid, detail))
    return hashlib.sha256(text.encode()).hexdigest()


def actor(db):
    """Who is using MindChart: the name given at setup, and the Mac account."""
    row = db.execute("SELECT value FROM settings WHERE key = 'user_name'").fetchone()
    return f"{row[0] if row else 'Unknown'} (Mac user {getpass.getuser()})"


def log(action, patient_id=None, detail=""):
    """Add an entry to the activity log. Entries are never edited or deleted."""
    if not is_unlocked():
        return
    with _log_lock, connect() as db:
        patient = db.execute("SELECT uid FROM patients WHERE id = ?", (patient_id,)).fetchone() if patient_id else None
        patient_uid = patient[0] if patient else None
        last = db.execute("SELECT hash FROM activity WHERE hash IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        uid, time_, who = new_uid("ACT"), now(), actor(db)
        db.execute("INSERT INTO activity (uid, patient_uid, time, actor, action, detail, hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (uid, patient_uid, time_, who, action, detail,
                    chain_hash(last[0] if last else "", uid, time_, who, action, patient_uid, detail)))


def verify_audit():
    """Recheck every fingerprint in the activity log. Returns whether it's intact and how many entries it has."""
    previous, count = "", 0
    with connect(read_only=True) as db:
        for r in db.execute("SELECT uid, time, actor, action, patient_uid, detail, hash FROM activity ORDER BY id"):
            expected = chain_hash(previous, r["uid"], r["time"], r["actor"], r["action"], r["patient_uid"], r["detail"])
            if r["hash"] != expected:
                return {"ok": False, "entries": count, "problem_at": r["uid"]}
            previous, count = expected, count + 1
    return {"ok": True, "entries": count}


def export_audit():
    """The whole activity log as CSV, for compliance review."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["id", "time", "by", "action", "patient_id", "patient", "detail", "hash"])
    with connect(read_only=True) as db:
        for r in db.execute("""SELECT a.uid, a.time, a.actor, a.action, a.patient_uid, p.name, a.detail, a.hash
                               FROM activity a LEFT JOIN patients p ON p.uid = a.patient_uid ORDER BY a.id"""):
            writer.writerow(list(r))
    return out.getvalue()


def get_setting(key, default=""):
    with connect() as db:
        row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(key, value):
    with connect() as db:
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                   (key, value))


def change_password(current, new):
    """Change the password that encrypts MindChart's data."""
    if len(new) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Use a new password of at least {MIN_PASSWORD_LENGTH} characters")
    if not is_unlocked():
        raise PermissionError("MindChart is locked")
    passwords = f"{current}\0{new}".encode()
    chpass = ["hdiutil", "chpass", "-oldstdinpass", "-newstdinpass", str(IMAGE)]
    # macOS only changes the password while the data is closed, but it does check the current password
    # while open, so check that first: a typo then doesn't lock anyone out.
    if b"Authentication" in subprocess.run(chpass, input=passwords, capture_output=True).stderr:
        raise ValueError("Your current password isn't right")
    log("changed the password")
    lock("locked to change the password")
    result = subprocess.run(chpass, input=passwords, capture_output=True)
    if result.returncode != 0:
        unlock(current)
        raise ValueError("Couldn't change the password: " + result.stderr.decode().strip())
    unlock(new)


def clean_fields(name, fields, creating=False):
    """Keep only fields people may set, checking required fields and picklist values."""
    spec = {f["name"]: f for f in OBJECTS[name]["fields"]}
    clean = {}
    for key, value in fields.items():
        field = spec.get(key)
        if not field or field.get("edit") is False:
            continue
        if field["type"] == "checkbox":
            value = 1 if str(value).lower() in ("1", "true", "yes", "on") else 0
        elif field["type"] == "picklist" and value not in ("", None) and value not in field["options"]:
            raise ValueError(f"{field['label']} must be one of: {', '.join(field['options'])}")
        else:
            value = "" if value is None else str(value).strip()
        clean[key] = value
    for field in spec.values():
        if field.get("required") and (creating or field["name"] in clean) and not clean.get(field["name"]):
            raise ValueError(f"{field['label']} is required")
    return clean


# --- Records (the CRM) ----------------------------------------------------------

def describe_objects():
    described = {name: dict(spec) for name, spec in OBJECTS.items()}
    for name, record_types in field_definitions().items():
        described[name]["record_type_field"] = fieldtypes.RECORD_TYPE_FIELD[name]
        described[name]["record_types"] = record_types
    return described


# --- Record types and their fields ------------------------------------------------

def seed_field_defs(db):
    """Copy the default record types' fields into the database. New fields are added; ones already there
    (including any whose type was changed later) are left as they are."""
    for obj, record_types in fieldtypes.DEFAULT_RECORD_TYPES.items():
        for record_type, definitions in record_types.items():
            for position, d in enumerate(definitions):
                db.execute("INSERT OR IGNORE INTO field_defs (object, record_type, name, label, type, options, ai, position) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (obj, record_type, d["name"], d["label"], d["type"],
                                                               json.dumps(d["options"]), int(d["ai"]), position))


def field_definitions():
    """{object: {record type: [field definitions]}}, as currently set."""
    result = {obj: {rt: [] for rt in types} for obj, types in fieldtypes.DEFAULT_RECORD_TYPES.items()}
    with connect() as db:
        for r in db.execute("SELECT * FROM field_defs ORDER BY object, record_type, position"):
            result.setdefault(r["object"], {}).setdefault(r["record_type"], []).append(
                {"name": r["name"], "label": r["label"], "type": r["type"], "options": json.loads(r["options"] or "[]"),
                 "ai": bool(r["ai"])})
    return result


def definitions_for(obj, record_type):
    return field_definitions().get(obj, {}).get(record_type or "", [])


def custom_values(text):
    """A record's own-field values (without MindChart's notes on where AI-filled values came from)."""
    values = json.loads(text or "{}")
    return {k: v for k, v in values.items() if not k.startswith("_")}, values.get("_ai", {})


def clean_custom(obj, record_type, incoming):
    """Check values for a record type's fields; returns {name: stored value} for the fields given."""
    definitions = {d["name"]: d for d in definitions_for(obj, record_type)}
    return {name: fieldtypes.coerce(definitions[name], value) for name, value in incoming.items() if name in definitions}


def preview_field_change(obj, record_type, name, new_type, options=None):
    """What changing a field's type would do to the values already saved, without changing anything."""
    new_definition, records = _field_change_inputs(obj, record_type, name, new_type, options)
    converted, unfit = 0, []
    for uid, custom in records:
        value = custom.get(name)
        if value in (None, "", []):
            continue
        _, fits = fieldtypes.convert(value, new_definition)
        if fits:
            converted += 1
        else:
            unfit.append({"id": uid, "value": fieldtypes.display(value)})
    return {"converted": converted, "unfit": unfit}


def change_field_type(obj, record_type, name, new_type, options=None):
    """Change a field's type (say, free text to a picklist) and convert every saved value. Values that don't fit
    the new type are cleared, so preview first. The database is backed up before anything changes."""
    preview = preview_field_change(obj, record_type, name, new_type, options)
    new_definition, records = _field_change_inputs(obj, record_type, name, new_type, options)
    table, type_field = obj, fieldtypes.RECORD_TYPE_FIELD[obj]
    with connect() as db:
        old = db.execute("SELECT type FROM field_defs WHERE object = ? AND record_type = ? AND name = ?",
                         (obj, record_type, name)).fetchone()[0]
        back_up(db, f"before-changing-{name}")
        db.isolation_level = None
        db.execute("BEGIN")
        try:
            db.execute("UPDATE field_defs SET type = ?, options = ? WHERE object = ? AND record_type = ? AND name = ?",
                       (new_type, json.dumps(new_definition["options"]), obj, record_type, name))
            for row in db.execute(f"SELECT id, custom FROM {table} WHERE {type_field} = ?", (record_type,)).fetchall():
                custom = json.loads(row["custom"] or "{}")
                if name in custom:
                    custom[name] = fieldtypes.convert(custom[name], new_definition)[0]
                    db.execute(f"UPDATE {table} SET custom = ? WHERE id = ?", (json.dumps(custom), row["id"]))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    log("changed a field's type", None, f"{record_type} · {new_definition['label']}: {old} → {new_type}; "
                                        f"{preview['converted']} converted, {len(preview['unfit'])} cleared")
    return preview


def _field_change_inputs(obj, record_type, name, new_type, options):
    if new_type not in fieldtypes.FIELD_TYPES:
        raise ValueError(f"Field types are: {', '.join(fieldtypes.FIELD_TYPES)}")
    if new_type in ("picklist", "multipicklist") and not options:
        raise ValueError("A picklist needs its choices")
    current = next((d for d in definitions_for(obj, record_type) if d["name"] == name), None)
    if not current:
        raise ValueError(f"{record_type} has no field called {name}")
    new_definition = {**current, "type": new_type, "options": list(options or [])}
    type_field = fieldtypes.RECORD_TYPE_FIELD[obj]
    with connect() as db:
        records = [(r["uid"], json.loads(r["custom"] or "{}"))
                   for r in db.execute(f"SELECT uid, custom FROM {obj} WHERE {type_field} = ?", (record_type,))]
    return new_definition, records


def document_field_definitions():
    """{document record type: [fields the AI fills in]}, for history building."""
    return {rt: [d for d in defs if d["ai"]] for rt, defs in field_definitions()["documents"].items()}


def list_records(name, search="", patient_uid=None, limit=1000):
    """A list view: newest first, optionally filtered by text in any column or by patient."""
    view = f"{name}_view"
    with connect() as db:
        columns = [row[1] for row in db.execute(f"PRAGMA table_info({view})")]
        where, args = [], []
        if search.strip():
            where.append("(" + " OR ".join(f"CAST({c} AS TEXT) LIKE ?" for c in columns) + ")")
            args += [f"%{search.strip()}%"] * len(columns)
        if patient_uid and "patient_id" in columns:
            where.append("patient_id = ?")
            args.append(patient_uid)
        order = {"patients": "name COLLATE NOCASE", "documents": "added DESC", "facts": "document_date DESC, section",
                 "drafts": "created DESC", "activity": "seq DESC"}[name]
        rows = db.execute(f"SELECT * FROM {view} {'WHERE ' + ' AND '.join(where) if where else ''} "
                          f"ORDER BY {order} LIMIT ?", (*args, limit)).fetchall()
    return {"columns": columns, "rows": [dict(r) for r in rows]}


def get_record(name, uid):
    """One record with its fields and related lists (a patient's documents, facts, drafts and activity)."""
    with connect() as db:
        row = db.execute(f"SELECT * FROM {name}_view WHERE id = ?", (uid,)).fetchone()
    if not row:
        raise ValueError(f"No {OBJECTS[name]['label'].lower()} with ID {uid}")
    record = {"object": name, "record": dict(row), "related": {}}
    if name in fieldtypes.RECORD_TYPE_FIELD:
        record["custom"], record["ai_sources"] = custom_values(row["details"])
    if name == "patients":
        for child in ("documents", "facts", "drafts", "activity"):
            record["related"][child] = list_records(child, patient_uid=uid, limit=500)["rows"]
        log("opened patient", internal(name, uid))
    elif name == "documents":
        with connect() as db:
            doc = db.execute("SELECT id, pages FROM documents WHERE uid = ?", (uid,)).fetchone()
            record["text"] = "\n\n".join(p["text"] for p in json.loads(doc["pages"] or "[]"))[:20000]
            record["related"]["facts"] = [dict(r) for r in db.execute(
                "SELECT * FROM facts_view WHERE document_id = ? ORDER BY section", (uid,))]
    elif name == "drafts":
        with connect() as db:
            record["result"] = json.loads(db.execute("SELECT result FROM drafts WHERE uid = ?", (uid,)).fetchone()["result"])
        log("opened draft", internal_patient(name, uid), uid)
    return record


def internal(name, uid):
    with connect() as db:
        return internal_id(db, name, uid)


def internal_patient(name, uid):
    with connect() as db:
        return patient_of(db, name, internal_id(db, name, uid))


def create_record(name, fields, parent_uid=None):
    """Create a patient (or a note, via add_note). Returns the new record ID."""
    if name != "patients":
        raise ValueError(f"New {OBJECTS[name]['plural'].lower()} are added from a patient's page")
    values = clean_fields(name, fields, creating=True)
    values["status"] = values.get("status") or "Active"
    values["record_type"] = values.get("record_type") or list(fieldtypes.DEFAULT_RECORD_TYPES["patients"])[0]
    values["created"] = now()
    # The record type's own fields can arrive grouped under "custom" or (from the command bar) mixed in.
    incoming = {**{k: v for k, v in fields.items() if k not in values}, **(fields.get("custom") or {})}
    values["custom"] = json.dumps(clean_custom(name, values["record_type"], incoming))
    with connect() as db:
        row_id, uid = insert(db, name, values)
    log("created patient", row_id, uid)
    return uid


def update_record(name, uid, fields):
    """Save edited fields. Only values that actually changed are saved and logged."""
    values = clean_fields(name, fields)
    custom_changes = {}
    with connect() as db:
        row_id = internal_id(db, name, uid)
        current = dict(db.execute(f"SELECT * FROM {name} WHERE id = ?", (row_id,)).fetchone())
    if name in fieldtypes.RECORD_TYPE_FIELD:
        record_type = values.get(fieldtypes.RECORD_TYPE_FIELD[name]) or current[fieldtypes.RECORD_TYPE_FIELD[name]]
        incoming = {**{k: v for k, v in fields.items() if k not in values and k != "custom"}, **(fields.get("custom") or {})}
        stored = json.loads(current.get("custom") or "{}")
        blank = lambda d: fieldtypes.coerce(d, "")
        definitions = {d["name"]: d for d in definitions_for(name, record_type)}
        for key, value in clean_custom(name, record_type, incoming).items():
            if stored.get(key, blank(definitions[key])) != value:
                custom_changes[key] = (stored.get(key), value)
                stored[key] = value
                stored.get("_ai", {}).pop(key, None)  # edited by a person now, so the AI won't overwrite it
        if custom_changes:
            values["custom"] = json.dumps(stored)
    with connect() as db:
        values = {k: v for k, v in values.items() if (current.get(k) if current.get(k) is not None else "") != v}
        if not values:
            return
        if name == "facts":
            values["edited"] = now()
        db.execute(f"UPDATE {name} SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?", (*values.values(), row_id))
        patient_id = patient_of(db, name, row_id)
    # The log keeps what each field was and what it became (inside the encrypted store), so any change can be traced.
    labels = {f["name"]: f["label"].lower() for f in OBJECTS[name]["fields"]}
    if custom_changes:
        labels.update({d["name"]: d["label"].lower() for defs in field_definitions()[name].values() for d in defs})
    short = lambda v: fieldtypes.display(v)[:120]
    changed = "; ".join([f"{labels[k]}: {short(current.get(k))} → {short(v)}" for k, v in values.items()
                         if k not in ("edited", "custom")] +
                        [f"{labels[k]}: {short(old)} → {short(new)}" for k, (old, new) in custom_changes.items()])
    log(f"edited {OBJECTS[name]['label'].lower()}", patient_id, f"{uid}: {changed}")


def delete_record(name, uid):
    if name == "activity":
        raise ValueError("The activity log can't be changed")
    with connect() as db:
        row_id = internal_id(db, name, uid)
        patient_id = patient_of(db, name, row_id)
        title = db.execute(f"SELECT {OBJECTS[name]['title_field']} FROM {name} WHERE id = ?", (row_id,)).fetchone()[0]
        db.execute(f"DELETE FROM {name} WHERE id = ?", (row_id,))
    # A deleted patient's own ID can't be linked any more, so their name goes in the detail.
    log(f"deleted {OBJECTS[name]['label'].lower()}", None if name == "patients" else patient_id, f"{uid}: {title}")
    if name in ("patients", "documents"):
        with connect() as db:
            db.execute("VACUUM")  # rewrite the database so deleted records don't linger in free space


def patients_for_assistant():
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT uid AS id, name, dob FROM patients ORDER BY name")]


# --- Documents, notes and history building ---------------------------------------

def add_document(patient_uid, name, original, pages):
    with connect() as db:
        patient_id = internal_id(db, "patients", patient_uid)
        _, uid = insert(db, "documents", {"patient_id": patient_id, "name": name, "kind": "File", "added": now(),
                                          "original": original, "pages": json.dumps(pages)})
    log("added document", patient_id, f"{uid}: {name}")
    return uid


def add_note(patient_uid, text, date="", author="", title=""):
    """A note typed or dictated into MindChart. It's a document like any other, so its facts feed future drafts."""
    if not text.strip():
        raise ValueError("The note is empty")
    date = date.strip() or datetime.date.today().strftime("%m/%d/%Y")
    with connect() as db:
        patient_id = internal_id(db, "patients", patient_uid)
        _, uid = insert(db, "documents", {
            "patient_id": patient_id, "name": title.strip() or f"Note {date}", "kind": "Note",
            "document_type": "Clinician Note", "document_date": date, "author": author.strip(), "added": now(),
            "pages": json.dumps([{"page": None, "text": text.strip(), "scanned": False}])})
    log("added note", patient_id, uid)
    return uid


def documents_for_history(patient_uid, doc_uids, reread):
    """The documents to build a history from, with facts saved from earlier runs unless reread is set.

    Saved facts include any corrections made by hand, so drafts use the corrected versions.
    """
    with connect() as db:
        patient_id = internal_id(db, "patients", patient_uid)
        rows = db.execute(f"""SELECT * FROM documents WHERE patient_id = ? AND uid IN ({",".join("?" * len(doc_uids))})
                              ORDER BY id""", (patient_id, *doc_uids)).fetchall()
        documents = []
        for r in rows:
            doc = {"id": r["id"], "uid": r["uid"], "name": r["name"], "pages": json.loads(r["pages"])}
            if r["document_type"]:
                doc["info"] = {"document_type": r["document_type"], "document_date": r["document_date"] or "",
                               "author": r["author"] or ""}
            if r["read_by_model"] and not reread:
                doc["facts"] = [
                    {"section": f["section"], "fact": f["fact"], "date": f["date"] or "", "quote": f["quote"] or "",
                     "page": f["page"], "verified": bool(f["verified"])}
                    for f in db.execute("SELECT * FROM facts WHERE document_id = ? ORDER BY id", (r["id"],))
                ]
            documents.append(doc)
    return documents


def save_document_facts(doc_id, info, facts, model, filled=None):
    """Save what a model found in a document, replacing anything found on an earlier read.

    filled: the record type's fields the AI filled in, {name: {"value", "quote", "page"}}. They never overwrite
    a value a person entered or edited; only empty fields, or ones the AI filled before, are updated.
    """
    with connect() as db:
        stored = json.loads(db.execute("SELECT custom FROM documents WHERE id = ?", (doc_id,)).fetchone()[0] or "{}")
        sources = stored.setdefault("_ai", {})
        for key, item in (filled or {}).items():
            if stored.get(key) in (None, "", [], False) or key in sources:
                stored[key] = item["value"]
                sources[key] = {"quote": item["quote"], "page": item.get("page")}
        db.execute("UPDATE documents SET document_type = ?, document_date = ?, author = ?, read_by_model = ?, read_at = ?, "
                   "custom = ? WHERE id = ?", (info.get("document_type"), info.get("document_date"), info.get("author"),
                                                model, now(), json.dumps(stored), doc_id))
        db.execute("DELETE FROM facts WHERE document_id = ?", (doc_id,))
        for f in facts:
            insert(db, "facts", {"document_id": doc_id, "section": f.get("section"), "fact": f.get("fact"),
                                 "date": f.get("date"), "quote": f.get("quote"), "page": f.get("page"),
                                 "verified": int(bool(f.get("verified")))})


def save_draft(patient_uid, model, result, draft_uid=None):
    with connect() as db:
        patient_id = internal_id(db, "patients", patient_uid)
        if draft_uid:
            db.execute("UPDATE drafts SET result = ?, updated = ? WHERE uid = ? AND patient_id = ?",
                       (json.dumps(result), now(), draft_uid, patient_id))
        else:
            _, draft_uid = insert(db, "drafts", {"patient_id": patient_id, "created": now(), "updated": now(),
                                                 "model": model, "result": json.dumps(result)})
    log("saved draft", patient_id, draft_uid)
    return draft_uid


def save_draft_edits(draft_uid, result):
    with connect() as db:
        row_id = internal_id(db, "drafts", draft_uid)
        db.execute("UPDATE drafts SET result = ?, updated = ? WHERE id = ?", (json.dumps(result), now(), row_id))
        patient_id = patient_of(db, "drafts", row_id)
    log("edited draft", patient_id, draft_uid)


def run_query(sql, limit=500):
    """Run a read-only SELECT for the assistant. The connection can't change anything, and long queries are stopped."""
    statement = sql.strip().rstrip(";").strip()
    if not statement.lower().startswith(("select", "with")):
        raise ValueError("Only questions that read data are allowed")
    with connect(read_only=True) as db:
        started = time.time()
        db.set_progress_handler(lambda: 1 if time.time() - started > 5 else 0, 10000)  # stop after 5 seconds
        cursor = db.execute(statement)  # sqlite refuses more than one statement here
        columns = [d[0] for d in cursor.description]
        rows = [[("(file)" if isinstance(v, bytes) else v) for v in row] for row in cursor.fetchmany(limit)]
    return {"columns": columns, "rows": rows}


# --- Upgrades for data saved by earlier versions ----------------------------------

def upgrade_to_v2(db):
    """Version 2: who did each thing, patients named by record ID in the log, a tamper-evident log, and settings."""
    for column in ("actor", "patient_uid", "hash"):
        db.execute(f"ALTER TABLE activity ADD COLUMN {column} TEXT")
    db.execute(SETTINGS_TABLE)
    db.execute("UPDATE activity SET patient_uid = (SELECT uid FROM patients WHERE patients.id = activity.patient_id)")
    previous = ""  # seal the existing entries into the chain, oldest first
    for r in db.execute("SELECT id, uid, time, actor, action, patient_uid, detail FROM activity ORDER BY id").fetchall():
        previous = chain_hash(previous, r["uid"], r["time"], r["actor"], r["action"], r["patient_uid"], r["detail"])
        db.execute("UPDATE activity SET hash = ? WHERE id = ?", (previous, r["id"]))


MIGRATIONS[2] = upgrade_to_v2


def upgrade_to_v3(db):
    """Version 3: record types. Patients get a record type, and each record keeps its type's own fields as JSON.
    A patient's old "placement" and "admission date" move into the Residential treatment record type's fields."""
    for table in ("patients", "documents"):
        columns = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        if not columns:
            continue  # a table that doesn't exist yet is created with these columns
        if "custom" not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN custom TEXT")
        if table == "patients" and "record_type" not in columns:
            db.execute("ALTER TABLE patients ADD COLUMN record_type TEXT")
    db.execute(FIELD_DEFS_TABLE)
    columns = {r[1] for r in db.execute("PRAGMA table_info(patients)")}
    for r in db.execute("SELECT * FROM patients").fetchall():
        custom = {}
        if "placement" in columns and r["placement"]:
            custom["cottage"] = r["placement"]
        if "admitted" in columns and r["admitted"]:
            custom["admission_date"] = r["admitted"]
        db.execute("UPDATE patients SET record_type = 'Residential treatment', custom = ? WHERE id = ?",
                   (json.dumps(custom), r["id"]))


MIGRATIONS[3] = upgrade_to_v3
