"""The command bar, with the AI model replaced by canned replies."""
import unittest

import support
import assistant
import history


class Assistant(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vault = support.unlocked_vault()
        cls.patient = cls.vault.create_record("patients", {"name": "Quinn Assistant (fictional)", "status": "Active"})

    def setUp(self):
        self.replies = []
        self.real_ask = history.ask
        history.ask = lambda *args, **kwargs: self.next_reply()

    def tearDown(self):
        history.ask = self.real_ask

    def next_reply(self):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return {**assistant.empty_reply(), **reply}

    def test_question_runs_a_read_only_query(self):
        self.replies = [{"intent": "question", "explanation": "Active patients.",
                         "sql": "SELECT id, name FROM patients_view WHERE name LIKE '%Quinn%'"}]
        answer = assistant.handle("Who is Quinn?", "m", False, "MindChart")
        self.assertEqual(answer["kind"], "answer")
        self.assertEqual(answer["rows"], [[self.patient, "Quinn Assistant (fictional)"]])

    def test_plain_sql_reply_is_still_used(self):
        self.replies = [history.BadReply("```sql\nSELECT COUNT(*) FROM patients_view\n```")]
        self.assertEqual(assistant.handle("How many patients?", "m", False, "MindChart")["kind"], "answer")

    def test_query_labelled_unclear_is_still_answered(self):
        self.replies = [{"intent": "unclear", "sql": "SELECT COUNT(*) FROM patients_view", "explanation": "Count."}]
        self.assertEqual(assistant.handle("How many?", "m", False, "MindChart")["kind"], "answer")

    def test_two_bad_queries_get_a_message_not_an_error(self):
        two = "SELECT name FROM patients_view; SELECT name FROM guardians_view"  # what bonsai-2:9b once wrote
        self.replies = [{"intent": "question", "sql": two}, {"intent": "question", "sql": two}]
        self.assertIn("another way", assistant.handle("Who is in foster care?", "m", False, "MindChart")["text"])

    def test_broken_query_gets_one_retry(self):
        self.replies = [{"intent": "question", "sql": "SELECT nonsense FROM nowhere"},
                        {"intent": "question", "sql": "SELECT COUNT(*) FROM patients_view", "explanation": "Fixed."}]
        self.assertEqual(assistant.handle("Count", "m", False, "MindChart")["explanation"], "Fixed.")

    def test_writes_are_never_run_from_a_question(self):
        self.replies = [{"intent": "question", "sql": "DELETE FROM patients"}, {"intent": "question", "sql": "DELETE FROM patients"}]
        before = len(self.vault.list_records("patients")["rows"])
        self.assertIn("another way", assistant.handle("Delete everyone", "m", False, "MindChart")["text"])
        self.assertEqual(len(self.vault.list_records("patients")["rows"]), before)  # nothing was deleted
        self.assertTrue(self.vault.get_record("patients", self.patient))

    def test_note_is_proposed_not_saved(self):
        before = len(self.vault.list_records("documents", patient_uid=self.patient)["rows"])
        self.replies = [{"intent": "add_note", "patient": "Quinn", "note_text": "Slept well.", "explanation": "Note."}]
        proposal = assistant.handle("Note for Quinn: slept well", "m", False, "MindChart")
        self.assertEqual((proposal["kind"], proposal["patient"]["id"]), ("proposal", self.patient))
        self.assertEqual(len(self.vault.list_records("documents", patient_uid=self.patient)["rows"]), before)
        done = assistant.confirm(proposal)
        self.assertEqual(done["kind"], "done")
        self.assertEqual(len(self.vault.list_records("documents", patient_uid=self.patient)["rows"]), before + 1)

    def test_unknown_or_ambiguous_patients_are_questioned(self):
        self.vault.create_record("patients", {"name": "Quinn Other (fictional)"})
        self.replies = [{"intent": "add_note", "patient": "Quinn", "note_text": "x"}]
        self.assertIn("More than one patient", assistant.handle("note for Quinn", "m", False, "MindChart")["text"])
        self.replies = [{"intent": "add_note", "patient": "Nobody Atall", "note_text": "x"}]
        self.assertIn("couldn't find", assistant.handle("note for Nobody", "m", False, "MindChart")["text"])

    def test_new_patient_with_record_type_fields(self):
        self.replies = [{"intent": "new_patient", "explanation": "New patient.", "patient_fields": {
            **{f: "" for f in assistant.PATIENT_FIELDS}, "name": "Maria Test (fictional)",
            "record_type": "Residential treatment", "cottage": "Cottage 4", "program": "Other"}}]
        proposal = assistant.handle("New patient Maria, Cottage 4", "m", False, "MindChart")
        self.assertEqual(proposal["fields"]["cottage"], "Cottage 4")
        self.assertNotIn("program", proposal["fields"])  # a Crisis / stabilization detail, not for residential treatment
        # Shown as first and last name and a placement, the way the form asks for them
        self.assertEqual((proposal["fields"]["first_name"], proposal["fields"]["last_name"]), ("Maria", "Test (fictional)"))
        self.assertEqual((proposal["fields"]["record_type"], proposal["fields"]["placement"]), ("Patient", "Residential treatment"))
        done = assistant.confirm({**proposal, "fields": {k: v for k, v in proposal["fields"].items() if k != "name"}})  # as the form sends it
        self.assertEqual(self.vault.get_record("patients", done["id"])["custom"]["cottage"], "Cottage 4")
        self.assertIn("Added Maria Test (fictional)", done["text"])
        self.assertIn("name", assistant.SCHEMA["properties"]["patient_fields"]["properties"])  # the model can give the name

    def test_scores_medications_and_follow_ups_can_be_logged(self):
        item = lambda **values: {**{f: "" for f in assistant.ITEM_FIELDS}, **values}
        self.replies = [{"intent": "add_measure", "patient": self.patient, "item": item(measure="PHQ-A", score="11", date="10/30/2026")}]
        proposal = assistant.handle("Quinn's PHQ-A was 11 today", "m", False, "MindChart")
        self.assertEqual(proposal["fields"]["measure"], "PHQ-A (depression)")  # matched to the full name
        done = assistant.confirm(proposal)
        self.assertEqual(self.vault.get_record("measures", done["id"])["record"]["score"], 11)
        self.replies = [{"intent": "add_follow_up", "patient": self.patient,
                         "item": item(title="Renew medication consent", due="12/01/2026", category="Consent")}]
        done = assistant.confirm(assistant.handle("Remind me to renew Quinn's consent by 12/01", "m", False, "MindChart"))
        self.assertEqual(self.vault.get_record("follow_ups", done["id"])["record"]["status"], "Open")
        self.replies = [{"intent": "add_medication", "patient": self.patient, "item": item(dose="25 mg")}]
        self.assertEqual(assistant.handle("Quinn started 25 mg", "m", False, "MindChart")["kind"], "message")  # no name: ask

    def test_update_ignores_a_repeated_name(self):
        self.replies = [{"intent": "update_patient", "patient": self.patient,
                         "patient_fields": {**{f: "" for f in assistant.PATIENT_FIELDS}, "name": "Quinn Assistant (fictional)",
                                            "status": "Discharged"}}]
        proposal = assistant.handle("Discharge Quinn", "m", False, "MindChart")
        self.assertEqual(proposal["fields"], {"status": "Discharged"})


if __name__ == "__main__":
    unittest.main()
