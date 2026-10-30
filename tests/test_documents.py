"""Reading PDFs (including scanned ones), Word files, spreadsheets and CSVs."""
import io
import unittest
import zipfile

import support
import documents


class Reading(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support.ensure_pdf_reader()

    def read(self, name):
        return documents.read_pages(name, (support.SAMPLES / name).read_bytes())

    def test_pdf_keeps_page_numbers(self):
        pages = self.read("Psychiatric_Evaluation_2023.pdf")
        self.assertEqual([p["page"] for p in pages], [1, 2])
        self.assertIn("guanfacine", " ".join(p["text"] for p in pages))

    def test_scanned_pdf_is_read_with_ocr(self):
        pages = self.read("Court_Order_2024_scanned.pdf")
        self.assertTrue(pages[0]["scanned"])
        self.assertIn("order of protection", pages[0]["text"].lower())

    def test_word_file(self):
        pages = self.read("IEP_2024.docx")
        self.assertIsNone(pages[0]["page"])
        self.assertIn("12:1:1", pages[0]["text"])

    def test_csv_from_older_excel(self):
        text = documents.read_pages("old.csv", "Name,City\r\nJosé,Zürich\r\n".encode("cp1252"))[0]["text"]
        self.assertIn("Zürich", text)

    def test_spreadsheet(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as z:  # the smallest valid .xlsx: one sheet, two cells
            z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                       '<sheet name="Meds" r:id="rId1"/></sheets></workbook>')
            z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                       'relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                       '<sheetData><row><c r="A1" t="inlineStr"><is><t>Drug</t></is></c><c r="C1"><v>50</v></c></row>'
                       '</sheetData></worksheet>')
        self.assertEqual(documents.read_pages("meds.xlsx", data.getvalue())[0]["text"], "Sheet: Meds\nDrug,,50")

    def test_unreadable_files_are_refused_clearly(self):
        with self.assertRaisesRegex(ValueError, "isn't a file type"):
            documents.read_pages("photo.bin", bytes(range(256)))
        with self.assertRaisesRegex(ValueError, "old-style Excel"):
            documents.read_pages("old.xls", b"x")


if __name__ == "__main__":
    unittest.main()
