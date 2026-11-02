"""Learning from the clinician: pages checked against the original, corrected facts that guide the next reading,
and writing preferences learned from edits to evaluation drafts (never with patient details)."""
import http.client
import json
import os
import threading
import unittest

import support
import history
import server

PORT = int(os.environ["MINDCHART_PORT"]) + 1  # test_server.py uses the port itself


def first_document(vault, doc):
    """Read a filed document now (as background reading would), clearing anything other tests queued first."""
    queued = vault.next_document_to_read()
    while queued and queued["uid"] != doc:
        vault.save_document_facts(queued["id"], queued["info"], [], "test-model")
        queued = vault.next_document_to_read()
    return queued


class CheckingPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vault = support.unlocked_vault()

    def upload(self, name="notes.png"):
        pages = [{"page": 1, "text": "Pt cdlm, slp bttr", "scanned": True, "unclear": True},
                 {"page": 2, "text": "Plan: continue therapy", "scanned": True, "unclear": False}]
        return self.vault.add_upload(name, b"picture", pages, None, {"document_type": "Evaluation notes"})

    def test_a_corrected_upload_page_is_used_and_identified_again(self):
        uid = self.upload()
        self.vault.save_suggestion(uid, {"document_type": "Evaluation notes"})  # identified once already
        self.vault.correct_page("uploads", uid, 1, "  Pt calm, sleep better  ")
        upload = self.vault.get_upload(uid)
        self.assertEqual(upload["pages"][0], {"page": 1, "text": "Pt calm, sleep better", "scanned": True, "unclear": False, "checked": True})
        self.assertEqual(upload["state"], "waiting")  # the AI identifies it again from the corrected text
        listed = next(u for u in self.vault.list_uploads() if u["id"] == uid)
        self.assertEqual((listed["unclear_pages"], listed["checked_pages"]), ([], [1]))
        with self.assertRaisesRegex(ValueError, "no page 3"):
            self.vault.correct_page("uploads", uid, 3, "text")
        with self.assertRaisesRegex(ValueError, "Type"):
            self.vault.correct_page("uploads", uid, 1, "   ")
        self.vault.discard_upload(uid)

    def test_a_corrected_document_page_is_read_again_keeping_hand_edited_facts(self):
        patient = self.vault.create_record("patients", {"name": "Page Check (fictional)"})
        doc, _ = self.vault.confirm_upload(self.upload(), {"patient_id": patient, "document_type": "Evaluation notes"})
        queued = first_document(self.vault, doc)
        self.vault.save_document_facts(queued["id"], queued["info"], [
            {"section": "mse", "fact": "Calm.", "quote": "Pt cdlm", "verified": False},
            {"section": "recommendations", "fact": "Continue therapy.", "quote": "continue therapy", "verified": True}], "test-model")
        facts = {f["fact"]: f["id"] for f in self.vault.get_record("documents", doc)["related"]["facts"]}
        self.vault.update_record("facts", facts["Calm."], {"fact": "Calm and cooperative."})
        self.assertEqual(self.vault.correct_page("documents", doc, 1, "Pt calm, sleep better"), {"reading_again": True})
        self.assertEqual(self.vault.get_record("documents", doc)["record"]["read_state"], "queued")
        queued = first_document(self.vault, doc)
        self.assertEqual(queued["pages"][0]["text"], "Pt calm, sleep better")
        self.vault.save_document_facts(queued["id"], queued["info"], [
            {"section": "mse", "fact": "Calm.", "quote": "Pt cdlm", "verified": False},  # the same as the one edited by hand
            {"section": "hpi", "fact": "Sleeping better.", "quote": "sleep better", "verified": True}], "test-model")
        found = sorted(f["fact"] for f in self.vault.get_record("documents", doc)["related"]["facts"])
        self.assertEqual(found, ["Calm and cooperative.", "Sleeping better."])
        self.assertEqual(self.vault.correct_page("documents", doc, 2, "Plan: continue therapy"), {"reading_again": False})  # unchanged

    def test_a_page_corrected_during_reading_is_read_again(self):
        patient = self.vault.create_record("patients", {"name": "Mid-read Check (fictional)"})
        doc, _ = self.vault.confirm_upload(self.upload(), {"patient_id": patient, "document_type": "Evaluation notes"})
        reading = first_document(self.vault, doc)  # being read, from the OCR's text
        self.vault.correct_page("documents", doc, 1, "Pt calm, sleep better")
        self.vault.save_document_facts(reading["id"], reading["info"], [], "test-model", texts=[p["text"] for p in reading["pages"]])
        self.assertEqual(self.vault.get_record("documents", doc)["record"]["read_state"], "queued")
        again = first_document(self.vault, doc)
        self.vault.save_document_facts(again["id"], again["info"], [], "test-model", texts=[p["text"] for p in again["pages"]])
        self.assertEqual(self.vault.get_record("documents", doc)["record"]["read_state"], "read")

    def test_pages_are_shown_from_the_original(self):
        support.ensure_pdf_reader()
        import documents
        sample = (support.SAMPLES / "Psychiatric_Evaluation_2023.pdf").read_bytes()
        picture = documents.render_page("eval.pdf", sample, 2, folder=self.vault.MOUNT)
        self.assertEqual(picture[:8], b"\x89PNG\r\n\x1a\n")
        with self.assertRaisesRegex(ValueError, "page 99"):
            documents.render_page("eval.pdf", sample, 99, folder=self.vault.MOUNT)
        with self.assertRaisesRegex(ValueError, "Only PDFs"):
            documents.render_page("letter.docx", b"PK", 1)


class CorrectedFacts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vault = support.unlocked_vault()

    def read(self, document_type, facts):
        patient = self.vault.create_record("patients", {"name": "Fact Fixes (fictional)"})
        doc = self.vault.add_note(patient, "Seen with foster mother. Sleeping 8 hours. Denies SI.", document_type=document_type)
        self.vault.save_document_facts(self.vault.internal("documents", doc), {"document_type": document_type}, facts, "test-model")
        return doc, {f["fact"]: f["id"] for f in self.vault.get_record("documents", doc)["related"]["facts"]}

    def test_corrections_become_examples_for_that_type_of_document(self):
        doc, facts = self.read("Discharge Summary", [
            {"section": "social", "fact": "Lives with mother.", "quote": "Seen with foster mother", "verified": True},
            {"section": "risk", "fact": "Sleeps 8 hours.", "quote": "Sleeping 8 hours", "verified": True},
            {"section": "risk", "fact": "Denies SI.", "quote": "Denies SI", "verified": True}])
        self.vault.update_record("facts", facts["Lives with mother."], {"fact": "Seen with foster mother."})
        self.vault.update_record("facts", facts["Lives with mother."], {"fact": "Attended with foster mother."})
        self.vault.update_record("facts", facts["Sleeps 8 hours."], {"section": "hpi"})
        self.vault.delete_record("facts", facts["Denies SI."])
        guidance = self.vault.reading_guidance("Discharge Summary")
        self.assertEqual(len(guidance), 3)
        mother = next(g for g in guidance if g["quote"] == "Seen with foster mother")
        self.assertEqual(mother["found"]["fact"], "Lives with mother.")  # the AI's own version, from the first correction
        self.assertEqual(mother["corrected"]["fact"], "Attended with foster mother.")
        self.assertIsNone(next(g for g in guidance if g["quote"] == "Denies SI")["corrected"])  # removed
        self.assertEqual(self.vault.reading_guidance("Court Order"), [])  # only for the same type
        self.vault.update_record("facts", facts["Sleeps 8 hours."], {"section": "risk"})  # put back as the AI had it
        self.assertEqual(len(self.vault.reading_guidance("Discharge Summary")), 2)
        self.assertIn({"type": "Discharge Summary", "count": 2}, self.vault.learned()["corrections"])
        queued_like = self.vault.documents_for_history(self.vault.get_record("documents", doc)["record"]["patient_id"], [doc], False)
        self.assertEqual(len(queued_like[0]["guidance"]), 2)  # drafts reading a document again get them too
        self.vault.forget_corrections("Discharge Summary")
        self.assertEqual(self.vault.reading_guidance("Discharge Summary"), [])
        self.assertEqual(self.vault.get_record("facts", facts["Lives with mother."])["record"]["fact"], "Attended with foster mother.")

    def test_corrections_go_with_their_document(self):
        doc, facts = self.read("Psychological Evaluation", [{"section": "hpi", "fact": "Sleeps well.", "quote": "Sleeping 8 hours", "verified": True}])
        self.vault.update_record("facts", facts["Sleeps well."], {"fact": "Sleeps 8 hours a night."})
        self.assertEqual(len(self.vault.reading_guidance("Psychological Evaluation")), 1)
        self.vault.delete_record("documents", doc)
        self.assertEqual(self.vault.reading_guidance("Psychological Evaluation"), [])

    def test_the_ai_is_shown_the_corrections_and_told_not_to_copy_them(self):
        prompts = []

        def ask(model, thinking, system, user, schema, max_tokens, history=()):
            prompts.append(system)
            return {"facts": [{"section": "hpi", "fact": "Lives with foster mother.", "date": "", "quote": "foster mother"}] * 3}
        real, history.ask = history.ask, ask
        try:
            doc = {"name": "summary.pdf", "pages": [{"page": 1, "text": "No guardians are named in this record.", "scanned": False}],
                   "info": {"document_type": "Discharge Summary", "document_date": "", "author": ""},
                   "guidance": [{"quote": "Seen with foster mother", "found": {"section": "social", "fact": "Lives with mother.", "date": ""},
                                 "corrected": {"section": "social", "fact": "Attended with foster mother.", "date": ""}},
                                {"quote": "Denies SI", "found": {"section": "risk", "fact": "Denies SI.", "date": "10/01/2026"}, "corrected": None}]}
            read = list(history.read_document(doc, "D1", "m", False))[-1]["document_read"]
        finally:
            history.ask = real
        self.assertIn("OTHER patients' records", prompts[0])
        self.assertIn('corrected it to [social] Attended with foster mother.', prompts[0])
        self.assertIn("[risk] Denies SI. (date: 10/01/2026). The clinician removed it", prompts[0])
        self.assertFalse(any(f["verified"] for f in read["facts"]))  # a fact copied from an example isn't in this record


class WritingPreferences(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vault = support.unlocked_vault()

    def test_preferences_can_be_added_edited_and_deleted(self):
        added = self.vault.add_writing_preferences(["Refer to the patient as 'the youth'.", "refer to the patient as 'the youth'.", " "])
        self.assertEqual(added, ["Refer to the patient as 'the youth'."])
        self.assertIn("Refer to the patient as 'the youth'.", self.vault.writing_preferences())
        number = next(p["id"] for p in self.vault.learned()["preferences"] if p["text"] == added[0])
        self.vault.change_writing_preference(number, "Call the patient 'the youth'.")
        self.assertIn("Call the patient 'the youth'.", self.vault.writing_preferences())
        self.vault.change_writing_preference(number, None)
        self.assertNotIn("Call the patient 'the youth'.", self.vault.writing_preferences())
        with self.assertRaises(ValueError):
            self.vault.change_writing_preference(number, None)

    def test_names_kept_out_of_what_is_learned(self):
        guardian = self.vault.create_record("patients", {"first_name": "Dana", "last_name": "Quill (fictional)", "record_type": "Legal Guardian"})
        patient = self.vault.create_record("patients", {"first_name": "Sky", "last_name": "Quill (fictional)", "placement": "Residential treatment",
                                                        "cottage": "Collins"})
        self.vault.update_record("patients", patient, {"guardians": [guardian]})
        draft = self.vault.save_draft(patient, "test-model", {"kind": "evaluation", "sections": []})
        self.assertEqual(sorted(self.vault.draft_people(draft)), ["Collins", "Dana Quill (fictional)", "Sky Quill (fictional)"])

    def test_evaluations_follow_them_and_keep_the_ais_own_text(self):
        prompts = []

        def ask(model, thinking, system, user, schema, max_tokens, history=()):
            prompts.append(system)
            return {"sentences": [{"text": "Takes guanfacine.", "facts": [1]}], "conflicts": []}
        real, history.ask = history.ask, ask
        try:
            doc = {"name": "eval.pdf", "pages": [{"page": 1, "text": "guanfacine 1 mg at bedtime", "scanned": False}],
                   "info": {"document_type": "Other", "document_date": "", "author": ""},
                   "facts": [{"section": "medications", "fact": "Takes guanfacine.", "date": "", "quote": "guanfacine 1 mg",
                              "page": 1, "verified": True}]}
            result = list(history.build([doc], "m", False, evaluation=True, preferences=["Lead with current functioning."]))[-1]["result"]
            list(history.build([dict(doc)], "m", False, preferences=["Lead with current functioning."]))
        finally:
            history.ask = real
        meds = next(s for s in result["sections"] if s["key"] == "medications")
        self.assertEqual(meds["ai_narrative"], meds["narrative"])
        self.assertIn("- Lead with current functioning.", prompts[0])
        self.assertNotIn("Lead with current functioning", prompts[-1])  # only evaluations are written to them

    def test_learning_keeps_only_general_habits(self):
        sent = []

        def ask(model, thinking, system, user, schema, max_tokens, history=()):
            sent.append(user)
            return {"preferences": ["Refer to the patient as 'the youth'.", "Mention Jordan's school first.",
                                    "Note the 09/18/2023 evaluation.", "List sertraline before other medications.",
                                    "Write diagnoses in DSM-5 terms.", "Keep the HPI under six sentences.",
                                    "Keep the hpi under six sentences."]}
        result = {"sections": [
            {"title": "History of Present Illness", "ai_narrative": "Jordan reports sleep problems.", "narrative": "The youth reports sleep problems."},
            {"title": "Medication History", "ai_narrative": "Takes sertraline.", "narrative": "Takes sertraline."}]}
        real, history.ask = history.ask, ask
        try:
            found = history.learn_preferences("m", False, result, known=["Keep the HPI under six sentences."], private=["Jordan", "Avery"])
        finally:
            history.ask = real
        self.assertEqual(found, ["Refer to the patient as 'the youth'.", "Write diagnoses in DSM-5 terms."])
        self.assertIn("The youth reports sleep problems.", sent[0])
        self.assertNotIn("Medication History", sent[0])  # an unchanged section isn't sent
        with self.assertRaisesRegex(ValueError, "nothing to learn"):
            history.learn_preferences("m", False, {"sections": [result["sections"][1]]})


class Routes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support.unlocked_vault()
        cls.httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", PORT), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=60)
        connection.request(method, path, body=json.dumps(body) if isinstance(body, dict) else body,
                           headers={"Host": f"localhost:{server.PORT}", "X-MindChart": "1", "Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, (json.loads(data) if response.getheader("Content-Type", "").startswith("application/json") else data)

    def test_an_uploads_pages_can_be_seen_and_corrected(self):
        support.ensure_pdf_reader()
        sample = (support.SAMPLES / "Psychiatric_Evaluation_2023.pdf").read_bytes()
        uid = self.request("POST", "/api/uploads?name=eval.pdf", sample, {"Content-Type": "application/pdf"})[1]["id"]
        status, info = self.request("GET", f"/api/uploads/{uid}/pages")
        self.assertEqual((status, info["viewable"], info["pages"][0]["page"]), (200, True, 1))
        status, picture = self.request("GET", f"/api/uploads/{uid}/page/1")
        self.assertEqual((status, picture[:4]), (200, b"\x89PNG"))
        self.assertEqual(self.request("POST", f"/api/uploads/{uid}/page/1", {"text": "Corrected text."})[0], 200)
        self.assertTrue(self.request("GET", f"/api/uploads/{uid}/pages")[1]["pages"][0]["checked"])
        self.assertEqual(self.request("GET", "/api/elsewhere/x/pages")[0], 400)  # only uploads and documents have pages
        self.request("POST", f"/api/uploads/{uid}/discard", {})

    def test_preferences_from_settings(self):
        status, _ = self.request("POST", "/api/learned/preferences", {"texts": ["Use plain language for families."]})
        self.assertEqual(status, 200)
        learned = self.request("GET", "/api/learned")[1]
        number = next(p["id"] for p in learned["preferences"] if p["text"] == "Use plain language for families.")
        self.assertEqual(self.request("POST", f"/api/learned/preferences/{number}/delete", {})[0], 200)
        self.assertNotIn("Use plain language for families.", [p["text"] for p in self.request("GET", "/api/learned")[1]["preferences"]])


if __name__ == "__main__":
    unittest.main()
