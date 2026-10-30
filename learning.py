"""Recognizing what type of document a file is, and getting better at it from the clinician's confirmations.

The AI model itself never changes. What MindChart learns is kept as examples in the encrypted store: every
document the clinician confirms or corrects is saved with its type and opening text (see vault.learn_type).
A new upload is compared with those examples word by word (TF-IDF similarity, a standard, fast technique
that needs no AI), so documents that look like ones confirmed before, such as an agency's own note template
or a particular court's orders, are recognized straight away. The most similar examples are also shown to
the AI when it identifies a document, so it learns from them too.

Before there are examples, built-in hints (wording typical of each type) give a first guess. Patients' names
are left out of the comparison, so two documents about the same child don't look alike just because of the name.
Everything here runs on this Mac, and nothing learned leaves it.
"""
import math
import re
from collections import Counter

STOP = set("""about above after again against also among an and any are as at be because been before being
below between both but by can could did do does doing down during each either else for from further had has
have having he her here hers him his how if in into is it its itself just may me might more most must my no nor
not now of off on once only or other our out over own per same she should so some such than that the their them
then there these they this those through to too under until up upon very was we were what when where which while
who whom why will with within without would you your yes
pdf doc docx rtf odt txt png jpg jpeg heic heif tif tiff gif bmp scan scanned img image copy final""".split())  # file names

# Wording typical of each built-in type. A title in the file name or near the top counts for more.
TITLES = {
    "Court Order": ["court order", "order of placement", "order of protection", "order of disposition", "family court"],
    "Individualized Education Program (IEP)": ["individualized education program", "iep", "committee on special education"],
    "Psychological Evaluation": ["psychological evaluation", "psychoeducational evaluation", "psychological assessment"],
    "Psychiatric Evaluation": ["psychiatric evaluation", "psychiatric assessment", "psychiatric consultation"],
    "Discharge Summary": ["discharge summary", "discharge note"],
    "Progress or Clinical Note": ["progress note", "clinical note", "medication management", "therapy note", "nursing note"],
}
HINTS = {
    "Court Order": ["it is ordered", "ordered that", "hereby ordered", "docket", "petition", "respondent", "petitioner",
                    "judge", "hon", "adjudged", "in the matter of", "article 10", "custody", "supreme court", "enter"],
    "Individualized Education Program (IEP)": ["annual goals", "related services", "classification", "cse",
                                               "least restrictive environment", "testing accommodations",
                                               "special education", "present levels", "school district", "12 1 1", "8 1 1"],
    "Psychological Evaluation": ["wisc", "wais", "full scale iq", "fsiq", "working memory index", "processing speed index",
                                 "verbal comprehension", "percentile", "standard score", "tests administered",
                                 "psychologist", "basc", "achievement"],
    "Psychiatric Evaluation": ["chief complaint", "history of present illness", "mental status", "diagnoses", "dsm",
                               "formulation", "past psychiatric history", "family psychiatric history", "pmhnp",
                               "reason for referral", "risk assessment"],
    "Discharge Summary": ["date of discharge", "discharge date", "admission date", "hospital course", "course of treatment",
                          "discharge medications", "discharge diagnosis", "aftercare", "discharged to", "reason for admission"],
    "Progress or Clinical Note": ["subjective", "objective", "assessment and plan", "session", "follow up",
                                  "interval history", "vital signs", "soap", "since last visit", "continue current"],
}
STRONG, LIKELY = 0.5, 0.35  # how similar an upload must be to confirmed examples to suggest their type
PRIOR = 10


def normalize(text):
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "


def tokens(text, exclude=()):
    """Words (and pairs of words) that say what kind of document this is; numbers, short words and names left out."""
    words = [w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in STOP and w not in exclude]
    return words + [f"{a} {b}" for a, b in zip(words, words[1:])]


def name_words(names):
    """The words in people's names, to leave out of comparisons."""
    return {w for name in names if name for w in re.findall(r"[a-z]{3,}", name.lower())}


class Recognizer:
    """Compares a document with the confirmed examples. examples: [{"document_type", "name", "text"}]."""

    def __init__(self, examples, exclude=()):
        self.exclude = set(exclude)
        self.examples = examples
        counts = [Counter(tokens(f"{e['name']}\n{e['text']}", self.exclude)) for e in examples]
        document_frequency = Counter(t for c in counts for t in c)
        # Counted as if there were PRIOR more documents that share no words, so that with only a few examples, wording
        # they all share (a template's headings) still counts for something.
        total = len(examples) + PRIOR
        self.unseen = math.log(total) + 1  # the weight of a word no example has
        self.idf = {t: math.log(total / (n + 1)) + 1 for t, n in document_frequency.items()}
        self.vectors = [self.vector(c) for c in counts]

    def vector(self, counts):
        weights = {t: (1 + math.log(n)) * self.idf.get(t, self.unseen) for t, n in counts.items()}
        norm = math.sqrt(sum(w * w for w in weights.values())) or 1
        return {t: w / norm for t, w in weights.items() if t in self.idf}

    def similar(self, name, text, k=5):
        """The k most similar examples: [(similarity 0 to 1, example)], most similar first."""
        query = self.vector(Counter(tokens(f"{name}\n{text}", self.exclude)))
        scored = [(sum(query.get(t, 0) * w for t, w in v.items()), i) for i, v in enumerate(self.vectors)]
        return [(round(score, 3), self.examples[i]) for score, i in sorted(scored, reverse=True)[:k] if score > 0.05]


def hint_scores(name, text, types):
    """How strongly the built-in hints point to each type."""
    # Titles are near the top, on a short line or one in capitals (not, say, "Plan: psychiatric evaluation in two weeks").
    lines = [line.strip() for line in text.splitlines() if line.strip()][:10]
    heading = " | ".join(line for line in lines if len(line) <= 70 or sum(c.isupper() for c in line) > 0.7 * sum(c.isalpha() for c in line))
    whole, top = normalize(f"{name} {text[:8000]}"), normalize(f"{name} {heading}")
    scores = {}
    for document_type in types:
        score = sum(3 for t in TITLES.get(document_type, []) if f" {t} " in top)
        score += sum(1 for h in HINTS.get(document_type, []) if f" {h} " in whole)
        if score:
            scores[document_type] = score
    return scores


def suggest(name, text, examples, types, exclude=()):
    """MindChart's own guess at a document's type, before (or without) the AI.

    Returns {"type", "confidence" ("high", "medium", "low" or None), "reason", "similar": [examples to show the AI]}.
    """
    types = list(types)
    similar = Recognizer([e for e in examples if e["document_type"] in types], exclude).similar(name, text) if examples else []
    votes = Counter()
    for score, example in similar:
        if score >= LIKELY:
            votes[example["document_type"]] += 1
    if similar:
        best_score, best = similar[0]
        agreeing = votes[best["document_type"]]
        if best_score >= STRONG and agreeing >= 2:
            return {"type": best["document_type"], "confidence": "high", "similar": similar[:3],
                    "reason": f"Looks like {agreeing} documents you confirmed as {best['document_type']}"}
        if best_score >= LIKELY:
            return {"type": best["document_type"], "confidence": "medium", "similar": similar[:3],
                    "reason": f"Looks like a document you confirmed as {best['document_type']}"}
    hints = hint_scores(name, text, types)
    if hints:
        ranked = sorted(hints.items(), key=lambda kv: -kv[1])
        document_type, score = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0
        confidence = "medium" if score >= 4 and score >= runner_up + 2 else "low"
        return {"type": document_type, "confidence": confidence, "similar": similar[:3],
                "reason": f"Has wording typical of {'an' if document_type[0] in 'AEIOU' else 'a'} {document_type}"}
    return {"type": None, "confidence": None, "similar": similar[:3], "reason": ""}
