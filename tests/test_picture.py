"""When facts were true: ongoing facts on a topic dated so the newest is current, and older ones earlier, so records from
different years ("10th grade" in 2023, "11th grade" in 2024) read as change over time, not a disagreement."""
import unittest

import support
import history
import picture
import vault
import worker


def fact(text, topic="", kind="Ongoing", document_date="", date="", verified=True):
    return {"fact": text, "kind": kind, "topic": topic, "date": date, "document_date": document_date, "verified": verified}


class Topics(unittest.TestCase):
    def test_the_same_topic_said_differently_is_one_topic(self):
        self.assertEqual(picture.topic_key("School Grade"), "grade")
        self.assertEqual(picture.topic_key("custody"), "guardianship")
        self.assertEqual(picture.topic_key("Medication: Zoloft 50 mg"), "medication: sertraline")
        self.assertEqual(picture.topic_key("meds: sertraline"), "medication: sertraline")
        self.assertEqual(picture.topic_key("Symptoms: nightmares"), "symptom: nightmares")
        self.assertEqual(picture.topic_key(""), "")


class CurrentAndEarlier(unittest.TestCase):
    def test_the_newest_record_on_a_topic_is_current(self):
        old, new = fact("In 10th grade.", "grade", document_date="05/10/2023"), fact("In 11th grade.", "school grade", document_date="09/18/2024")
        picture.annotate([old, new])
        self.assertEqual((new["status"], new["as_of"]), ("current", "09/18/2024"))
        self.assertEqual((old["status"], old["newer"]), ("earlier", "09/18/2024"))
        self.assertEqual(new["group"], "School")

    def test_records_from_about_the_same_time_are_both_current(self):
        a, b = fact("Takes sertraline 50 mg.", "medication: sertraline", document_date="09/01/2024"), \
            fact("Sertraline 50 mg daily.", "medication: Zoloft", document_date="09/20/2024")
        picture.annotate([a, b])
        self.assertEqual([a["status"], b["status"]], ["current", "current"])

    def test_a_fact_is_true_as_of_its_records_date(self):
        # "since 2019" in a 2024 record is newer than a 2023 record, and an end date isn't when it was true
        since = fact("Lives with foster mother since 2019.", "lives with", document_date="05/20/2024", date="2019")
        older = fact("Lives in a group home.", "lives with", document_date="09/18/2023")
        order = fact("Order of protection until March 4, 2025.", "legal status", document_date="March 4, 2024", date="March 4, 2025")
        picture.annotate([since, older, order])
        self.assertEqual((since["status"], since["as_of"], older["status"]), ("current", "05/20/2024", "earlier"))
        self.assertEqual(order["as_of"], "March 4, 2024")
        undated_record = fact("In 6th grade.", "grade", date="2022")
        picture.annotate([undated_record])
        self.assertEqual(undated_record["as_of"], "2022")  # the fact's own date, when the record's isn't known

    def test_diagnoses_written_differently_are_one_topic(self):
        self.assertEqual(picture.topic_key("diagnosis: chronic post-traumatic stress disorder"), "diagnosis: ptsd")
        self.assertEqual(picture.topic_key("Dx: PTSD"), "diagnosis: ptsd")
        self.assertEqual(picture.topic_key("diagnosis: ADHD, combined type"), "diagnosis: adhd")
        self.assertEqual(picture.topic_key("diagnosis: rule out MDD"), "diagnosis: rule out major depressive disorder")
        self.assertEqual(picture.topic_key("diagnosis: enuresis"), "diagnosis: enuresis")

    def test_broad_topics_never_make_other_facts_earlier(self):
        anxious, nightmares = fact("Reports anxiety.", "symptoms", document_date="05/10/2023"), fact("Has nightmares.", "symptoms", document_date="09/18/2024")
        picture.annotate([anxious, nightmares])
        self.assertEqual([anxious["status"], nightmares["status"]], ["current", "current"])  # newer nightmares don't end the anxiety

    def test_unverified_facts_and_undated_ones_dont_win(self):
        verified = fact("In 10th grade.", "grade", document_date="05/10/2023")
        unverified = fact("In 12th grade.", "grade", document_date="09/18/2024", verified=False)
        undated = fact("In 9th grade.", "grade")
        picture.annotate([verified, unverified, undated])
        self.assertEqual([verified["status"], undated["status"]], ["current", "earlier"])

    def test_events_and_background_keep_their_own_dates(self):
        event, background = fact("Hospitalized.", kind="Event", date="March 2022", document_date="05/10/2023"), fact("Born at 34 weeks.", kind="Background")
        untagged = fact("Likes drawing.", kind=None)
        picture.annotate([event, background, untagged])
        self.assertEqual([(event["status"], event["as_of"]), background["status"], untagged["status"]], [("event", "March 2022"), "background", ""])
        ordered = picture.in_order(picture.annotate([untagged, event, background, fact("Now", "grade", document_date="2024")]))
        self.assertEqual([f["fact"] for f in ordered], ["Now", "Born at 34 weeks.", "Hospitalized.", "Likes drawing."])


class Writing(unittest.TestCase):
    def setUp(self):
        self.real_ask, self.prompts = history.ask, []
        history.ask = self.ask

    def tearDown(self):
        history.ask = self.real_ask

    def ask(self, model, thinking, system, user, schema, max_tokens, history=()):
        self.prompts.append((system, user))
        if "tags" in schema["properties"]:
            return {"tags": [{"number": 1, "kind": "Ongoing", "topic": "grade"}, {"number": 2, "kind": "Event", "topic": "ignored"},
                             {"number": 9, "kind": "Event", "topic": ""}]}
        return {"sentences": [{"text": "Is in 11th grade, after 10th grade in 2023.", "facts": [1, 2]}],
                "conflicts": [{"description": "Records disagree on the grade.", "facts": [1, 2]}]}

    def test_the_writer_is_told_what_is_earlier_and_change_isnt_a_conflict(self):
        def doc(name, date, text, grade):
            return {"name": name, "pages": [{"page": 1, "text": text, "scanned": False}],
                    "info": {"document_type": "IEP", "document_date": date, "author": ""},
                    "facts": [{"section": "educational", "fact": f"Is in {grade} grade.", "date": "", "quote": text, "page": 1,
                               "verified": True, "kind": "Ongoing", "topic": "grade"}]}
        result = list(history.build([doc("old.pdf", "05/10/2023", "is in the 10th grade at Central", "10th"),
                                     doc("new.pdf", "09/18/2024", "is in the 11th grade at Central", "11th")], "m", False))[-1]["result"]
        school = next(s for s in result["sections"] if s["key"] == "educational")
        system, user = self.prompts[-1]
        self.assertIn("EARLIER", system)
        self.assertIn("current as of 09/18/2024) Is in 11th grade.", user)  # the newest first
        self.assertIn("EARLIER, replaced by a newer record of 09/18/2024) Is in 10th grade.", user)
        self.assertEqual(school["conflicts"], [])  # 10th then 11th grade is change over time, not a disagreement
        self.assertEqual([f["status"] for f in school["facts"]], ["current", "earlier"])

    def test_facts_read_before_tags_existed_are_tagged(self):
        tags = history.tag_facts("m", False, {"document_type": "IEP", "document_date": "2024", "facts": [
            {"id": 11, "section": "educational", "fact": "Is in 11th grade.", "date": ""},
            {"id": 12, "section": "legal", "fact": "Was arrested.", "date": "2022"},
            {"id": 13, "section": "social", "fact": "Likes drawing.", "date": ""}]})
        self.assertEqual(tags, {11: ("Ongoing", "grade"), 12: ("Event", ""), 13: ("", "")})
        self.assertIn("[2] (legal; date: 2022) Was arrested.", self.prompts[-1][1])

    def test_reading_keeps_a_topic_only_for_ongoing_facts(self):
        history.ask = lambda *a, **k: {"facts": [
            {"section": "educational", "fact": "Is in 11th grade.", "kind": "Ongoing", "topic": " grade ", "date": "", "quote": "in the 11th grade"},
            {"section": "risk", "fact": "Was hospitalized.", "kind": "Event", "topic": "hospital", "date": "2022", "quote": "hospitalized in 2022"}] * 3}
        doc = {"name": "iep.pdf", "pages": [{"page": 1, "text": "is in the 11th grade; hospitalized in 2022", "scanned": False}],
               "info": {"document_type": "IEP", "document_date": "", "author": ""}}
        read = list(history.read_document(doc, "D1", "m", False))[-1]["document_read"]
        self.assertEqual([(f["kind"], f["topic"]) for f in read["facts"][:2]], [("Ongoing", "grade"), ("Event", "")])
        self.assertIn('"kind"', history.EXTRACT_PROMPT)


class Stored(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support.unlocked_vault()

    def test_a_newer_document_makes_an_older_ones_fact_earlier_everywhere(self):
        patient = vault.create_record("patients", {"name": "Grade Change (fictional)"})
        for date, grade in (("05/10/2023", "10th"), ("09/18/2024", "11th")):
            doc = vault.add_note(patient, f"School: {grade} grade.", date)
            vault.save_document_facts(vault.internal("documents", doc), {"document_type": "Clinician Note", "document_date": date}, [
                {"section": "educational", "fact": f"Is in {grade} grade.", "kind": "Ongoing", "topic": "grade", "quote": f"{grade} grade",
                 "verified": True}], "test-model")
        facts = vault.list_records("facts", patient_uid=patient)
        self.assertIn("status", facts["columns"])
        self.assertEqual([(f["fact"], f["status"]) for f in facts["rows"]], [("Is in 11th grade.", "current"), ("Is in 10th grade.", "earlier")])
        older_doc = facts["rows"][1]["document_id"]
        self.assertEqual(vault.get_record("documents", older_doc)["related"]["facts"][0]["status"], "earlier")  # seen from its own document too
        self.assertEqual(vault.get_record("facts", facts["rows"][1]["id"])["record"]["newer"], "09/18/2024")

    def test_facts_from_before_are_tagged_in_the_background(self):
        patient = vault.create_record("patients", {"name": "Old Facts (fictional)"})
        doc = vault.add_note(patient, "In 9th grade. Arrested in 2022.", "01/02/2023")
        row = vault.internal("documents", doc)
        vault.save_document_facts(row, {"document_type": "Clinician Note", "document_date": "01/02/2023"}, [
            {"section": "educational", "fact": "In 9th grade.", "quote": "In 9th grade", "verified": True},
            {"section": "legal", "fact": "Arrested in 2022.", "quote": "Arrested in 2022", "verified": True}], "test-model")
        with vault.connect() as db:  # as they were before version 9
            db.execute("UPDATE facts SET kind = NULL, topic = NULL WHERE document_id = ?", (row,))
        real = history.tag_facts
        history.tag_facts = lambda model, thinking, batch: {f["id"]: ("Ongoing", "grade") if "grade" in f["fact"] else ("Event", "")
                                                            for f in batch["facts"]}
        try:
            for _ in range(20):  # other tests' documents may be waiting too
                if not worker.step(lambda: ("test-model", False)):
                    break
        finally:
            history.tag_facts = real
        tagged = {f["fact"]: (f["kind"], f["topic"]) for f in vault.list_records("facts", patient_uid=patient)["rows"]}
        self.assertEqual(tagged, {"In 9th grade.": ("Ongoing", "grade"), "Arrested in 2022.": ("Event", "")})
        self.assertIsNone(vault.next_facts_to_tag())


if __name__ == "__main__":
    unittest.main()
