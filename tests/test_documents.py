import unittest
from pathlib import Path

from website_mcp import documents
from website_mcp.http import SiteError

FIX = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    kind, text = documents.extract((FIX / name).read_bytes(), name)
    return text


class DocumentsTest(unittest.TestCase):
    def test_detect_by_content(self):
        kinds = {"sample.pdf": "pdf", "sample.docx": "docx", "sample.doc": "doc", "sample.pptx": "pptx",
                 "sample.ppt": "ppt", "sample.xlsx": "xlsx", "sample.xls": "xls", "sample.odt": "odf"}
        for name, kind in kinds.items():
            self.assertEqual(documents.detect((FIX / name).read_bytes(), "download.bin", ""), kind, name)

    def test_word(self):
        for name in ("sample.docx", "sample.doc", "sample.odt"):
            text = read(name)
            self.assertIn("EVA-Prinzip", text, name)
            self.assertIn("äöü ß", text, name)
        self.assertIn("Maus | Eingabe", read("sample.docx"))
        self.assertIn("Maus | Eingabe", read("sample.doc"))

    def test_powerpoint(self):
        for name in ("sample.pptx", "sample.ppt"):
            text = read(name)
            self.assertIn("--- Folie 1 ---", text, name)
            self.assertIn("Regeln für gute Algorithmen", text, name)
            self.assertIn("Scratch Beispiel", text, name)
            self.assertIn("Notiz zur Folie", text, name)
        self.assertLess(read("sample.pptx").index("Algorithmen"), read("sample.pptx").index("Zweite Folie"))

    def test_excel(self):
        for name in ("sample.xlsx", "sample.xls"):
            text = read(name)
            self.assertIn("--- Tabelle Noten ---", text, name)
            self.assertIn("Ben | 9.5", text, name)

    def test_pdf(self):
        text = read("sample.pdf")
        self.assertIn("--- Seite 1 ---", text)
        self.assertIn("EVA-Prinzip", text)

    def test_plain_and_unsupported(self):
        self.assertEqual(documents.extract("grüße".encode("cp1252"), "a.txt")[1], "grüße")
        self.assertIn("nicht unterstützt", documents.extract(b"\x00\x01\x02", "a.bin")[1])
        self.assertIn("Bild", documents.extract(b"\x89PNG\r\n\x1a\n", "a.png")[1])

    def test_corrupt_file_gives_clean_error(self):
        with self.assertRaises(SiteError):
            documents.extract(b"%PDF-1.4 kaputt", "x.pdf")


if __name__ == "__main__":
    unittest.main()
