"""Field types: checking values and converting them when a field's type changes."""
import unittest

import support  # noqa: F401
import fields


def f(kind, options=None):
    return {"name": "x", "label": "Test", "type": kind, "options": options or []}


class Checking(unittest.TestCase):
    def test_each_type(self):
        self.assertEqual(fields.coerce(f("text"), "  Cottage 2 "), "Cottage 2")
        self.assertEqual(fields.coerce(f("number"), "1,084"), 1084)
        self.assertEqual(fields.coerce(f("number"), "84.5"), 84.5)
        self.assertIs(fields.coerce(f("checkbox"), "yes"), True)
        self.assertIs(fields.coerce(f("checkbox"), ""), False)
        self.assertEqual(fields.coerce(f("picklist", ["12:1:1", "8:1:1"]), "12:1:1"), "12:1:1")
        self.assertEqual(fields.coerce(f("multipicklist", ["Sertraline", "Guanfacine"]), "guanfacine; Sertraline"),
                         ["Guanfacine", "Sertraline"])
        self.assertEqual(fields.coerce(f("multipicklist", ["A"]), ""), [])

    def test_values_that_dont_fit_are_refused(self):
        with self.assertRaisesRegex(ValueError, "must be a number"):
            fields.coerce(f("number"), "about 80")
        with self.assertRaisesRegex(ValueError, "must be one of"):
            fields.coerce(f("picklist", ["Yes"]), "Maybe")
        with self.assertRaisesRegex(ValueError, "isn't one of the choices"):
            fields.coerce(f("multipicklist", ["A", "B"]), "A, C")


class Converting(unittest.TestCase):
    def test_text_to_picklist(self):
        self.assertEqual(fields.convert("cottage 2", f("picklist", ["Cottage 2"])), ("Cottage 2", True))
        self.assertEqual(fields.convert("the blue house", f("picklist", ["Cottage 2"])), ("", False))

    def test_text_to_multi_select_and_back(self):
        new, ok = fields.convert("Sertraline, Guanfacine", f("multipicklist", ["Sertraline", "Guanfacine"]))
        self.assertEqual((new, ok), (["Sertraline", "Guanfacine"], True))
        self.assertEqual(fields.convert(new, f("text")), ("Sertraline, Guanfacine", True))

    def test_checkbox_and_number(self):
        self.assertEqual(fields.convert(True, f("text")), ("Yes", True))
        self.assertEqual(fields.convert("84", f("number")), (84, True))


class DatesAndScores(unittest.TestCase):
    def test_partial_dates_still_sort(self):
        self.assertEqual(fields.date_key("03/04/2019"), (2019, 3, 4))
        self.assertEqual(fields.date_key("March 2019"), (2019, 3, 0))
        self.assertEqual(fields.date_key("sometime in 2018"), (2018, 0, 0))
        self.assertIsNone(fields.date_key("at age 7"))

    def test_published_score_bands(self):
        self.assertEqual(fields.measure_band("PHQ-A (depression)", 12), "Moderate")
        self.assertEqual(fields.measure_band("GAD-7 (anxiety)", 4), "Minimal")
        self.assertEqual(fields.measure_band("CRAFFT (substance use)", 2), "Positive screen (2 or more)")
        self.assertEqual(fields.measure_band("Vitals", 100), "")


class Names(unittest.TestCase):
    def test_full_names_split_into_first_and_last(self):
        self.assertEqual(fields.split_name("Jordan Avery"), ("Jordan", "Avery"))
        self.assertEqual(fields.split_name("Maria Test (fictional)"), ("Maria", "Test (fictional)"))  # the note stays with it
        self.assertEqual(fields.split_name("ACS"), ("ACS", ""))
        self.assertEqual(fields.full_name("Mary Ann", " De Luca "), "Mary Ann De Luca")

    def test_gender_as_people_write_it(self):
        self.assertEqual([fields.normalize_gender(g) for g in ("boy", "F", "non-binary", "")], ["Male", "Female", "X", ""])


if __name__ == "__main__":
    unittest.main()
