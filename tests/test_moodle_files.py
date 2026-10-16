import unittest
from pathlib import Path

import httpx

from website_mcp import moodle
from website_mcp.http import Session, SiteError

FIX = Path(__file__).parent / "fixtures"
FOLDER_HTML = (
    '<div class="foldertree"><a href="https://lernen.gymnasium-babelsberg.de/pluginfile.php/9/mod_folder/content/0/A%20B.docx?forcedownload=1">A B.docx</a>'
    '<a href="https://evil.example/pluginfile.php/x/y/z/q.pdf">fremd</a></div>'
)


def handler(request: httpx.Request) -> httpx.Response:
    path, query = request.url.path, request.url.query.decode()
    if path == "/mod/resource/view.php" and query == "id=1":
        return httpx.Response(303, headers={"location": "/pluginfile.php/5/mod_resource/content/1/Folie.pptx"})
    if path == "/mod/resource/view.php" and query == "id=2":
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"},
                              content=b'<html><object data="/pluginfile.php/5/mod_resource/content/1/Blatt.pdf"></object></html>')
    if path == "/mod/resource/view.php" and query == "id=3":
        return httpx.Response(302, headers={"location": "https://evil.example/steal"})
    if path == "/mod/resource/view.php" and query == "id=4":
        return httpx.Response(302, headers={"location": "/login/index.php"})
    if path.endswith("Folie.pptx"):
        return httpx.Response(200, content=(FIX / "sample.pptx").read_bytes(),
                              headers={"content-type": "application/octet-stream"})
    if path.endswith("Blatt.pdf"):
        return httpx.Response(200, content=(FIX / "sample.pdf").read_bytes(),
                              headers={"content-type": "application/pdf",
                                       "content-disposition": "inline; filename*=UTF-8''Arbeitsblatt%20%C3%9Cbung.pdf"})
    if path == "/mod/folder/view.php":
        return httpx.Response(200, text=FOLDER_HTML)
    return httpx.Response(404)


class FakeCache:
    def __init__(self):
        self.session = Session(httpx.Client(base_url=moodle.BASE, transport=httpx.MockTransport(handler)))

    def run(self, fn):
        return fn(self.session)


class MoodleFilesTest(unittest.TestCase):
    def setUp(self):
        self.original = moodle._cache
        moodle._cache = FakeCache()

    def tearDown(self):
        moodle._cache = self.original

    def test_resource_redirect_to_pptx(self):
        result = moodle.read_file("/mod/resource/view.php?id=1")
        self.assertEqual((result["datei"], result["typ"]), ("Folie.pptx", "pptx"))
        self.assertIn("Regeln für gute Algorithmen", result["text"])

    def test_embedded_pdf_page(self):
        result = moodle.read_file("https://lernen.gymnasium-babelsberg.de/mod/resource/view.php?id=2")
        self.assertEqual((result["datei"], result["typ"]), ("Arbeitsblatt Übung.pdf", "pdf"))
        self.assertIn("EVA-Prinzip", result["text"])

    def test_paging(self):
        first = moodle.read_file("/mod/resource/view.php?id=1", max_zeichen=20)
        self.assertEqual(len(first["text"]), 20)
        self.assertEqual(first["weiter_ab"], 20)
        rest = moodle.read_file("/mod/resource/view.php?id=1", max_zeichen=5000, ab=first["weiter_ab"])
        self.assertIsNone(rest["weiter_ab"])
        self.assertTrue(rest["text"])

    def test_blocks_foreign_hosts_and_paths(self):
        for target in ("/mod/resource/view.php?id=3", "https://evil.example/a.pdf", "//evil.example/a", "relativ"):
            with self.assertRaises(SiteError, msg=target):
                moodle.read_file(target)

    def test_session_expiry_detected(self):
        from website_mcp.http import LoggedOut
        with self.assertRaises(LoggedOut):
            moodle._fetch_file(moodle._cache.session, "/mod/resource/view.php?id=4")

    def test_probe_without_body(self):
        info = moodle._fetch_file(moodle._cache.session, "/mod/resource/view.php?id=2", body=False)
        self.assertEqual(info["filename"], "Arbeitsblatt Übung.pdf")
        self.assertIsNone(info["data"])

    def test_folder_files_only_from_moodle_host(self):
        files = moodle._activity_files(moodle._cache.session, {"name": "Ordner", "typ": "folder",
                                                               "url": "https://lernen.gymnasium-babelsberg.de/mod/folder/view.php?id=7"})
        self.assertEqual([f["name"] for f in files], ["A B.docx"])
        self.assertEqual(files[0]["bereich"], "mod_folder/content")

    def test_parse_course_collects_inline_files_and_labels(self):
        html = (
            '<li class="section" aria-label="Woche 2"><div class="summarytext"><p>Plan</p></div>'
            '<ul><li class="activity modtype_label"><div><p>Bitte lesen: '
            '<a href="/pluginfile.php/1/course/section/2/Info.pdf">Info</a></p></div></li>'
            '<li class="activity modtype_resource"><a class="aalink" href="/mod/resource/view.php?id=1">'
            '<span class="instancename">Folien</span></a></li></ul></li>'
        )
        acts = moodle.parse_course(html)[0]["aktivitaeten"]
        self.assertEqual(acts[0]["name"], "Abschnittsbeschreibung")
        label = next(a for a in acts if a["typ"] == "label")
        self.assertEqual(label["dateien"][0]["name"], "Info.pdf")
        self.assertIn("Bitte lesen", label["text"])
        self.assertEqual(acts[-1]["typ"], "resource")


if __name__ == "__main__":
    unittest.main()
