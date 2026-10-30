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
import datetime
import json
import os
import secrets
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

# Where the data lives. This stays the same if the app is renamed, so saved records are always found.
HOME = Path(os.environ.get("MINDCHART_DATA", Path.home() / "Library" / "Application Support" / "MindChart"))
IMAGE = HOME / "Patient Records.sparsebundle"
MOUNT = HOME / ".unlocked"
DATABASE = MOUNT / "records.db"
AUTO_LOCK_MINUTES = 15
MIN_PASSWORD_LENGTH = 10
SCHEMA_VERSION = 1

_last_activity = time.time()
_lock = threading.Lock()

SECTIONS = ["identifying", "referral", "hpi", "past_psych", "medications", "risk", "trauma", "substance",
            "medical", "developmental", "educational", "testing", "family", "social", "legal", "strengths"]
DOCUMENT_TYPES = ["Psychiatric Evaluation", "Psychological Evaluation", "Progress or Clinical Note",
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
            {"name": "status", "label": "Status", "type": "picklist", "options": ["Active", "Pending", "Discharged"]},
            {"name": "placement", "label": "Current placement", "type": "text"},
            {"name": "admitted", "label": "Admission date", "type": "date"},
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
            {"name": "document_type", "label": "Type", "type": "picklist", "options": DOCUMENT_TYPES},
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
        "label": "Activity", "plural": "Activity", "prefix": "ACT", "title_field": "action", "parent": "patients",
        "fields": [
            {"name": "time", "label": "Time", "type": "datetime", "edit": False},
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
    columns += {"documents": ["original BLOB", "pages TEXT", "read_at TEXT"], "drafts": ["result TEXT"]}.get(name, [])
    return f"CREATE TABLE IF NOT EXISTS {name} ({', '.join(columns)});"


# Views with patient and document details joined in, used by lists, search and the assistant.
VIEWS = """
CREATE VIEW IF NOT EXISTS patients_view AS
    SELECT p.uid AS id, p.name, p.dob, p.gender, p.mrn, p.status, p.placement, p.admitted, p.guardian, p.notes, p.created,
           (SELECT COUNT(*) FROM documents d WHERE d.patient_id = p.id) AS documents
    FROM patients p;
CREATE VIEW IF NOT EXISTS documents_view AS
    SELECT d.uid AS id, p.uid AS patient_id, p.name AS patient, d.name, d.kind, d.document_type, d.document_date,
           d.author, d.added, d.read_by_model, (SELECT COUNT(*) FROM facts f WHERE f.document_id = d.id) AS facts
    FROM documents d JOIN patients p ON p.id = d.patient_id;
CREATE VIEW IF NOT EXISTS facts_view AS
    SELECT f.uid AS id, p.uid AS patient_id, p.name AS patient, d.uid AS document_id, d.name AS document,
           d.document_type, d.document_date, f.section, f.fact, f.date, f.quote, f.page, f.verified, f.edited
    FROM facts f JOIN documents d ON d.id = f.document_id JOIN patients p ON p.id = d.patient_id;
CREATE VIEW IF NOT EXISTS drafts_view AS
    SELECT r.uid AS id, p.uid AS patient_id, p.name AS patient, r.created, r.updated, r.model
    FROM drafts r JOIN patients p ON p.id = r.patient_id;
CREATE VIEW IF NOT EXISTS activity_view AS
    SELECT a.uid AS id, p.uid AS patient_id, p.name AS patient, a.time, a.action, a.detail
    FROM activity a LEFT JOIN patients p ON p.id = a.patient_id;
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


def create(password):
    if IMAGE.exists():
        raise ValueError("MindChart is already set up")
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
    log("set up MindChart")


def unlock(password):
    with _lock:
        if is_unlocked():
            touch()
            return
        MOUNT.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["hdiutil", "attach", "-stdinpass", "-nobrowse", "-mountpoint", str(MOUNT), str(IMAGE)],
            input=password.encode(), capture_output=True)
        if result.returncode != 0:
            raise ValueError("Wrong password" if b"Authentication" in result.stderr else result.stderr.decode().strip())
        with connect() as db:
            db.executescript("\n".join(table_sql(name) for name in OBJECTS) + VIEWS)
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")  # lets future versions upgrade saved data safely
        touch()
    log("signed in")


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
    db = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True) if read_only else sqlite3.connect(DATABASE)
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
        "activity": "SELECT patient_id FROM activity WHERE id = ?",
    }[name]
    row = db.execute(query, (row_id,)).fetchone()
    return row[0] if row else None


def log(action, patient_id=None, detail=""):
    if not is_unlocked():
        return
    with connect() as db:
        insert(db, "activity", {"patient_id": patient_id, "time": now(), "action": action, "detail": detail})


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
    return {name: {k: v for k, v in spec.items()} for name, spec in OBJECTS.items()}


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
                 "drafts": "created DESC", "activity": "time DESC"}[name]
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
    values.setdefault("status", "Active")
    values["created"] = now()
    with connect() as db:
        row_id, uid = insert(db, name, values)
    log("created patient", row_id, uid)
    return uid


def update_record(name, uid, fields):
    """Save edited fields. Only values that actually changed are saved and logged."""
    values = clean_fields(name, fields)
    with connect() as db:
        row_id = internal_id(db, name, uid)
        current = dict(db.execute(f"SELECT * FROM {name} WHERE id = ?", (row_id,)).fetchone())
        values = {k: v for k, v in values.items() if (current.get(k) if current.get(k) is not None else "") != v}
        if not values:
            return
        if name == "facts":
            values["edited"] = now()
        db.execute(f"UPDATE {name} SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?", (*values.values(), row_id))
        patient_id = patient_of(db, name, row_id)
    labels = {f["name"]: f["label"].lower() for f in OBJECTS[name]["fields"]}
    changed = ", ".join(labels[k] for k in values if k != "edited")
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


def save_document_facts(doc_id, info, facts, model):
    """Save what a model found in a document, replacing anything found on an earlier read."""
    with connect() as db:
        db.execute("UPDATE documents SET document_type = ?, document_date = ?, author = ?, read_by_model = ?, read_at = ? "
                   "WHERE id = ?", (info.get("document_type"), info.get("document_date"), info.get("author"), model, now(), doc_id))
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
