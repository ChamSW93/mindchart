"""The local server: who it accepts requests from, locking, and the main API routes."""
import http.client
import json
import os
import threading
import unittest

import support
import server

PORT = int(os.environ["MINDCHART_PORT"])


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", PORT), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
        all_headers = {"Host": f"localhost:{PORT}", "X-MindChart": "1", "Content-Type": "application/json"}
        all_headers.update(headers or {})
        connection.request(method, path, body=json.dumps(body) if isinstance(body, dict) else body, headers=all_headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, (json.loads(data) if response.getheader("Content-Type", "").startswith("application/json") else data)

    def test_requests_from_elsewhere_are_refused(self):
        self.assertEqual(self.request("GET", "/api/session", headers={"X-MindChart": ""})[0], 403)  # no MindChart header
        self.assertEqual(self.request("GET", "/api/session", headers={"Host": "evil.example:80"})[0], 403)  # DNS rebinding
        self.assertEqual(self.request("POST", "/api/session/lock", {}, headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request("GET", "/api/session")[0], 200)

    def test_data_needs_unlocking(self):
        support.unlocked_vault().lock()
        status, body = self.request("GET", "/api/records/patients")
        self.assertEqual((status, body.get("locked")), (423, True))
        self.assertEqual(self.request("POST", "/api/session/unlock", {"password": support.PASSWORD})[0], 200)
        self.assertEqual(self.request("GET", "/api/records/patients")[0], 200)

    def test_touch_id_signs_in_with_the_sealed_password(self):
        undo = support.fake_touch_id()
        try:
            support.unlocked_vault()
            status, body = self.request("POST", "/api/settings/touch-id", {"on": True, "password": "not-the-password"})
            self.assertEqual((status, body["error"]), (400, "That password isn't right"))
            self.assertEqual(self.request("POST", "/api/settings/touch-id", {"on": True, "password": support.PASSWORD})[1], {"on": True})
            support.vault.lock()
            self.assertTrue(self.request("GET", "/api/session")[1]["touch_id"])
            self.assertTrue(self.request("POST", "/api/session/touch-id", {})[1]["unlocked"])
            self.assertEqual(support.vault.list_records("activity")["rows"][0]["action"], "signed in with Touch ID")
            # A changed password keeps Touch ID working
            self.request("POST", "/api/settings/password", {"current": support.PASSWORD, "new": "another-password-1"})
            support.vault.lock()
            self.assertTrue(self.request("POST", "/api/session/touch-id", {})[1]["unlocked"])
            self.request("POST", "/api/settings/password", {"current": "another-password-1", "new": support.PASSWORD})
            # If the sealed password stops working (changed some other way), Touch ID turns itself off
            import touchid
            touchid.turn_on("an-old-password-1")
            support.vault.lock()
            status, body = self.request("POST", "/api/session/touch-id", {})
            self.assertEqual(status, 400)
            self.assertIn("turned off", body["error"])
            self.assertFalse(self.request("GET", "/api/session")[1]["touch_id"])
        finally:
            undo()
            support.vault._failed_attempts = 0
            support.unlocked_vault()

    def test_guardians_are_listed_apart_from_patients(self):
        support.unlocked_vault()
        guardian = self.request("POST", "/api/records/patients", {"record_type": "Legal Guardian", "first_name": "Server",
                                                                   "last_name": "Guardian (fictional)"})[1]["id"]
        self.assertTrue(guardian.startswith("LG-"))
        listed = self.request("GET", "/api/records/patients?type=Legal%20Guardian")[1]["rows"]
        self.assertIn(guardian, [r["id"] for r in listed])
        self.assertNotIn(guardian, [r["id"] for r in self.request("GET", "/api/records/patients")[1]["rows"]])
        self.assertEqual(self.request("GET", f"/api/records/patients/{guardian}")[1]["record"]["record_type"], "Legal Guardian")
        fields = self.request("GET", "/api/objects")[1]["objects"]["patients"]["fields"]
        self.assertEqual(next(f for f in fields if f["name"] == "gender")["options"], ["Male", "Female", "X"])

    def test_zip_and_medication_lookups(self):
        support.unlocked_vault()
        found = self.request("GET", "/api/zip?q=10701")[1]
        self.assertEqual((found["results"][0]["town"], found["home"]), ("Yonkers", "Children's Village"))
        self.assertEqual(self.request("GET", "/api/zip?q=Dobbs")[1]["results"][0]["zip"], "10522")
        names = [m["name"] for m in self.request("GET", "/api/medication-names")[1]["medications"]]
        self.assertIn("Sertraline", names)

    def test_record_routes(self):
        support.unlocked_vault()
        status, created = self.request("POST", "/api/records/patients", {"name": "Server Test (fictional)"})
        self.assertEqual(status, 200)
        status, record = self.request("POST", f"/api/records/patients/{created['id']}", {"custom": {"cottage": "Rose"}})
        self.assertEqual(record["custom"]["cottage"], "Rose")
        self.assertEqual(self.request("GET", "/api/records/nonsense")[0], 400)
        self.assertEqual(self.request("GET", "/api/no/such/route")[0], 400)
        settings = self.request("GET", "/api/settings")[1]
        self.assertTrue(settings["audit"]["ok"])
        self.assertEqual(self.request("POST", f"/api/records/patients/{created['id']}/delete", {})[0], 200)

    def test_uploads_wait_for_review_then_are_filed(self):
        support.unlocked_vault()
        support.ensure_pdf_reader()
        patient = self.request("POST", "/api/records/patients", {"name": "Server Upload (fictional)"})[1]["id"]
        sample = (support.SAMPLES / "Psychiatric_Evaluation_2023.pdf").read_bytes()
        status, created = self.request("POST", f"/api/uploads?name=eval.pdf&patient={patient}", sample,
                                       headers={"Content-Type": "application/pdf"})
        self.assertEqual(status, 200)
        listed = self.request("GET", "/api/uploads")[1]
        upload = next(u for u in listed["uploads"] if u["id"] == created["id"])
        self.assertEqual(upload["suggestion"]["learned_type"], "Psychiatric Evaluation")  # recognized before the AI has run
        self.assertEqual(upload["suggestion"]["patient_id"], patient)  # dropped on this patient's page
        self.assertEqual(self.request("GET", "/api/work")[0], 200)
        status, filed = self.request("POST", f"/api/uploads/{created['id']}/confirm",
                                     {"patient_id": patient, "document_type": "Psychiatric Evaluation"})
        self.assertEqual((status, filed["patient_id"]), (200, patient))
        self.assertEqual(self.request("GET", f"/api/records/documents/{filed['document_id']}")[1]["record"]["document_type"],
                         "Psychiatric Evaluation")

    def test_separate_files_can_be_combined_into_one_document(self):
        support.unlocked_vault()
        support.ensure_pdf_reader()
        ids = []
        for name in ("Progress_Note_2024-08.pdf", "Court_Order_2024_scanned.pdf"):
            ids.append(self.request("POST", f"/api/uploads?name={name}", (support.SAMPLES / name).read_bytes(),
                                    headers={"Content-Type": "application/pdf"})[1]["id"])
        status, combined = self.request("POST", "/api/uploads/combine", {"ids": ids})
        self.assertEqual(status, 200)
        uploads = {u["id"]: u for u in self.request("GET", "/api/uploads")[1]["uploads"]}
        self.assertNotIn(ids[0], uploads)  # the separate files are gone
        self.assertEqual((uploads[combined["id"]]["name"], uploads[combined["id"]]["pages"]), ("Progress_Note_2024-08 + 1 more.pdf", 2))
        self.request("POST", f"/api/uploads/{combined['id']}/discard", {})

    def test_the_tour_is_offered_until_finished_or_skipped(self):
        support.unlocked_vault()
        self.request("POST", "/api/settings/tour", {"state": ""})
        self.assertEqual(self.request("GET", "/api/onboarding")[1]["tour"], "")
        self.request("POST", "/api/settings/tour", {"state": "skipped"})
        self.assertEqual(self.request("GET", "/api/onboarding")[1]["tour"], "skipped")

    def test_huge_uploads_are_refused(self):
        support.unlocked_vault()
        connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
        connection.putrequest("POST", "/api/uploads?name=big.pdf")
        connection.putheader("Host", f"localhost:{PORT}")
        connection.putheader("X-MindChart", "1")
        connection.putheader("Content-Length", str(server.MAX_UPLOAD_BYTES + 1))
        connection.endheaders()
        self.assertEqual(connection.getresponse().status, 413)
        connection.close()

    def test_an_evaluation_is_written_as_a_job_the_page_follows(self):
        support.unlocked_vault()
        patient = self.request("POST", "/api/records/patients", {"name": "Job Test (fictional)"})[1]["id"]

        def ask(model, thinking, system, user, schema, max_tokens, history=()):
            if "facts" in schema["properties"]:
                return {"facts": [{"section": "mse", "fact": "Calm and cooperative.", "kind": "Ongoing", "topic": "behavior: demeanor",
                                   "date": "", "quote": "Calm and cooperative today"}] * 3}
            return {"sentences": [{"text": "Calm and cooperative.", "facts": [1]}], "conflicts": []}
        real, server.history.ask = server.history.ask, ask
        try:
            status, started = self.request("POST", f"/api/patients/{patient}/history",
                                           {"doc_ids": [], "kind": "evaluation", "notes": "Calm and cooperative today, good eye contact.",
                                            "model": "test-model"})
            self.assertEqual(status, 200)  # it answers straight away, and the writing carries on without the page
            for _ in range(100):
                job = self.request("GET", f"/api/builds/{started['build_id']}")[1]
                if job["done"]:
                    break
                server.time.sleep(0.1)
        finally:
            server.history.ask = real
        self.assertIsNone(job["error"])
        self.assertTrue(job["draft_id"].startswith("DRF-"))
        self.assertEqual(self.request("GET", f"/api/builds?patient={patient}")[1], {"build": None})  # nothing left running
        self.assertEqual(self.request("GET", "/api/builds/unknown")[0], 400)

    def test_pages_are_served(self):
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>MindChart</title>", page)


if __name__ == "__main__":
    unittest.main()
