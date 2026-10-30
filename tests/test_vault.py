"""The encrypted store: records, IDs, edits, the audit trail, sign-in limits, passwords and upgrades."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import support


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

    def test_new_patients_default_to_residential(self):
        uid = self.vault.create_record("patients", {"name": "Default Type"})
        self.assertEqual(self.vault.get_record("patients", uid)["record"]["record_type"], "Residential treatment")

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
        for name, cottage in (("Cottage A", "Cottage 2"), ("Cottage B", "cottage 3"), ("Cottage C", "the blue house")):
            self.vault.create_record("patients", {"name": name, "cottage": cottage})
        preview = self.vault.preview_field_change("patients", "Residential treatment", "cottage", "picklist",
                                                  ["Cottage 1", "Cottage 2", "Cottage 3"])
        self.assertGreaterEqual(preview["converted"], 2)  # other tests' patients may be counted too
        self.assertIn("the blue house", [u["value"] for u in preview["unfit"]])
        self.vault.change_field_type("patients", "Residential treatment", "cottage", "picklist",
                                     ["Cottage 1", "Cottage 2", "Cottage 3"])
        cottages = {r["name"]: self.vault.get_record("patients", r["id"])["custom"].get("cottage")
                    for r in self.vault.list_records("patients", "Cottage ")["rows"] if r["name"].startswith("Cottage ")}
        self.assertEqual(cottages, {"Cottage A": "Cottage 2", "Cottage B": "Cottage 3", "Cottage C": ""})
        field = next(d for d in self.vault.definitions_for("patients", "Residential treatment") if d["name"] == "cottage")
        self.assertEqual(field["type"], "picklist")
        with self.assertRaises(ValueError):  # the new type is enforced from now on
            self.vault.create_record("patients", {"name": "Cottage D", "cottage": "Cottage 9"})
        self.vault.change_field_type("patients", "Residential treatment", "cottage", "text")  # back, for other tests


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
            placed = db.execute("SELECT record_type, custom FROM patients WHERE uid = 'PT-BBBBBB'").fetchone()
            self.assertEqual(placed["record_type"], "Residential treatment")
            self.assertEqual(json.loads(placed["custom"]), {"cottage": "Cottage 4", "admission_date": "01/02/2026"})
            self.assertTrue(db.execute("SELECT COUNT(*) FROM field_defs").fetchone()[0])
            self.assertTrue(list((Path(folder) / "backups").glob("records-*.db")))

    def test_newer_data_is_refused(self):
        vault = support.vault
        with tempfile.TemporaryDirectory() as folder:
            db = self.make_version_1(Path(folder) / "records.db")
            db.execute("PRAGMA user_version = 99")
            with self.assertRaisesRegex(ValueError, "newer version"):
                vault.prepare_database(db)


if __name__ == "__main__":
    unittest.main()
