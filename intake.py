"""Working out what an uploaded file is and who it's about, for the clinician to confirm.

Two passes:
1. Straight away, without AI: MindChart's learned guess at the type (learning.py), and a search of the
   opening pages for the names, birth dates and ID numbers of patients already in MindChart.
2. In the background, the AI reads the opening text for the type, date, author and the child's name, birth
   date and ID numbers (helped by the most similar confirmed examples), and the patient match is redone.

Nothing is filed until the clinician confirms it on the upload's review card.
"""
import datetime
import re

import history
import learning
import vault

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "fictional"}
SEARCH_CHARS = 6000  # how much of the opening text is searched for patients' names and birth dates


# --- Dates and names --------------------------------------------------------------

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


def name_parts(name):
    """(first, last) in lower case. Handles "Rivera, Jordan", middle names and initials, and suffixes."""
    name = re.sub(r"\(.*?\)", " ", name or "")
    if "," in name:
        last, _, first = name.partition(",")
        name = f"{first} {last}"
    words = [w for w in re.findall(r"[a-z][a-z'-]*", name.lower()) if w not in SUFFIXES and len(w) > 1]
    return (words[0], words[-1]) if len(words) >= 2 else (words[0], "") if words else ("", "")


def close(a, b):
    """Equal, or one letter different (an OCR slip) for longer names."""
    if a == b:
        return True
    if len(a) < 5 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long_ = sorted((a, b), key=len)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def date_variants(date):
    """How a date might be written in a document, for searching the text."""
    return {date.strftime("%m/%d/%Y"), f"{date.month}/{date.day}/{date.year}", date.strftime("%m/%d/%y"),
            f"{date.month}/{date.day}/{date.strftime('%y')}", date.isoformat(), date.strftime("%m-%d-%Y"),
            f"{date.strftime('%B')} {date.day}, {date.year}", f"{date.strftime('%b')} {date.day}, {date.year}",
            f"{date.day} {date.strftime('%B')} {date.year}"}


# --- Matching a document to a patient ------------------------------------------------

def match_patients(child, patients, text):
    """Which patients a document might be about, best first: [{"id", "name", "dob", "score", "strength", "reasons"}].

    child: what was read about the child {"name", "dob", "identifiers"} (may be empty before the AI has run).
    text: the document's opening text, searched for each patient's name, birth date and MRN as well.
    """
    found_first, found_last = name_parts(child.get("name"))
    found_dob = parse_date(child.get("dob"))
    identifiers = {re.sub(r"[^a-z0-9]", "", i.lower()) for i in child.get("identifiers") or [] if i}
    lowered = text[:SEARCH_CHARS].lower()
    words = set(re.findall(r"[a-z][a-z'-]*", lowered))
    matches = []
    for p in patients:
        first, last = name_parts(p["name"])
        dob = parse_date(p.get("dob"))
        mrn = re.sub(r"[^a-z0-9]", "", (p.get("mrn") or "").lower())
        score, reasons = 0, []
        if mrn and len(mrn) >= 4 and (mrn in identifiers or mrn in re.sub(r"[^a-z0-9]", "", lowered)):
            score += 5
            reasons.append("MRN / ID matches")
        if found_last and last:
            if close(found_last, last) and close(found_first, first):
                score += 3
                reasons.append("name matches" if (found_first, found_last) == (first, last) else "name matches (close spelling)")
            elif close(found_last, last):
                score += 1
                reasons.append("last name matches")
        elif first and last and first in words and last in words and re.search(
                rf"\b{re.escape(first)}\b[^.\n]{{0,25}}\b{re.escape(last)}\b|\b{re.escape(last)},\s*{re.escape(first)}\b", lowered):
            score += 3
            reasons.append("name appears in the document")
        if dob:
            if found_dob == dob:
                score += 3
                reasons.append("date of birth matches")
            elif found_dob:
                score -= 3
                reasons.append(f"different date of birth ({found_dob.strftime('%m/%d/%Y')} in the document)")
            elif any(v.lower() in lowered for v in date_variants(dob)):
                score += 3
                reasons.append("date of birth appears in the document")
        if score > 0:
            strength = "strong" if score >= 6 else "possible"
            matches.append({"id": p["id"], "name": p["name"], "dob": p.get("dob") or "", "score": score,
                            "strength": strength, "reasons": reasons})
    # A full name that matches only one patient, with nothing against it, is likely them even without a birth date.
    named = [m for m in matches if any(r.startswith("name") for r in m["reasons"])]
    if len(named) == 1 and named[0]["strength"] == "possible" and not any(r.startswith("different") for r in named[0]["reasons"]):
        named[0]["strength"] = "likely"
        named[0]["reasons"].append("no date of birth in the document to confirm it")
    return sorted(matches, key=lambda m: -m["score"])


def target_warning(target, matches, child, patients):
    """A warning when a file dropped on one patient's page looks like it's about someone else."""
    if not target:
        return ""
    patient = next((p for p in patients if p["id"] == target), None)
    if not patient:
        return ""
    best = matches[0] if matches else None
    if best and best["id"] != target and best["strength"] in ("strong", "likely"):
        return f"This looks like {best['name']}'s document, not {patient['name']}'s."
    own = next((m for m in matches if m["id"] == target), None)
    if own and any(r.startswith("different date of birth") for r in own["reasons"]):
        return f"The date of birth in this document doesn't match {patient['name']}'s."
    found_first, found_last = name_parts(child.get("name"))
    first, last = name_parts(patient["name"])
    same_first = close(found_first, first) or found_first.startswith(first) or first.startswith(found_first)  # Alex / Alexander
    if found_last and not (close(found_last, last) and same_first):  # a sibling shares the last name, so check both
        return f"This document names {child['name']}, not {patient['name']}."
    return ""


# --- Putting a suggestion together ---------------------------------------------------

def opening_text(pages):
    return "\n".join(p["text"] for p in pages)[:SEARCH_CHARS]


def document_types():
    return {t: d for t, d in vault.record_type_descriptions("documents").items() if t != "Clinician Note"}


def suggest(name, pages, target, ai=None, pending=True):
    """The review card's suggestion for an upload, and the confirmed examples most like it.

    ai: what the AI read (from history.identify), once it has. pending: whether the AI is still to read it.
    """
    text = opening_text(pages)
    patients = vault.patients_for_matching()
    types = document_types()
    exclude = learning.name_words([p["name"] for p in patients] + [p.get("guardian") for p in patients]
                                  + [(ai or {}).get("patient_name")])
    learned = learning.suggest(name, text, vault.type_examples(), types, exclude)
    child = {"name": (ai or {}).get("patient_name", ""), "dob": (ai or {}).get("date_of_birth", ""),
             "identifiers": (ai or {}).get("identifiers", [])}
    matches = match_patients(child, patients, text)
    suggestion = {"learned_type": learned["type"], "learned_confidence": learned["confidence"], "learned_reason": learned["reason"],
                  "child": child, "matches": matches[:5], "ai_done": not pending,
                  "document_date": (ai or {}).get("document_date", ""), "author": (ai or {}).get("author", "")}
    suggestion.update(choose_type(learned, (ai or {}).get("document_type"), pending))
    suggestion.update(choose_patient(target, matches, child, patients))
    return suggestion, learned["similar"]


def choose_patient(target, matches, child, patients):
    """The patient to suggest: the page it was dropped on unless the document names someone else, else a good match."""
    best = matches[0] if matches else None
    warning = target_warning(target, matches, child, patients)
    named = any(r.startswith("name") for m in matches for r in m["reasons"])
    if child.get("name") and not named and not (best and best["strength"] == "strong") and (warning or not target):
        patient = "__new"  # about a child who isn't in MindChart yet: offer a new patient, filled in from the document
    elif target and not (best and best["strength"] in ("strong", "likely") and best["id"] != target):
        patient = target
    else:
        patient = best["id"] if best and best["strength"] in ("strong", "likely") else ""
    return {"patient_id": patient, "warning": warning}


def refresh(upload):
    """Redo an upload's patient match, since patients may have been added (or changed) since it arrived."""
    suggestion = upload["suggestion"]
    if "child" not in suggestion:
        return upload
    patients = vault.patients_for_matching()
    matches = match_patients(suggestion["child"], patients, upload["preview"])
    suggestion.update(matches=matches[:5], **choose_patient(upload["patient_id"], matches, suggestion["child"], patients))
    return upload


def choose_type(learned, ai_type, pending=False):
    """Combine MindChart's learned guess with the AI's: agreement is sure; strong learning beats the AI; else the AI."""
    mine, confidence = learned["type"], learned["confidence"]
    if ai_type and mine == ai_type:
        reason = learned["reason"] + ", and the AI agrees" if learned["reason"].startswith("Looks like") \
            else "The AI and the document's wording agree"
        return {"document_type": ai_type, "type_confidence": "high", "type_reason": reason}
    if mine and confidence == "high":
        note = f" (the AI suggested {ai_type})" if ai_type else ""
        return {"document_type": mine, "type_confidence": "medium" if ai_type else "high", "type_reason": learned["reason"] + note}
    if ai_type:
        source = "your past documents suggest" if learned["reason"].startswith("Looks like") else "its wording suggests"
        note = f"; {source} {mine}" if mine and confidence == "medium" else ""
        return {"document_type": ai_type, "type_confidence": "medium" if ai_type != "Other" else "low",
                "type_reason": "The AI's reading of the document" + note}
    return {"document_type": mine or "Other", "type_confidence": "low" if confidence != "high" else "high",
            "type_reason": (learned["reason"] or "Not recognized yet") + (" · the AI is still reading it" if pending else "")}


def identify(upload, model, thinking):
    """The background pass: have the AI read the upload, then redo the suggestion with what it found."""
    _, similar = suggest(upload["name"], upload["pages"], upload["patient_uid"])
    ai = history.identify(model, thinking, upload["name"], opening_text(upload["pages"]), document_types(),
                          [example for _, example in similar])
    suggestion, _ = suggest(upload["name"], upload["pages"], upload["patient_uid"], ai, pending=False)
    return suggestion
