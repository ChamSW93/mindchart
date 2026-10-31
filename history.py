"""Builds the history section of a child & adolescent psychiatric evaluation from source records.

How it works, all on this computer:
1. Each document is split into parts small enough for the local model to read carefully.
2. For each part, the model lists the facts it finds, each with a short exact quote.
3. Every quote is checked against the original text. Facts whose quote can't be found
   are marked unverified and kept out of the draft, so the model can't slip in
   something the records don't say.
4. For each history section, the model writes a draft paragraph from the verified
   facts only, citing them, and flags facts that disagree between documents.
"""
import io
import json
import re
import urllib.error
import urllib.request
import zipfile
from xml.sax.saxutils import escape

import fields as fieldtypes

OLLAMA_URL = "http://localhost:11434/api/chat"
CONTEXT_LENGTH = 16384
CHUNK_CHARS = 10000  # about 2,500 words per part; small models miss details in longer parts

SECTIONS = [
    ("identifying", "Identifying Information",
     "ONLY name, date of birth, gender, grade, current placement or program, legal guardian. "
     "Birth history and milestones go in developmental; court orders go in legal"),
    ("referral", "Reason for Referral",
     "why the evaluation was requested, who referred, chief complaint, presenting concerns"),
    ("hpi", "History of Present Illness",
     "recent and current symptoms, behaviours and events leading up to the referral, recent changes"),
    ("past_psych", "Past Psychiatric History",
     "previous diagnoses, psychiatric hospitalizations, residential or crisis placements, outpatient therapy, past evaluations"),
    ("medications", "Medication History",
     "current medications and doses, past medication trials, responses, side effects, adherence "
     "(therapy and counseling services are not medications)"),
    ("risk", "Safety and Risk History",
     "suicidal ideation or attempts, self-harm, aggression or violence, homicidal ideation, running away/AWOL, fire-setting, sexualized behaviour, restraints"),
    ("trauma", "Trauma and Abuse History",
     "physical, sexual or emotional abuse, neglect, domestic violence exposure, losses, other traumatic events"),
    ("substance", "Substance Use History",
     "alcohol, cannabis, other drug or tobacco/vaping use, treatment, prenatal substance exposure is NOT here (use developmental)"),
    ("medical", "Medical History",
     "medical conditions, allergies, head injuries, seizures, surgeries, hospitalizations for medical reasons, primary care "
     "(birth history goes in developmental)"),
    ("developmental", "Developmental History",
     "gestational age at birth, NICU stay, pregnancy, prenatal substance exposure, birth complications, "
     "developmental milestones (walking, talking), early intervention"),
    ("educational", "Educational History",
     "schools, grade, IEP classification, class type or size (such as 12:1:1), school counseling, speech and other "
     "related services, testing accommodations, behavior plans, academic performance, attendance, suspensions"),
    ("testing", "Psychological and Cognitive Testing",
     "IQ and cognitive test results with every index score, achievement testing, other standardized test scores "
     "and their dates (school testing accommodations go in educational)"),
    ("family", "Family History",
     "family members' psychiatric illness, substance use, suicide, medical conditions, incarceration"),
    ("social", "Social and Placement History",
     "who the child has lived with, foster care and placement history, child welfare (ACS/CPS) involvement, siblings, relationships, visitation"),
    ("legal", "Legal History",
     "anything a court ordered (placement, custody, services, visitation, orders of protection), family court cases, PINS or juvenile delinquency, arrests, probation"),
    ("strengths", "Strengths and Interests",
     "strengths, interests, hobbies, positive relationships, protective factors"),
]
SECTION_TITLES = {key: title for key, title, _ in SECTIONS}

EXTRACT_PROMPT = """You help a psychiatric nurse practitioner gather the history for a child and adolescent psychiatric evaluation. You will be given an excerpt from one source record.

List every clinically relevant fact in the excerpt. Rules:
- Only facts the text states. Never guess, infer, interpret or add diagnoses.
- Never calculate ages, durations or dates; copy them only as written.
- One fact per item, written as one short clinical sentence in the third person.
- "date": when the event happened, exactly as written in the text (empty if not stated).
- "quote": copy 5 to 30 consecutive words EXACTLY as they appear in the excerpt that support the fact. Do not paraphrase the quote.
- "section": the best category below.
- Skip letterheads, signatures, page numbers, form instructions and legal boilerplate.

Categories:
""" + "\n".join(f"- {key}: {description}" for key, _, description in SECTIONS)

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section": {"type": "string", "enum": [key for key, _, _ in SECTIONS]},
                    # Length limits stop the model getting stuck repeating itself inside a field.
                    "fact": {"type": "string", "maxLength": 400},
                    "date": {"type": "string", "maxLength": 40},
                    "quote": {"type": "string", "maxLength": 300},
                },
                "required": ["section", "fact", "date", "quote"],
            },
        }
    },
    "required": ["facts"],
}

IDENTIFY_PROMPT = """Identify this clinical record from its file name and opening text.
- document_type: the closest type:
{types}
- document_date: the date the document was written, exactly as written.
- author: only the name and credentials of the clinician, judge or team who wrote it.
- patient_name: the full name of the child or adolescent the record is about (not a parent, sibling, clinician or judge).
- date_of_birth: that child's date of birth, exactly as written.
- identifiers: record, case, docket, client or ID numbers for that child, exactly as written.
Use an empty string (or an empty list) for anything not stated.{examples}"""


def identify(model, thinking, name, text, types, examples=()):
    """Work out a document's type, date, author and who it's about from its opening text.

    types: {type: what it is}. examples: [{"document_type", "name", "text"}] the clinician confirmed before, which
    help the model recognize documents like them. Returns the fields in IDENTIFY_PROMPT.
    """
    listed = "\n".join(f"  - {t}: {d}" if d else f"  - {t}" for t, d in types.items())
    shown = "".join(f'\n- {e["document_type"]}: "{" ".join(e["text"].split())[:300]}"' for e in examples)
    if shown:
        shown = "\n\nThe clinician has confirmed these documents' types before; use them to recognize similar ones:" + shown
    schema = {"type": "object", "properties": {
        "document_type": {"type": "string", "enum": list(types)},
        "document_date": {"type": "string", "maxLength": 30},
        "author": {"type": "string", "maxLength": 80},
        "patient_name": {"type": "string", "maxLength": 80},
        "date_of_birth": {"type": "string", "maxLength": 30},
        "identifiers": {"type": "array", "maxItems": 5, "items": {"type": "string", "maxLength": 40}},
    }, "required": ["document_type", "document_date", "author", "patient_name", "date_of_birth", "identifiers"]}
    return ask(model, thinking, IDENTIFY_PROMPT.format(types=listed, examples=shown),
               f"File name: {name}\n\n{text[:4000]}", schema, max_tokens=1000)


DEFAULT_TYPES = {t: d for t, d in fieldtypes.TYPE_DESCRIPTIONS.items() if t != "Clinician Note"}

WRITE_PROMPT = """You help a psychiatric nurse practitioner draft the history for a child and adolescent psychiatric evaluation.

Write the "{title}" section using ONLY the numbered facts provided, as a list of sentences. Rules:
- For each sentence, list the numbers of the facts it relies on in "facts". Every sentence needs at least one.
- Clinical, concise, third person. Order events chronologically where dates allow.
- Never add information that is not in the facts. Do not diagnose or interpret.
- Never calculate ages, durations or dates. When stating an age or a "current" detail, say which source date it is as of
  (for example "12 years old as of 09/18/2023").
- Only include facts that belong in this section; leave out any that belong elsewhere.
- Keep every specific detail: each score, dose, date, frequency and name. Don't summarize them away.
- Combine facts that repeat across documents into one sentence citing all of them.
- If facts disagree (for example different dates, diagnoses, doses or accounts), say so in a sentence citing both, and also list each disagreement in "conflicts"."""

WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        # Each sentence must name the facts behind it, so every sentence in the draft gets a citation.
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string", "maxLength": 600},
                               "facts": {"type": "array", "minItems": 1, "items": {"type": "integer"}}},
                "required": ["text", "facts"],
            },
        },
        "conflicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"description": {"type": "string", "maxLength": 400}, "facts": {"type": "array", "items": {"type": "integer"}}},
                "required": ["description", "facts"],
            },
        },
    },
    "required": ["sentences", "conflicts"],
}


class TooLong(Exception):
    """The model hit its output limit before finishing its answer."""


class AIUnavailable(Exception):
    """The local AI (Ollama) isn't running, so nothing can be read right now."""

    def __init__(self):
        super().__init__("The AI isn't running. Open Ollama and try again.")


class BadReply(Exception):
    """The model answered, but not in the requested JSON format. The reply text is kept in .text."""

    def __init__(self, text):
        super().__init__("The model's reply wasn't in the expected format")
        self.text = text


def ask(model, thinking, system, user, schema, max_tokens, history=()):
    """One request to the local model, answered as JSON matching `schema`.

    history: earlier (assistant, user) turns to continue a conversation, e.g. asking for more.
    """
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for reply, follow_up in history:
        messages += [{"role": "assistant", "content": reply}, {"role": "user", "content": follow_up}]
    request = {
        "model": model,
        "messages": messages,
        "format": schema,
        "stream": False,
        # max_tokens caps the answer (plus any reasoning) so a confused model can't run forever.
        "options": {"num_ctx": CONTEXT_LENGTH, "temperature": 0, "num_predict": max_tokens},
    }
    if thinking:
        # gpt-oss always reasons and takes a level; other thinking models can skip it for speed.
        request["think"] = "low" if model.startswith("gpt-oss") else False
    req = urllib.request.Request(OLLAMA_URL, data=json.dumps(request).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=900) as response:
            reply = json.load(response)
    except urllib.error.URLError as error:
        if isinstance(error.reason, ConnectionRefusedError):
            raise AIUnavailable() from error
        raise
    if reply.get("done_reason") == "length":
        raise TooLong()
    content = reply["message"]["content"]
    try:
        return tidy(json.loads(content))
    except json.JSONDecodeError:
        # Sometimes the JSON comes wrapped in other text; use it if it's there.
        start, end = content.find("{"), content.rfind("}")
        if 0 <= start < end:
            try:
                return tidy(json.loads(content[start:end + 1]))
            except json.JSONDecodeError:
                pass
        raise BadReply(content)


# Some models write special spaces and hyphens that look odd in Word; swap them for plain ones.
PLAIN = str.maketrans({"\u00a0": " ", "\u202f": " ", "\u2009": " ", "\u2011": "-", "\u2010": "-"})


def tidy(value):
    if isinstance(value, str):
        return value.translate(PLAIN)
    if isinstance(value, list):
        return [tidy(v) for v in value]
    if isinstance(value, dict):
        return {k: tidy(v) for k, v in value.items()}
    return value


FILL_PROMPT = """You help a psychiatric nurse practitioner record key details from a clinical record. Fill in the
fields listed below from the record's text. Rules:
- Only use what the text states. Leave out any field the text doesn't state. Never guess.
- "value": for yes/no fields, "yes" or "no"; for choice fields, exactly one of the listed choices; dates and
  numbers exactly as written.
- "quote": 5 to 30 consecutive words copied EXACTLY from the text that show the value.

Fields:
{fields}"""


def fill_fields(model, thinking, doc, definitions):
    """Fill in a document's record-type fields. Each value must come with a quote that's really in the document;
    values without one, or that don't fit the field's type, are left out. Returns {name: {value, quote, page}}."""
    described = "\n".join(
        f"- {d['name']}: {d['label']} ({d['type']}{': ' + ', '.join(d['options']) if d['options'] else ''})" for d in definitions)
    schema = {"type": "object", "properties": {"fields": {"type": "array", "items": {
        "type": "object", "properties": {"name": {"type": "string", "enum": [d["name"] for d in definitions]},
                                         "value": {"type": "string", "maxLength": 600},
                                         "quote": {"type": "string", "maxLength": 300}},
        "required": ["name", "value", "quote"]}}}, "required": ["fields"]}
    text = "".join(doc["chunks"][:2])
    reply = ask(model, thinking, FILL_PROMPT.format(fields=described),
                f"Record: {doc['document_type']}, dated {doc['document_date'] or 'unknown'}\n\n{text}", schema, max_tokens=3000)
    by_name = {d["name"]: d for d in definitions}
    filled = {}
    for item in reply.get("fields", []):
        definition = by_name.get(item.get("name"))
        found, page = locate(item.get("quote", ""), doc["pages"])
        if not definition or not found or item["name"] in filled or not str(item.get("value", "")).strip():
            continue
        try:
            value = fieldtypes.coerce(definition, item["value"])
        except ValueError:
            continue  # e.g. a choice that isn't on the list
        filled[item["name"]] = {"value": value, "quote": item["quote"], "page": page}
    return filled


MEDICATIONS_PROMPT = """You help a psychiatric nurse practitioner build a patient's medication list from their records.
From the numbered facts, list each medication trial (one entry per medication; a dose change of the same medication
stays one entry, described in "dose" and "response"). Rules:
- Only what the facts state. Never guess doses, dates or reasons. Leave anything not stated as "".
- "status": "Active" if the latest facts say it's current, "Stopped" if they say it was stopped, otherwise "Unknown".
- "dose": the most recent dose stated (earlier doses go in "response"), without how often. "frequency": how often.
- Dates exactly as written. "response": response, side effects and dose changes as stated.
- "facts": the numbers of the facts each entry comes from (at least one).
Psychotherapy, counseling and other services are not medications."""


def suggest_medications(model, thinking, facts):
    """A medication list drafted from a patient's medication facts, for the clinician to check and add.
    facts: [{"fact", "date", "document", "document_date"}]. Returns entries citing the facts they came from."""
    if not facts:
        return []
    numbered = "\n".join(f"[{n}] ({f['document']}, {f['document_date'] or 'undated'}{'; happened: ' + f['date'] if f['date'] else ''}) "
                         f"{f['fact']}" for n, f in enumerate(facts, 1))
    text = {"type": "string", "maxLength": 300}
    schema = {"type": "object", "properties": {"medications": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string", "maxLength": 80}, "dose": text, "frequency": text, "target": text,
        "status": {"type": "string", "enum": ["Active", "Stopped", "Unknown"]}, "start_date": {"type": "string", "maxLength": 40},
        "stop_date": {"type": "string", "maxLength": 40}, "stop_reason": text, "response": {"type": "string", "maxLength": 600},
        "facts": {"type": "array", "minItems": 1, "items": {"type": "integer"}}},
        "required": ["name", "dose", "frequency", "target", "status", "start_date", "stop_date", "stop_reason", "response", "facts"]}}},
        "required": ["medications"]}
    reply = ask(model, thinking, MEDICATIONS_PROMPT, f"Facts:\n{numbered}", schema, max_tokens=4000)
    # Smaller models sometimes list each fact as its own entry, so entries for the same medication are combined here:
    # the most recent record gives the current name, dose and status, and the earlier ones add to the history.
    trials = {}
    for item in reply.get("medications", []):
        cited = [facts[n - 1] for n in item.get("facts", []) if 0 < n <= len(facts)]
        if not item.get("name", "").strip() or not cited:
            continue  # every suggestion must come from the records
        newest = max(fieldtypes.date_key(f["document_date"] or f["date"]) or (0, 0, 0) for f in cited)
        trials.setdefault(medication_key(item["name"]), []).append((newest, item, cited))
    suggestions = []
    for entries in trials.values():
        entries.sort(key=lambda e: e[0])
        items = [item for _, item, _ in entries]
        latest = lambda key: next((i[key] for i in reversed(items) if i.get(key, "").strip()), "")
        cited = list({id(f): f for _, _, c in entries for f in c}.values())
        status = items[-1].get("status") if items[-1].get("status") in ("Active", "Stopped") else ""
        starts = [i["start_date"] for i in items if i.get("start_date", "").strip()]
        sources = list(dict.fromkeys(f"{f['document']}{' (' + f['document_date'] + ')' if f['document_date'] else ''}" for f in cited))
        dose, frequency = latest("dose"), latest("frequency")
        split = re.fullmatch(r"\s*([\d.,]+\s*(?:mg|mcg|g|ml|mL|units?)\b)\s+(.+?)\s*", dose)
        if split and not frequency:  # smaller models put how often in the dose: "2 mg at bedtime"
            dose, frequency = split.groups()
        suggestions.append({"name": items[-1]["name"], "dose": dose, "frequency": frequency,
                            "target": latest("target"),
                            "start_date": min(starts, key=lambda d: fieldtypes.date_key(d) or (9999, 0, 0)) if starts else "",
                            "stop_date": latest("stop_date") if status != "Active" else "",
                            "stop_reason": latest("stop_reason") if status != "Active" else "",
                            "response": "; ".join(dict.fromkeys(i["response"].strip() for i in items if i.get("response", "").strip())),
                            "status": status, "source": "; ".join(sources), "quotes": [f["fact"] for f in cited]})
    return suggestions


FORMULATIONS = {"er", "xr", "xl", "sr", "cr", "la", "ir", "dr", "odt", "extended", "immediate", "delayed", "release",
                "tablet", "tablets", "capsule", "capsules", "oral", "liquid", "solution", "mg", "mcg", "ml"}


def medication_key(name):
    """What identifies a medication across records: "Guanfacine ER" and "guanfacine" are the same medication,
    and so are "Methylphenidate (Ritalin)" and "methylphenidate"."""
    words = re.split(r"[^a-z0-9]+", re.sub(r"\([^)]*\)", " ", name.lower()))
    return " ".join(w for w in words if w and w not in FORMULATIONS and not re.fullmatch(r"[\d.]+", w)) or name.strip().lower()


NOTE_PROMPT = """You help a child and adolescent psychiatric nurse practitioner write up a visit. Write the note from ONLY
the clinician's notes below, in four parts: "subjective" (what the youth, staff and guardians report since the last
visit), "objective" (the mental status exam as written, then current medications and any scores), "assessment" and
"plan". Rules:
- Never add symptoms, findings, diagnoses, doses, risk statements or plans that aren't in the clinician's notes.
- Keep every detail given: names, doses, dates, scores, quotes.
- Clear, concise clinical prose in the third person, referring to the patient as "the patient" or by first name.
- The clinician may write assessment and plan together: put their clinical impression in "assessment" and the actions
  (medications, monitoring, follow-up) in "plan", without repeating anything between the two. If they give only
  actions and no clinical impression, "assessment" is "Not documented."
- Don't start a part with its own label (no "Subjective:" or "Since the last visit:").
- If a part has nothing in the notes, write "Not documented." for it."""


def draft_visit_note(model, thinking, inputs):
    """A visit note drafted from the clinician's own notes. inputs: plain text of everything they entered.
    Returns {"subjective", "objective", "assessment", "plan"}."""
    part = {"type": "string", "maxLength": 4000}
    schema = {"type": "object", "properties": {"subjective": part, "objective": part, "assessment": part, "plan": part},
              "required": ["subjective", "objective", "assessment", "plan"]}
    note = ask(model, thinking, NOTE_PROMPT, inputs, schema, max_tokens=4000)
    # Smaller models sometimes copy the plan into the assessment, or put some of its actions there
    sentences = lambda text: [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    key = lambda sentence: re.sub(r"\W+", " ", sentence).strip().lower()
    in_plan = {key(s) for s in sentences(note.get("plan", ""))}
    assessment = [s for s in sentences(note.get("assessment", "")) if key(s) not in in_plan]
    actions = [s for s in assessment if PLAN_ACTION.match(s)]
    plan = note.get("plan", "").strip()
    note["assessment"] = " ".join(s for s in assessment if s not in actions) or "Not documented."
    note["plan"] = " ".join(actions + ([plan] if plan and plan != "Not documented." else [])) or plan or "Not documented."
    return note


# Sentences that are actions, so they belong in the plan ("Continue sertraline", not "Continued improvement")
PLAN_ACTION = re.compile(r"(continue|start|begin|initiate|increase|decrease|reduce|stop|discontinue|hold|resume|taper|titrate|"
                         r"recheck|repeat|follow[ -]up|refer|schedule|monitor|obtain|update)\s", re.I)


COTTAGE_PROMPT = """You help a child and adolescent psychiatric nurse practitioner at a residential treatment center understand
the dynamics within one cottage, a living unit several youths share. From ONLY the residents' details and the numbered
facts below, write a short briefing for the clinical team:
- "summary": two to four sentences on who lives there (ages, genders, how long they've been there) and the overall picture.
- "themes": patterns several residents share, or that affect the group (aggression toward peers, trauma reminders,
  substance use, sleep, recent incidents...), naming the residents involved.
- "interactions": what the records say about two or more of these residents together (conflicts, incidents, alliances).
  Only what the facts state; leave it empty if nothing is recorded.
- "watch_for": specific things staff could watch for.
Rules:
- Never add diagnoses, events, risks or interactions the facts don't state, and don't guess at causes.
- Refer to residents by first name. Every theme, interaction and thing to watch for cites the numbers of the facts it
  comes from (at least one).
- Plain, respectful clinical language. If the records say too little to describe the group, say so in the summary."""


INTERCOTTAGE_PROMPT = """You help a child and adolescent psychiatric nurse practitioner at a residential treatment center see how the
cottages (living units) compare and how their residents affect one another. From ONLY the cottages' residents and the
numbered facts below (facts in which one resident's records name a resident of another cottage), write a short briefing:
- "summary": two to four sentences comparing the cottages (how many residents, ages, genders) and the overall picture.
- "themes": differences or patterns across cottages worth the team's attention, naming the cottages involved.
- "interactions": what the records say about residents of different cottages together (conflicts, incidents, contact).
  Only what the facts state; leave it empty if nothing is recorded.
- "watch_for": specific things staff could watch for across cottages.
Rules:
- Never add diagnoses, events, risks or interactions the facts don't state, and don't guess at causes.
- Refer to residents by first name. Every theme, interaction and thing to watch for cites the numbers of the facts it
  comes from (at least one); a theme comparing cottages' makeup may cite none if it rests only on the resident lists.
- Plain, respectful clinical language. If the records say too little, say so in the summary."""

FEEDBACK_NOTE = """
This is experimental, and the clinician gives feedback on these briefings. Where it fits, follow their recent feedback:
{feedback}"""


def unnumbered(text):
    """Text without fact numbers the model wrote into it ("[3]", "(facts 2 and 5)")."""
    return re.sub(r"\s*[\[(](?:facts?\s*)?\d+(?:\s*(?:,|and|-|&)\s*(?:facts?\s*)?\d+)*[\])]", "", text or "", flags=re.I).strip()


def grounded(items, keys, facts, required=True):
    """The points that cite real facts (or all of them, when required is False), with those facts as their sources."""
    kept = []
    for item in items or []:
        sources = [facts[n - 1] for n in dict.fromkeys(item.get("facts") or []) if isinstance(n, int) and 0 < n <= len(facts)]
        if (sources or not required) and unnumbered(item.get("detail")):
            kept.append({**{k: unnumbered(item.get(k)) if isinstance(item.get(k), str) else item.get(k) for k in keys},
                         "sources": [{k: s[k] for k in ("resident", "resident_id", "fact", "document", "date")} for s in sources]})
    return kept


def briefing_schema(group_key):
    text, cited = {"type": "string", "maxLength": 700}, {"type": "array", "items": {"type": "integer"}}
    names = {"type": "array", "items": {"type": "string", "maxLength": 80}}
    return {"type": "object", "properties": {
        "summary": {"type": "string", "maxLength": 1200},
        "themes": {"type": "array", "items": {"type": "object", "properties": {"title": {"type": "string", "maxLength": 80},
                   "detail": text, group_key: names, "facts": cited}, "required": ["title", "detail", group_key, "facts"]}},
        "interactions": {"type": "array", "items": {"type": "object", "properties": {"detail": text, "residents": names, "facts": cited},
                         "required": ["detail", "residents", "facts"]}},
        "watch_for": {"type": "array", "items": {"type": "object", "properties": {"detail": text, "facts": cited},
                      "required": ["detail", "facts"]}}},
        "required": ["summary", "themes", "interactions", "watch_for"]}


def with_feedback(prompt, feedback):
    lines = [f"- ({f['rating']}) {f['comment']}" for f in feedback or [] if f.get("comment")]
    return prompt + FEEDBACK_NOTE.format(feedback="\n".join(lines)) if lines else prompt


def analyze_cottage(model, thinking, inputs, feedback=()):
    """An experimental briefing on one cottage's dynamics from its residents' records (vault.cottage_inputs). Every point
    cites the facts it comes from; points citing none are dropped. feedback: the clinician's recent comments, to follow.
    Returns {"summary", "themes", "interactions", "watch_for"}, each point with its "sources"."""
    if not inputs["residents"]:
        raise ValueError("No one lives in this cottage yet. Set patients' Cottage to it first.")
    facts = inputs["facts"]
    lines = [f"Cottage: {inputs['cottage']}", "Residents:"]
    for r in inputs["residents"]:
        about = ", ".join(x for x in (f"{r['age']}" if r["age"] is not None else "", r["gender"],
                                      f"admitted {r['admitted']}" if r["admitted"] else "") if x)
        lines.append(f"- {r['name']}{' (' + about + ')' if about else ''}."  # (no "no medications": models over-generalize it)
                     f"{' Current medications: ' + ', '.join(r['medications']) + '.' if r['medications'] else ''}"
                     f"{' Latest scores: ' + '; '.join(r['scores']) + '.' if r['scores'] else ''}")
    lines.append("Facts:")
    lines += [f"[{n}] ({f['resident']}; {f['document']}{', ' + f['date'] if f['date'] else ''}) {f['fact']}" for n, f in enumerate(facts, 1)]
    reply = ask(model, thinking, with_feedback(COTTAGE_PROMPT, feedback), "\n".join(lines), briefing_schema("residents"), max_tokens=4000)
    return {"summary": unnumbered(reply.get("summary")), "themes": grounded(reply.get("themes"), ("title", "detail", "residents"), facts),
            "interactions": grounded(reply.get("interactions"), ("detail", "residents"), facts),
            "watch_for": grounded(reply.get("watch_for"), ("detail",), facts)}


def analyze_cottages(model, thinking, inputs, feedback=()):
    """An experimental briefing across all the cottages (vault.intercottage_inputs), in the same shape as analyze_cottage."""
    if not inputs["cottages"]:
        raise ValueError("No one is in a cottage yet. Set patients' Cottage first.")
    facts = inputs["facts"]
    lines = ["Cottages and their current residents:"]
    for c in inputs["cottages"]:
        lines.append(f"- {c['name']}: " + "; ".join(f"{r['name']}" + (f" ({', '.join(x for x in (str(r['age']) if r['age'] is not None else '', r['gender']) if x)})"
                                                                      if r["age"] is not None or r["gender"] else "") for r in c["residents"]))
    lines.append("Facts naming a resident of another cottage:" if facts else "No facts name a resident of another cottage.")
    lines += [f"[{n}] ({f['resident']}; {f['document']}{', ' + f['date'] if f['date'] else ''}) {f['fact']}" for n, f in enumerate(facts, 1)]
    reply = ask(model, thinking, with_feedback(INTERCOTTAGE_PROMPT, feedback), "\n".join(lines), briefing_schema("cottages"), max_tokens=4000)
    return {"summary": unnumbered(reply.get("summary")),
            "themes": grounded(reply.get("themes"), ("title", "detail", "cottages"), facts, required=False),
            "interactions": grounded(reply.get("interactions"), ("detail", "residents"), facts),
            "watch_for": grounded(reply.get("watch_for"), ("detail",), facts)}


def extract_facts(model, thinking, doc, chunk):
    """The facts in one part of a document. If the answer runs too long, read each half separately.

    Models sometimes stop after a fact or two. If the answer looks too short for the amount of
    text (fewer than one fact per ~400 characters), ask once more for everything it missed.
    """
    excerpt = f"Source: {doc['document_type']}, dated {doc['document_date'] or 'unknown'}\n\nExcerpt:\n{chunk}"
    try:
        facts = ask(model, thinking, EXTRACT_PROMPT, excerpt, EXTRACT_SCHEMA, max_tokens=6000)["facts"]
        if len(chunk) > 800 and len(facts) < len(chunk) / 400:
            more = ask(model, thinking, EXTRACT_PROMPT, excerpt, EXTRACT_SCHEMA, max_tokens=6000, history=[(
                json.dumps({"facts": facts}),
                "That list is incomplete. Go through the excerpt again from the start and list every clinically "
                "relevant fact: symptoms, diagnoses, dates, medications, test scores, placements, services, events "
                "and family details. Include the ones you already listed.")])["facts"]
            if len(more) > len(facts):
                facts = more
        return facts
    except TooLong:
        if len(chunk) < 2000:
            raise
        middle = chunk.rfind("\n", 0, len(chunk) // 2) + 1 or len(chunk) // 2
        return extract_facts(model, thinking, doc, chunk[:middle]) + extract_facts(model, thinking, doc, chunk[middle:])


def chunk_pages(pages):
    """Group pages into parts of about CHUNK_CHARS, keeping page markers for the model."""
    chunks, current = [], ""
    for p in pages:
        if p["page"] is None:  # Word documents: split on paragraph breaks
            for paragraph in p["text"].split("\n"):
                if len(current) + len(paragraph) > CHUNK_CHARS and current:
                    chunks.append(current)
                    current = ""
                current += paragraph + "\n"
            continue
        block = f"=== Page {p['page']} ===\n{p['text']}\n\n"
        if len(current) + len(block) > CHUNK_CHARS and current:
            chunks.append(current)
            current = ""
        current += block
    if current.strip():
        chunks.append(current)
    return chunks


def normalize(text):
    text = text.lower().replace("’", "'").replace("‘", "'")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def trigrams(words):
    return {tuple(words[i:i + 3]) for i in range(len(words) - 2)}


def locate(quote, pages):
    """Find which page a quote comes from. Returns (found, page number or None).

    Exact match first; then a looser match (80% of three-word runs present) so small
    OCR errors or the model fixing a typo don't count against it.
    """
    q = normalize(quote)
    words = q.split()
    if len(words) < 3:
        return False, None
    for p in pages:
        if q in p["normalized"]:
            return True, p["page"]
    wanted = trigrams(words)
    best_page, best_score = None, 0
    for p in pages:
        score = len(wanted & p["trigrams"]) / len(wanted)
        if score > best_score:
            best_page, best_score = p["page"], score
    return (True, best_page) if best_score >= 0.8 else (False, None)


def cite(fact, documents):
    page = f" p.{fact['page']}" if fact.get("page") else ""
    return f"D{fact['doc'] + 1}{page}"


def prepare(doc):
    """Split a document into parts and index its pages for quote checking (once)."""
    if "chunks" not in doc:
        doc["chunks"] = chunk_pages(doc["pages"])
        for p in doc["pages"]:
            p["normalized"] = normalize(p["text"])
            p["trigrams"] = trigrams(p["normalized"].split())


def read_document(doc, label, model, thinking, document_fields=None, pull=None, types=None):
    """Read one document: identify it if its type isn't known yet, pull out its facts, and fill in its type's fields.

    Yields {"progress": text, "done": parts finished} as it goes, warnings, and finally
    {"document_read": {"info", "facts", "filled"}}. The parts are identifying (1) and each chunk.
    pull: what to take, {"facts": bool, "fields": [names], or None for all}. When facts aren't taken, "facts" is None.
    """
    prepare(doc)
    pull = pull or {"facts": True, "fields": None}
    done = 0
    if doc.get("info"):  # already known: confirmed when uploaded, typed into MindChart, or corrected by hand
        info = doc["info"]
    else:
        yield {"progress": f"Identifying {label}", "done": done}
        try:
            found = identify(model, thinking, doc["name"], doc["chunks"][0], types or DEFAULT_TYPES)
            info = {key: found.get(key, "") for key in ("document_type", "document_date", "author")}
        except AIUnavailable:
            raise
        except Exception:
            info = {"document_type": "Other", "document_date": "", "author": ""}
    doc.update(info)
    doc["info"] = info
    done += 1

    facts = None
    if pull.get("facts", True):
        facts = []
        for c_index, chunk in enumerate(doc["chunks"]):
            yield {"progress": f"Reading {label}, part {c_index + 1} of {len(doc['chunks'])}", "done": done}
            try:
                found = extract_facts(model, thinking, doc, chunk)
            except AIUnavailable:
                raise
            except Exception as error:
                yield {"warning": f"Couldn't read part {c_index + 1} of {label}: {error}"}
                found = []
            for f in found:
                if not f.get("fact", "").strip():
                    continue
                verified, page = locate(f.get("quote", ""), doc["pages"])
                facts.append({**f, "page": page, "verified": verified})
            done += 1

    wanted = [d for d in (document_fields or {}).get(doc["document_type"]) or []
              if pull.get("fields") is None or d["name"] in pull["fields"]]
    filled = {}
    if wanted:
        yield {"progress": f"Filling in {doc['document_type']} details for {label}", "done": done}
        try:
            filled = fill_fields(model, thinking, doc, wanted)
        except AIUnavailable:
            raise
        except Exception as error:
            yield {"warning": f"Couldn't fill in the details for {label}: {error}"}
    yield {"document_read": {"info": info, "facts": facts, "filled": filled}}


def build(documents, model, thinking, document_fields=None, types=None):
    """Run the whole process, yielding progress updates and finally the result.

    documents: [{"name": "file.pdf", "pages": [{"page", "text", "scanned"}, ...]}, ...]
    A document that was read before can include its saved "info" and "facts"; it isn't
    read again. Each newly read document is reported as {"document_read": ...} so it
    can be saved for next time.
    document_fields: {record type: [field definitions the AI fills in]}; filled values come back in "document_read".
    types: {document type: what it is}, for identifying documents whose type isn't known.
    """
    for doc in documents:
        prepare(doc)
    total = sum(len(d["chunks"]) + 1 for d in documents if "facts" not in d) + len(SECTIONS)
    step = 0
    facts = []

    for d_index, doc in enumerate(documents):
        label = f"D{d_index + 1} ({doc['name']})"
        if "facts" in doc:  # read on an earlier run
            doc.update(doc.get("info") or {"document_type": "Other", "document_date": "", "author": ""})
            facts.extend({**f, "id": len(facts) + i, "doc": d_index} for i, f in enumerate(doc["facts"]))
            continue
        for update in read_document(doc, label, model, thinking, document_fields, doc.get("pull"), types):
            if "document_read" in update:
                read = update["document_read"]
                facts.extend({**f, "id": len(facts) + i, "doc": d_index} for i, f in enumerate(read["facts"] or []))
                yield {"document_read": {"index": d_index, **read}}
            elif "progress" in update:
                yield {"progress": update["progress"], "step": step + update["done"], "total": total}
            else:
                yield update
        step += len(doc["chunks"]) + 1

    sections = []
    for key, title, _ in SECTIONS:
        yield {"progress": f"Writing {title}", "step": step, "total": total}
        section_facts = [f for f in facts if f["section"] == key]
        usable = [f for f in section_facts if f["verified"]]
        narrative, conflicts = "No information found in the provided records.", []
        if usable:
            numbered = "\n".join(
                # The event's date goes inside the source note, so it isn't copied into the text as "Date: ...".
                f"[{n}] (source: {documents[f['doc']]['document_type']}, {documents[f['doc']]['document_date'] or 'undated'}"
                f"{'; happened: ' + f['date'] if f['date'] else ''}) {f['fact']}"
                for n, f in enumerate(usable, 1)
            )
            try:
                written = ask(model, thinking, WRITE_PROMPT.format(title=title), f"Facts:\n{numbered}", WRITE_SCHEMA, max_tokens=8000)
                narrative = join_sentences(written["sentences"], usable, documents)
                conflicts = [
                    {"description": c["description"],
                     "sources": "; ".join(dict.fromkeys(cite(usable[n - 1], documents) for n in c["facts"] if 0 < n <= len(usable)))}
                    for c in written.get("conflicts", []) if c.get("description")
                ]
            except Exception as error:
                # Fall back to the verified facts themselves, each with its source, so nothing is lost.
                yield {"warning": f"Couldn't write {title} as a paragraph ({type(error).__name__}); listed its facts instead"}
                narrative = " ".join(f"{f['fact'].rstrip('.')}. [{cite(f, documents)}]" for f in usable)
        sections.append({
            "key": key, "title": title, "narrative": narrative, "conflicts": conflicts,
            "facts": [{**f, "source": cite(f, documents)} for f in section_facts],
        })
        step += 1

    yield {"result": {
        "sections": sections,
        "documents": [
            {"label": f"D{i + 1}", "name": d["name"], "type": d["document_type"], "date": d["document_date"],
             "author": d["author"], "pages": len(d["pages"]) if d["pages"][0]["page"] else None,
             "scanned_pages": [p["page"] for p in d["pages"] if p["scanned"]],
             "unclear_pages": [p["page"] for p in d["pages"] if p.get("unclear")]}
            for i, d in enumerate(documents)
        ],
    }, "step": total, "total": total}


def join_sentences(sentences, usable, documents):
    """Turn the model's sentences and fact numbers into a paragraph with sources like [D1 p.3; D2 p.7].

    A sentence whose fact numbers don't match any fact is marked [no source] so it stands out.
    """
    parts = []
    for sentence in sentences:
        # Drop fact numbers the model wrote itself, like "[3]" or "(fact 2)" or "(facts 1 and 4)"; MindChart adds sources.
        text = re.sub(r"\s*(\[[\d,\s]+\]|\((?:facts?|fact\s*#)\s*[\d,\s]+(?:and\s*\d+)?\))", "", sentence.get("text", "")).strip()
        if not text:
            continue
        labels = list(dict.fromkeys(cite(usable[n - 1], documents) for n in sentence.get("facts", []) if 0 < n <= len(usable)))
        parts.append(f"{text} [{'; '.join(labels) or 'no source'}]")
    return " ".join(parts) or "No information found in the provided records."


# --- Word export ---------------------------------------------------------------

def build_docx(sections, documents):
    """A simple .docx (Word) file of the draft history, made without extra software."""
    def paragraph(text, bold=False, italic=False, size=None, color=None, space_after=120):
        props = ("<w:b/>" if bold else "") + ("<w:i/>" if italic else "") + \
                (f'<w:sz w:val="{size}"/>' if size else "") + (f'<w:color w:val="{color}"/>' if color else "")
        return (f'<w:p><w:pPr><w:spacing w:after="{space_after}"/></w:pPr>'
                f'<w:r><w:rPr>{props}</w:rPr><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>')

    body = [
        paragraph("Psychiatric Evaluation: History (DRAFT)", bold=True, size=32),
        paragraph("Drafted by a local AI model from the source documents listed at the end. "
                  "It must be reviewed and verified against the source records by the clinician before use. "
                  "Citations like [D2 p.4] refer to document 2, page 4.", italic=True, size=18, color="666666", space_after=240),
    ]
    for s in sections:
        body.append(paragraph(s["title"], bold=True, size=26, space_after=80))
        for line in (s.get("narrative") or "").split("\n"):
            if line.strip():
                body.append(paragraph(line.strip()))
        for c in s.get("conflicts", []):
            body.append(paragraph(f"Check: {c['description']} [{c.get('sources', '')}]", italic=True, color="B00020"))
        body.append(paragraph("", space_after=120))
    body.append(paragraph("Source documents", bold=True, size=26, space_after=80))
    for d in documents:
        details = ", ".join(x for x in [d.get("type"), d.get("date"), d.get("author")] if x)
        body.append(paragraph(f"{d['label']}: {d['name']}" + (f" ({details})" if details else ""), space_after=60))

    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                + "".join(body) +
                '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
                '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>'
                '</w:body></w:document>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   '</Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" '
                   'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                   'Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", document)
    return buffer.getvalue()
