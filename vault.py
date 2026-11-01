"""Encrypted storage for all of MindChart's data, using macOS's built-in encrypted disk images.

Everything MindChart saves lives in an AES-256 encrypted disk image that opens only with
the password chosen the first time MindChart starts. While it's unlocked it appears as a
hidden drive that only MindChart uses; when it's locked (Lock button, 15 minutes of
inactivity, closing the app, or restarting the computer) the data can't be read without
the password. The password itself is never stored, unless Touch ID is turned on (touchid.py), which keeps
it sealed by a key in the Mac's Secure Enclave that only works after a fingerprint.

The data is organized as objects, like a CRM. Each record has its own ID with a prefix
for its type (PT-7K3M9Q is a patient), and each type has its own fields, defined once in
OBJECTS below and used everywhere: the database, the app's pages, and the assistant.

  Patient    who the records are about
  Document   a source file (PDF/Word) or a note typed or dictated into MindChart
  Fact       one fact found in a document, with its quote, page and whether it was verified
  Draft      a history drafted for a patient
  Activity   the access log: who did what, when
  Medication a medication a patient takes or took, with its consent
  Measure    a rating scale score (PHQ-A, GAD-7...) or vitals, for following change over time
  Follow-up  something due for a patient: a court date, an IEP meeting, a consent, labs

Files dropped into MindChart wait in an uploads inbox (also inside the encrypted store) until the clinician
confirms which patient they belong to and what type they are. Each confirmation is kept as an example, so
MindChart gets better at recognizing that kind of document (see learning.py).

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
import re
import secrets
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import fields as fieldtypes
import zipcodes

# Where the data lives. This stays the same if the app is renamed, so saved records are always found.
HOME = Path(os.environ.get("MINDCHART_DATA", Path.home() / "Library" / "Application Support" / "MindChart"))
IMAGE = HOME / "Patient Records.sparsebundle"
MOUNT = HOME / ".unlocked"
DATABASE = MOUNT / "records.db"
AUTO_LOCK_MINUTES = 15
MIN_PASSWORD_LENGTH = 10
SCHEMA_VERSION = 8
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
# The record types themselves, so new ones (say, a "Safety plan" document type) can be added in the app.
RECORD_TYPES_TABLE = """CREATE TABLE IF NOT EXISTS record_types (
    object TEXT, name TEXT, description TEXT, position INTEGER, PRIMARY KEY (object, name));"""
# Files waiting for the clinician to confirm their patient and type. "suggestion" is what MindChart worked out.
UPLOADS_TABLE = """CREATE TABLE IF NOT EXISTS uploads (
    id INTEGER PRIMARY KEY, uid TEXT UNIQUE NOT NULL, name TEXT, original BLOB, pages TEXT, added TEXT,
    patient_uid TEXT, state TEXT, suggestion TEXT);"""
# Each confirmed document's type and opening text, which MindChart learns to recognize types from.
# Examples go when their document is deleted, so deleting records leaves nothing behind.
EXAMPLES_TABLE = """CREATE TABLE IF NOT EXISTS type_examples (
    id INTEGER PRIMARY KEY, document_id INTEGER UNIQUE REFERENCES documents(id) ON DELETE CASCADE,
    document_type TEXT, name TEXT, text TEXT, added TEXT);"""
# Which legal guardians each patient has. Either side can have several; links go when either person is deleted.
GUARDIANSHIPS_TABLE = """CREATE TABLE IF NOT EXISTS guardianships (
    patient_id INTEGER REFERENCES patients(id) ON DELETE CASCADE, guardian_id INTEGER REFERENCES patients(id) ON DELETE CASCADE,
    PRIMARY KEY (patient_id, guardian_id));"""
# Who was involved in each incident (any number of patients); links go when the incident or the patient is deleted.
INCIDENT_PEOPLE_TABLE = """CREATE TABLE IF NOT EXISTS incident_people (
    incident_id INTEGER REFERENCES incidents(id) ON DELETE CASCADE, patient_id INTEGER REFERENCES patients(id) ON DELETE CASCADE,
    role TEXT, PRIMARY KEY (incident_id, patient_id));"""
# The AI's experimental readings of a cottage's dynamics (cottage_id) or of all the cottages together (no cottage_id),
# with the clinician's feedback on each, which guides later readings. A cottage's go when it's deleted.
COTTAGE_ANALYSES_TABLE = """CREATE TABLE IF NOT EXISTS cottage_analyses (
    id INTEGER PRIMARY KEY, cottage_id INTEGER REFERENCES cottages(id) ON DELETE CASCADE, created TEXT, model TEXT, result TEXT,
    feedback TEXT);"""
EXAMPLE_CHARS = 3000  # how much of a document's opening text is kept as an example

_last_activity = time.time()
_lock = threading.Lock()

SECTIONS = ["identifying", "referral", "hpi", "past_psych", "medications", "risk", "trauma", "substance",
            "medical", "developmental", "educational", "testing", "family", "social", "legal", "strengths",
            # What the clinician found at the evaluation being written (only from their evaluation notes)
            "presentation", "mse", "risk_assessment", "impression", "diagnoses", "recommendations"]
DOCUMENT_TYPES = list(fieldtypes.DEFAULT_RECORD_TYPES["documents"])
DOCUMENT_TYPES_BEFORE_V3 = ["Psychiatric Evaluation", "Psychological Evaluation", "Progress or Clinical Note",
                  "Individualized Education Program (IEP)", "Court Order", "Discharge Summary", "Clinician Note", "Other"]

# Every object type and its fields. "edit": False fields are filled in by MindChart, not people.
OBJECTS = {
    # People: patients and their legal guardians, told apart by record type. (The table keeps its first name,
    # "patients", so everything that links to a patient still does.) "for" limits a field to some record types.
    "patients": {
        "label": "Person", "plural": "People", "prefix": "PT", "title_field": "name",
        "prefixes": {"Legal Guardian": "LG"},  # PT-... for patients, LG-... for legal guardians
        "fields": [
            {"name": "first_name", "label": "First name", "type": "text"},
            {"name": "last_name", "label": "Last name", "type": "text"},
            {"name": "name", "label": "Name", "type": "text", "edit": False, "hidden": True},  # first and last, kept in step
            {"name": "record_type", "label": "Record type", "type": "picklist",
             "options": list(fieldtypes.DEFAULT_RECORD_TYPES["patients"])},
            {"name": "dob", "label": "Date of birth", "type": "date", "for": ["Patient"]},
            {"name": "gender", "label": "Gender", "type": "picklist", "options": fieldtypes.GENDERS, "for": ["Patient"]},
            {"name": "mrn", "label": "MRN / ID", "type": "text", "for": ["Patient"]},
            {"name": "status", "label": "Status", "type": "picklist", "options": ["Active", "Pending", "Discharged"],
             "for": ["Patient"]},
            {"name": "placement", "label": "Placement", "type": "picklist", "options": fieldtypes.PLACEMENTS, "for": ["Patient"]},
            # Links to the patient's guardians' records, kept in the guardianships table rather than a column
            {"name": "guardians", "label": "Legal guardians", "type": "people", "record_type": "Legal Guardian",
             "for": ["Patient"], "virtual": True},
            # A guardian typed in before guardians had records of their own, kept until someone links their record
            {"name": "guardian", "label": "Legal guardian", "type": "text", "edit": False, "hidden": True},
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
    "medications": {
        "label": "Medication", "plural": "Medications", "prefix": "MED", "title_field": "name", "parent": "patients",
        "fields": [
            {"name": "name", "label": "Medication", "type": "text", "required": True},
            {"name": "dose", "label": "Dose", "type": "text"},
            {"name": "frequency", "label": "How often", "type": "text", "suggest": fieldtypes.FREQUENCIES},
            {"name": "target", "label": "For (target symptoms)", "type": "text"},
            {"name": "status", "label": "Status", "type": "picklist", "options": ["Active", "Stopped", "Planned"]},
            {"name": "start_date", "label": "Started", "type": "date"},
            {"name": "stop_date", "label": "Stopped", "type": "date"},
            {"name": "stop_reason", "label": "Why stopped", "type": "text"},
            {"name": "response", "label": "Response and side effects", "type": "longtext"},
            {"name": "prescriber", "label": "Prescriber", "type": "text"},
            {"name": "consent", "label": "Consent", "type": "picklist", "options": ["On file", "Requested", "Not yet requested", "Not needed"]},
            {"name": "consent_by", "label": "Consent from", "type": "text"},
            {"name": "consent_date", "label": "Consent date", "type": "date"},
            {"name": "source", "label": "Found in", "type": "text", "edit": False},
        ],
    },
    "measures": {
        "label": "Measure", "plural": "Measures", "prefix": "MSR", "title_field": "measure", "parent": "patients",
        "fields": [
            {"name": "measure", "label": "Measure", "type": "picklist", "options": list(fieldtypes.DEFAULT_RECORD_TYPES["measures"]),
             "required": True},
            {"name": "date", "label": "Date", "type": "date", "required": True},
            {"name": "score", "label": "Total score", "type": "number"},
            {"name": "rater", "label": "Completed by", "type": "text"},
            {"name": "notes", "label": "Notes", "type": "longtext"},
        ],
    },
    "follow_ups": {
        "label": "Follow-up", "plural": "Follow-ups", "prefix": "FUP", "title_field": "title", "parent": "patients",
        "fields": [
            {"name": "title", "label": "What", "type": "text", "required": True},
            {"name": "due", "label": "Due", "type": "date"},
            {"name": "category", "label": "Kind", "type": "picklist",
             "options": ["Court", "School / IEP", "Medication / labs", "Consent", "Appointment", "Other"]},
            {"name": "status", "label": "Status", "type": "picklist", "options": ["Open", "Done"]},
            {"name": "notes", "label": "Notes", "type": "longtext"},
        ],
    },
    # The Children's Village cottages. Patients name theirs in their Cottage field, whose choices are these records.
    "cottages": {
        "label": "Cottage", "plural": "Cottages", "prefix": "CTG", "title_field": "name",
        "fields": [
            {"name": "name", "label": "Name", "type": "text", "required": True},
            {"name": "lead", "label": "Cottage lead", "type": "text"},
            {"name": "capacity", "label": "Capacity", "type": "number"},
            {"name": "notes", "label": "Notes", "type": "longtext"},
            {"name": "created", "label": "Created", "type": "datetime", "edit": False},
        ],
    },
    # Incidents: events involving one or more youths (a fight, an injury, running away), linked to everyone involved.
    "incidents": {
        "label": "Incident", "plural": "Incidents", "prefix": "INC", "title_field": "title",
        "fields": [
            {"name": "title", "label": "What happened, in brief", "type": "text", "required": True},
            {"name": "date", "label": "Date", "type": "date"},
            {"name": "time", "label": "Time", "type": "text"},
            {"name": "type", "label": "Type", "type": "picklist", "options": fieldtypes.INCIDENT_TYPES},
            {"name": "severity", "label": "Severity", "type": "picklist", "options": ["Minor", "Moderate", "Serious"]},
            # Links to the patients involved, kept in the incident_people table rather than a column
            {"name": "people", "label": "Youths involved", "type": "people", "record_type": "Patient", "virtual": True},
            {"name": "location", "label": "Where", "type": "text"},
            {"name": "description", "label": "Account", "type": "longtext"},
            {"name": "injuries", "label": "Injuries", "type": "longtext"},
            {"name": "response", "label": "Staff response", "type": "longtext"},
            {"name": "reported_by", "label": "Reported by", "type": "text"},
            {"name": "created", "label": "Created", "type": "datetime", "edit": False},
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
    columns += [f"{f['name']} {column_type(f)}" for f in spec["fields"] if not f.get("virtual")]
    # "custom" holds the record type's own fields as JSON (see fields.py).
    # Documents also note whether they're waiting to be read in the background (read_state) and what to take from them (pull).
    columns += {"patients": ["custom TEXT"], "documents": ["original BLOB", "pages TEXT", "read_at TEXT", "custom TEXT",
                                                           "read_state TEXT", "pull TEXT"], "measures": ["custom TEXT"],
                "drafts": ["result TEXT"],
                # The log names its patient by record ID rather than a link that could change or be reused,
                # and each entry carries a hash chained to the one before (see chain_hash).
                "activity": ["patient_uid TEXT", "hash TEXT"]}.get(name, [])
    return f"CREATE TABLE IF NOT EXISTS {name} ({', '.join(columns)});"


# Views with patient and document details joined in, used by lists, search and the assistant.
VIEWS = """
DROP VIEW IF EXISTS people_view; DROP VIEW IF EXISTS patients_view; DROP VIEW IF EXISTS guardians_view;
DROP VIEW IF EXISTS guardianships_view; DROP VIEW IF EXISTS cottages_view; DROP VIEW IF EXISTS incidents_view;
DROP VIEW IF EXISTS incident_people_view;
DROP VIEW IF EXISTS documents_view; DROP VIEW IF EXISTS facts_view;
DROP VIEW IF EXISTS drafts_view; DROP VIEW IF EXISTS activity_view; DROP VIEW IF EXISTS medications_view;
DROP VIEW IF EXISTS measures_view; DROP VIEW IF EXISTS follow_ups_view;
CREATE VIEW people_view AS
    SELECT p.uid AS id, p.name, p.first_name, p.last_name, p.record_type, p.dob, p.gender, p.mrn, p.status, p.placement,
           COALESCE((SELECT group_concat(g.name, ', ') FROM guardianships gs JOIN patients g ON g.id = gs.guardian_id
                     WHERE gs.patient_id = p.id), NULLIF(p.guardian, '')) AS guardian,  -- their legal guardians' names
           p.notes, p.created, (SELECT COUNT(*) FROM documents d WHERE d.patient_id = p.id) AS documents,
           p.custom AS details  -- the record type's own fields, as JSON
    FROM patients p;
CREATE VIEW patients_view AS SELECT * FROM people_view WHERE record_type = 'Patient';
CREATE VIEW guardians_view AS
    SELECT g.uid AS id, g.name, g.first_name, g.last_name, g.notes, g.created,
           (SELECT group_concat(p.name, ', ') FROM guardianships gs JOIN patients p ON p.id = gs.patient_id
            WHERE gs.guardian_id = g.id) AS guardian_of,
           g.custom AS details
    FROM patients g WHERE g.record_type = 'Legal Guardian';
CREATE VIEW cottages_view AS
    SELECT c.uid AS id, c.name, c.lead, c.capacity,
           (SELECT COUNT(*) FROM patients p WHERE p.record_type = 'Patient' AND COALESCE(p.status, '') != 'Discharged'
            AND json_extract(p.custom, '$.cottage') = c.name) AS residents, c.notes, c.created
    FROM cottages c;
CREATE VIEW incidents_view AS
    SELECT i.uid AS id, i.title, i.date, i.time, i.type, i.severity,
           (SELECT group_concat(p.name, ', ') FROM incident_people l JOIN patients p ON p.id = l.patient_id
            WHERE l.incident_id = i.id) AS people, i.location, i.description, i.injuries, i.response, i.reported_by, i.created
    FROM incidents i;
CREATE VIEW incident_people_view AS
    SELECT i.uid AS incident_id, i.title AS incident, i.date, i.type, p.uid AS patient_id, p.name AS patient
    FROM incident_people l JOIN incidents i ON i.id = l.incident_id JOIN patients p ON p.id = l.patient_id;
CREATE VIEW guardianships_view AS
    SELECT p.uid AS patient_id, p.name AS patient, g.uid AS guardian_id, g.name AS guardian
    FROM guardianships gs JOIN patients p ON p.id = gs.patient_id JOIN patients g ON g.id = gs.guardian_id;
CREATE VIEW documents_view AS
    SELECT d.uid AS id, p.uid AS patient_id, p.name AS patient, d.name, d.kind, d.document_type, d.document_date,
           d.author, d.added, d.read_by_model, (SELECT COUNT(*) FROM facts f WHERE f.document_id = d.id) AS facts,
           d.read_state, d.custom AS details
    FROM documents d JOIN patients p ON p.id = d.patient_id;
CREATE VIEW facts_view AS
    SELECT f.uid AS id, p.uid AS patient_id, p.name AS patient, d.uid AS document_id, d.name AS document,
           d.document_type, d.document_date, f.section, f.fact, f.date, f.quote, f.page, f.verified, f.edited
    FROM facts f JOIN documents d ON d.id = f.document_id JOIN patients p ON p.id = d.patient_id;
CREATE VIEW drafts_view AS
    SELECT r.uid AS id, p.uid AS patient_id, p.name AS patient, COALESCE(json_extract(r.result, '$.kind'), 'history') AS kind,
           r.created, r.updated, r.model
    FROM drafts r JOIN patients p ON p.id = r.patient_id;
CREATE VIEW medications_view AS
    SELECT m.uid AS id, p.uid AS patient_id, p.name AS patient, m.name, m.dose, m.frequency, m.target, m.status,
           m.start_date, m.stop_date, m.stop_reason, m.response, m.prescriber, m.consent, m.consent_by, m.consent_date, m.source
    FROM medications m JOIN patients p ON p.id = m.patient_id;
CREATE VIEW measures_view AS
    SELECT s.uid AS id, p.uid AS patient_id, p.name AS patient, s.measure, s.date, s.score, s.rater, s.notes,
           s.custom AS details  -- the measure's own fields (subscales, vitals), as JSON
    FROM measures s JOIN patients p ON p.id = s.patient_id;
CREATE VIEW follow_ups_view AS
    SELECT f.uid AS id, p.uid AS patient_id, p.name AS patient, f.title, f.due, f.category, f.status, f.notes
    FROM follow_ups f JOIN patients p ON p.id = f.patient_id;
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


def unlock(password, method=""):
    """Open the store. method: how the password was given, for the activity log (e.g. "Touch ID")."""
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
    log("signed in" + (f" with {method}" if method else "") +
        (f" after {failures} wrong password attempt{'s' if failures > 1 else ''}" if failures else ""))


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
    db.executescript("\n".join(table_sql(name) for name in OBJECTS) + SETTINGS_TABLE + FIELD_DEFS_TABLE
                     + RECORD_TYPES_TABLE + UPLOADS_TABLE + EXAMPLES_TABLE + GUARDIANSHIPS_TABLE + INCIDENT_PEOPLE_TABLE + COTTAGE_ANALYSES_TABLE + VIEWS)
    seed_field_defs(db)
    seed_record_types(db)
    seed_cottages(db)
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
    prefix = OBJECTS[name].get("prefixes", {}).get(values.get("record_type"), OBJECTS[name]["prefix"])
    for _ in range(10):
        uid = new_uid(prefix)
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
        "medications": "SELECT patient_id FROM medications WHERE id = ?",
        "measures": "SELECT patient_id FROM measures WHERE id = ?",
        "follow_ups": "SELECT patient_id FROM follow_ups WHERE id = ?",
    }.get(name)
    if not query:  # a cottage belongs to no one patient
        return None
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


def check_password(password, wrong="That password isn't right"):
    """Make sure the password is right, while the store is open (before changing it, or turning on Touch ID)."""
    if not is_unlocked():
        raise PermissionError("MindChart is locked")
    # macOS only changes a password while the data is closed, but it does check the password while open.
    # Changing it to itself, it reports a wrong one as an authentication failure and changes nothing.
    chpass = ["hdiutil", "chpass", "-oldstdinpass", "-newstdinpass", str(IMAGE)]
    if b"Authentication" in subprocess.run(chpass, input=f"{password}\0{password}".encode(), capture_output=True).stderr:
        raise ValueError(wrong)


def change_password(current, new):
    """Change the password that encrypts MindChart's data."""
    if len(new) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Use a new password of at least {MIN_PASSWORD_LENGTH} characters")
    check_password(current, "Your current password isn't right")  # so a typo doesn't lock anyone out
    passwords = f"{current}\0{new}".encode()
    chpass = ["hdiutil", "chpass", "-oldstdinpass", "-newstdinpass", str(IMAGE)]
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
        # Record types can be added in the app, so their choices come from the database.
        options = record_type_names(name) if key == fieldtypes.RECORD_TYPE_FIELD.get(name) else field.get("options")
        if field["type"] == "checkbox":
            value = 1 if str(value).lower() in ("1", "true", "yes", "on") else 0
        elif field["type"] == "picklist" and value not in ("", None) and value not in options:
            raise ValueError(f"{field['label']} must be one of: {', '.join(options)}")
        elif field["type"] == "people":  # record IDs, as a list (or written with commas)
            value = [str(v).strip() for v in (value if isinstance(value, list) else str(value or "").split(",")) if str(v).strip()]
        elif field["type"] == "date":
            value = fieldtypes.normalize_date(value)
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
        type_field = fieldtypes.RECORD_TYPE_FIELD[name]
        described[name]["record_type_field"] = type_field
        described[name]["record_types"] = record_types
        described[name]["record_type_descriptions"] = record_type_descriptions(name)
        described[name]["fields"] = [{**f, "options": list(record_types)} if f["name"] == type_field else f
                                     for f in OBJECTS[name]["fields"]]
    described["measures"].update(bands=fieldtypes.MEASURE_BANDS, max=fieldtypes.MEASURE_MAX)
    described["patients"]["placement_fields"] = fieldtypes.PLACEMENT_FIELDS  # which of a patient's own fields each placement uses
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


def seed_record_types(db):
    """Add the default record types (with what each one is) if they aren't there yet."""
    for obj, record_types in fieldtypes.DEFAULT_RECORD_TYPES.items():
        for position, name in enumerate(record_types):
            db.execute("INSERT OR IGNORE INTO record_types (object, name, description, position) VALUES (?, ?, ?, ?)",
                       (obj, name, fieldtypes.TYPE_DESCRIPTIONS.get(name, ""), position))


def record_type_names(obj):
    with connect() as db:
        return [r[0] for r in db.execute("SELECT name FROM record_types WHERE object = ? ORDER BY position", (obj,))]


def record_type_descriptions(obj):
    with connect() as db:
        return {r[0]: r[1] or "" for r in db.execute(
            "SELECT name, description FROM record_types WHERE object = ? ORDER BY position", (obj,))}


def add_record_type(obj, name, description=""):
    """A new record type, such as a document type MindChart didn't know. It starts with no fields of its own."""
    name, description = " ".join(name.split()), " ".join(description.split())
    if obj not in fieldtypes.RECORD_TYPE_FIELD:
        raise ValueError("Only patients and documents have record types")
    if not name or len(name) > 60:
        raise ValueError("Give the type a name of up to 60 characters")
    if name.lower() in (n.lower() for n in record_type_names(obj)):
        raise ValueError(f"There's already a type called {name}")
    with connect() as db:
        position = db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM record_types WHERE object = ?", (obj,)).fetchone()[0]
        db.execute("INSERT INTO record_types (object, name, description, position) VALUES (?, ?, ?, ?)",
                   (obj, name, description[:300], position))
    log(f"added a {OBJECTS[obj]['label'].lower()} type", None, name)
    return name


def field_definitions():
    """{object: {record type: [field definitions]}}, as currently set, in the record types' order."""
    with connect() as db:
        result = {obj: {} for obj in fieldtypes.DEFAULT_RECORD_TYPES}
        for r in db.execute("SELECT object, name FROM record_types ORDER BY object, position"):
            result.setdefault(r["object"], {})[r["name"]] = []
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


def list_records(name, search="", patient_uid=None, limit=1000, record_type=None):
    """A list view: newest first, optionally filtered by text in any column or by patient.
    People are listed as patients, unless record_type asks for legal guardians."""
    view = "guardians_view" if name == "patients" and record_type == "Legal Guardian" else f"{name}_view"
    with connect() as db:
        columns = [row[1] for row in db.execute(f"PRAGMA table_info({view})")]
        where, args = [], []
        if search.strip():
            matches = [f"CAST({c} AS TEXT) LIKE ?" for c in columns]
            if name == "documents":  # documents' own text too
                matches.append("id IN (SELECT uid FROM documents WHERE pages LIKE ?)")
            where.append("(" + " OR ".join(matches) + ")")
            args += [f"%{search.strip()}%"] * len(matches)
        if patient_uid and "patient_id" in columns:
            where.append("patient_id = ?")
            args.append(patient_uid)
        order = {"patients": "name COLLATE NOCASE", "cottages": "name COLLATE NOCASE", "incidents": "created DESC", "documents": "added DESC", "facts": "document_date DESC, section",
                 "drafts": "created DESC", "activity": "seq DESC", "medications": "patient, name COLLATE NOCASE",
                 "measures": "patient", "follow_ups": "patient"}[name]
        rows = [dict(r) for r in db.execute(f"SELECT * FROM {view} {'WHERE ' + ' AND '.join(where) if where else ''} "
                                            f"ORDER BY {order} LIMIT ?", (*args, limit))]
    # Dates are written as people write them (MM/DD/YYYY), so these are put in date order here rather than as text.
    later = lambda text: fieldtypes.date_key(text) or (9999, 0, 0)
    if name == "medications":  # current ones first, then most recently started
        rows.sort(key=lambda r: (r["status"] != "Active", r["status"] != "Planned", tuple(-x for x in later(r["start_date"]))))
    elif name == "measures":  # newest first
        rows.sort(key=lambda r: tuple(-x for x in later(r["date"])))
    elif name == "follow_ups":  # open ones first, soonest due first
        rows.sort(key=lambda r: (r["status"] == "Done", later(r["due"])))
    elif name == "incidents":  # newest first
        rows.sort(key=lambda r: tuple(-x for x in later(r["date"])))
    elif view == "guardians_view":  # each guardian's town and rough drive time, from their ZIP code
        for r in rows:
            found = zipcodes.place(json.loads(r["details"] or "{}").get("zip"))
            r.update(town=f"{found['town']}, {found['state']}" if found else None, drive_minutes=found and found["minutes"])
    return {"columns": columns, "rows": rows}


def get_record(name, uid):
    """One record with its fields and related lists (a patient's documents, facts, drafts and activity)."""
    with connect() as db:
        row = db.execute(f"SELECT * FROM {'people' if name == 'patients' else name}_view WHERE id = ?", (uid,)).fetchone()
    if not row:
        raise ValueError(f"No {OBJECTS[name]['label'].lower()} with ID {uid}")
    record = {"object": name, "record": dict(row), "related": {}}
    if name in fieldtypes.RECORD_TYPE_FIELD:
        record["custom"], record["ai_sources"] = custom_values(row["details"])
    if name == "patients" and row["record_type"] == "Legal Guardian":
        with connect() as db:
            record["related"]["guardian_of"] = [dict(r) for r in db.execute(
                "SELECT id, name, placement, dob, status FROM patients_view WHERE id IN "
                "(SELECT patient_id FROM guardianships_view WHERE guardian_id = ?) ORDER BY name", (uid,))]
        record["place"] = zipcodes.place(record["custom"].get("zip"))
        log("opened legal guardian", None, uid)
    elif name == "patients":
        with connect() as db:
            record["guardians"] = [{"id": r[0], "name": r[1]} for r in db.execute(
                "SELECT guardian_id, guardian FROM guardianships_view WHERE patient_id = ? ORDER BY guardian", (uid,))]
        for child in ("documents", "facts", "drafts", "activity", "medications", "measures", "follow_ups"):
            record["related"][child] = list_records(child, patient_uid=uid, limit=500)["rows"]
        record["related"]["incidents"] = patient_incidents(uid)
        log("opened patient", internal(name, uid))
    elif name == "incidents":
        with connect() as db:
            record["people"] = incident_people(uid)
    elif name == "cottages":
        with connect() as db:
            residents = [dict(r) for r in db.execute(
                "SELECT id, name, dob, gender, status, json_extract(details, '$.admission_date') AS admitted, documents "
                "FROM patients_view WHERE json_extract(details, '$.cottage') = ? ORDER BY name", (row["name"],))]
            latest = db.execute("SELECT * FROM cottage_analyses WHERE cottage_id = "
                                "(SELECT id FROM cottages WHERE uid = ?) ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
        record["related"]["residents"] = [r for r in residents if r["status"] != "Discharged"]
        record["related"]["past_residents"] = [r for r in residents if r["status"] == "Discharged"]
        record["analysis"] = analysis_dict(latest)
        log("opened cottage", None, f"{uid}: {row['name']}")
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


def patient_incidents(uid):
    """The incidents a patient was involved in, newest first, each naming who else was involved."""
    with connect() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT * FROM incidents_view WHERE id IN (SELECT incident_id FROM incident_people_view WHERE patient_id = ?)", (uid,))]
    later = lambda text: fieldtypes.date_key(text) or (9999, 0, 0)
    return sorted(rows, key=lambda r: tuple(-x for x in later(r["date"])))


def internal(name, uid):
    with connect() as db:
        return internal_id(db, name, uid)


def internal_patient(name, uid):
    with connect() as db:
        return patient_of(db, name, internal_id(db, name, uid))


CREATED_BY_HAND = ("patients", "medications", "measures", "follow_ups", "cottages", "incidents")


def seed_cottages(db):
    """The Children's Village cottages, the first time (ones deleted later don't come back), as the Cottage choices."""
    if not db.execute("SELECT COUNT(*) FROM cottages").fetchone()[0]:
        for name in fieldtypes.COTTAGES:
            insert(db, "cottages", {"name": name, "created": now()})
    sync_cottage_options(db)


def check_cottage_name(name, uid=None):
    with connect() as db:
        if db.execute("SELECT 1 FROM cottages WHERE lower(name) = lower(?) AND uid != ?", (name, uid or "")).fetchone():
            raise ValueError(f"There's already a cottage called {name}")


def sync_cottage_options(db):
    """A patient's Cottage choices are the cottages' names, A to Z."""
    names = sorted((r[0] for r in db.execute("SELECT name FROM cottages WHERE name IS NOT NULL AND name != ''")), key=str.lower)
    db.execute("UPDATE field_defs SET options = ? WHERE object = 'patients' AND name = 'cottage' AND type = 'picklist'",
               (json.dumps(names),))


def person_fields(fields):
    """A person's incoming fields, tidied: a full name becomes first and last names, a placement given as the record
    type (how patients were typed before guardians had records) becomes the placement, and gender is written one way."""
    fields = dict(fields)
    if fields.get("record_type") in fieldtypes.PLACEMENTS:
        fields["placement"] = fields.get("placement") or fields["record_type"]
        fields["record_type"] = "Patient"
    if "gender" in fields:
        fields["gender"] = fieldtypes.normalize_gender(fields["gender"])
    if fields.get("name") and not (fields.get("first_name") or fields.get("last_name")):
        fields["first_name"], fields["last_name"] = fieldtypes.split_name(fields["name"])
    return fields


def person_values(db, values, current=None):
    """Work out a person's full name from first and last."""
    if current is None or "first_name" in values or "last_name" in values:
        first = values.get("first_name", (current or {}).get("first_name"))
        last = values.get("last_name", (current or {}).get("last_name"))
        values["name"] = fieldtypes.full_name(first, last)
        if not values["name"]:
            raise ValueError("Enter a first or last name")


# Fields that link a record to people: {field: (link table, the record's column, the person's column, their record type)}
LINKS = {"guardians": ("guardianships", "patient_id", "guardian_id", "Legal Guardian"),
         "people": ("incident_people", "incident_id", "patient_id", "Patient")}


def set_links(db, field, row_id, uids):
    """Link a record to exactly these people (record IDs). Returns their names before and after."""
    table, own, other, record_type = LINKS[field]
    names = lambda: ", ".join(r[0] for r in db.execute(
        f"SELECT p.name FROM {table} l JOIN patients p ON p.id = l.{other} WHERE l.{own} = ? ORDER BY p.name", (row_id,)))
    before, ids = names(), []
    for uid in dict.fromkeys(uids):
        row = db.execute("SELECT id, record_type FROM patients WHERE uid = ?", (uid,)).fetchone()
        if not row or row["record_type"] != record_type:
            raise ValueError(f"Choose {'legal guardians' if field == 'guardians' else 'patients'} from the list")
        ids.append(row["id"])
    db.execute(f"DELETE FROM {table} WHERE {own} = ?", (row_id,))
    db.executemany(f"INSERT INTO {table} ({own}, {other}) VALUES (?, ?)", [(row_id, i) for i in ids])
    return before, names()


def person_label(record_type):
    return (record_type or "patient").lower()


def create_record(name, fields, parent_uid=None):
    """Create a patient, or a medication, measure or follow-up for the patient parent_uid. Returns the new record ID.
    (Documents come in through uploads and notes, and facts and drafts are made by MindChart.)"""
    if name not in CREATED_BY_HAND:
        raise ValueError(f"New {OBJECTS[name]['plural'].lower()} are added from a patient's page")
    if name == "patients":
        fields = person_fields(fields)
    values = clean_fields(name, fields, creating=True)
    defaults = {"patients": {"record_type": "Patient"}, "medications": {"status": "Active"},
                "follow_ups": {"status": "Open"}}.get(name, {})
    values.update({k: v for k, v in defaults.items() if not values.get(k)})
    if name == "patients":
        if values["record_type"] == "Patient" and not values.get("status"):
            values["status"] = "Active"
        with connect() as db:
            person_values(db, values)
        values["created"] = now()
    links = {field: values.pop(field) for field in LINKS if field in values}
    if name == "cottages":
        values["name"] = " ".join(values["name"].split())
        check_cottage_name(values["name"])
    if name in ("cottages", "incidents"):
        values["created"] = now()
    if name == "measures":
        check_measure(values, fields)
    if name == "medications" and values.get("name"):  # "zoloft" -> "Sertraline", so a medication is listed one way
        values["name"] = fieldtypes.canonical_medication(values["name"])
    if name == "medications" and values.get("frequency"):  # "BID" -> "Twice a day"
        values["frequency"] = fieldtypes.standard_frequency(values["frequency"])
    if name == "medications" and fields.get("source"):  # where a medication suggested from the records was found
        values["source"] = str(fields["source"])[:300]
    type_field = fieldtypes.RECORD_TYPE_FIELD.get(name)
    if type_field:
        # The record type's own fields can arrive grouped under "custom" or (from the command bar) mixed in.
        incoming = {**{k: v for k, v in fields.items() if k not in values and k != "custom"}, **(fields.get("custom") or {})}
        values["custom"] = json.dumps(clean_custom(name, values[type_field], incoming))
    with connect() as db:
        if OBJECTS[name].get("parent"):
            if not parent_uid:
                raise ValueError("Choose the patient this is for")
            values["patient_id"] = internal_id(db, "patients", parent_uid)
        row_id, uid = insert(db, name, values)
        patient_id = row_id if name == "patients" else values.get("patient_id")
        for field, uids in links.items():
            set_links(db, field, row_id, uids)
        involved = [r[0] for r in db.execute("SELECT patient_id FROM incident_people WHERE incident_id = ?", (row_id,))] \
            if name == "incidents" else []
        if name == "cottages":
            sync_cottage_options(db)
    title = values.get(OBJECTS[name]["title_field"]) or ""
    label = person_label(values["record_type"]) if name == "patients" else OBJECTS[name]["label"].lower()
    for person in involved:  # an incident goes in the activity of everyone involved
        log("recorded incident", person, f"{uid}: {title}")
    if not involved:
        log(f"created {label}", patient_id, uid if name == "patients" else f"{uid}: {title}")
    return uid


def check_measure(values, fields):
    """A scale's total must be within its range; vitals get their BMI worked out from height and weight."""
    measure, score = values["measure"], values.get("score")
    highest = fieldtypes.MEASURE_MAX.get(measure)
    if score not in (None, ""):
        try:
            values["score"] = fieldtypes.coerce({"type": "number", "label": "Total score"}, score)
        except ValueError:
            raise ValueError("The total score must be a number")
        if values["score"] < 0 or (highest is not None and values["score"] > highest):
            raise ValueError(f"A {measure} total is between 0 and {highest}")
    elif measure in fieldtypes.MEASURE_MAX:
        raise ValueError("Enter the total score")
    if measure == "Vitals":
        custom = fields.setdefault("custom", {})
        try:
            weight, height = float(custom.get("weight_lb") or 0), float(custom.get("height_in") or 0)
        except ValueError:
            return
        if weight and height and not custom.get("bmi"):
            custom["bmi"] = round(703 * weight / height ** 2, 1)


def update_record(name, uid, fields):
    """Save edited fields. Only values that actually changed are saved and logged."""
    if name == "patients":
        fields = person_fields(fields)
    values = clean_fields(name, fields)
    if name == "medications" and values.get("name"):
        values["name"] = fieldtypes.canonical_medication(values["name"])
    if name == "medications" and values.get("frequency"):
        values["frequency"] = fieldtypes.standard_frequency(values["frequency"])
    if name == "cottages" and values.get("name"):
        values["name"] = " ".join(values["name"].split())
        check_cottage_name(values["name"], uid)
    custom_changes = {}
    with connect() as db:
        row_id = internal_id(db, name, uid)
        current = dict(db.execute(f"SELECT * FROM {name} WHERE id = ?", (row_id,)).fetchone())
        if name == "patients":
            person_values(db, values, current)
    links, link_changes = {field: values.pop(field) for field in LINKS if field in values}, {}
    if name in fieldtypes.RECORD_TYPE_FIELD:
        record_type = values.get(fieldtypes.RECORD_TYPE_FIELD[name]) or current[fieldtypes.RECORD_TYPE_FIELD[name]]
        incoming = {**{k: v for k, v in fields.items() if k not in values and k != "custom"}, **(fields.get("custom") or {})}
        stored = json.loads(current.get("custom") or "{}")
        incoming = {k: v for k, v in incoming.items() if k not in stored or stored[k] != v}  # untouched values aren't re-checked
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
        for field, uids in links.items():
            before, after = set_links(db, field, row_id, uids)
            if before != after:
                link_changes[field] = (before, after)
        if not values and not link_changes:
            return
        if name == "facts":
            values["edited"] = now()
        if values:
            db.execute(f"UPDATE {name} SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?", (*values.values(), row_id))
        if name == "cottages" and "name" in values:  # its residents move with it
            db.execute("UPDATE patients SET custom = json_set(custom, '$.cottage', ?) WHERE json_extract(custom, '$.cottage') = ?",
                       (values["name"], current["name"]))
            sync_cottage_options(db)
        patient_id = patient_of(db, name, row_id)
        if name == "documents" and values.get("document_type"):
            learn_type(db, row_id, values["document_type"])  # a correction teaches MindChart too
            if current.get("read_by_model") and current.get("kind") == "File":
                db.execute("UPDATE documents SET read_state = 'fields' WHERE id = ?", (row_id,))  # fill in the new type's fields
    # The log keeps what each field was and what it became (inside the encrypted store), so any change can be traced.
    labels = {f["name"]: f["label"].lower() for f in OBJECTS[name]["fields"]}
    if custom_changes:
        labels.update({d["name"]: d["label"].lower() for defs in field_definitions()[name].values() for d in defs})
    short = lambda v: fieldtypes.display(v)[:120]
    changed = "; ".join([f"{labels[k]}: {short(current.get(k))} → {short(v)}" for k, v in values.items()
                         if k not in ("edited", "custom")] +
                        [f"{labels[k]}: {short(old)} → {short(new)}" for k, (old, new) in custom_changes.items()] +
                        [f"{labels[k]}: {short(old)} → {short(new)}" for k, (old, new) in link_changes.items()])
    label = person_label(values.get("record_type") or current["record_type"]) if name == "patients" else OBJECTS[name]["label"].lower()
    log(f"edited {label}", patient_id, f"{uid}: {changed}")


def delete_record(name, uid):
    if name == "activity":
        raise ValueError("The activity log can't be changed")
    with connect() as db:
        row_id = internal_id(db, name, uid)
        patient_id = patient_of(db, name, row_id)
        title = db.execute(f"SELECT {OBJECTS[name]['title_field']} FROM {name} WHERE id = ?", (row_id,)).fetchone()[0]
        label = OBJECTS[name]["label"].lower()
        if name == "patients":
            label = person_label(db.execute("SELECT record_type FROM patients WHERE id = ?", (row_id,)).fetchone()[0])
        db.execute(f"DELETE FROM {name} WHERE id = ?", (row_id,))
        if name == "cottages":
            sync_cottage_options(db)  # its residents keep the name, shown as no longer on the list
    # A deleted patient's own ID can't be linked any more, so their name goes in the detail.
    log(f"deleted {label}", None if name == "patients" else patient_id, f"{uid}: {title}")
    if name in ("patients", "documents"):
        with connect() as db:
            db.execute("VACUUM")  # rewrite the database so deleted records don't linger in free space


COTTAGE_SECTIONS = ("risk", "social", "trauma", "substance", "hpi", "legal")  # what bears on how youths get along


def cottage_inputs(uid, facts_each=12, notes_each=2):
    """What the AI reads to describe a cottage's dynamics: each current resident's age, gender, admission, current
    medications and latest scores, their most recent facts in the sections that bear on how youths get along, and
    their latest clinician notes. Everything the AI may cite is numbered."""
    record = get_record("cottages", uid)
    today, residents, facts = datetime.date.today(), [], []
    with connect() as db:
        for r in record["related"]["residents"]:
            patient_id = internal_id(db, "patients", r["id"])
            born = fieldtypes.parse_date(r["dob"])
            age = today.year - born.year - ((today.month, today.day) < (born.month, born.day)) if born else None
            meds = [f"{m[0]} {m[1] or ''}".strip() for m in db.execute(
                "SELECT name, dose FROM medications WHERE patient_id = ? AND status = 'Active' ORDER BY name", (patient_id,))]
            scores = [f"{m[0]} {m[1]} ({m[2]})" for m in db.execute(
                "SELECT measure, score, date FROM measures WHERE patient_id = ? AND measure != 'Vitals' AND score IS NOT NULL "
                "ORDER BY id DESC LIMIT 3", (patient_id,))]
            found = db.execute(
                f"SELECT f.fact, f.date, d.name AS document, d.document_date FROM facts f JOIN documents d ON d.id = f.document_id "
                f"WHERE d.patient_id = ? AND f.section IN ({','.join('?' * len(COTTAGE_SECTIONS))}) ORDER BY d.id DESC, f.id LIMIT ?",
                (patient_id, *COTTAGE_SECTIONS, facts_each)).fetchall()
            notes = db.execute("SELECT name, document_date, pages FROM documents WHERE patient_id = ? AND kind = 'Note' "
                               "ORDER BY id DESC LIMIT ?", (patient_id, notes_each)).fetchall()
            numbers = []
            for f in found:
                facts.append({"resident": r["name"], "resident_id": r["id"], "fact": f["fact"], "document": f["document"],
                              "date": f["date"] or f["document_date"] or ""})
                numbers.append(len(facts))
            for n in notes:
                text = " ".join(p["text"] for p in json.loads(n["pages"] or "[]"))[:500]
                facts.append({"resident": r["name"], "resident_id": r["id"], "fact": f"Clinician note: {text}", "document": n["name"],
                              "date": n["document_date"] or ""})
                numbers.append(len(facts))
            residents.append({"name": r["name"], "id": r["id"], "age": age, "gender": r["gender"] or "", "admitted": r["admitted"] or "",
                              "medications": meds, "scores": scores, "facts": numbers})
    facts += incident_facts([r["id"] for r in record["related"]["residents"]])
    return {"cottage": record["record"]["name"], "residents": residents, "facts": facts}


def incident_facts(patient_uids, involving_several=1, limit=20):
    """Incidents involving these patients as facts the cottage briefings can cite, newest first. involving_several:
    how many of these patients an incident must involve (2 for incidents between residents of different cottages)."""
    found = {}
    for uid in patient_uids:
        for i in patient_incidents(uid):
            found.setdefault(i["id"], {**i, "count": 0})["count"] += 1
    later = lambda text: fieldtypes.date_key(text) or (0, 0, 0)
    chosen = sorted((i for i in found.values() if i["count"] >= involving_several), key=lambda i: later(i["date"]), reverse=True)[:limit]
    return [{"resident": f"Incident{' ' + i['date'] if i['date'] else ''}", "resident_id": i["id"], "document": "Incident report",
             "date": i["date"] or "",
             "fact": " ".join(x for x in (f"Incident ({', '.join(y for y in (i['type'], i['severity']) if y)}):" if i["type"] or i["severity"] else "Incident:",
                                          f"{i['title']}.", i["description"] or "", f"Injuries: {i['injuries']}." if i["injuries"] else "",
                                          f"Involved: {i['people']}." if i["people"] else "") if x)[:900]} for i in chosen]


def intercottage_inputs(facts_each=8):
    """What the AI reads to compare the cottages: each cottage's current residents (age, gender), and the facts and notes
    in which a resident's records name a resident of a different cottage."""
    cottages, everyone, facts = [], [], []
    today = datetime.date.today()
    with connect() as db:
        for c in db.execute("SELECT uid, name FROM cottages ORDER BY name COLLATE NOCASE").fetchall():
            residents = []
            for r in db.execute("SELECT id, name, first_name, dob, gender FROM patients_view WHERE COALESCE(status, '') != 'Discharged' "
                                "AND json_extract(details, '$.cottage') = ? ORDER BY name", (c["name"],)):
                born = fieldtypes.parse_date(r["dob"])
                age = today.year - born.year - ((today.month, today.day) < (born.month, born.day)) if born else None
                residents.append({"name": r["name"], "id": r["id"], "first": (r["first_name"] or r["name"]).split()[0], "age": age,
                                  "gender": r["gender"] or ""})
            if residents:
                cottages.append({"name": c["name"], "residents": residents})
                everyone += [(c["name"], r) for r in residents]
        for cottage, r in everyone:
            others = {o["first"]: (other_cottage, o) for other_cottage, o in everyone if other_cottage != cottage and len(o["first"]) > 2}
            if not others:
                continue
            mentions = re.compile(r"\b(" + "|".join(re.escape(name) for name in others) + r")\b")
            texts = [(f["fact"], f["document"], f["date"]) for f in db.execute(
                "SELECT f.fact, d.name AS document, COALESCE(f.date, d.document_date) AS date FROM facts f "
                "JOIN documents d ON d.id = f.document_id JOIN patients p ON p.id = d.patient_id WHERE p.uid = ?", (r["id"],))]
            texts += [("Clinician note: " + " ".join(page["text"] for page in json.loads(n["pages"] or "[]")), n["name"], n["document_date"])
                      for n in db.execute("SELECT d.name, d.document_date, d.pages FROM documents d JOIN patients p ON p.id = d.patient_id "
                                          "WHERE p.uid = ? AND d.kind = 'Note'", (r["id"],))]
            for text, document, date in [x for x in texts if mentions.search(x[0])][:facts_each]:
                facts.append({"resident": r["name"], "resident_id": r["id"], "fact": text[:600], "document": document, "date": date or ""})
    # Incidents involving residents of two or more different cottages
    cottage_of = {r["id"]: c for c, r in everyone}
    for f in incident_facts(list(cottage_of)):
        involved = {cottage_of[uid] for uid in cottage_of if uid in [x["id"] for x in incident_people(f["resident_id"])]}
        if len(involved) > 1:
            facts.append(f)
    return {"cottages": cottages, "facts": facts}


def incident_people(incident_uid):
    with connect() as db:
        return [{"id": r[0], "name": r[1]} for r in db.execute(
            "SELECT patient_id, patient FROM incident_people_view WHERE incident_id = ? ORDER BY patient", (incident_uid,))]


def save_cottage_analysis(uid, result, model):
    """Keep an analysis of one cottage (uid) or of all of them (uid None). Returns its number, for feedback."""
    with connect() as db:
        cursor = db.execute("INSERT INTO cottage_analyses (cottage_id, created, model, result) VALUES (?, ?, ?, ?)",
                            (internal_id(db, "cottages", uid) if uid else None, now(), model, json.dumps(result)))
    log("analyzed cottage dynamics" if uid else "analyzed dynamics across cottages", None, uid or "")
    return cursor.lastrowid


def analysis_dict(row):
    if not row:
        return None
    return {**json.loads(row["result"]), "id": row["id"], "created": row["created"], "model": row["model"],
            "feedback": json.loads(row["feedback"]) if row["feedback"] else None}


def intercottage_analysis():
    with connect() as db:
        return analysis_dict(db.execute("SELECT * FROM cottage_analyses WHERE cottage_id IS NULL ORDER BY id DESC LIMIT 1").fetchone())


def save_analysis_feedback(analysis_id, rating, comment):
    """The clinician's view of an analysis: "helpful" or "not helpful", and anything to do differently next time."""
    if rating not in ("helpful", "not helpful"):
        raise ValueError("Choose Helpful or Not helpful")
    with connect() as db:
        if not db.execute("SELECT 1 FROM cottage_analyses WHERE id = ?", (analysis_id,)).fetchone():
            raise ValueError("That analysis isn't there any more")
        db.execute("UPDATE cottage_analyses SET feedback = ? WHERE id = ?",
                   (json.dumps({"rating": rating, "comment": " ".join(str(comment or "").split())[:1000], "at": now()}), analysis_id))
    log("gave feedback on a cottage analysis", None, f"{rating}")


def analysis_feedback(limit=6):
    """Recent feedback with a comment, newest first, to guide the next analyses."""
    with connect() as db:
        rows = db.execute("SELECT feedback FROM cottage_analyses WHERE feedback IS NOT NULL ORDER BY id DESC").fetchall()
    found = [json.loads(r[0]) for r in rows]
    return [f for f in found if f.get("comment")][:limit]


def medication_names():
    """The medications to offer when adding one: the catalog, plus any other name already on someone's list,
    each with how many patients are on it now."""
    with connect() as db:
        active = dict(db.execute("SELECT name, COUNT(DISTINCT patient_id) FROM medications WHERE status = 'Active' GROUP BY name"))
        used = [r[0] for r in db.execute("SELECT DISTINCT name FROM medications WHERE name IS NOT NULL AND name != '' ORDER BY name")]
    catalog = fieldtypes.medication_catalog()
    known = {m["name"].lower() for m in catalog}
    added = [{"name": n, "brands": [], "category": "Added in MindChart"} for n in used if n.lower() not in known]
    return [{**m, "patients": active.get(m["name"], 0)} for m in catalog + added]


def patients_for_assistant():
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT uid AS id, name, dob FROM patients WHERE record_type = 'Patient' ORDER BY name")]


# --- Documents, notes and history building ---------------------------------------

def add_document(patient_uid, name, original, pages):
    with connect() as db:
        patient_id = internal_id(db, "patients", patient_uid)
        _, uid = insert(db, "documents", {"patient_id": patient_id, "name": name, "kind": "File", "added": now(),
                                          "original": original, "pages": json.dumps(pages)})
    log("added document", patient_id, f"{uid}: {name}")
    return uid


def add_note(patient_uid, text, date="", author="", title="", document_type="Clinician Note"):
    """A note typed or dictated into MindChart. It's a document like any other, so its facts feed future drafts.
    document_type "Evaluation notes" makes it the clinician's notes for an evaluation being written."""
    if not text.strip():
        raise ValueError("The note is empty")
    date = fieldtypes.normalize_date(date) or datetime.date.today().strftime("%m/%d/%Y")
    with connect() as db:
        patient_id = internal_id(db, "patients", patient_uid)
        _, uid = insert(db, "documents", {
            "patient_id": patient_id, "name": title.strip() or f"{'Evaluation notes' if document_type == fieldtypes.EVALUATION_NOTES else 'Note'} {date}",
            "kind": "Note", "document_type": document_type, "document_date": date, "author": author.strip(), "added": now(),
            "pages": json.dumps([{"page": None, "text": text.strip(), "scanned": False}])})
    log("added note", patient_id, uid)
    return uid


# --- The uploads inbox, and learning document types --------------------------------

def add_upload(name, original, pages, patient_uid, suggestion):
    """A file waiting for review. patient_uid: the patient whose page it was dropped on, if any."""
    with connect() as db:
        uid = new_uid("UP")
        db.execute("INSERT INTO uploads (uid, name, original, pages, added, patient_uid, state, suggestion) "
                   "VALUES (?, ?, ?, ?, ?, ?, 'waiting', ?)",
                   (uid, name, original, json.dumps(pages), now(), patient_uid or None, json.dumps(suggestion)))
        patient = db.execute("SELECT id FROM patients WHERE uid = ?", (patient_uid,)).fetchone() if patient_uid else None
    log("uploaded a file for review", patient[0] if patient else None, f"{uid}: {name}")
    return uid


def page_summary(pages):
    return {"pages": len(pages) if pages and pages[0]["page"] else None,
            "scanned_pages": [p["page"] for p in pages if p.get("scanned")],
            "unclear_pages": [p["page"] for p in pages if p.get("unclear")]}


def list_uploads():
    """Files waiting for review, oldest first, with the start of their text for a preview."""
    with connect() as db:
        rows = db.execute("SELECT uid, name, pages, added, patient_uid, state, suggestion FROM uploads ORDER BY id").fetchall()
    uploads = []
    for r in rows:
        pages = json.loads(r["pages"])
        uploads.append({"id": r["uid"], "name": r["name"], "added": r["added"], "patient_id": r["patient_uid"],
                        "state": r["state"], "suggestion": json.loads(r["suggestion"] or "{}"),
                        "preview": "\n\n".join(p["text"] for p in pages)[:4000], **page_summary(pages)})
    return uploads


def get_upload(uid):
    with connect() as db:
        row = db.execute("SELECT * FROM uploads WHERE uid = ?", (uid,)).fetchone()
    if not row:
        raise ValueError("That upload has already been dealt with")
    return {**dict(row), "pages": json.loads(row["pages"]), "suggestion": json.loads(row["suggestion"] or "{}")}


def next_upload_to_identify():
    with connect() as db:
        row = db.execute("SELECT uid FROM uploads WHERE state = 'waiting' ORDER BY id LIMIT 1").fetchone()
    return get_upload(row[0]) if row else None


def save_suggestion(uid, suggestion, state="ready"):
    with connect() as db:
        db.execute("UPDATE uploads SET suggestion = ?, state = ? WHERE uid = ?", (json.dumps(suggestion), state, uid))


def combine_pages(uploads):
    """The pages of several uploads, in order, numbered as one document's."""
    pages = []
    for upload in uploads:
        pages += [{**p, "page": len(pages) + n} for n, p in enumerate(upload["pages"], 1)]
    return pages


def combine_uploads(uids, name, original, pages, patient_uid, suggestion):
    """Uploads that are pages of one document (photos of handwritten notes, say) replaced by one upload of them all,
    which is identified again as a whole. original: the files joined into one PDF. Returns the new upload's ID."""
    if len(uids) < 2:
        raise ValueError("Choose at least two files to combine")
    uid = add_upload(name, original, pages, patient_uid, suggestion)
    with connect() as db:
        names = [r[0] for r in db.execute(f"SELECT name FROM uploads WHERE uid IN ({','.join('?' * len(uids))})", uids)]
        db.executemany("DELETE FROM uploads WHERE uid = ?", [(old,) for old in uids])
    log("combined uploads into one document", None, f"{uid}: {', '.join(names)}")
    with connect() as db:
        db.execute("VACUUM")  # so the separate files don't linger in free space
    return uid


def discard_upload(uid):
    with connect() as db:
        row = db.execute("SELECT name FROM uploads WHERE uid = ?", (uid,)).fetchone()
        if not row:
            return
        db.execute("DELETE FROM uploads WHERE uid = ?", (uid,))
    log("discarded an upload", None, f"{uid}: {row[0]}")
    with connect() as db:
        db.execute("VACUUM")  # so the discarded file doesn't linger in free space


def confirm_upload(uid, choice):
    """File an upload under a patient as the type the clinician confirmed. Returns (document ID, patient ID).

    choice: {"patient_id"} or {"new_patient": {fields}}, plus "document_type", "document_date", "author", and
    "pull": {"facts": bool, "fields": [names]}, what to take from the document. The document is then read in the
    background, the choice of what to pull is remembered for that type, and the confirmed type becomes an example.
    """
    upload = get_upload(uid)
    document_type = choice.get("document_type") or ""
    if document_type not in record_type_names("documents"):
        raise ValueError("Choose the document's type")
    patient_uid = choice.get("patient_id")
    if choice.get("new_patient"):
        patient_uid = create_record("patients", choice["new_patient"])
    if not patient_uid:
        raise ValueError("Choose which patient this document belongs to")
    pull = choice.get("pull") or {"facts": True, "fields": None}
    pull = {"facts": bool(pull.get("facts")), "fields": [str(f) for f in pull["fields"]] if pull.get("fields") is not None else None}
    wants_reading = pull["facts"] or pull["fields"] is None or bool(pull["fields"])
    with connect() as db:
        patient_id = internal_id(db, "patients", patient_uid)
        row_id, doc_uid = insert(db, "documents", {
            "patient_id": patient_id, "name": upload["name"], "kind": "File", "document_type": document_type,
            "document_date": fieldtypes.normalize_date(choice.get("document_date")), "author": str(choice.get("author") or "").strip(),
            "added": now(), "original": upload["original"], "pages": json.dumps(upload["pages"]),
            "read_state": "queued" if wants_reading else None, "pull": json.dumps(pull)})
        learn_type(db, row_id, document_type)
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                   (f"pull.{document_type}", json.dumps(pull)))
        db.execute("DELETE FROM uploads WHERE uid = ?", (uid,))
    log("added document", patient_id, f"{doc_uid}: {upload['name']} ({document_type})")
    return doc_uid, patient_uid


def pull_preferences():
    """What the clinician last chose to take from each type of document: {type: {"facts", "fields"}}."""
    with connect() as db:
        return {r[0][5:]: json.loads(r[1]) for r in db.execute("SELECT key, value FROM settings WHERE key LIKE 'pull.%'")}


def learn_type(db, document_id, document_type):
    """Keep (or correct) a document's confirmed type, with its opening text, as an example to learn from."""
    row = db.execute("SELECT name, pages FROM documents WHERE id = ?", (document_id,)).fetchone()
    text = "\n".join(p["text"] for p in json.loads(row["pages"] or "[]"))[:EXAMPLE_CHARS]
    db.execute("INSERT INTO type_examples (document_id, document_type, name, text, added) VALUES (?, ?, ?, ?, ?) "
               "ON CONFLICT(document_id) DO UPDATE SET document_type = excluded.document_type",
               (document_id, document_type, row["name"], text, now()))


def type_examples():
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT document_type, name, text FROM type_examples ORDER BY id")]


def patients_for_matching():
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT id, name, dob, mrn, guardian FROM patients_view ORDER BY name")]


def next_document_to_read():
    """The next confirmed document waiting to be read in the background, ready for history.read_document."""
    with connect() as db:
        row = db.execute("SELECT d.*, p.uid AS patient_uid FROM documents d JOIN patients p ON p.id = d.patient_id "
                         "WHERE d.read_state IN ('queued', 'fields') ORDER BY d.id LIMIT 1").fetchone()
    if not row:
        return None
    return {"id": row["id"], "uid": row["uid"], "name": row["name"], "pages": json.loads(row["pages"]),
            "fields_only": row["read_state"] == "fields", "pull": json.loads(row["pull"] or "null"),
            "info": {"document_type": row["document_type"] or "Other", "document_date": row["document_date"] or "",
                     "author": row["author"] or ""}}


def mark_unreadable(doc_id):
    with connect() as db:
        db.execute("UPDATE documents SET read_state = 'failed' WHERE id = ?", (doc_id,))


def work_counts():
    with connect() as db:
        return {"uploads": db.execute("SELECT COUNT(*) FROM uploads").fetchone()[0],
                "identifying": db.execute("SELECT COUNT(*) FROM uploads WHERE state = 'waiting'").fetchone()[0],
                "to_read": db.execute("SELECT COUNT(*) FROM documents WHERE read_state IN ('queued', 'fields')").fetchone()[0]}


def onboarding():
    """The getting-started checklist: which of MindChart's main features have been tried so far."""
    with connect() as db:
        count = lambda sql, *args: db.execute(sql, args).fetchone()[0]
        did = lambda action: count("SELECT COUNT(*) FROM activity WHERE action LIKE ?", action) > 0
        steps = {"patient": count("SELECT COUNT(*) FROM patients WHERE record_type = 'Patient'") > 0,
                 "upload": count("SELECT COUNT(*) FROM documents WHERE kind = 'File'") > 0,
                 "question": did("asked a question"),
                 "note": count("SELECT COUNT(*) FROM documents WHERE kind = 'Note'") > 0,
                 "draft": count("SELECT COUNT(*) FROM drafts") > 0,
                 "edit": did("edited %"),
                 "export": did("exported a draft to Word"),
                 "lock": did("locked")}
        hidden = db.execute("SELECT value FROM settings WHERE key = 'checklist'").fetchone()
    return {"steps": steps, "hidden": bool(hidden and hidden[0] == "hidden")}


# --- Coming up, the timeline, and medications from the records ---------------------

def coming_up(days=45):
    """What's due soon (or overdue) across all patients, soonest first: open follow-ups, court orders expiring, IEP
    annual reviews, planned discharges, and medications still waiting for consent (which have no date, so come last)."""
    today = datetime.date.today()
    horizon, recent = today + datetime.timedelta(days=days), today - datetime.timedelta(days=30)
    items = []

    def add(date, row, text, kind, record_id, follow_up=False):
        items.append({"date": date.isoformat() if date else "", "when": date.strftime("%m/%d/%Y") if date else "",
                      "days": (date - today).days if date else None, "patient": row["patient"],
                      "patient_id": row["patient_id"], "text": text, "kind": kind, "id": record_id, "follow_up": follow_up})

    with connect() as db:
        for r in db.execute("SELECT * FROM follow_ups_view WHERE status = 'Open'"):
            date = fieldtypes.parse_date(r["due"])
            if date is None or date <= horizon:
                add(date, r, r["title"], r["category"] or "Follow-up", r["id"], follow_up=True)
        for r in db.execute("SELECT * FROM documents_view WHERE document_type IN ('Court Order', 'Individualized Education Program (IEP)')"):
            custom = json.loads(r["details"] or "{}")
            if r["document_type"] == "Court Order":
                date, text, kind = fieldtypes.parse_date(custom.get("expires")), "Court order expires", "Court"
            else:
                meeting = fieldtypes.parse_date(custom.get("meeting_date"))
                date, text, kind = (meeting.replace(year=meeting.year + 1) if meeting and not (meeting.month == 2 and meeting.day == 29)
                                    else None), "IEP annual review due (a year after the last meeting)", "School / IEP"
            if date and recent <= date <= horizon:
                add(date, r, text, kind, r["id"])
        for r in db.execute("SELECT p.uid AS patient_id, p.name AS patient, p.custom FROM patients p WHERE p.status = 'Active'"):
            custom = json.loads(r["custom"] or "{}")
            for key in ("expected_discharge", "planned_discharge"):
                date = fieldtypes.parse_date(custom.get(key))
                if date and recent <= date <= horizon:
                    add(date, r, "Planned discharge", "Discharge", r["patient_id"])
        for r in db.execute("SELECT * FROM medications_view WHERE status IN ('Active', 'Planned') "
                            "AND consent IN ('Requested', 'Not yet requested')"):
            add(None, r, f"Consent {'requested' if r['consent'] == 'Requested' else 'needed'} for {r['name']}", "Consent", r["id"])
    return sorted(items, key=lambda i: (i["date"] == "", i["date"]))


def timeline(patient_uid):
    """A patient's history in date order: dated facts from their documents (verified ones only), documents,
    medications started and stopped, measures, follow-ups and admission. Partial dates ("March 2019") count."""
    events = []

    def add(when, kind, text, record_id, source="", section=""):
        key = fieldtypes.date_key(when)
        if key:
            events.append({"key": key, "when": when, "kind": kind, "text": text, "id": record_id, "source": source,
                           "section": section})

    with connect() as db:
        patient = db.execute("SELECT * FROM patients WHERE uid = ?", (patient_uid,)).fetchone()
        if not patient:
            raise ValueError(f"No patient with ID {patient_uid}")
        add(patient["dob"], "birth", "Born", patient_uid)
        custom = json.loads(patient["custom"] or "{}")
        admitted = f"Admitted ({patient['placement']})" if patient["placement"] else "Admitted"
        for key, text in (("admission_date", admitted), ("placement_date", "Placed in foster home")):
            add(custom.get(key), "placement", text, patient_uid)
        for f in db.execute("""SELECT f.uid, f.fact, f.date, f.section, d.name AS document FROM facts f
                               JOIN documents d ON d.id = f.document_id WHERE d.patient_id = ? AND f.verified = 1""", (patient["id"],)):
            add(f["date"], "fact", f["fact"], f["uid"], f["document"], f["section"])
        for d in db.execute("SELECT uid, name, document_type, document_date FROM documents WHERE patient_id = ?", (patient["id"],)):
            add(d["document_date"], "document", f"{d['document_type'] or 'Document'}: {d['name']}", d["uid"])
        for m in db.execute("SELECT * FROM medications WHERE patient_id = ?", (patient["id"],)):
            dose = " ".join(x for x in (m["dose"], m["frequency"]) if x)
            add(m["start_date"], "medication", f"Started {m['name']}{' ' + dose if dose else ''}", m["uid"])
            add(m["stop_date"], "medication", f"Stopped {m['name']}{': ' + m['stop_reason'] if m['stop_reason'] else ''}", m["uid"])
        for m in db.execute("SELECT * FROM measures WHERE patient_id = ?", (patient["id"],)):
            band = fieldtypes.measure_band(m["measure"], m["score"])
            score = f" {fieldtypes.display(m['score'])}" if m["score"] not in (None, "") else ""
            add(m["date"], "measure", f"{m['measure']}{score}{' (' + band + ')' if band else ''}", m["uid"])
        for f in db.execute("SELECT * FROM follow_ups WHERE patient_id = ?", (patient["id"],)):
            add(f["due"], "follow_up", f"{'Done' if f['status'] == 'Done' else 'Due'}: {f['title']}", f["uid"])
    for i in patient_incidents(patient_uid):
        others = ", ".join(n for n in (i["people"] or "").split(", ") if n and n != patient["name"])
        add(i["date"], "incident", f"Incident: {i['title']}{' (with ' + others + ')' if others else ''}", i["id"])
    seen, unique = set(), []
    for e in sorted(events, key=lambda e: e["key"]):
        signature = (e["key"], e["text"].lower().strip(" ."))  # the same fact read from two documents
        if signature not in seen:
            seen.add(signature)
            unique.append(e)
    return unique


def medication_facts(patient_uid):
    """The verified facts about medications in a patient's documents, for suggesting their medication list."""
    with connect() as db:
        return [dict(r) for r in db.execute(
            """SELECT f.fact, f.date, f.quote, d.name AS document, d.document_type, d.document_date FROM facts f
               JOIN documents d ON d.id = f.document_id JOIN patients p ON p.id = d.patient_id
               WHERE p.uid = ? AND f.section = 'medications' AND f.verified = 1 ORDER BY d.id, f.id""", (patient_uid,))]


def evaluation_header(patient_uid):
    """Who was evaluated, when and by whom, for the top of an evaluation: from the patient's record, not from the AI."""
    with connect() as db:
        p = db.execute("SELECT * FROM people_view WHERE id = ?", (patient_uid,)).fetchone()
        guardians = [r[0] for r in db.execute("SELECT guardian FROM guardianships_view WHERE patient_id = ? ORDER BY guardian", (patient_uid,))]
    if not p:
        raise ValueError(f"No patient with ID {patient_uid}")
    custom, today, born = json.loads(p["details"] or "{}"), datetime.date.today(), fieldtypes.parse_date(p["dob"])
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day)) if born else None
    rows = [("Patient", p["name"]),
            ("Date of birth", f"{p['dob']}{f' ({age} years old on the date of evaluation)' if age is not None else ''}" if p["dob"] else ""),
            ("Gender", p["gender"] or ""),
            ("Placement", ", ".join(x for x in (p["placement"], f"{custom['cottage']} cottage" if custom.get("cottage") else "") if x)),
            ("Legal guardians" if len(guardians) > 1 else "Legal guardian", ", ".join(guardians) or p["guardian"] or ""),
            ("Date of evaluation", today.strftime("%m/%d/%Y")), ("Evaluator", get_setting("user_name") or "")]
    return [(label, value) for label, value in rows if value]


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
            if r["pull"]:
                doc["pull"] = json.loads(r["pull"])
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

    facts: None leaves the facts as they are (when only the record type's fields were filled in).
    filled: the record type's fields the AI filled in, {name: {"value", "quote", "page"}}. They never overwrite
    a value a person entered or edited; only empty fields, or ones the AI filled before, are updated.
    """
    with connect() as db:
        row = db.execute("SELECT custom FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if not row:
            return  # deleted while it was being read
        stored = json.loads(row[0] or "{}")
        sources = stored.setdefault("_ai", {})
        for key, item in (filled or {}).items():
            if stored.get(key) in (None, "", [], False) or key in sources:
                stored[key] = item["value"]
                sources[key] = {"quote": item["quote"], "page": item.get("page")}
        db.execute("UPDATE documents SET document_type = ?, document_date = ?, author = ?, read_by_model = ?, read_at = ?, "
                   "custom = ?, read_state = 'read' WHERE id = ?", (info.get("document_type"), info.get("document_date"),
                                                                     info.get("author"), model, now(), json.dumps(stored), doc_id))
        if facts is None:
            return
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


def first_statement(sql):
    """The SQL statement in sql, without its semicolon. Stray symbols after it (models sometimes add "; }") are
    ignored, but a second statement is refused."""
    for i, character in enumerate(sql):
        if character == ";" and sqlite3.complete_statement(sql[:i + 1]):  # a semicolon outside quotes ends it
            if re.search(r"[A-Za-z]", sql[i + 1:]):
                raise ValueError("Ask one thing at a time: only one query can run")
            return sql[:i]
    return sql


def run_query(sql, limit=500):
    """Run a read-only SELECT for the assistant. The connection can't change anything, and long queries are stopped."""
    statement = first_statement(sql.strip()).strip()
    if not statement.lower().startswith(("select", "with")):
        raise ValueError("Only questions that read data are allowed")
    with connect(read_only=True) as db:
        started = time.time()
        db.set_progress_handler(lambda: 1 if time.time() - started > 5 else 0, 10000)  # stop after 5 seconds
        try:
            cursor = db.execute(statement)  # sqlite refuses more than one statement here
            columns = [d[0] for d in cursor.description]
            rows = [[("(file)" if isinstance(v, bytes) else v) for v in row] for row in cursor.fetchmany(limit)]
        except (sqlite3.Error, sqlite3.Warning) as error:  # a Warning: more than one statement
            raise ValueError(f"The query didn't work: {error}")
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


def upgrade_to_v4(db):
    """Version 4: the uploads inbox, learning document types, types added in the app, and background reading."""
    columns = {r[1] for r in db.execute("PRAGMA table_info(documents)")}
    if columns:  # a table that doesn't exist yet is created with these columns
        for column in ("read_state", "pull"):
            if column not in columns:
                db.execute(f"ALTER TABLE documents ADD COLUMN {column} TEXT")
        db.execute("UPDATE documents SET read_state = 'read' WHERE read_by_model IS NOT NULL")
    for table in (RECORD_TYPES_TABLE, UPLOADS_TABLE, EXAMPLES_TABLE):
        db.execute(table)
    seed_record_types(db)


MIGRATIONS[4] = upgrade_to_v4


def upgrade_to_v5(db):
    """Version 5: medications, measures (rating scales and vitals) and follow-ups."""
    for name in ("medications", "measures", "follow_ups"):
        db.execute(table_sql(name))
    seed_record_types(db)


MIGRATIONS[5] = upgrade_to_v5


def upgrade_to_v6(db):
    """Version 6: people. Patients and legal guardians become two record types of one object. A patient's placement,
    which used to be their record type, becomes a field; names split into first and last; a patient links to their
    guardian's record; and gender is Male, Female or X."""
    columns = {r[1] for r in db.execute("PRAGMA table_info(patients)")}
    for column in ("first_name", "last_name", "placement", "guardian_uid"):
        if column not in columns:  # (the very first version had a "placement" column; version 3 moved it to "cottage")
            db.execute(f"ALTER TABLE patients ADD COLUMN {column} TEXT")
    for r in db.execute("SELECT id, name, record_type, gender FROM patients").fetchall():
        first, last = fieldtypes.split_name(r["name"])
        gender = fieldtypes.normalize_gender(r["gender"])
        db.execute("UPDATE patients SET first_name = ?, last_name = ?, record_type = 'Patient', placement = ?, gender = ? "
                   "WHERE id = ?", (first, last, r["record_type"] if r["record_type"] in fieldtypes.PLACEMENTS else "",
                                    gender if gender in fieldtypes.GENDERS or not gender else "X", r["id"]))
    # The placements' own fields all belong to the Patient type now (the page shows the ones for the placement)
    db.execute(RECORD_TYPES_TABLE)
    db.execute(FIELD_DEFS_TABLE)
    db.execute("DELETE FROM record_types WHERE object = 'patients'")  # Patient and Legal Guardian are added after this
    db.execute("UPDATE OR IGNORE field_defs SET record_type = 'Patient' WHERE object = 'patients'")
    db.execute("DELETE FROM field_defs WHERE object = 'patients' AND record_type != 'Patient'")


MIGRATIONS[6] = upgrade_to_v6


def upgrade_to_v7(db):
    """Version 7: a patient can have several legal guardians (and a guardian several patients), so the links move to
    a table of their own. And dates typed another way (such as "12271994") are written MM/DD/YYYY, like dates picked
    from the calendar; dates written only partly (such as "2019", as records often put them) stay as they are.
    Medications typed by a brand name ("zoloft") take the name the medication list uses ("Sertraline"). And a patient's
    cottage becomes a choice from the Children's Village cottages: typed names that match one take its spelling, and
    any others stay as typed until someone picks one."""
    db.execute(GUARDIANSHIPS_TABLE)
    if {r[1] for r in db.execute("PRAGMA table_info(medications)")}:
        for r in db.execute("SELECT id, name FROM medications").fetchall():
            if r["name"] and fieldtypes.canonical_medication(r["name"]) != r["name"]:
                db.execute("UPDATE medications SET name = ? WHERE id = ?", (fieldtypes.canonical_medication(r["name"]), r["id"]))
    if "guardian_uid" in {r[1] for r in db.execute("PRAGMA table_info(patients)")}:
        db.execute("INSERT OR IGNORE INTO guardianships (patient_id, guardian_id) SELECT p.id, g.id FROM patients p "
                   "JOIN patients g ON g.uid = p.guardian_uid AND g.record_type = 'Legal Guardian'")
        # Those patients' guardian names came from the link, which the table now holds
        db.execute("UPDATE patients SET guardian = '' WHERE COALESCE(guardian_uid, '') != ''")
    db.execute("UPDATE field_defs SET type = 'picklist', options = ? WHERE object = 'patients' AND name = 'cottage' AND type = 'text'",
               (json.dumps(fieldtypes.COTTAGES),))
    spelling = {c.lower(): c for c in fieldtypes.COTTAGES}
    for r in db.execute("SELECT id, custom FROM patients WHERE custom LIKE '%\"cottage\"%'").fetchall():
        custom = json.loads(r["custom"])
        typed = str(custom.get("cottage") or "").strip()
        if spelling.get(typed.lower(), typed) != custom.get("cottage"):
            custom["cottage"] = spelling.get(typed.lower(), typed)
            db.execute("UPDATE patients SET custom = ? WHERE id = ?", (json.dumps(custom), r["id"]))
    custom_dates = {}
    for r in db.execute("SELECT object, name FROM field_defs WHERE type = 'date'"):
        custom_dates.setdefault(r["object"], set()).add(r["name"])
    for table, spec in OBJECTS.items():
        columns = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        dates = [f["name"] for f in spec["fields"] if f["type"] == "date" and f["name"] in columns]
        own = custom_dates.get(table, set()) if "custom" in columns else set()
        if not dates and not own:
            continue
        for row in db.execute(f"SELECT {', '.join(['id', *dates, *(['custom'] if own else [])])} FROM {table}").fetchall():
            changes = {c: fieldtypes.normalize_date(row[c]) for c in dates if row[c] and fieldtypes.normalize_date(row[c]) != row[c]}
            custom = json.loads(row["custom"] or "{}") if own else {}
            tidied = {k: fieldtypes.normalize_date(v) for k, v in custom.items() if k in own and isinstance(v, str) and v}
            if any(custom[k] != v for k, v in tidied.items()):
                changes["custom"] = json.dumps({**custom, **tidied})
            if changes:
                db.execute(f"UPDATE {table} SET {', '.join(f'{c} = ?' for c in changes)} WHERE id = ?", (*changes.values(), row["id"]))


MIGRATIONS[7] = upgrade_to_v7


def upgrade_to_v8(db):
    """Version 8: incidents (their tables are created after this), and how often a medication is taken written the
    standard way ("BID" and "twice daily" become "Twice a day"); anything else stays as written."""
    if {r[1] for r in db.execute("PRAGMA table_info(medications)")}:
        for r in db.execute("SELECT id, frequency FROM medications WHERE COALESCE(frequency, '') != ''").fetchall():
            if fieldtypes.standard_frequency(r["frequency"]) != r["frequency"]:
                db.execute("UPDATE medications SET frequency = ? WHERE id = ?", (fieldtypes.standard_frequency(r["frequency"]), r["id"]))


MIGRATIONS[8] = upgrade_to_v8
