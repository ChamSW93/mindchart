"""When each fact was true, and which ongoing facts are current.

Records from different years say different things: "in 10th grade" in a 2023 evaluation and "in 11th grade" in a 2024
IEP are both right, for their dates. So each fact is tagged when it's read (history.EXTRACT_RULES):
- an Event happened once, on its date (a hospitalization, an incident, a medication started or stopped);
- an Ongoing fact was true when its record was written, and can change (grade, placement, a medication's dose);
- Background doesn't change (birth and family history).
Ongoing facts also get a topic, such as "grade" or "medication: sertraline". Which ongoing fact on a topic is current is
then worked out here from the dates alone, never by the AI: the newest is current, and older ones are "earlier".
"""
import re

import fields as fieldtypes

KINDS = ["Event", "Ongoing", "Background"]
CURRENT_DAYS = 45  # ongoing facts on a topic dated within this of the newest are current too (records from about then)

# The topic names the AI is asked to use (history.EXTRACT_RULES), with other ways of saying them
TOPICS = {
    "grade": ["grade level", "school grade", "year in school", "class"],
    "school": ["school placement", "educational placement", "educational setting", "school setting"],
    "special education": ["iep", "504", "504 plan", "special ed", "special education services", "classification"],
    "placement": ["residential placement", "level of care", "setting", "program", "residence"],
    "lives with": ["living situation", "living arrangement", "household", "home", "caregiver", "caregivers"],
    "guardianship": ["custody", "legal guardian", "guardian", "legal custody"],  # who has legal custody, not visits
    "legal status": ["legal", "court", "probation", "court involvement", "juvenile justice"],
    "sleep": [], "appetite": [],
}
SYNONYMS = {other: topic for topic, others in TOPICS.items() for other in others}
# Topics that name one thing each ("medication: sertraline", "symptom: nightmares"). A broad topic on its own ("symptoms")
# never makes another fact earlier: newer facts about nightmares say nothing about whether older anxiety has gone.
ITEM_TOPICS = {"medication": ["medication", "medications", "med", "meds", "medicine"],
               "diagnosis": ["diagnosis", "diagnoses", "dx"],
               "substance": ["substance", "substance use", "substances", "drug", "drugs"],
               "symptom": ["symptom", "symptoms", "presenting symptom"],
               "behavior": ["behavior", "behaviors", "behaviour", "behaviours", "conduct"],
               "therapy": ["therapy", "psychotherapy", "counseling", "treatment"],
               "service": ["service", "services", "support", "supports"],
               "visitation": ["visitation", "visits", "visit", "contact"]}
ITEM_SYNONYMS = {other: topic for topic, others in ITEM_TOPICS.items() for other in others}
# Where each topic shows in the current picture
GROUPS = [("School", ("grade", "school", "special education")),
          ("Home and legal", ("placement", "lives with", "guardianship", "legal status")),
          ("Treatment", ("medication", "diagnosis", "therapy", "service")),
          ("How they're doing", ())]  # everything else


# Diagnoses written different ways in different records ("PTSD", "chronic post-traumatic stress disorder")
DIAGNOSES = [(r"post[- ]?traumatic stress|\bptsd\b", "ptsd"), (r"attention[- ]deficit|\badhd\b|\badd\b", "adhd"),
             (r"major depressi|\bmdd\b", "major depressive disorder"), (r"generali[sz]ed anxiety|\bgad\b", "generalized anxiety disorder"),
             (r"oppositional defiant|\bodd\b", "oppositional defiant disorder"), (r"disruptive mood|\bdmdd\b", "dmdd"),
             (r"autis|\basd\b", "autism spectrum disorder"), (r"conduct disorder", "conduct disorder"), (r"bipolar", "bipolar disorder"),
             (r"reactive attachment|\brad\b", "reactive attachment disorder"), (r"intellectual disabilit", "intellectual disability"),
             (r"learning disorder|learning disabilit", "learning disorder"), (r"social anxiety", "social anxiety disorder"),
             (r"obsessive|\bocd\b", "ocd"), (r"psychosis|psychotic|schizo", "psychotic disorder"),
             (r"substance use|cannabis use|alcohol use", "substance use disorder"), (r"adjustment disorder", "adjustment disorder")]


def diagnosis_key(name):
    """ "chronic PTSD" -> "ptsd"; a rule-out stays apart from the diagnosis itself ("rule out ptsd")."""
    ruled = re.match(r"\s*(r/o|rule[- ]out)\b", name)
    found = next((key for pattern, key in DIAGNOSES if re.search(pattern, name)), name)
    return f"rule out {found}" if ruled else found


def topic_key(topic):
    """A topic written a standard way, so the same thing said differently in two records is one topic:
    "School Grade" -> "grade"; "Medication: Zoloft 50 mg" -> "medication: sertraline"."""
    text = " ".join(str(topic or "").lower().replace("_", " ").split()).strip(" .:-")
    if not text:
        return ""
    head, colon, item = (part.strip() for part in text.partition(":"))
    if colon and item and head in ITEM_SYNONYMS:
        head = ITEM_SYNONYMS[head]
        if head == "medication":  # the name without its dose ("sertraline 50 mg" -> "sertraline")
            item = fieldtypes.canonical_medication(re.split(r"\s+\d", item)[0]).lower()
        elif head == "diagnosis":
            item = diagnosis_key(item)
        return f"{head}: {item}"
    if colon and item:
        return f"{SYNONYMS.get(head, head)}: {item}"
    return SYNONYMS.get(text, text)


def group_of(key):
    head = key.split(":")[0]
    return next((name for name, topics in GROUPS if head in topics), GROUPS[-1][0])


def day_number(key):
    """A (year, month, day) date key as a rough count of days, for comparing; unknown months and days count as the middle."""
    year, month, day = key
    return year * 372 + (month or 6.5) * 31 + (day or 15)


def as_of(f):
    """When an ongoing fact was known to be true: its record's date. A date in the fact itself is when it started or ends
    ("lives with foster mother since 2019", "an order of protection until March 2025"), so it's only used when the
    record's date isn't known."""
    return f.get("document_date") if fieldtypes.date_key(f.get("document_date")) else (f.get("date") or "")


def annotate(facts):
    """Add to each fact (dicts with "kind", "topic", "date", "document_date", "verified" and optionally "patient_id"):
    "as_of": when it was true (an ongoing fact's record's date; an event's own date),
    "topic_key", "group" (where an ongoing fact shows in the current picture: GROUPS), and "status": "current" or
    "earlier" for ongoing facts, "event", "background", or "" if not tagged yet.
    An earlier fact also gets "newer": the date of the newest record on its topic. Facts whose quote wasn't found in their
    source don't make others earlier. Returns the same list."""
    topics = {}
    for f in facts:
        kind = f.get("kind") or ""
        f["topic_key"] = topic_key(f.get("topic")) if kind == "Ongoing" else ""
        f["status"], f["newer"] = kind.lower(), ""
        if kind == "Ongoing":
            f["as_of"] = as_of(f)
            f["status"] = "current"
            f["group"] = group_of(f["topic_key"])
            one_thing = f["topic_key"] in TOPICS or ":" in f["topic_key"]  # (a broad topic or none: it stands alone)
            topics.setdefault((f.get("patient_id"), f["topic_key"] if one_thing else id(f)), []).append(f)
        else:
            f["as_of"] = f.get("date") or ""
    for group in topics.values():
        dated = [(day_number(fieldtypes.date_key(f["as_of"])), f) for f in group if fieldtypes.date_key(f["as_of"])]
        counted = [d for d, f in dated if f.get("verified")] or [d for d, _ in dated]
        if not counted:
            continue  # nothing to go by, so all are current
        newest = max(counted)
        newest_text = next(f["as_of"] for d, f in dated if d == newest)
        for f in group:
            key = fieldtypes.date_key(f["as_of"])
            if not key or newest - day_number(key) > CURRENT_DAYS:
                f.update(status="earlier", newer=newest_text)  # (undated ones give way to dated ones)
    return facts


ORDER = {"current": 0, "background": 1, "event": 2, "": 3, "earlier": 4}


def in_order(facts):
    """Current facts first, then background, events in date order, untagged facts, and earlier ones last (newest first)."""
    def key(f):
        when = fieldtypes.date_key(f.get("as_of")) or (0, 0, 0)
        return (ORDER.get(f.get("status"), 3), when if f.get("status") == "event" else tuple(-x for x in when))
    return sorted(facts, key=key)
