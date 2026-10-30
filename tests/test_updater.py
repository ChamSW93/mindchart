"""Self-updating: version comparison, checksums, and safely swapping in a new release."""
import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

import support  # noqa: F401
import updater


def release_zip(files):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return data.getvalue()


class Updating(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        (self.folder / "VERSION").write_text("0.1.0")
        (self.folder / "server.py").write_text("old")
        (self.folder / "retired.py").write_text("no longer used")
        (self.folder / "update.json").write_text("local settings")
        (self.folder / "MANIFEST").write_text("VERSION\nserver.py\nretired.py\nMANIFEST")
        self.real_here, self.real_download = updater.HERE, updater.download
        updater.HERE = self.folder

    def tearDown(self):
        updater.HERE, updater.download = self.real_here, self.real_download

    def serve(self, data, checksum=None):
        files = {"zip": data, "sum": (checksum or hashlib.sha256(data).hexdigest() + "  MindChart.zip").encode()}
        updater.download = lambda url: files[url]
        return {"version": "0.2.0", "zip": "zip", "sha256": "sum"}

    def test_versions_compare_as_numbers(self):
        self.assertGreater(updater.as_tuple("0.10.0"), updater.as_tuple("0.9.9"))
        self.assertEqual(updater.as_tuple("v1.2.3"), (1, 2, 3))

    def test_update_replaces_app_files_only(self):
        data = release_zip({"VERSION": "0.2.0", "server.py": "new", "MANIFEST": "VERSION\nserver.py\nMANIFEST",
                            "update.json": "from the release"})
        updater.install(self.serve(data))
        self.assertEqual((self.folder / "VERSION").read_text(), "0.2.0")
        self.assertEqual((self.folder / "server.py").read_text(), "new")
        self.assertFalse((self.folder / "retired.py").exists())  # files the new version dropped are removed
        self.assertEqual((self.folder / "update.json").read_text(), "local settings")  # local files are kept

    def test_wrong_checksum_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            updater.install(self.serve(release_zip({"VERSION": "0.2.0", "server.py": "x"}), checksum="0" * 64))
        self.assertEqual((self.folder / "server.py").read_text(), "old")

    def test_strange_archives_are_refused(self):
        with self.assertRaisesRegex(RuntimeError, "doesn't look like"):
            updater.install(self.serve(release_zip({"VERSION": "0.2.0", "server.py": "x", "../outside.py": "x"})))
        with self.assertRaisesRegex(RuntimeError, "doesn't look like"):
            updater.install(self.serve(release_zip({"readme.txt": "not MindChart"})))

    def test_development_copy_never_updates(self):
        (self.folder / ".git").mkdir()
        updater.download = lambda url: self.fail("a development copy shouldn't download anything")
        updater.main()


if __name__ == "__main__":
    unittest.main()
