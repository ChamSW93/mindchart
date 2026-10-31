"""Fact checking, citations and the drafting flow, with the AI model replaced by canned answers."""
import io
import unittest
import zipfile

import support  # noqa: F401  (sets up paths)
import fields
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

    def test_fact_numbers_the_model_writes_are_removed(self):
        text = history.join_sentences([{"text": "Has a behavior plan (fact 1).", "facts": [1]},
                                       {"text": "Visits are suspended (facts 1 and 2).", "facts": [2]}], self.facts, [])
        self.assertEqual(text, "Has a behavior plan. [D1 p.3] Visits are suspended. [D2]")

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
        if "document_type" in schema["properties"]:  # identifying the document
            return {"document_type": "Psychiatric Evaluation", "document_date": "09/18/2023", "author": "Dr. Test",
                    "patient_name": "Jordan Avery", "date_of_birth": "", "identifiers": []}
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


class ReadingOneDocument(unittest.TestCase):
    """Reading a single document, as background reading does: identifying it, and taking only what was asked for."""

    def setUp(self):
        self.real_ask, self.calls = history.ask, []
        history.ask = self.fake_ask

    def tearDown(self):
        history.ask = self.real_ask

    def fake_ask(self, model, thinking, system, user, schema, max_tokens, history=()):
        self.calls.append((system, schema))
        if "document_type" in schema["properties"]:
            return {"document_type": "Safety Plan", "document_date": "", "author": "", "patient_name": "", "date_of_birth": "",
                    "identifiers": []}
        if "facts" in schema["properties"]:
            return {"facts": [{"section": "risk", "fact": "Has a safety plan.", "date": "", "quote": "warning signs and coping"}] * 3}
        return {"fields": []}

    def doc(self):
        return {"name": "plan.txt", "pages": [{"page": None, "text": "Safety plan: warning signs and coping strategies.", "scanned": False}]}

    def test_types_added_in_the_app_and_confirmed_examples_reach_the_ai(self):
        found = history.identify("m", False, "plan.txt", "Safety plan", {"Safety Plan": "a crisis plan", "Other": ""},
                                 [{"document_type": "Safety Plan", "name": "a.txt", "text": "SAFETY PLAN Warning signs"}])
        system, schema = self.calls[-1]
        self.assertEqual(found["document_type"], "Safety Plan")
        self.assertEqual(schema["properties"]["document_type"]["enum"], ["Safety Plan", "Other"])
        self.assertIn("SAFETY PLAN Warning signs", system)

    def test_only_what_was_asked_for_is_taken(self):
        doc = {**self.doc(), "info": {"document_type": "Court Order", "document_date": "", "author": ""}}
        definitions = {"Court Order": [d for d in fields.DEFAULT_RECORD_TYPES["documents"]["Court Order"]]}
        updates = list(history.read_document(doc, "D1", "m", False, definitions, {"facts": False, "fields": ["judge"]}))
        read = updates[-1]["document_read"]
        self.assertIsNone(read["facts"])  # facts weren't asked for, so none were looked for
        fill_system = self.calls[-1][0]
        self.assertIn("judge", fill_system)
        self.assertNotIn("docket_number", fill_system)

    def test_the_ai_not_running_stops_reading_instead_of_saving_nothing(self):
        def unavailable(*args, **kwargs):
            raise history.AIUnavailable()
        history.ask = unavailable
        with self.assertRaises(history.AIUnavailable):
            list(history.read_document(self.doc(), "D1", "m", False))


class MedicationsAndNotes(unittest.TestCase):
    def setUp(self):
        self.real_ask = history.ask

    def tearDown(self):
        history.ask = self.real_ask

    def test_suggested_medications_must_cite_the_records(self):
        history.ask = lambda *a, **k: {"medications": [
            {"name": "Sertraline", "dose": "50 mg", "frequency": "daily", "target": "", "status": "Active", "start_date": "05/01/2024",
             "stop_date": "", "stop_reason": "", "response": "", "facts": [1]},
            {"name": "Made Up", "dose": "", "frequency": "", "target": "", "status": "Unknown", "start_date": "", "stop_date": "",
             "stop_reason": "", "response": "", "facts": [7]}]}
        facts = [{"fact": "Sertraline was increased to 50 mg daily.", "date": "06/01/2024", "document": "note.pdf", "document_date": "08/02/2024"}]
        found = history.suggest_medications("m", False, facts)
        self.assertEqual([m["name"] for m in found], ["Sertraline"])  # the second cites a fact that doesn't exist
        self.assertEqual(found[0]["source"], "note.pdf (08/02/2024)")
        self.assertEqual(history.suggest_medications("m", False, []), [])

    def test_entries_for_the_same_medication_are_combined(self):
        # What ornith-1.5:9b returned for the sample records: one entry per fact, with guanfacine three times
        entry = lambda name, dose, status, start, response, fact, stop="", reason="": {
            "name": name, "dose": dose, "frequency": "at bedtime", "target": "hyperactivity" if fact == 4 else "", "status": status,
            "start_date": start, "stop_date": stop, "stop_reason": reason, "response": response, "facts": [fact]}
        history.ask = lambda *a, **k: {"medications": [
            entry("Guanfacine ER", "2 mg", "Active", "05/01/2024", "Changed to guanfacine ER 2 mg on 05/01/2024", 2),
            entry("Methylphenidate (Ritalin)", "10 mg", "Stopped", "2018", "", 3, "2019", "poor appetite and weight loss"),
            entry("Guanfacine", "1 mg", "Active", "2021", "Partial benefit for hyperactivity", 4),
            entry("Guanfacine", "1 mg", "Active", "", "", 5)]}
        note, evaluation = ("progress.pdf", "08/02/2024"), ("evaluation.pdf", "09/18/2023")
        facts = [{"fact": f"Fact {n}", "date": "", "document": doc, "document_date": day}
                 for n, (doc, day) in enumerate([note, note, evaluation, evaluation, evaluation], 1)]
        found = history.suggest_medications("m", False, facts)
        self.assertEqual([m["name"] for m in found], ["Guanfacine ER", "Methylphenidate (Ritalin)"])
        guanfacine = found[0]
        self.assertEqual((guanfacine["dose"], guanfacine["status"], guanfacine["start_date"], guanfacine["target"]),
                         ("2 mg", "Active", "2021", "hyperactivity"))  # the newest record gives the dose; the oldest the start
        self.assertEqual(guanfacine["response"], "Partial benefit for hyperactivity; Changed to guanfacine ER 2 mg on 05/01/2024")
        self.assertEqual(guanfacine["source"], "evaluation.pdf (09/18/2023); progress.pdf (08/02/2024)")
        self.assertEqual(guanfacine["quotes"], ["Fact 4", "Fact 5", "Fact 2"])
        self.assertEqual((found[1]["stop_date"], found[1]["stop_reason"]), ("2019", "poor appetite and weight loss"))

    def test_how_often_is_taken_out_of_the_dose(self):
        # What bonsai-2:9b returned: the frequency inside the dose
        entry = lambda dose, frequency: {"name": "Guanfacine ER", "dose": dose, "frequency": frequency, "target": "",
                                         "status": "Active", "start_date": "", "stop_date": "", "stop_reason": "", "response": "",
                                         "facts": [1]}
        facts = [{"fact": "Guanfacine ER 2 mg at bedtime.", "date": "", "document": "note.pdf", "document_date": "08/02/2024"}]
        for dose, frequency, expected in [("2 mg at bedtime", "", ("2 mg", "at bedtime")), ("0.5 mg twice daily", "", ("0.5 mg", "twice daily")),
                                          ("2 mg at bedtime", "nightly", ("2 mg at bedtime", "nightly")), ("2 mg", "", ("2 mg", ""))]:
            history.ask = lambda *a, **k: {"medications": [entry(dose, frequency)]}
            found = history.suggest_medications("m", False, facts)[0]
            self.assertEqual((found["dose"], found["frequency"]), expected)

    def test_medication_names_match_across_formulations(self):
        key = history.medication_key
        self.assertEqual(key("Guanfacine ER"), key("guanfacine"))
        self.assertEqual(key("Methylphenidate (Ritalin)"), key("methylphenidate"))
        self.assertEqual(key("Sertraline 50 mg tablets"), "sertraline")
        self.assertNotEqual(key("Vitamin D"), key("Vitamin B12"))

    def test_fact_numbers_are_taken_out_of_briefings(self):
        self.assertEqual(history.unnumbered("Devon was vaping (Fact 1). Eli felt pressured (Fact 1, Fact 3) [2]."),
                         "Devon was vaping. Eli felt pressured.")

    def test_visit_notes_have_four_parts(self):
        history.ask = lambda *a, **k: {"subjective": "S", "objective": "O", "assessment": "A", "plan": "P"}
        self.assertEqual(history.draft_visit_note("m", False, "Since last visit: better"), {"subjective": "S", "objective": "O",
                                                                                        "assessment": "A", "plan": "P"})

    def test_the_plan_is_not_repeated_in_the_assessment(self):
        history.ask = lambda *a, **k: {"subjective": "Not documented.", "objective": "Not documented.",
                                       "assessment": "Anxiety improving. Continue sertraline. Follow up in 1 month.",
                                       "plan": "Continue sertraline. Follow up in 1 month."}
        self.assertEqual(history.draft_visit_note("m", False, "Plan: continue sertraline")["assessment"], "Anxiety improving.")
        history.ask = lambda *a, **k: {"subjective": "S", "objective": "O", "assessment": "Follow up in 1 month.",
                                       "plan": "Follow up in 1 month."}
        self.assertEqual(history.draft_visit_note("m", False, "Plan: follow up")["assessment"], "Not documented.")

    def test_actions_in_the_assessment_move_to_the_plan(self):
        # What bonsai-2:9b did with "Continue current medications. Follow up in 1 month."
        history.ask = lambda *a, **k: {"subjective": "S", "objective": "O", "assessment": "Continue current medications.",
                                       "plan": "Follow up in 1 month."}
        note = history.draft_visit_note("m", False, "Plan: continue meds, follow up")
        self.assertEqual((note["assessment"], note["plan"]), ("Not documented.", "Continue current medications. Follow up in 1 month."))
        history.ask = lambda *a, **k: {"subjective": "S", "objective": "O", "plan": "Not documented.",
                                       "assessment": "Continued improvement in sleep. Increase sertraline to 75 mg daily."}
        note = history.draft_visit_note("m", False, "Plan: increase sertraline")
        self.assertEqual((note["assessment"], note["plan"]), ("Continued improvement in sleep.", "Increase sertraline to 75 mg daily."))


class FillingFields(unittest.TestCase):
    """The AI filling in a document's record-type fields: only values backed by a real quote are kept."""

    def setUp(self):
        self.real_ask = history.ask

    def tearDown(self):
        history.ask = self.real_ask

    def test_only_quoted_values_that_fit_are_kept(self):
        history.ask = lambda *a, **k: {"fields": [
            {"name": "judge", "value": "Hon. R. Castellano", "quote": "ENTER: Hon. R. Castellano, Judge of the Family Court"},
            {"name": "order_of_protection", "value": "yes", "quote": "an order of protection is issued directing"},
            {"name": "docket_number", "value": "NA-99", "quote": "a docket number that isn't in the record"},
            {"name": "case_type", "value": "Traffic court", "quote": "an order of protection is issued directing"},
        ]}
        text = "It is ORDERED that an order of protection is issued directing the father to stay away. " \
               "ENTER: Hon. R. Castellano, Judge of the Family Court"
        doc = {"chunks": [text], "document_type": "Court Order", "document_date": "", "pages": [page(text, 1)]}
        definitions = [d for d in fields.DEFAULT_RECORD_TYPES["documents"]["Court Order"]]
        filled = history.fill_fields("m", False, doc, definitions)
        self.assertEqual(filled["judge"]["value"], "Hon. R. Castellano")
        self.assertIs(filled["order_of_protection"]["value"], True)
        self.assertNotIn("docket_number", filled)  # its quote isn't in the document
        self.assertNotIn("case_type", filled)  # not one of the choices


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
