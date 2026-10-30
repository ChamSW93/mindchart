"""Record types and their fields: the defaults, checking values, and changing a field's type later.

Every object has shared fields (defined in vault.OBJECTS). Patients and documents also have record types,
and each record type adds its own fields, defined below. The definitions are copied into the database
(the field_defs table) the first time MindChart opens, and are read from there afterwards. That means a
field's type or picklist options can be changed later, existing values are converted (see convert()),
and no new version of MindChart is needed. Values live in each record's "custom" column as JSON, keyed
by field name, so changing a type never changes the database's layout.
"""
import re

FIELD_TYPES = ["text", "longtext", "number", "date", "datetime", "checkbox", "picklist", "multipicklist"]

# The field that holds each object's record type.
RECORD_TYPE_FIELD = {"patients": "record_type", "documents": "document_type"}


def field(name, label, type="text", options=None, ai=False):
    """One field definition. ai=True means the AI fills it in when it reads a document (backed by a quote)."""
    return {"name": name, "label": label, "type": type, "options": options or [], "ai": ai}


def ai(name, label, type="text", options=None):
    return field(name, label, type, options, ai=True)


DEFAULT_RECORD_TYPES = {
    "patients": {
        "Residential treatment": [
            field("cottage", "Cottage / unit"),
            field("admission_date", "Admission date", "date"),
            field("expected_discharge", "Expected discharge", "date"),
        ],
        "Crisis / stabilization": [
            field("program", "Program", "picklist", ["Crisis residence", "21-day stabilization", "60-day stabilization", "Other"]),
            field("admission_date", "Admission date", "date"),
            field("planned_discharge", "Planned discharge", "date"),
        ],
        "Foster care": [
            field("foster_home", "Foster home"),
            field("agency", "Agency"),
            field("caseworker", "Caseworker"),
            field("placement_date", "Placement date", "date"),
        ],
        "Outpatient / community": [
            field("clinic", "Clinic"),
            field("referral_source", "Referral source"),
        ],
    },
    "documents": {
        "Court Order": [
            ai("case_type", "Case type", "picklist", ["Placement", "Custody", "PINS", "Juvenile delinquency",
                                                      "Neglect or abuse (Article 10)", "Order of protection", "Other"]),
            ai("court", "Court"),
            ai("docket_number", "Docket number"),
            ai("judge", "Judge"),
            ai("order_date", "Order date", "date"),
            ai("expires", "Expires", "date"),
            ai("order_of_protection", "Order of protection", "checkbox"),
            ai("visitation_terms", "Visitation terms", "longtext"),
        ],
        "Individualized Education Program (IEP)": [
            ai("classification", "Classification", "picklist", [
                "Emotional disability", "Learning disability", "Autism", "Other health impairment",
                "Speech or language impairment", "Intellectual disability", "Multiple disabilities", "Other"]),
            ai("class_ratio", "Class ratio", "picklist", ["12:1", "12:1:1", "8:1:1", "6:1:1", "General education", "Other"]),
            ai("school", "School"),
            ai("grade", "Grade"),
            ai("services", "Services", "longtext"),
            ai("accommodations", "Testing accommodations", "longtext"),
            ai("behavior_plan", "Behavior intervention plan", "checkbox"),
            ai("meeting_date", "Meeting date", "date"),
        ],
        "Psychological Evaluation": [
            ai("examiner", "Examiner"),
            ai("testing_dates", "Testing dates"),
            ai("tests_given", "Tests given", "longtext"),
            ai("fsiq", "Full Scale IQ", "number"),
            ai("verbal_comprehension", "Verbal Comprehension Index", "number"),
            ai("visual_spatial", "Visual Spatial Index", "number"),
            ai("fluid_reasoning", "Fluid Reasoning Index", "number"),
            ai("working_memory", "Working Memory Index", "number"),
            ai("processing_speed", "Processing Speed Index", "number"),
        ],
        "Psychiatric Evaluation": [
            ai("setting", "Setting", "picklist", ["Inpatient", "Residential", "Outpatient", "Emergency", "Other"]),
            ai("diagnoses", "Diagnoses", "longtext"),
            ai("medications", "Medications", "longtext"),
            ai("reason_for_referral", "Reason for referral", "longtext"),
        ],
        "Discharge Summary": [
            ai("discharged_from", "Discharged from"),
            ai("admission_date", "Admission date", "date"),
            ai("discharge_date", "Discharge date", "date"),
            ai("discharge_diagnoses", "Discharge diagnoses", "longtext"),
            ai("discharge_medications", "Discharge medications", "longtext"),
        ],
        "Progress or Clinical Note": [
            ai("note_type", "Note type", "picklist", ["Medication management", "Therapy", "Nursing", "Incident", "Other"]),
        ],
        "Clinician Note": [],
        "Other": [],
    },
}

# What each document type is, for the AI that recognizes uploads. Types added later carry their own description.
TYPE_DESCRIPTIONS = {
    "Court Order": "an order, decision or petition from a court",
    "Individualized Education Program (IEP)": "a school special education plan (classification, services, goals)",
    "Psychological Evaluation": "a psychologist's testing report (IQ, achievement or personality tests)",
    "Psychiatric Evaluation": "a psychiatrist's or nurse practitioner's diagnostic evaluation (often with history, "
                              "mental status exam, diagnoses)",
    "Discharge Summary": "a summary written when leaving a hospital or program",
    "Progress or Clinical Note": "a medication management, therapy, nursing or progress note",
    "Clinician Note": "a note written in MindChart itself",
    "Other": "anything that doesn't fit another type",
}

YES = {"1", "true", "yes", "y", "on", "checked", "x"}


def coerce(definition, value):
    """Check a value against its field's type and return it as stored. Raises ValueError if it doesn't fit."""
    kind, label, options = definition["type"], definition["label"], definition.get("options") or []
    if value is None or value == "" or value == []:
        return [] if kind == "multipicklist" else ("" if kind != "checkbox" else False)
    if kind == "checkbox":
        return value if isinstance(value, bool) else str(value).strip().lower() in YES
    if kind == "number":
        try:
            number = float(str(value).replace(",", "").strip())
        except ValueError:
            raise ValueError(f"{label} must be a number")
        return int(number) if number.is_integer() else number
    if kind == "picklist":
        match = next((o for o in options if o.lower() == str(value).strip().lower()), None)
        if match is None:
            raise ValueError(f"{label} must be one of: {', '.join(options)}")
        return match
    if kind == "multipicklist":
        items = value if isinstance(value, list) else re.split(r"[,;\n]", str(value))
        chosen = []
        for item in (str(i).strip() for i in items if str(i).strip()):
            match = next((o for o in options if o.lower() == item.lower()), None)
            if match is None:
                raise ValueError(f"{label}: \"{item}\" isn't one of the choices ({', '.join(options)})")
            if match not in chosen:
                chosen.append(match)
        return chosen
    return str(value).strip()


def convert(value, new_definition):
    """Convert a stored value to a new field type. Returns (new value, True) or (blank, False) if it doesn't fit."""
    if value in (None, "", []):
        return coerce(new_definition, ""), True
    as_text = ", ".join(value) if isinstance(value, list) else ("Yes" if value is True else "No" if value is False else str(value))
    try:
        return coerce(new_definition, as_text), True
    except ValueError:
        return coerce(new_definition, ""), False


def display(value):
    """A stored value as plain text, for logs."""
    if isinstance(value, list):
        return ", ".join(value) or "(blank)"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value) if value not in (None, "") else "(blank)"
