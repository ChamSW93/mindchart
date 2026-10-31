"""The encrypted store: records, IDs, edits, the audit trail, sign-in limits, passwords and upgrades."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import support
import history

REAL_ASK = history.ask


class Records(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vault = support.unlocked_vault()

    def test_records_get_ids_with_their_type(self):
        uid = self.vault.create_record("patients", {"name": "Robin Test (fictional)", "dob": "01/02/2012"})
        self.assertRegex(uid, r"^PT-[23456789ABCDEFGHJKMNPQRSTVWXYZ]{6}$")
        doc = self.vault.add_note(uid, "Calm week.", "10/01/2026")
        self.assertTrue(doc.startswith("DOC-"))
        self.assertEqual(self.vault.get_record("patients", uid)["record"]["documents"], 1)

    def test_fields_are_checked(self):
        with self.assertRaises(ValueError):
            self.vault.create_record("patients", {"name": ""})
        with self.assertRaises(ValueError):
            self.vault.create_record("patients", {"name": "A", "status": "Unknown status"})
        uid = self.vault.create_record("patients", {"name": "Fields Test", "created": "1999"})  # not editable: ignored
        self.assertNotEqual(self.vault.get_record("patients", uid)["record"]["created"], "1999")

    def test_edits_are_logged_with_old_and_new_values(self):
        uid = self.vault.create_record("patients", {"name": "Edit Test", "status": "Pending"})
        self.vault.update_record("patients", uid, {"status": "Active", "name": "Edit Test"})
        entry = self.vault.list_records("activity", patient_uid=uid)["rows"][0]
        self.assertEqual(entry["action"], "edited patient")
        self.assertIn("status: Pending → Active", entry["detail"])
        self.assertNotIn("name", entry["detail"].split(":", 1)[1])  # unchanged fields aren't logged
        self.assertIn("Test Clinician", entry["actor"])

    def test_questions_can_only_read(self):
        self.assertEqual(self.vault.run_query("SELECT COUNT(*) AS n FROM patients_view")["columns"], ["n"])
        # What bonsai-2:9b wrote: a good query with "; }" after it; and a semicolon inside quotes isn't an end
        self.assertEqual(self.vault.run_query("SELECT 1 AS n FROM patients_view LIMIT 1; }")["columns"], ["n"])
        self.assertEqual(self.vault.run_query("SELECT 'a;b' AS t")["rows"], [["a;b"]])
        for sql in ["DELETE FROM patients", "UPDATE patients SET name = 'x'", "SELECT 1; DELETE FROM patients",
                    "WITH x AS (SELECT 1) DELETE FROM patients"]:
            with self.assertRaises(Exception, msg=sql):
                self.vault.run_query(sql)

    def test_deleting_a_patient_keeps_the_log(self):
        uid = self.vault.create_record("patients", {"name": "Delete Test"})
        self.vault.delete_record("patients", uid)
        with self.assertRaises(ValueError):
            self.vault.get_record("patients", uid)
        self.assertTrue(any(f"{uid}: Delete Test" in (r["detail"] or "") for r in self.vault.list_records("activity")["rows"]))
        self.assertTrue(self.vault.verify_audit()["ok"])


class RecordTypes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vault = support.unlocked_vault()

    def test_record_type_fields_are_saved_checked_and_logged(self):
        uid = self.vault.create_record("patients", {"name": "Type Test", "record_type": "Crisis / stabilization",
                                                    "program": "21-day stabilization"})
        data = self.vault.get_record("patients", uid)
        self.assertEqual(data["custom"]["program"], "21-day stabilization")
        with self.assertRaisesRegex(ValueError, "must be one of"):
            self.vault.update_record("patients", uid, {"custom": {"program": "Summer camp"}})
        self.vault.update_record("patients", uid, {"custom": {"program": "60-day stabilization"}})
        entry = self.vault.list_records("activity", patient_uid=uid)["rows"][0]
        self.assertIn("program: 21-day stabilization → 60-day stabilization", entry["detail"])
        self.assertTrue(self.vault.verify_audit()["ok"])

    def test_new_people_are_patients(self):
        uid = self.vault.create_record("patients", {"name": "Default Type"})
        record = self.vault.get_record("patients", uid)["record"]
        self.assertEqual((record["record_type"], record["status"], record["placement"]), ("Patient", "Active", None))
        self.assertTrue(uid.startswith("PT-"))

    def test_ai_values_never_overwrite_a_persons_edit(self):
        patient = self.vault.create_record("patients", {"name": "AI Fill Test"})
        doc = self.vault.add_document(patient, "order.pdf", b"%PDF", [{"page": 1, "text": "x", "scanned": False}])
        info = {"document_type": "Court Order", "document_date": "", "author": ""}
        doc_id = self.vault.internal("documents", doc)
        self.vault.save_document_facts(doc_id, info, [], "m", {"judge": {"value": "Hon. A", "quote": "q", "page": 1}})
        data = self.vault.get_record("documents", doc)
        self.assertEqual((data["custom"]["judge"], data["ai_sources"]["judge"]["page"]), ("Hon. A", 1))
        self.vault.update_record("documents", doc, {"custom": {"judge": "Hon. B"}})  # a person corrects it
        self.vault.save_document_facts(doc_id, info, [], "m", {"judge": {"value": "Hon. C", "quote": "q", "page": 1}})
        data = self.vault.get_record("documents", doc)
        self.assertEqual(data["custom"]["judge"], "Hon. B")
        self.assertNotIn("judge", data["ai_sources"])

    def test_changing_a_fields_type_converts_values(self):
        for name, home in (("Cottage A", "Home 2"), ("Cottage B", "home 3"), ("Cottage C", "the blue house")):
            self.vault.create_record("patients", {"name": name, "foster_home": home})
        preview = self.vault.preview_field_change("patients", "Patient", "foster_home", "picklist",
                                                  ["Home 1", "Home 2", "Home 3"])
        self.assertGreaterEqual(preview["converted"], 2)  # other tests' patients may be counted too
        self.assertIn("the blue house", [u["value"] for u in preview["unfit"]])
        self.vault.change_field_type("patients", "Patient", "foster_home", "picklist",
                                     ["Home 1", "Home 2", "Home 3"])
        homes = {r["name"]: self.vault.get_record("patients", r["id"])["custom"].get("foster_home")
                    for r in self.vault.list_records("patients", "Cottage ")["rows"] if r["name"].startswith("Cottage ")}
        self.assertEqual(homes, {"Cottage A": "Home 2", "Cottage B": "Home 3", "Cottage C": ""})
        field = next(d for d in self.vault.definitions_for("patients", "Patient") if d["name"] == "foster_home")
        self.assertEqual(field["type"], "picklist")
        with self.assertRaises(ValueError):  # the new type is enforced from now on
            self.vault.create_record("patients", {"name": "Cottage D", "foster_home": "Home 9"})
        self.vault.change_field_type("patients", "Patient", "foster_home", "text")  # back, for other tests


class Uploads(unittest.TestCase):
    """The uploads inbox: files wait for review, then are filed under a patient and read in the background."""

    @classmethod
    def setUpClass(cls):
        cls.vault = support.unlocked_vault()

    def upload(self, text="ORDERED that the child is placed. Family Court.", name="order.pdf", target=None):
        pages = [{"page": 1, "text": text, "scanned": True, "unclear": False}]
        return self.vault.add_upload(name, b"%PDF", pages, target, {"document_type": "Court Order"})

    def test_confirming_files_it_learns_and_queues_reading(self):
        patient = self.vault.create_record("patients", {"name": "Inbox Test (fictional)"})
        uid = self.upload()
        listed = next(u for u in self.vault.list_uploads() if u["id"] == uid)
        self.assertEqual(listed["scanned_pages"], [1])
        doc, _ = self.vault.confirm_upload(uid, {"patient_id": patient, "document_type": "Court Order",
                                                 "pull": {"facts": True, "fields": ["judge", "court"]}})
        self.assertFalse(any(u["id"] == uid for u in self.vault.list_uploads()))
        self.assertEqual(self.vault.get_record("documents", doc)["record"]["read_state"], "queued")
        self.assertIn({"document_type": "Court Order", "name": "order.pdf", "text": "ORDERED that the child is placed. Family Court."},
                      self.vault.type_examples())
        self.assertEqual(self.vault.pull_preferences()["Court Order"], {"facts": True, "fields": ["judge", "court"]})
        queued = self.vault.next_document_to_read()
        while queued["uid"] != doc:  # documents other tests queued first
            self.vault.save_document_facts(queued["id"], queued["info"], [], "test-model")
            queued = self.vault.next_document_to_read()
        self.assertEqual(queued["info"]["document_type"], "Court Order")
        self.vault.save_document_facts(queued["id"], queued["info"], [], "test-model")
        self.assertEqual(self.vault.get_record("documents", doc)["record"]["read_state"], "read")

    def test_a_new_patient_can_be_made_from_a_file(self):
        doc, patient = self.vault.confirm_upload(self.upload(), {"new_patient": {"name": "Casey New (fictional)", "dob": "07/22/2010"},
                                                                 "document_type": "Court Order"})
        self.assertEqual(self.vault.get_record("patients", patient)["record"]["dob"], "07/22/2010")
        self.assertEqual(self.vault.get_record("documents", doc)["record"]["patient_id"], patient)

    def test_type_and_patient_are_required(self):
        uid = self.upload()
        with self.assertRaisesRegex(ValueError, "type"):
            self.vault.confirm_upload(uid, {"document_type": "Not a type"})
        with self.assertRaisesRegex(ValueError, "patient"):
            self.vault.confirm_upload(uid, {"document_type": "Court Order"})
        self.vault.discard_upload(uid)
        with self.assertRaises(ValueError):
            self.vault.get_upload(uid)

    def test_correcting_a_type_teaches_it_and_refills_details(self):
        patient = self.vault.create_record("patients", {"name": "Correction Test (fictional)"})
        doc, _ = self.vault.confirm_upload(self.upload(name="iep.pdf"), {"patient_id": patient, "document_type": "Court Order"})
        row_id = self.vault.internal("documents", doc)
        self.vault.save_document_facts(row_id, {"document_type": "Court Order"}, [], "test-model")
        self.vault.update_record("documents", doc, {"document_type": "Individualized Education Program (IEP)"})
        self.assertIn("Individualized Education Program (IEP)",
                      [e["document_type"] for e in self.vault.type_examples() if e["name"] == "iep.pdf"])
        self.assertEqual(self.vault.get_record("documents", doc)["record"]["read_state"], "fields")
        self.vault.delete_record("documents", doc)  # its example goes with it
        self.assertNotIn("iep.pdf", [e["name"] for e in self.vault.type_examples()])

    def test_types_can_be_added(self):
        self.vault.add_record_type("documents", "Safety Plan (test)", "A written plan for staying safe")
        self.assertIn("Safety Plan (test)", self.vault.describe_objects()["documents"]["record_types"])
        self.assertEqual(self.vault.record_type_descriptions("documents")["Safety Plan (test)"], "A written plan for staying safe")
        with self.assertRaisesRegex(ValueError, "already"):
            self.vault.add_record_type("documents", "safety plan (TEST)")
        patient = self.vault.create_record("patients", {"name": "Type Test (fictional)"})
        doc, _ = self.vault.confirm_upload(self.upload(), {"patient_id": patient, "document_type": "Safety Plan (test)"})
        self.assertEqual(self.vault.get_record("documents", doc)["record"]["document_type"], "Safety Plan (test)")


class Clinical(unittest.TestCase):
    """Medications, rating scales, follow-ups, what's coming up, and the timeline."""

    @classmethod
    def setUpClass(cls):
        cls.vault = support.unlocked_vault()
        cls.patient = cls.vault.create_record("patients", {"name": "Clinical Test (fictional)", "dob": "01/02/2012",
                                                           "record_type": "Residential treatment", "custom": {"admission_date": "04/10/2024"}})

    def test_scores_are_checked_and_bmi_is_worked_out(self):
        with self.assertRaisesRegex(ValueError, "between 0 and 27"):
            self.vault.create_record("measures", {"measure": "PHQ-A (depression)", "date": "10/01/2026", "score": 30}, self.patient)
        with self.assertRaisesRegex(ValueError, "total score"):
            self.vault.create_record("measures", {"measure": "GAD-7 (anxiety)", "date": "10/01/2026"}, self.patient)
        vitals = self.vault.create_record("measures", {"measure": "Vitals", "date": "10/01/2026",
                                                       "custom": {"weight_lb": "100", "height_in": "60"}}, self.patient)
        self.assertEqual(self.vault.get_record("measures", vitals)["custom"]["bmi"], 19.5)
        with self.assertRaisesRegex(ValueError, "patient"):
            self.vault.create_record("follow_ups", {"title": "No patient"})

    def test_lists_are_in_date_order(self):
        for date, score in (("09/01/2026", 9), ("01/15/2026", 15), ("12/01/2025", 20)):
            self.vault.create_record("measures", {"measure": "PHQ-A (depression)", "date": date, "score": score}, self.patient)
        dates = [r["date"] for r in self.vault.list_records("measures", patient_uid=self.patient)["rows"] if r["measure"].startswith("PHQ")]
        self.assertEqual(dates, ["09/01/2026", "01/15/2026", "12/01/2025"])  # newest first, across years

    def test_coming_up_collects_whats_due(self):
        import datetime
        soon = (datetime.date.today() + datetime.timedelta(days=5)).strftime("%m/%d/%Y")
        far = (datetime.date.today() + datetime.timedelta(days=200)).strftime("%m/%d/%Y")
        due = self.vault.create_record("follow_ups", {"title": "Court report", "due": soon}, self.patient)
        later = self.vault.create_record("follow_ups", {"title": "Far away", "due": far}, self.patient)
        med = self.vault.create_record("medications", {"name": "Sertraline", "consent": "Requested"}, self.patient)
        items = {i["id"]: i for i in self.vault.coming_up()}
        self.assertEqual(items[due]["days"], 5)
        self.assertNotIn(later, items)
        self.assertEqual(items[med]["text"], "Consent requested for Sertraline")
        self.vault.update_record("follow_ups", due, {"status": "Done"})
        self.assertNotIn(due, {i["id"] for i in self.vault.coming_up()})

    def test_the_timeline_puts_history_in_order(self):
        self.vault.create_record("medications", {"name": "Guanfacine", "start_date": "March 2021", "stop_date": "05/01/2024",
                                                 "stop_reason": "Changed to ER", "status": "Stopped"}, self.patient)
        texts = [e["text"] for e in self.vault.timeline(self.patient)]
        self.assertEqual(texts[0], "Born")
        self.assertLess(texts.index("Started Guanfacine"), texts.index("Admitted (Residential treatment)"))
        self.assertLess(texts.index("Admitted (Residential treatment)"), texts.index("Stopped Guanfacine: Changed to ER"))

    def test_documents_can_be_searched_by_their_text(self):
        doc = self.vault.add_document(self.patient, "scan.pdf", b"%PDF", [{"page": 1, "text": "Mentions zolpidem once.", "scanned": False}])
        self.assertIn(doc, [r["id"] for r in self.vault.list_records("documents", "zolpidem")["rows"]])


class GettingStarted(unittest.TestCase):
    def test_the_checklist_ticks_off_features_as_they_are_tried(self):
        vault = support.unlocked_vault()
        patient = vault.create_record("patients", {"name": "Checklist Test (fictional)"})
        vault.add_note(patient, "First note.")
        steps = vault.onboarding()["steps"]
        self.assertTrue(steps["patient"] and steps["note"])
        vault.set_setting("checklist", "hidden")
        self.assertTrue(vault.onboarding()["hidden"])
        vault.set_setting("checklist", "")


class AuditTrail(unittest.TestCase):
    def test_tampering_is_detected(self):
        vault = support.unlocked_vault()
        vault.log("test entry", None, "original")
        self.assertTrue(vault.verify_audit()["ok"])
        with vault.connect() as db:
            db.execute("UPDATE activity SET detail = 'changed' WHERE detail = 'original'")
        self.assertFalse(vault.verify_audit()["ok"])
        with vault.connect() as db:  # put it back for the other tests
            db.execute("UPDATE activity SET detail = 'original' WHERE detail = 'changed'")
        self.assertTrue(vault.verify_audit()["ok"])
        self.assertIn("test entry", vault.export_audit())


class SigningIn(unittest.TestCase):
    def test_wrong_passwords_are_slowed_down(self):
        vault = support.unlocked_vault()
        vault.lock()
        for attempt in range(vault.MAX_ATTEMPTS):
            with self.assertRaisesRegex(ValueError, "Wrong password"):
                vault.unlock(f"wrong-{attempt}")
        with self.assertRaisesRegex(ValueError, "Too many wrong passwords"):
            vault.unlock(support.PASSWORD)  # even the right password waits
        vault._wait_until = 0
        vault.unlock(support.PASSWORD)
        self.assertIn("after 5 wrong password attempts", vault.list_records("activity")["rows"][0]["action"])

    def test_password_can_be_checked_while_open(self):
        vault = support.unlocked_vault()
        vault.check_password(support.PASSWORD)
        with self.assertRaisesRegex(ValueError, "isn't right"):
            vault.check_password("not-the-password")
        vault.lock()
        vault.unlock(support.PASSWORD)  # checking changed nothing

    def test_password_can_be_changed(self):
        vault = support.unlocked_vault()
        with self.assertRaisesRegex(ValueError, "current password"):
            vault.change_password("not-the-password", "another-password-1")
        self.assertTrue(vault.is_unlocked())
        vault.change_password(support.PASSWORD, "another-password-1")
        vault.lock()
        with self.assertRaises(ValueError):
            vault.unlock(support.PASSWORD)
        vault.unlock("another-password-1")
        vault.change_password("another-password-1", support.PASSWORD)  # back to normal for other tests
        vault._failed_attempts = 0


class People(unittest.TestCase):
    """Patients and legal guardians: one object, two record types."""
    @classmethod
    def setUpClass(cls):
        cls.vault = support.unlocked_vault()

    def test_names_are_first_and_last(self):
        uid = self.vault.create_record("patients", {"first_name": "Riley", "last_name": "Moss (fictional)"})
        self.assertEqual(self.vault.get_record("patients", uid)["record"]["name"], "Riley Moss (fictional)")
        self.vault.update_record("patients", uid, {"first_name": "Rylee"})
        self.assertEqual(self.vault.get_record("patients", uid)["record"]["name"], "Rylee Moss (fictional)")
        typed = self.vault.create_record("patients", {"name": "Dana Q Reyes"})  # a full name, from the command bar
        record = self.vault.get_record("patients", typed)["record"]
        self.assertEqual((record["first_name"], record["last_name"]), ("Dana Q", "Reyes"))
        with self.assertRaisesRegex(ValueError, "first or last name"):
            self.vault.create_record("patients", {"first_name": " ", "last_name": ""})

    def test_gender_is_male_female_or_x(self):
        uid = self.vault.create_record("patients", {"name": "Gender Test", "gender": "girl"})
        self.assertEqual(self.vault.get_record("patients", uid)["record"]["gender"], "Female")
        with self.assertRaisesRegex(ValueError, "Male, Female, X"):
            self.vault.update_record("patients", uid, {"gender": "Unknown"})

    def test_placement_used_to_be_the_record_type(self):
        uid = self.vault.create_record("patients", {"name": "Placement Test", "record_type": "Foster care", "agency": "Agency A"})
        data = self.vault.get_record("patients", uid)
        self.assertEqual((data["record"]["record_type"], data["record"]["placement"]), ("Patient", "Foster care"))
        self.assertEqual(data["custom"]["agency"], "Agency A")

    def test_a_guardian_typed_in_before_stays_until_one_is_chosen(self):
        uid = self.vault.create_record("patients", {"name": "Typed Guardian Test"})
        with self.vault.connect() as db:  # as version 6 leaves a patient whose guardian was typed in
            db.execute("UPDATE patients SET guardian = 'Foster mother (Ms. R)' WHERE uid = ?", (uid,))
        self.vault.update_record("patients", uid, {"mrn": "123", "guardians": []})  # the edit form, with none chosen
        self.assertEqual(self.vault.get_record("patients", uid)["record"]["guardian"], "Foster mother (Ms. R)")

    def test_guardians_link_to_patients(self):
        guardian = self.vault.create_record("patients", {"first_name": "Lena", "last_name": "Ortiz (fictional)",
                                                         "record_type": "Legal Guardian", "relationship": "Grandparent"})
        self.assertTrue(guardian.startswith("LG-"))
        self.assertIsNone(self.vault.get_record("patients", guardian)["record"]["status"])  # status is for patients
        first = self.vault.create_record("patients", {"name": "Ana Ortiz (fictional)", "guardians": [guardian]})
        second = self.vault.create_record("patients", {"name": "Ben Ortiz (fictional)"})
        self.vault.update_record("patients", second, {"guardians": [guardian]})
        self.assertEqual(self.vault.get_record("patients", first)["record"]["guardian"], "Lena Ortiz (fictional)")
        wards = self.vault.get_record("patients", guardian)["related"]["guardian_of"]
        self.assertEqual([w["name"] for w in wards], ["Ana Ortiz (fictional)", "Ben Ortiz (fictional)"])
        # Guardians are listed apart from patients, and uploads never match them
        self.assertNotIn(guardian, [r["id"] for r in self.vault.list_records("patients")["rows"]])
        listed = self.vault.list_records("patients", record_type="Legal Guardian")["rows"]
        self.assertIn("Ana Ortiz (fictional), Ben Ortiz (fictional)", [r["guardian_of"] for r in listed if r["id"] == guardian])
        self.assertNotIn(guardian, [p["id"] for p in self.vault.patients_for_matching()])
        # A new name shows on their patients' records; only a guardian can be chosen; deleting one unlinks their patients
        self.vault.update_record("patients", guardian, {"first_name": "Lina"})
        self.assertEqual(self.vault.get_record("patients", second)["record"]["guardian"], "Lina Ortiz (fictional)")
        with self.assertRaisesRegex(ValueError, "legal guardians from the list"):
            self.vault.update_record("patients", second, {"guardians": [first]})
        self.vault.delete_record("patients", guardian)
        data = self.vault.get_record("patients", first)
        self.assertEqual((data["guardians"], data["record"]["guardian"]), ([], None))
        self.assertIn("deleted legal guardian", [r["action"] for r in self.vault.list_records("activity")["rows"][:3]])

    def test_a_patient_can_have_several_guardians(self):
        mother = self.vault.create_record("patients", {"name": "Mia Park (fictional)", "record_type": "Legal Guardian"})
        father = self.vault.create_record("patients", {"name": "Leo Park (fictional)", "record_type": "Legal Guardian"})
        child = self.vault.create_record("patients", {"name": "Kai Park (fictional)", "guardians": [mother, father]})
        data = self.vault.get_record("patients", child)
        self.assertEqual([g["name"] for g in data["guardians"]], ["Leo Park (fictional)", "Mia Park (fictional)"])
        self.vault.update_record("patients", child, {"guardians": [mother]})
        entry = self.vault.list_records("activity", patient_uid=child)["rows"][0]
        self.assertIn("legal guardians: Leo Park (fictional), Mia Park (fictional) → Mia Park (fictional)", entry["detail"])
        self.assertEqual(self.vault.get_record("patients", father)["related"]["guardian_of"], [])

    def test_typed_dates_are_saved_as_dates(self):
        uid = self.vault.create_record("patients", {"name": "Date Test", "dob": "12271994", "admission_date": "4-10-24",
                                                    "placement": "Residential treatment"})
        data = self.vault.get_record("patients", uid)
        self.assertEqual((data["record"]["dob"], data["custom"]["admission_date"]), ("12/27/1994", "04/10/2024"))
        follow_up = self.vault.create_record("follow_ups", {"title": "Court date", "due": "Dec 1 2026"}, uid)
        self.assertEqual(self.vault.get_record("follow_ups", follow_up)["record"]["due"], "12/01/2026")


class MedicationsAndPlaces(unittest.TestCase):
    """The medication list offered when adding one, and towns and drive times from ZIP codes."""
    @classmethod
    def setUpClass(cls):
        cls.vault = support.unlocked_vault()

    def test_medication_names_are_saved_the_catalog_way(self):
        patient = self.vault.create_record("patients", {"name": "Med Name Test"})
        med = self.vault.create_record("medications", {"name": "zoloft", "dose": "25 mg"}, patient)
        self.assertEqual(self.vault.get_record("medications", med)["record"]["name"], "Sertraline")
        self.vault.create_record("medications", {"name": "Brand New Med (test)"}, patient)  # not in the catalog: kept as typed
        names = {m["name"]: m for m in self.vault.medication_names()}
        self.assertGreaterEqual(names["Sertraline"]["patients"], 1)
        self.assertEqual(names["Brand New Med (test)"]["category"], "Added in MindChart")

    def test_a_guardians_zip_gives_their_town_and_drive_time(self):
        guardian = self.vault.create_record("patients", {"name": "Zip Guardian (fictional)", "record_type": "Legal Guardian",
                                                         "zip": "10701"})
        place = self.vault.get_record("patients", guardian)["place"]
        self.assertEqual((place["town"], place["state"]), ("Yonkers", "NY"))
        self.assertLess(place["minutes"], 30)
        row = next(r for r in self.vault.list_records("patients", record_type="Legal Guardian")["rows"] if r["id"] == guardian)
        self.assertEqual(row["town"], "Yonkers, NY")
        with self.assertRaisesRegex(ValueError, "5-digit ZIP"):
            self.vault.update_record("patients", guardian, {"zip": "1070"})


class Cottages(unittest.TestCase):
    """Cottages, who lives in each, and the AI's experimental readings of their dynamics (with feedback)."""
    @classmethod
    def setUpClass(cls):
        cls.vault = support.unlocked_vault()

    def cottage(self, name):
        return next(r["id"] for r in self.vault.list_records("cottages")["rows"] if r["name"] == name)

    def test_the_cottages_are_there_from_the_start_and_are_the_choices(self):
        names = [r["name"] for r in self.vault.list_records("cottages")["rows"]]
        self.assertTrue({"Collins", "Van Horn", "AOBH"} <= set(names))
        options = next(d for d in self.vault.definitions_for("patients", "Patient") if d["name"] == "cottage")["options"]
        self.assertEqual(options[:3], ["AOBH", "Butler", "Collins"])

    def test_residents_follow_a_renamed_cottage(self):
        new = self.vault.create_record("cottages", {"name": "Test Cottage A", "capacity": "8"})
        with self.assertRaisesRegex(ValueError, "already a cottage"):
            self.vault.create_record("cottages", {"name": "test cottage a"})
        kid = self.vault.create_record("patients", {"name": "Resident Kid (fictional)", "placement": "Residential treatment",
                                                    "cottage": "Test Cottage A"})
        self.assertEqual([r["name"] for r in self.vault.get_record("cottages", new)["related"]["residents"]], ["Resident Kid (fictional)"])
        self.vault.update_record("cottages", new, {"name": "Test Cottage B"})
        self.assertEqual(self.vault.get_record("patients", kid)["custom"]["cottage"], "Test Cottage B")
        self.assertIn("Test Cottage B", next(d for d in self.vault.definitions_for("patients", "Patient") if d["name"] == "cottage")["options"])
        self.vault.delete_record("cottages", new)  # the resident keeps the name, as one no longer on the list
        self.assertEqual(self.vault.get_record("patients", kid)["custom"]["cottage"], "Test Cottage B")
        self.vault.update_record("patients", kid, {"mrn": "7"})  # and can still be saved

    def test_an_analysis_keeps_only_points_from_the_records_and_takes_feedback(self):
        cottage = self.cottage("Kendall")
        a = self.vault.create_record("patients", {"name": "Ari Lane (fictional)", "placement": "Residential treatment", "cottage": "Kendall"})
        self.vault.add_note(a, "Ari argued with a peer at dinner and was redirected by staff.", "10/01/2026")
        inputs = self.vault.cottage_inputs(cottage)
        self.assertEqual([r["name"] for r in inputs["residents"]], ["Ari Lane (fictional)"])
        self.assertIn("argued with a peer", inputs["facts"][0]["fact"])
        history.ask = lambda *a, **k: {"summary": "One resident [1].", "themes": [
            {"title": "Peer conflict", "detail": "Ari argued with a peer (fact 1).", "residents": ["Ari"], "facts": [1]},
            {"title": "Made up", "detail": "Not in the records.", "residents": [], "facts": [9]}], "interactions": [], "watch_for": []}
        try:
            result = history.analyze_cottage("m", False, inputs)
        finally:
            history.ask = REAL_ASK
        self.assertEqual(result["summary"], "One resident.")
        self.assertEqual([t["title"] for t in result["themes"]], ["Peer conflict"])  # the point citing nothing real is dropped
        self.assertEqual(result["themes"][0]["sources"][0]["resident"], "Ari Lane (fictional)")
        number = self.vault.save_cottage_analysis(cottage, result, "m")
        self.vault.save_analysis_feedback(number, "not helpful", "Too vague; name the peer when the records do.")
        self.assertEqual(self.vault.get_record("cottages", cottage)["analysis"]["feedback"]["rating"], "not helpful")
        self.assertIn("name the peer", self.vault.analysis_feedback()[0]["comment"])
        prompts = []
        history.ask = lambda model, thinking, system, *a, **k: prompts.append(system) or {"summary": "", "themes": [], "interactions": [], "watch_for": []}
        try:
            history.analyze_cottage("m", False, inputs, self.vault.analysis_feedback())
        finally:
            history.ask = REAL_ASK
        self.assertIn("name the peer", prompts[0])  # the next analysis is told

    def test_across_cottages_only_records_naming_another_cottages_resident(self):
        x = self.vault.create_record("patients", {"first_name": "Quinby", "last_name": "Test (fictional)", "placement": "Residential treatment", "cottage": "Rose"})
        self.vault.create_record("patients", {"first_name": "Marlow", "last_name": "Test (fictional)", "placement": "Residential treatment", "cottage": "Smith"})
        self.vault.add_note(x, "Quinby got into a shoving match with Marlow at the gym.", "10/02/2026")
        self.vault.add_note(x, "Quinby slept well.", "10/03/2026")
        found = [f["fact"] for f in self.vault.intercottage_inputs()["facts"] if f["resident"].startswith("Quinby")]
        self.assertEqual(len(found), 1)
        self.assertIn("Marlow", found[0])


class Upgrades(unittest.TestCase):
    """Upgrading saved data, on a plain database file (the steps don't depend on encryption)."""

    def make_version_1(self, path):
        db = sqlite3.connect(path)
        db.executescript("""
            CREATE TABLE patients (id INTEGER PRIMARY KEY, uid TEXT UNIQUE NOT NULL, name TEXT, dob TEXT, gender TEXT, mrn TEXT,
                status TEXT, placement TEXT, admitted TEXT, guardian TEXT, notes TEXT, created TEXT);
            CREATE TABLE activity (id INTEGER PRIMARY KEY, uid TEXT UNIQUE NOT NULL,
                patient_id INTEGER REFERENCES patients(id) ON DELETE SET NULL, time TEXT, action TEXT, detail TEXT);
            INSERT INTO patients (uid, name) VALUES ('PT-AAAAAA', 'Old Patient');
            INSERT INTO patients (uid, name, placement, admitted) VALUES ('PT-BBBBBB', 'Placed Patient', 'Cottage 4', '01/02/2026');
            INSERT INTO patients (uid, name, gender) VALUES ('PT-CCCCCC', 'Girl Patient', 'F'), ('PT-DDDDDD', 'Other Patient', 'Nonbinary');
            INSERT INTO activity (uid, patient_id, time, action, detail) VALUES ('ACT-AAAAAA', 1, '2026-01-01T09:00:00', 'created patient', '');
            PRAGMA user_version = 1;""")
        return db

    def test_version_1_data_is_upgraded(self):
        vault = support.vault
        with tempfile.TemporaryDirectory() as folder:
            db = self.make_version_1(Path(folder) / "records.db")
            db.row_factory = sqlite3.Row
            original_mount = vault.MOUNT
            vault.MOUNT = Path(folder)  # backups go next to this test database
            try:
                vault.prepare_database(db)
            finally:
                vault.MOUNT = original_mount
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], vault.SCHEMA_VERSION)
            row = db.execute("SELECT patient_uid, hash FROM activity").fetchone()
            self.assertEqual(row["patient_uid"], "PT-AAAAAA")
            self.assertTrue(row["hash"])
            # Version 3: the old placement and admission date moved into the Residential treatment record type.
            # Version 6: that record type became the placement of a Patient, with the name split in two.
            placed = db.execute("SELECT * FROM patients WHERE uid = 'PT-BBBBBB'").fetchone()
            self.assertEqual((placed["record_type"], placed["placement"]), ("Patient", "Residential treatment"))
            self.assertEqual((placed["first_name"], placed["last_name"]), ("Placed", "Patient"))
            genders = dict(db.execute("SELECT uid, gender FROM patients WHERE uid IN ('PT-CCCCCC', 'PT-DDDDDD')").fetchall())
            self.assertEqual(genders, {"PT-CCCCCC": "Female", "PT-DDDDDD": "X"})
            self.assertEqual([r[0] for r in db.execute("SELECT name FROM record_types WHERE object = 'patients' ORDER BY position")],
                             ["Patient", "Legal Guardian"])
            self.assertEqual(json.loads(placed["custom"]), {"cottage": "Cottage 4", "admission_date": "01/02/2026"})
            self.assertTrue(db.execute("SELECT COUNT(*) FROM field_defs").fetchone()[0])
            self.assertTrue(list((Path(folder) / "backups").glob("records-*.db")))

    def test_version_3_documents_get_background_reading(self):
        vault = support.vault
        with tempfile.TemporaryDirectory() as folder:
            db = sqlite3.connect(Path(folder) / "records.db")
            db.row_factory = sqlite3.Row
            db.executescript("""
                CREATE TABLE patients (id INTEGER PRIMARY KEY, uid TEXT UNIQUE NOT NULL, name TEXT, dob TEXT, gender TEXT, mrn TEXT,
                    record_type TEXT, status TEXT, guardian TEXT, notes TEXT, created TEXT, custom TEXT);
                CREATE TABLE documents (id INTEGER PRIMARY KEY, uid TEXT UNIQUE NOT NULL, patient_id INTEGER REFERENCES patients(id),
                    name TEXT, kind TEXT, document_type TEXT, document_date TEXT, author TEXT, added TEXT, read_by_model TEXT,
                    original BLOB, pages TEXT, read_at TEXT, custom TEXT);
                INSERT INTO patients (uid, name) VALUES ('PT-AAAAAA', 'Old Patient');
                INSERT INTO documents (uid, patient_id, name, read_by_model) VALUES ('DOC-AAAAAA', 1, 'read.pdf', 'gpt-oss:20b');
                INSERT INTO documents (uid, patient_id, name) VALUES ('DOC-BBBBBB', 1, 'unread.pdf');
                PRAGMA user_version = 3;""")
            original_mount = vault.MOUNT
            vault.MOUNT = Path(folder)
            try:
                vault.prepare_database(db)
            finally:
                vault.MOUNT = original_mount
            self.assertEqual({r["name"]: r["read_state"] for r in db.execute("SELECT name, read_state FROM documents")},
                             {"read.pdf": "read", "unread.pdf": None})
            types = [r[0] for r in db.execute("SELECT name FROM record_types WHERE object = 'documents'")]
            self.assertIn("Court Order", types)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM uploads").fetchone()[0], 0)

    def test_newer_data_is_refused(self):
        vault = support.vault
        with tempfile.TemporaryDirectory() as folder:
            db = self.make_version_1(Path(folder) / "records.db")
            db.execute("PRAGMA user_version = 99")
            with self.assertRaisesRegex(ValueError, "newer version"):
                vault.prepare_database(db)


if __name__ == "__main__":
    unittest.main()
