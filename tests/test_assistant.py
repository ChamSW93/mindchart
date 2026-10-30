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

    def test_broken_query_gets_one_retry(self):
        self.replies = [{"intent": "question", "sql": "SELECT nonsense FROM nowhere"},
                        {"intent": "question", "sql": "SELECT COUNT(*) FROM patients_view", "explanation": "Fixed."}]
        self.assertEqual(assistant.handle("Count", "m", False, "MindChart")["explanation"], "Fixed.")

    def test_writes_are_never_run_from_a_question(self):
        self.replies = [{"intent": "question", "sql": "DELETE FROM patients"}, {"intent": "question", "sql": "DELETE FROM patients"}]
        with self.assertRaises(ValueError):
            assistant.handle("Delete everyone", "m", False, "MindChart")
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

    def test_update_ignores_a_repeated_name(self):
        self.replies = [{"intent": "update_patient", "patient": self.patient,
                         "patient_fields": {**{f: "" for f in assistant.PATIENT_FIELDS}, "name": "Quinn Assistant (fictional)",
                                            "status": "Discharged"}}]
        proposal = assistant.handle("Discharge Quinn", "m", False, "MindChart")
        self.assertEqual(proposal["fields"], {"status": "Discharged"})


if __name__ == "__main__":
    unittest.main()
