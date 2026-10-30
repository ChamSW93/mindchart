"""Background work: identifying uploads and reading confirmed documents, with the AI model replaced by canned answers."""
import unittest

import support
import history
import vault
import worker

TEXT = ("FAMILY COURT OF THE STATE OF NEW YORK\nIn the Matter of Worker Test (fictional), born 04/04/2012.\n"
        "It is hereby ORDERED that the child shall receive psychiatric services.")


class Background(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support.unlocked_vault()
        cls.patient = vault.create_record("patients", {"name": "Worker Test (fictional)", "dob": "04/04/2012"})

    def setUp(self):
        self.real_ask = history.ask
        history.ask = self.fake_ask

    def tearDown(self):
        history.ask = self.real_ask

    def fake_ask(self, model, thinking, system, user, schema, max_tokens, history=()):
        if "document_type" in schema["properties"]:
            return {"document_type": "Court Order", "document_date": "06/12/2024", "author": "Hon. Test",
                    "patient_name": "Worker Test", "date_of_birth": "04/04/2012", "identifiers": []}
        if "facts" in schema["properties"]:
            return {"facts": [{"section": "legal", "fact": "Ordered to receive psychiatric services.", "date": "",
                               "quote": "the child shall receive psychiatric services"}] * 3}
        return {"fields": []}

    def test_an_upload_is_identified_then_read_after_confirming(self):
        pages = [{"page": 1, "text": TEXT, "scanned": False, "unclear": False}]
        uid = vault.add_upload("order.pdf", b"%PDF", pages, None, {"ai_done": False})
        for _ in range(10):  # anything queued earlier by other tests goes first
            if vault.get_upload(uid)["state"] == "ready":
                break
            worker.step(lambda: ("test-model", False))
        suggestion = vault.get_upload(uid)["suggestion"]
        self.assertTrue(suggestion["ai_done"])
        self.assertEqual(suggestion["document_type"], "Court Order")
        self.assertEqual(suggestion["patient_id"], self.patient)  # name and birth date match
        self.assertEqual(suggestion["document_date"], "06/12/2024")

        doc, _ = vault.confirm_upload(uid, {"patient_id": self.patient, "document_type": "Court Order", "author": "Hon. Test"})
        for _ in range(10):
            if vault.get_record("documents", doc)["record"]["read_state"] == "read":
                break
            worker.step(lambda: ("test-model", False))
        record = vault.get_record("documents", doc)
        self.assertEqual(record["record"]["read_state"], "read")
        self.assertEqual(record["related"]["facts"][0]["section"], "legal")
        self.assertTrue(record["related"]["facts"][0]["verified"])

    def test_building_a_history_pauses_background_work(self):
        with worker.paused():
            self.assertTrue(worker._pauses)
        self.assertFalse(worker._pauses)


if __name__ == "__main__":
    unittest.main()
