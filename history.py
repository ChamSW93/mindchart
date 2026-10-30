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
import urllib.request
import zipfile
from xml.sax.saxutils import escape

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
DOCUMENT_TYPES = [
    "Psychiatric Evaluation", "Psychological Evaluation", "Progress or Clinical Note",
    "Individualized Education Program (IEP)", "Court Order", "Discharge Summary", "Other",
]

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

DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {"type": "string", "enum": DOCUMENT_TYPES},
        "document_date": {"type": "string", "maxLength": 30},
        "author": {"type": "string", "maxLength": 80},
    },
    "required": ["document_type", "document_date", "author"],
}

DESCRIBE_PROMPT = """Identify this clinical record from its file name and first page.
- document_type: the closest type:
  - Psychiatric Evaluation: a psychiatrist's or nurse practitioner's diagnostic evaluation (often with history, mental status exam, diagnoses)
  - Psychological Evaluation: a psychologist's testing report (IQ, achievement or personality tests)
  - Progress or Clinical Note: a medication management, therapy or progress note
  - Individualized Education Program (IEP): a school special education plan (classification, services, goals)
  - Court Order: an order, decision or petition from a court
  - Discharge Summary: a summary written when leaving a hospital or program
- document_date: the date the document was written, exactly as written.
- author: only the name and credentials of the clinician, judge or team who wrote it.
Use an empty string for anything not stated."""

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
    with urllib.request.urlopen(req, timeout=900) as response:
        reply = json.load(response)
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


def build(documents, model, thinking):
    """Run the whole process, yielding progress updates and finally the result.

    documents: [{"name": "file.pdf", "pages": [{"page", "text", "scanned"}, ...]}, ...]
    A document that was read before can include its saved "info" and "facts"; it isn't
    read again. Each newly read document is reported as {"document_read": ...} so it
    can be saved for next time.
    """
    for doc in documents:
        doc["chunks"] = chunk_pages(doc["pages"])
        for p in doc["pages"]:
            p["normalized"] = normalize(p["text"])
            p["trigrams"] = trigrams(p["normalized"].split())
    total = sum(len(d["chunks"]) + 1 for d in documents if "facts" not in d) + len(SECTIONS)
    step = 0
    facts = []

    for d_index, doc in enumerate(documents):
        label = f"D{d_index + 1} ({doc['name']})"
        if "facts" in doc:  # read on an earlier run
            doc.update(doc.get("info") or {"document_type": "Other", "document_date": "", "author": ""})
            facts.extend({**f, "id": len(facts) + i, "doc": d_index} for i, f in enumerate(doc["facts"]))
            continue
        doc_facts = []
        if doc.get("info"):  # already known, e.g. a note typed into MindChart or a type corrected by hand
            info = doc["info"]
        else:
            yield {"progress": f"Identifying {label}", "step": step, "total": total}
            try:
                info = ask(model, thinking, DESCRIBE_PROMPT, f"File name: {doc['name']}\n\n{doc['chunks'][0][:4000]}",
                           DESCRIBE_SCHEMA, max_tokens=800)
            except Exception:
                info = {"document_type": "Other", "document_date": "", "author": ""}
        doc.update(info)
        doc["info"] = info
        step += 1

        for c_index, chunk in enumerate(doc["chunks"]):
            yield {"progress": f"Reading {label}, part {c_index + 1} of {len(doc['chunks'])}", "step": step, "total": total}
            try:
                found = extract_facts(model, thinking, doc, chunk)
            except Exception as error:
                yield {"warning": f"Couldn't read part {c_index + 1} of {label}: {error}"}
                found = []
            for f in found:
                if not f.get("fact", "").strip():
                    continue
                verified, page = locate(f.get("quote", ""), doc["pages"])
                doc_facts.append({**f, "page": page, "verified": verified})
            step += 1
        facts.extend({**f, "id": len(facts) + i, "doc": d_index} for i, f in enumerate(doc_facts))
        yield {"document_read": {"index": d_index, "info": info, "facts": doc_facts}}

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
             "scanned_pages": [p["page"] for p in d["pages"] if p["scanned"]]}
            for i, d in enumerate(documents)
        ],
    }, "step": total, "total": total}


def join_sentences(sentences, usable, documents):
    """Turn the model's sentences and fact numbers into a paragraph with sources like [D1 p.3; D2 p.7].

    A sentence whose fact numbers don't match any fact is marked [no source] so it stands out.
    """
    parts = []
    for sentence in sentences:
        text = re.sub(r"\s*\[[\d,\s]+\]", "", sentence.get("text", "")).strip()  # drop any [n] the model wrote itself
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
