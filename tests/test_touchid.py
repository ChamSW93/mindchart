"""Touch ID: sealing the password, opening it again, and what happens when it's cancelled or fingerprints change.

A stand-in helper takes the place of the Secure Enclave here (nobody can touch the sensor during tests), except in
the last test, which builds the real helper and checks it runs.
"""
import subprocess
import unittest

import support
import touchid


class TouchID(unittest.TestCase):
    def tearDown(self):
        self.undo()

    def test_sealed_password_comes_back(self):
        self.undo = support.fake_touch_id()
        self.assertFalse(touchid.is_on())
        touchid.turn_on("a-password-123")
        self.assertTrue(touchid.is_on())
        self.assertEqual(touchid.get_password(), "a-password-123")
        touchid.turn_off()
        self.assertFalse(touchid.is_on())

    def test_cancelling_keeps_touch_id_on(self):
        self.undo = support.fake_touch_id(exit_code=touchid.CANCELLED)
        touchid.turn_on("a-password-123")
        with self.assertRaises(touchid.Cancelled):
            touchid.get_password()
        self.assertTrue(touchid.is_on())

    def test_changed_fingerprints_turn_it_off(self):
        self.undo = support.fake_touch_id(exit_code=touchid.KEY_CHANGED)
        touchid.turn_on("a-password-123")
        with self.assertRaisesRegex(ValueError, "fingerprints changed"):
            touchid.get_password()
        self.assertFalse(touchid.is_on())  # so MindChart asks for the password from now on

    def test_real_helper_builds_and_runs(self):
        self.undo = lambda: None
        touchid.helper()
        touchid.HELPER.chmod(0o644)  # as an older updater leaves it
        helper = touchid.helper()
        self.assertIn(subprocess.run([helper, "available"], capture_output=True, text=True).stdout.strip(), ("yes", "no"))
        self.assertEqual(subprocess.run([helper, "unlock", str(support.DATA / "missing.json")], capture_output=True).returncode, 1)
