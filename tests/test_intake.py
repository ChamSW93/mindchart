"""Working out who an upload is about and what type it is, before the clinician confirms it."""
import datetime
import unittest

import support  # noqa: F401  (sets up paths)
import intake

PATIENTS = [{"id": "PT-JORDAN", "name": "Jordan Avery", "dob": "05/14/2011", "mrn": "CV-20931"},
            {"id": "PT-TAYLOR", "name": "Taylor Avery", "dob": "02/02/2014", "mrn": ""},
            {"id": "PT-MARIAL", "name": "Maria Lopez", "dob": "03/04/2012", "mrn": ""}]


def child(name="", dob="", identifiers=()):
    return {"name": name, "dob": dob, "identifiers": list(identifiers)}


class Dates(unittest.TestCase):
    def test_common_ways_of_writing_a_date(self):
        expected = datetime.date(2011, 5, 14)
        for text in ["05/14/2011", "5/14/11", "2011-05-14", "May 14, 2011", "14 May 2011", "DOB: 5-14-2011", "May 14th, 2011"]:
            self.assertEqual(intake.parse_date(text), expected, text)

    def test_nonsense_is_not_a_date(self):
        for text in ["", "unknown", "13/45/2011", "Smarch 3, 2011"]:
            self.assertIsNone(intake.parse_date(text), text)


class Matching(unittest.TestCase):
    def test_name_and_birth_date_is_a_strong_match(self):
        best = intake.match_patients(child("Avery, Jordan", "5/14/11"), PATIENTS, "")[0]
        self.assertEqual((best["id"], best["strength"]), ("PT-JORDAN", "strong"))

    def test_a_unique_full_name_without_a_birth_date_is_likely(self):
        matches = intake.match_patients(child("Jordan Avery"), PATIENTS, "")
        self.assertEqual((matches[0]["id"], matches[0]["strength"]), ("PT-JORDAN", "likely"))
        self.assertEqual(matches[1]["id"], "PT-TAYLOR")  # a sibling: same last name, only a possible match

    def test_ocr_slips_in_a_name_still_match(self):
        self.assertEqual(intake.match_patients(child("Jordan Averv", "05/14/2011"), PATIENTS, "")[0]["strength"], "strong")

    def test_mrn_found_in_the_text(self):
        best = intake.match_patients(child(), PATIENTS, "Client ID: CV-20931 Seen for follow up.")[0]
        self.assertEqual(best["id"], "PT-JORDAN")
        self.assertIn("MRN / ID matches", best["reasons"])

    def test_names_and_birth_dates_found_in_the_text_before_the_ai_reads_it(self):
        best = intake.match_patients(child(), PATIENTS, "Patient: Jordan Avery (fictional)  DOB: 05/14/2011")[0]
        self.assertEqual((best["id"], best["strength"]), ("PT-JORDAN", "strong"))

    def test_a_different_birth_date_counts_against(self):
        matches = intake.match_patients(child("Jordan Avery", "01/01/2009"), PATIENTS, "")
        self.assertNotEqual(matches[0:1] and matches[0]["strength"], "strong")


class Choosing(unittest.TestCase):
    def test_a_file_dropped_on_the_wrong_sibling_is_flagged(self):
        found = child("Jordan Avery")
        matches = intake.match_patients(found, PATIENTS, "")
        choice = intake.choose_patient("PT-TAYLOR", matches, found, PATIENTS)
        self.assertEqual(choice["patient_id"], "PT-JORDAN")
        self.assertIn("Jordan Avery", choice["warning"])

    def test_dropped_on_the_right_patient(self):
        found = child("Jordan Avery")
        choice = intake.choose_patient("PT-JORDAN", intake.match_patients(found, PATIENTS, ""), found, PATIENTS)
        self.assertEqual(choice, {"patient_id": "PT-JORDAN", "warning": ""})

    def test_a_child_not_in_mindchart_is_offered_as_a_new_patient(self):
        found = child("Casey Lin", "07/22/2010")
        choice = intake.choose_patient(None, intake.match_patients(found, PATIENTS, ""), found, PATIENTS)
        self.assertEqual(choice["patient_id"], "__new")

    def test_learned_and_ai_types_combined(self):
        hint = {"type": "Court Order", "confidence": "medium", "reason": "Has wording typical of a Court Order"}
        self.assertEqual(intake.choose_type(hint, "Court Order")["type_confidence"], "high")
        learned = {"type": "Safety Plan", "confidence": "high", "reason": "Looks like 3 documents you confirmed as Safety Plan"}
        self.assertEqual(intake.choose_type(learned, "Other")["document_type"], "Safety Plan")  # strong learning beats the AI
        self.assertEqual(intake.choose_type(hint, "Discharge Summary")["document_type"], "Discharge Summary")


if __name__ == "__main__":
    unittest.main()
