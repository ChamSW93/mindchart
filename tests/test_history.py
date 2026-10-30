"""Fact checking, citations and the drafting flow, with the AI model replaced by canned answers."""
import io
import unittest
import zipfile

import support  # noqa: F401  (sets up paths)
import history


def page(text, number=1):
    normalized = history.normalize(text)
    return {"page": number, "text": text, "scanned": False, "normalized": normalized,
            "trigrams": history.trigrams(normalized.split())}


class QuoteChecking(unittest.TestCase):
    pages = [page("Jordan was born at 34 weeks gestation and spent 10 days in the NICU.", 1),
             page("Sertraline 25 mg daily was started on 05/01/2024.", 2)]

    def test_exact_quote_is_found_with_its_page(self):
        self.assertEqual(history.locate("started on 05/01/2024", self.pages), (True, 2))

    def test_small_differences_still_match(self):
        # OCR slips and curly quotes shouldn't count against a real quote.
        self.assertEqual(history.locate("born at 34 weeks gestation and spent 10 days in the N1CU", self.pages), (True, 1))

    def test_made_up_quote_is_rejected(self):
        self.assertEqual(history.locate("no history of aggression was reported", self.pages), (False, None))

    def test_too_short_quote_is_rejected(self):
        self.assertEqual(history.locate("Grade 7", self.pages), (False, None))


class Citations(unittest.TestCase):
    facts = [{"doc": 0, "page": 3}, {"doc": 1, "page": None}]

    def test_sentences_get_sources(self):
        text = history.join_sentences([{"text": "Born preterm.", "facts": [1]}, {"text": "Has an IEP. [2]", "facts": [2, 1]}],
                                      self.facts, [])
        self.assertEqual(text, "Born preterm. [D1 p.3] Has an IEP. [D2; D1 p.3]")

    def test_unknown_fact_numbers_are_flagged(self):
        self.assertEqual(history.join_sentences([{"text": "Something.", "facts": [9]}], self.facts, []), "Something. [no source]")

    def test_special_characters_are_made_plain(self):
        self.assertEqual(history.tidy({"a": ["12‑year old"]}), {"a": ["12-year old"]})


class Chunking(unittest.TestCase):
    def test_pages_are_grouped_and_long_word_files_split(self):
        pdf = history.chunk_pages([{"page": n, "text": "x" * 4000, "scanned": False} for n in range(1, 6)])
        self.assertTrue(all("=== Page" in c for c in pdf))
        self.assertGreater(len(pdf), 1)
        word = history.chunk_pages([{"page": None, "text": ("paragraph " * 50 + "\n") * 60, "scanned": False}])
        self.assertTrue(all(len(c) <= history.CHUNK_CHARS + 600 for c in word))


class Drafting(unittest.TestCase):
    """The whole build flow with a fake model, so it runs in seconds and needs no AI."""

    def setUp(self):
        self.real_ask = history.ask
        history.ask = self.fake_ask

    def tearDown(self):
        history.ask = self.real_ask

    def fake_ask(self, model, thinking, system, user, schema, max_tokens, history=()):
        if schema is history_module.DESCRIBE_SCHEMA:
            return {"document_type": "Psychiatric Evaluation", "document_date": "09/18/2023", "author": "Dr. Test"}
        if schema is history_module.EXTRACT_SCHEMA:
            return {"facts": [
                {"section": "medications", "fact": "Takes guanfacine 1 mg.", "date": "", "quote": "guanfacine 1 mg at bedtime"},
                {"section": "risk", "fact": "Denies all risk.", "date": "", "quote": "this sentence is not in the record"},
            ] * 6}  # plenty of facts, so the "too few facts" retry doesn't kick in
        return {"sentences": [{"text": "He takes guanfacine.", "facts": [1]}], "conflicts": []}

    def test_only_verified_facts_reach_the_draft(self):
        doc = {"name": "eval.pdf", "pages": [{"page": 1, "text": "Current medication: guanfacine 1 mg at bedtime.", "scanned": False}]}
        updates = list(history.build([doc], "test-model", False))
        result = updates[-1]["result"]
        meds = next(s for s in result["sections"] if s["key"] == "medications")
        risk = next(s for s in result["sections"] if s["key"] == "risk")
        self.assertEqual(meds["narrative"], "He takes guanfacine. [D1 p.1]")
        self.assertEqual(risk["narrative"], "No information found in the provided records.")  # its quote wasn't real
        self.assertTrue(any("document_read" in u for u in updates))

    def test_documents_read_before_are_not_read_again(self):
        doc = {"name": "eval.pdf", "pages": [{"page": 1, "text": "guanfacine 1 mg at bedtime", "scanned": False}],
               "info": {"document_type": "Other", "document_date": "", "author": ""},
               "facts": [{"section": "medications", "fact": "Takes guanfacine.", "date": "", "quote": "guanfacine 1 mg",
                          "page": 1, "verified": True}]}
        updates = list(history.build([doc], "test-model", False))
        self.assertFalse(any("document_read" in u for u in updates))
        self.assertFalse(any("Reading" in (u.get("progress") or "") for u in updates))


history_module = history


class WordExport(unittest.TestCase):
    def test_export_is_a_valid_word_file(self):
        data = history.build_docx([{"title": "Medication History", "narrative": "Takes <guanfacine> & more.", "conflicts": []}],
                                  [{"label": "D1", "name": "eval.pdf", "type": "Psychiatric Evaluation", "date": "", "author": ""}])
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIn("word/document.xml", z.namelist())
            body = z.read("word/document.xml").decode()
        self.assertIn("Medication History", body)
        self.assertIn("&lt;guanfacine&gt; &amp; more", body)  # text is escaped, so odd characters can't break the file


if __name__ == "__main__":
    unittest.main()
