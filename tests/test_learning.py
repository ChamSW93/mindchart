"""Recognizing document types: built-in hints at first, then learning from the clinician's confirmations."""
import unittest

import support  # noqa: F401  (sets up paths)
import fields
import learning

TYPES = list(fields.DEFAULT_RECORD_TYPES["documents"])
NOTE = """RESIDENTIAL TREATMENT PROGRAM - PSYCHIATRIC PROGRESS NOTE
Youth: Casey Lin Date: 10/14/2024 Clinician: Sam Ortiz, PMHNP
Casey was seen for monthly medication management. Staff report two incidents of verbal aggression this month and
improved sleep. Fluoxetine 10 mg daily continues. Plan: continue fluoxetine, follow up in four weeks."""
ORDER = """FAMILY COURT OF THE STATE OF NEW YORK, COUNTY OF WESTCHESTER
In the Matter of Riley Moss, a child under eighteen years of age. Docket No. NA-12345-24
ORDER OF PLACEMENT. Upon the petition filed, it is hereby ORDERED that the child is placed in the custody of the
Commissioner of Social Services. ENTER: Hon. A. Judge, Judge of the Family Court"""
PLAN = "SAFETY PLAN\nWarning signs: {}. Coping strategies: {}. People I can call: {}. Crisis line 988. Making the environment safe: {}."


class Hints(unittest.TestCase):
    def test_wording_gives_a_first_guess_without_examples(self):
        self.assertEqual(learning.suggest("scan001.pdf", ORDER, [], TYPES)["type"], "Court Order")
        self.assertEqual(learning.suggest("scan002.pdf", NOTE, [], TYPES)["type"], "Progress or Clinical Note")

    def test_titles_only_count_near_the_top(self):
        text = "INTAKE NOTE\nClient: Casey Lin\nNo current suicidal ideation, and sleep is better than last month. Plan: psychiatric evaluation within two weeks."
        self.assertNotIn("Psychiatric Evaluation", learning.hint_scores("note.txt", text, TYPES))

    def test_nothing_recognizable(self):
        self.assertIsNone(learning.suggest("x.txt", "Shopping list: apples, bread.", [], TYPES)["type"])


class Learning(unittest.TestCase):
    def test_confirmed_examples_are_recognized_again(self):
        examples = [{"document_type": "Progress or Clinical Note", "name": "note_sept.pdf",
                     "text": NOTE.replace("Casey Lin", "Riley Moss").replace("Fluoxetine 10", "Aripiprazole 5")},
                    {"document_type": "Court Order", "name": "order.pdf", "text": ORDER}]
        guess = learning.suggest("scan_0042.pdf", NOTE, examples, TYPES, learning.name_words(["Casey Lin", "Riley Moss"]))
        self.assertEqual(guess["type"], "Progress or Clinical Note")
        self.assertIn("confirmed", guess["reason"])
        self.assertEqual(guess["similar"][0][1]["name"], "note_sept.pdf")

    def test_a_type_added_in_the_app_is_learned_from_examples(self):
        examples = [{"document_type": "Safety Plan", "name": "a.pdf",
                     "text": PLAN.format("feeling alone", "music, drawing", "cottage staff", "no sharps")},
                    {"document_type": "Safety Plan", "name": "b.pdf",
                     "text": PLAN.format("not sleeping", "walking, journaling", "aunt", "medications locked")}]
        text = PLAN.format("anger after calls", "deep breathing", "grandmother", "belts removed")
        self.assertEqual(learning.suggest("upload.pdf", text, examples, TYPES + ["Safety Plan"])["type"], "Safety Plan")

    def test_names_dont_make_documents_look_alike(self):
        exclude = learning.name_words(["Jordan Avery"])
        self.assertNotIn("jordan", learning.tokens("Jordan Avery was seen today", exclude))
        recognizer = learning.Recognizer([{"document_type": "Court Order", "name": "a.pdf", "text": "Jordan Avery Jordan Avery"}], exclude)
        self.assertEqual(recognizer.similar("b.pdf", "Jordan Avery reading scores"), [])


if __name__ == "__main__":
    unittest.main()
