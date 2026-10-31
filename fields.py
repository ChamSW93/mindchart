"""Record types and their fields: the defaults, checking values, and changing a field's type later.

Every object has shared fields (defined in vault.OBJECTS). Patients and documents also have record types,
and each record type adds its own fields, defined below. The definitions are copied into the database
(the field_defs table) the first time MindChart opens, and are read from there afterwards. That means a
field's type or picklist options can be changed later, existing values are converted (see convert()),
and no new version of MindChart is needed. Values live in each record's "custom" column as JSON, keyed
by field name, so changing a type never changes the database's layout.
"""
import datetime
import re

FIELD_TYPES = ["text", "longtext", "number", "date", "datetime", "checkbox", "picklist", "multipicklist", "zip"]

# The field that holds each object's record type.
RECORD_TYPE_FIELD = {"patients": "record_type", "documents": "document_type", "measures": "measure"}


def field(name, label, type="text", options=None, ai=False):
    """One field definition. ai=True means the AI fills it in when it reads a document (backed by a quote)."""
    return {"name": name, "label": label, "type": type, "options": options or [], "ai": ai}


def ai(name, label, type="text", options=None):
    return field(name, label, type, options, ai=True)


# People are patients or legal guardians (the "patients" object holds both; its record type says which).
# A patient's placement decides which of the Patient type's own fields apply.
PLACEMENT_FIELDS = {
    "Residential treatment": ["cottage", "admission_date", "expected_discharge"],
    "Crisis / stabilization": ["program", "admission_date", "planned_discharge"],
    "Foster care": ["foster_home", "agency", "caseworker", "placement_date"],
    "Outpatient / community": ["clinic", "referral_source"],
}
PLACEMENTS = list(PLACEMENT_FIELDS)
# The Children's Village cottages and programs, A to Z
COTTAGES = sorted(["Rose", "Collins", "Smith", "Crest", "Fanshaw", "Kendall", "Howard", "Van Horn", "Dwight", "Williams",
                   "Butler", "Minturn", "JRIC", "NEST", "AOBH"], key=str.lower)
GENDERS = ["Male", "Female", "X"]

DEFAULT_RECORD_TYPES = {
    "patients": {
        "Patient": [
            field("cottage", "Cottage / unit", "picklist", COTTAGES),
            field("admission_date", "Admission date", "date"),
            field("expected_discharge", "Expected discharge", "date"),
            field("program", "Program", "picklist", ["Crisis residence", "21-day stabilization", "60-day stabilization", "Other"]),
            field("planned_discharge", "Planned discharge", "date"),
            field("foster_home", "Foster home"),
            field("agency", "Agency"),
            field("caseworker", "Caseworker"),
            field("placement_date", "Placement date", "date"),
            field("clinic", "Clinic"),
            field("referral_source", "Referral source"),
        ],
        "Legal Guardian": [
            field("relationship", "Relationship", "picklist",
                  ["Parent", "Grandparent", "Other relative", "Foster parent", "Agency (e.g. ACS)", "Other"]),
            field("organization", "Agency or organization"),
            field("phone", "Phone"),
            field("email", "Email"),
            field("address", "Address", "longtext"),
            field("zip", "ZIP code", "zip"),  # gives their town and a rough drive time from Children's Village
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
    # Rating scales and vitals. Only scores are kept (never the questions), so no scale's wording is reproduced.
    "measures": {
        "PHQ-A (depression)": [field("self_harm_item", "Question 9: thoughts of self-harm (0 to 3)", "number")],
        "GAD-7 (anxiety)": [],
        "SCARED (anxiety)": [
            field("panic", "Panic / somatic", "number"), field("generalized", "Generalized anxiety", "number"),
            field("separation", "Separation anxiety", "number"), field("social", "Social anxiety", "number"),
            field("school", "School avoidance", "number"),
        ],
        "Vanderbilt ADHD": [
            field("inattentive", "Inattentive symptoms rated often or very often (0 to 9)", "number"),
            field("hyperactive", "Hyperactive/impulsive symptoms rated often or very often (0 to 9)", "number"),
            field("performance", "Performance items rated problematic", "number"),
        ],
        "CRAFFT (substance use)": [],
        "Vitals": [
            field("weight_lb", "Weight (lb)", "number"), field("height_in", "Height (in)", "number"),
            field("bmi", "BMI", "number"), field("blood_pressure", "Blood pressure"), field("pulse", "Pulse", "number"),
        ],
        "Other scale": [field("scale_name", "Scale")],
    },
}

# Each scale's highest possible total, and its published score bands: [(highest score in the band, label)].
MEASURE_MAX = {"PHQ-A (depression)": 27, "GAD-7 (anxiety)": 21, "SCARED (anxiety)": 82, "CRAFFT (substance use)": 6}
MEASURE_BANDS = {
    "PHQ-A (depression)": [(4, "Minimal"), (9, "Mild"), (14, "Moderate"), (19, "Moderately severe"), (27, "Severe")],
    "GAD-7 (anxiety)": [(4, "Minimal"), (9, "Mild"), (14, "Moderate"), (21, "Severe")],
    "SCARED (anxiety)": [(24, "Below the cutoff"), (82, "At or above the cutoff of 25")],
    "CRAFFT (substance use)": [(1, "Lower risk"), (6, "Positive screen (2 or more)")],
}


def measure_band(measure, score):
    """The published band a score falls in (say, "Moderate" for a PHQ-A of 12), or "" if the scale has none."""
    if score in (None, ""):
        return ""
    for highest, label in MEASURE_BANDS.get(measure, []):
        if float(score) <= highest:
            return label
    return ""

# What each document type is, for the AI that recognizes uploads. Types added later carry their own description.
TYPE_DESCRIPTIONS = {
    "Patient": "a child or adolescent seen for psychiatric care",
    "Legal Guardian": "a parent, relative, foster parent or agency legally responsible for a patient",
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
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_date(text):
    """A date written in any common way (03/04/2011, 3-4-11, 2011-03-04, March 4, 2011, 4 Mar 2011), or None."""
    text = (text or "").strip().lower()
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text)
    if m:
        year, month, day = map(int, m.groups())
    elif re.search(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b", text):
        month, day, year = map(int, re.search(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b", text).groups())
    else:
        m = (re.search(r"\b([a-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", text)
             or re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3})[a-z]*\.?,?\s+(\d{4})\b", text))
        if not m:
            return None
        first, second, year = m.groups()
        month_name, day = (first, second) if first.isalpha() else (second, first)
        if month_name not in MONTHS:
            return None
        month, day, year = MONTHS[month_name], int(day), int(year)
    if year < 100:  # two-digit years: the most recent past one (birth dates are never in the future)
        year += 2000 if year <= datetime.date.today().year % 100 else 1900
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return None


def normalize_date(text):
    """A date as MindChart writes it (MM/DD/YYYY, as the calendar fills it in), when it was typed another way that
    can only mean one date: "12271994", "122794", "12-27-94", "1994-12-27" or "Dec 27, 1994". Anything else, such
    as "2019" or "March 2019" (as records often put it), is kept as written."""
    raw = str(text or "").strip()
    typed = " ".join(raw.split())
    digits = re.fullmatch(r"(\d{2})(\d{2})(\d{4}|\d{2})", typed)  # MMDDYYYY or MMDDYY
    m = re.fullmatch(r"(\d{1,2})[/. -](\d{1,2})[/. -](\d{4}|\d{2})", "/".join(digits.groups()) if digits else typed)
    if m:
        month, day, year = map(int, m.groups())
        if len(m.group(3)) == 2:  # the century that puts it nearest today, so "94" is 1994 and a due date in "27" is 2027
            this_year = datetime.date.today().year
            year = min((century + year for century in (this_year // 100 * 100 - 100, this_year // 100 * 100, this_year // 100 * 100 + 100)),
                       key=lambda y: abs(y - this_year))
    elif re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}|[A-Za-z]{3,}\.? \d{1,2}(st|nd|rd|th)?,? \d{4}|\d{1,2}(st|nd|rd|th)? [A-Za-z]{3,}\.?,? \d{4}", typed):
        found = parse_date(typed)
        if not found:
            return raw
        month, day, year = found.month, found.day, found.year
    else:
        return raw
    try:
        return datetime.date(year, month, day).strftime("%m/%d/%Y")
    except ValueError:  # no such day (13/45/2020): kept as typed, for a person to correct
        return raw


def date_key(text):
    """For putting dates in order, including partial ones: (year, month, day), with 0 for what isn't known.
    "03/04/2019" -> (2019, 3, 4); "March 2019" -> (2019, 3, 0); "in 2018" -> (2018, 0, 0); "age 7" -> None."""
    full = parse_date(text)
    if full:
        return (full.year, full.month, full.day)
    text = (text or "").lower()
    m = re.search(r"\b([a-z]{3})[a-z]*\.?,?\s+(19\d\d|20\d\d)\b", text)
    if m and m.group(1) in MONTHS:
        return (int(m.group(2)), MONTHS[m.group(1)], 0)
    m = re.search(r"\b(\d{1,2})/(19\d\d|20\d\d)\b", text)
    if m and 1 <= int(m.group(1)) <= 12:
        return (int(m.group(2)), int(m.group(1)), 0)
    m = re.search(r"\b(19\d\d|20\d\d)\b", text)
    return (int(m.group(1)), 0, 0) if m else None


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
    if kind == "date":
        return normalize_date(value)
    if kind == "zip":
        code = str(value).strip()
        if not re.fullmatch(r"\d{5}(-\d{4})?", code):
            raise ValueError(f"{label} must be a 5-digit ZIP code")
        return code
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


# --- People's names and gender ----------------------------------------------------

def split_name(name):
    """A full name as (first, last): the last word is the last name, with any note in brackets after it.
    "Jordan Avery" -> ("Jordan", "Avery"); "Maria Test (fictional)" -> ("Maria", "Test (fictional)")."""
    base, note = re.match(r"^(.*?)\s*(\([^()]*\))?$", " ".join((name or "").split())).groups()
    words = base.split(" ") if base else [""]
    first, last = (" ".join(words[:-1]), words[-1]) if len(words) > 1 else (words[0], "")
    return first, " ".join(part for part in (last, note) if part)


def full_name(first, last):
    return " ".join(f"{first or ''} {last or ''}".split())


def normalize_gender(value):
    """Gender as one of GENDERS, from how people (or the AI) write it. Anything unrecognized is left for checking."""
    text = (value or "").strip()
    known = {"m": "Male", "male": "Male", "boy": "Male", "man": "Male", "f": "Female", "female": "Female", "girl": "Female",
             "woman": "Female", "x": "X", "nonbinary": "X", "non-binary": "X", "non binary": "X"}
    return known.get(text.lower(), text)


# --- Medications -------------------------------------------------------------------
# Medications commonly used in child and adolescent psychiatry and general care, offered when adding one (any other
# name can be typed too). Each is (name, brand names); a brand name typed in is saved as the medication's name.
MEDICATIONS = {
    "ADHD": [("Methylphenidate", ["Ritalin", "Concerta", "Metadate", "Quillichew", "Jornay PM"]),
             ("Dexmethylphenidate", ["Focalin", "Focalin XR"]), ("Methylphenidate patch", ["Daytrana"]),
             ("Mixed amphetamine salts", ["Adderall", "Adderall XR", "Mydayis"]), ("Lisdexamfetamine", ["Vyvanse"]),
             ("Dextroamphetamine", ["Dexedrine", "Zenzedi"]), ("Atomoxetine", ["Strattera"]), ("Viloxazine", ["Qelbree"]),
             ("Guanfacine", ["Tenex"]), ("Guanfacine ER", ["Intuniv"]), ("Clonidine", ["Catapres"]), ("Clonidine ER", ["Kapvay"])],
    "Antidepressant": [("Sertraline", ["Zoloft"]), ("Fluoxetine", ["Prozac"]), ("Escitalopram", ["Lexapro"]),
                       ("Citalopram", ["Celexa"]), ("Fluvoxamine", ["Luvox"]), ("Paroxetine", ["Paxil"]),
                       ("Bupropion", ["Wellbutrin", "Wellbutrin XL"]), ("Venlafaxine", ["Effexor XR"]),
                       ("Duloxetine", ["Cymbalta"]), ("Desvenlafaxine", ["Pristiq"]), ("Mirtazapine", ["Remeron"]),
                       ("Trazodone", ["Desyrel"]), ("Vilazodone", ["Viibryd"]), ("Vortioxetine", ["Trintellix"]),
                       ("Amitriptyline", ["Elavil"]), ("Nortriptyline", ["Pamelor"]), ("Clomipramine", ["Anafranil"]),
                       ("Imipramine", ["Tofranil"])],
    "Antipsychotic": [("Aripiprazole", ["Abilify", "Abilify Maintena"]), ("Risperidone", ["Risperdal"]),
                      ("Quetiapine", ["Seroquel", "Seroquel XR"]), ("Olanzapine", ["Zyprexa"]), ("Ziprasidone", ["Geodon"]),
                      ("Lurasidone", ["Latuda"]), ("Paliperidone", ["Invega"]), ("Brexpiprazole", ["Rexulti"]),
                      ("Cariprazine", ["Vraylar"]), ("Asenapine", ["Saphris"]), ("Haloperidol", ["Haldol"]),
                      ("Chlorpromazine", ["Thorazine"]), ("Clozapine", ["Clozaril"])],
    "Mood stabilizer": [("Lithium", ["Lithobid"]), ("Divalproex", ["Depakote", "Depakote ER"]), ("Valproic acid", ["Depakene"]),
                        ("Lamotrigine", ["Lamictal"]), ("Oxcarbazepine", ["Trileptal"]), ("Carbamazepine", ["Tegretol"]),
                        ("Topiramate", ["Topamax"])],
    "Anxiety, sleep and agitation": [("Hydroxyzine", ["Vistaril", "Atarax"]), ("Buspirone", ["Buspar"]),
                                     ("Propranolol", ["Inderal"]), ("Prazosin", ["Minipress"]), ("Melatonin", []),
                                     ("Diphenhydramine", ["Benadryl"]), ("Lorazepam", ["Ativan"]), ("Clonazepam", ["Klonopin"]),
                                     ("Diazepam", ["Valium"]), ("Alprazolam", ["Xanax"]), ("Gabapentin", ["Neurontin"])],
    "Other psychiatric": [("Naltrexone", ["Revia", "Vivitrol"]), ("Benztropine", ["Cogentin"]), ("Nicotine patch", ["NicoDerm CQ"]),
                          ("Nicotine gum", ["Nicorette"]), ("N-acetylcysteine", ["NAC"]), ("Desmopressin", ["DDAVP"])],
    "General care": [("Acetaminophen", ["Tylenol"]), ("Ibuprofen", ["Advil", "Motrin"]), ("Naproxen", ["Aleve"]),
                     ("Albuterol inhaler", ["ProAir", "Ventolin"]), ("Fluticasone inhaler", ["Flovent"]),
                     ("Budesonide-formoterol inhaler", ["Symbicort"]), ("Montelukast", ["Singulair"]),
                     ("Cetirizine", ["Zyrtec"]), ("Loratadine", ["Claritin"]), ("Fexofenadine", ["Allegra"]),
                     ("Fluticasone nasal spray", ["Flonase"]), ("Epinephrine auto-injector", ["EpiPen", "Auvi-Q"]),
                     ("Amoxicillin", ["Amoxil"]), ("Azithromycin", ["Zithromax"]), ("Cephalexin", ["Keflex"]),
                     ("Metformin", ["Glucophage"]), ("Insulin", ["Humalog", "Lantus", "Novolog"]),
                     ("Levothyroxine", ["Synthroid"]), ("Omeprazole", ["Prilosec"]), ("Famotidine", ["Pepcid"]),
                     ("Ondansetron", ["Zofran"]), ("Polyethylene glycol", ["MiraLAX"]), ("Docusate", ["Colace"]),
                     ("Ferrous sulfate (iron)", ["Feosol"]), ("Vitamin D", []), ("Multivitamin", []),
                     ("Hydrocortisone cream", ["Cortaid"]), ("Mupirocin ointment", ["Bactroban"]),
                     ("Benzoyl peroxide", ["PanOxyl"]), ("Tretinoin", ["Retin-A"]),
                     ("Levonorgestrel-ethinyl estradiol", ["Alesse", "Aviane"]), ("Medroxyprogesterone injection", ["Depo-Provera"])],
}


def medication_catalog():
    """[{"name", "brands", "category"}] for every medication above."""
    return [{"name": name, "brands": brands, "category": category}
            for category, entries in MEDICATIONS.items() for name, brands in entries]


def canonical_medication(name):
    """A medication's name as the catalog writes it, when what was typed is one of its names or brand names
    ("zoloft" or "Methylphenidate (Ritalin)"). Anything else is kept as typed, as a new medication."""
    typed = " ".join(str(name or "").split())
    bare = re.sub(r"\s*\([^)]*\)\s*$", "", typed).lower()  # "Methylphenidate (Ritalin)" -> "methylphenidate"
    for entry in medication_catalog():
        if typed.lower() == entry["name"].lower() or bare == entry["name"].lower() or typed.lower() in (b.lower() for b in entry["brands"]):
            return entry["name"]
    return typed
