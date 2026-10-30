"""The command bar: ask questions about the data, or log new records, in plain English.

Questions are turned into a read-only database query (it can't change anything) and the
results are shown as a table. Requests to log something (a new patient, a note, a change
to a patient's details) come back as a proposal that the person reviews and confirms;
nothing is saved by the model on its own.
"""
import datetime
import json
import re

import history
import vault

PATIENT_FIELDS = [f["name"] for f in vault.OBJECTS["patients"]["fields"] if f.get("edit", True)]

SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["question", "new_patient", "add_note", "update_patient", "unclear"]},
        "sql": {"type": "string", "maxLength": 1500},
        "explanation": {"type": "string", "maxLength": 400},
        "patient": {"type": "string", "maxLength": 120},
        "patient_fields": {
            "type": "object",
            "properties": {name: {"type": "string", "maxLength": 500} for name in PATIENT_FIELDS},
            "required": PATIENT_FIELDS,
        },
        "note_text": {"type": "string", "maxLength": 4000},
        "note_date": {"type": "string", "maxLength": 30},
    },
    "required": ["intent", "sql", "explanation", "patient", "patient_fields", "note_text", "note_date"],
}

PROMPT = """You are the assistant inside {app}, a private, offline app a psychiatric nurse practitioner uses to keep
patient records for child and adolescent psychiatric evaluations. Today is {today}.

Decide what the person wants:
- "question": they want information from the records. Write ONE SQLite SELECT statement in "sql" using only the
  views below. To filter by a patient, use their record ID from the list at the end (patient_id = 'PT-...'),
  never their name. Match other text with LIKE and % wildcards (it ignores case). Add LIMIT 200. Never change data.
- "new_patient": they want to add a patient. Fill "patient_fields" with what they gave (name is required).
- "add_note": they're giving information about a patient to record (an observation, event, medication change,
  phone call, incident). Put the patient's ID or name in "patient". Write "note_text" as a clear clinical note in
  the third person keeping every detail they gave and adding nothing. "note_date" is the date they mention, else "".
- "update_patient": they want to change a patient's details (status, placement, guardian, admission date, etc.).
  Put the patient in "patient" and only the changed values in "patient_fields".
- "unclear": you can't tell. Say what you need in "explanation".
Use "" for every field that doesn't apply. "explanation" is one short plain-English sentence for the person,
describing what you looked up or what you propose to save.

Views you can query (each "id" is a record ID like PT-7K3M9Q; "patient_id" is the patient's record ID):
{views}

What the columns mean: facts_view.section is the history section a fact belongs to (values below);
documents_view.document_type is the kind of record (evaluation, IEP, court order...); facts_view.fact is the fact's text.
To find what the records say about a topic, search facts_view.fact with LIKE.

Examples (with PT-XXXXXX standing for the patient's real record ID from the list below):
- "What documents does Sam have?" -> SELECT id, name, document_type, document_date FROM documents_view WHERE patient_id = 'PT-XXXXXX' LIMIT 200
- "Sam's medications" -> SELECT fact, date, document FROM facts_view WHERE patient_id = 'PT-XXXXXX' AND section = 'medications' LIMIT 200
- "Who has an order of protection?" -> SELECT DISTINCT patient, patient_id, fact FROM facts_view WHERE fact LIKE '%order of protection%' LIMIT 200
- "How many active patients are there?" -> SELECT COUNT(*) AS active_patients FROM patients_view WHERE status = 'Active'
- "Facts that aren't verified" -> SELECT patient, section, fact FROM facts_view WHERE verified = 0 LIMIT 200

Fact "section" values: {sections}
Patient "status" values: Active, Pending, Discharged. Fact "verified" is 1 or 0.

Patients on file (ID, name, date of birth):
{patients}"""


def view_columns():
    with vault.connect(read_only=True) as db:
        return {
            name: [row[1] for row in db.execute(f"PRAGMA table_info({name}_view)")]
            for name in vault.OBJECTS
        }


def build_prompt(app_name):
    views = "\n".join(f"- {name}_view({', '.join(columns)})" for name, columns in view_columns().items())
    patients = "\n".join(f"- {p['id']}: {p['name']}{' (DOB ' + p['dob'] + ')' if p['dob'] else ''}"
                         for p in vault.patients_for_assistant()) or "(none yet)"
    return PROMPT.format(app=app_name, today=datetime.date.today().strftime("%m/%d/%Y"), views=views,
                         sections=", ".join(vault.SECTIONS), patients=patients)


def find_patient(reference):
    """Match a patient by record ID or (part of) their name. Returns the list of matches."""
    reference = reference.strip()
    if not reference:
        return []
    with vault.connect(read_only=True) as db:
        exact = db.execute("SELECT uid AS id, name, dob FROM patients WHERE uid = ? COLLATE NOCASE", (reference,)).fetchall()
        if exact:
            return [dict(r) for r in exact]
        return [dict(r) for r in db.execute("SELECT uid AS id, name, dob FROM patients WHERE name LIKE ? ORDER BY name",
                                            (f"%{reference}%",))]


def empty_reply():
    return {"intent": "unclear", "sql": "", "explanation": "", "patient": "", "note_text": "", "note_date": "",
            "patient_fields": {name: "" for name in PATIENT_FIELDS}}


def ask_assistant(model, thinking, system, message):
    """Ask the model, coping with replies that come back as plain text instead of the JSON asked for."""
    try:
        return history.ask(model, thinking, system, message, SCHEMA, max_tokens=3000)
    except history.BadReply as bad:
        # Models sometimes answer a question with just the SQL; that's still usable.
        sql = re.search(r"```(?:sql)?\s*((?:SELECT|WITH)\b[\s\S]+?)```", bad.text, re.I) or \
            re.search(r"^\s*((?:SELECT|WITH)\b[\s\S]+)$", bad.text, re.I)
        if sql:
            return {**empty_reply(), "intent": "question", "sql": sql.group(1).strip(), "explanation": "Here's what I found."}
        return history.ask(model, thinking, system, message, SCHEMA, max_tokens=3000, history=[(
            bad.text, "Reply with only the JSON object described in your instructions, with every field filled in.")])


def handle(message, model, thinking, app_name):
    """Answer a question, or return a proposal to confirm. Never saves anything itself."""
    system = build_prompt(app_name)
    reply = {**empty_reply(), **ask_assistant(model, thinking, system, message)}
    intent, explanation = reply["intent"], reply["explanation"]
    if intent not in ("new_patient", "add_note", "update_patient") and reply["sql"].strip().lower().startswith(("select", "with")):
        intent = "question"  # models sometimes label a plain question "unclear" while still writing the right query

    if intent == "question":
        try:
            result = vault.run_query(reply["sql"])
        except Exception as error:  # let the model fix its query once
            reply = history.ask(model, thinking, system, message, SCHEMA, max_tokens=3000, history=[(
                json.dumps(reply), f"That query failed with: {error}. Write a corrected SELECT using only the views listed.")])
            result = vault.run_query(reply["sql"])
            explanation = reply["explanation"]
        if not result["rows"] and re.search(r"(name|patient|document|fact|detail)\s*=\s*'", reply["sql"], re.I):
            # An exact text match that found nothing usually means the wording differs slightly; try once more.
            retry = history.ask(model, thinking, system, message, SCHEMA, max_tokens=3000, history=[(
                json.dumps(reply), "That query found no rows because it matched text exactly. Filter patients by their "
                                   "record ID from the list, and match other text with LIKE '%...%'.")])
            if retry.get("intent") == "question" and retry.get("sql"):
                result, reply, explanation = vault.run_query(retry["sql"]), {**reply, **retry}, retry.get("explanation") or explanation
        vault.log("asked a question", None, message[:200])
        return {"kind": "answer", "explanation": explanation, "sql": reply["sql"], **result}

    fields = {k: v.strip() for k, v in reply["patient_fields"].items() if v and v.strip()}
    if intent == "new_patient":
        if not fields.get("name"):
            return {"kind": "message", "text": "What's the new patient's name?"}
        return {"kind": "proposal", "action": "new_patient", "summary": explanation, "fields": fields}

    if intent in ("add_note", "update_patient"):
        matches = find_patient(reply["patient"])
        if not matches:
            return {"kind": "message", "text": f"I couldn't find a patient matching \"{reply['patient']}\". "
                                               "Check the name, or use their record ID (like PT-7K3M9Q)."}
        if len(matches) > 1:
            return {"kind": "message", "text": "More than one patient matches: " +
                    "; ".join(f"{m['name']} ({m['id']})" for m in matches) + ". Which one did you mean?"}
        patient = matches[0]
        if intent == "add_note":
            if not reply["note_text"].strip():
                return {"kind": "message", "text": "What should the note say?"}
            return {"kind": "proposal", "action": "add_note", "summary": explanation, "patient": patient,
                    "fields": {"text": reply["note_text"].strip(), "date": reply["note_date"].strip()}}
        if fields.get("name") == patient["name"]:  # the model often repeats the name; it isn't a change
            del fields["name"]
        if not fields:
            return {"kind": "message", "text": "What should change for " + patient["name"] + "?"}
        return {"kind": "proposal", "action": "update_patient", "summary": explanation, "patient": patient, "fields": fields}

    return {"kind": "message", "text": explanation or "Sorry, I didn't understand. Try asking a question or describing what to log."}


def confirm(proposal):
    """Save a proposal the person has reviewed (and possibly edited)."""
    action, fields = proposal["action"], proposal["fields"]
    if action == "new_patient":
        uid = vault.create_record("patients", fields)
        return {"kind": "done", "object": "patients", "id": uid, "text": f"Added {fields.get('name')} ({uid})."}
    patient_uid = proposal["patient"]["id"]
    if action == "add_note":
        uid = vault.add_note(patient_uid, fields.get("text", ""), fields.get("date", ""))
        return {"kind": "done", "object": "documents", "id": uid,
                "text": f"Saved the note to {proposal['patient']['name']}'s record ({uid})."}
    if action == "update_patient":
        vault.update_record("patients", patient_uid, fields)
        return {"kind": "done", "object": "patients", "id": patient_uid, "text": f"Updated {proposal['patient']['name']}."}
    raise ValueError("Unknown action")
