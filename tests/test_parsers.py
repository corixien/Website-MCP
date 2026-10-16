import json
import os
import unittest

from website_mcp import mail, moodle, navigium, vertretungsplan
from website_mcp.http import clean_text

PLAN_HTML = """
<table class="mon_head"><tr><td><p>Vertretungsplan 2026/2027 Stand: 14.10.2026 13:37</p></td></tr></table>
<center>
<div class="mon_title">15.10.2026 Donnerstag, Woche B</div>
<table class="info"><tr class="info"><th colspan="2">Nachrichten zum Tag</th></tr>
<tr class="info"><td class="info" colspan="2">Hinweis A</td></tr></table>
<table class="mon_list"><tr class="list"><th>Art</th><th>Klasse(n)</th><th>Stunde</th></tr>
<tr class="list odd"><td class="list">Entfall</td><td class="list">11</td><td class="list">3</td></tr>
<tr class="list even"><td class="list">Vertretung</td><td class="list">7a</td><td class="list">2</td></tr>
<tr class="list odd"><td class="list">Entfall</td><td class="list">7b, 7c</td><td class="list">4</td></tr>
</table>
<div class="mon_title">16.10.2026 Freitag, Woche B</div>
</center>
"""


class VertretungsplanTest(unittest.TestCase):
    def test_parse(self):
        plan = vertretungsplan.parse(PLAN_HTML)
        self.assertEqual(plan["stand"], "14.10.2026 13:37")
        self.assertEqual(len(plan["tage"]), 2)
        day = plan["tage"][0]
        self.assertEqual(day["nachrichten"], ["Hinweis A"])
        self.assertEqual(day["eintraege"][1], {"Art": "Vertretung", "Klasse(n)": "7a", "Stunde": "2"})
        self.assertEqual(plan["tage"][1]["eintraege"], [])

    def test_class_filter(self):
        entries = vertretungsplan.parse(PLAN_HTML)["tage"][0]["eintraege"]
        self.assertEqual([e["Stunde"] for e in entries if vertretungsplan._matches(e, "7a")], ["2"])
        self.assertEqual([e["Stunde"] for e in entries if vertretungsplan._matches(e, "7c")], ["4"])
        self.assertEqual([e["Stunde"] for e in entries if vertretungsplan._matches(e, "11")], ["3"])

    @unittest.skipUnless(os.environ.get("LIVE"), "set LIVE=1 for network test")
    def test_live(self):
        plan = vertretungsplan.get_plan()
        self.assertTrue(plan["tage"])


class MailTest(unittest.TestCase):
    def test_parse_rows(self):
        row = {"subject": "<span>Hallo</span>", "fromto": "Frau X", "date": "Heute 10:00"}
        js = (
            f"this.set_rowcount('1');this.add_message_row(42,{json.dumps(row)},"
            f"{json.dumps({'seen': 0})},false);"
        )
        self.assertEqual(
            mail.parse_rows(js),
            [{"uid": "42", "von": "Frau X", "betreff": "Hallo", "datum": "Heute 10:00", "ungelesen": True}],
        )

    def test_parse_message(self):
        raw = b"From: a@b.de\r\nTo: c@d.de\r\nSubject: Test\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nHallo Welt"
        msg = mail.parse_message(raw)
        self.assertEqual((msg["betreff"], msg["text"], msg["anhaenge"]), ("Test", "Hallo Welt", []))


class MoodleTest(unittest.TestCase):
    def test_parse_course(self):
        html = (
            '<ul><li class="section" aria-label="Woche 1"><ul>'
            '<li class="activity"><a class="aalink" href="/mod/assign/view.php?id=5">'
            '<span class="instancename">Aufgabe 1<span class="accesshide"> Aufgabe</span></span></a></li>'
            "</ul></li></ul>"
        )
        self.assertEqual(
            moodle.parse_course(html),
            [{"abschnitt": "Woche 1",
              "aktivitaeten": [{"name": "Aufgabe 1", "typ": "assign", "url": "/mod/assign/view.php?id=5"}]}],
        )

    def test_page_rejects_foreign_paths(self):
        from website_mcp.http import SiteError
        with self.assertRaises(SiteError):
            moodle.page("//evil.example/")


class NavigiumTest(unittest.TestCase):
    def test_parse_lessons_dedupes(self):
        item = {"name": "prima_1-1", "eigner": "x", "cat": "default", "lang": "LA",
                "key": {"id": "92975"}, "subcat": {"id": 0, "name": "-"}}
        out = navigium.parse_lessons([item, dict(item), {"name": "ohne key"}])
        self.assertEqual(out, [{"id": 92975, "name": "prima_1-1", "eigner": "x", "gruppe": "default",
                                "unterkategorie": None, "sprache": "LA"}])

    def test_parse_vocab_drops_empty_fields(self):
        data = {"lektionId": 1, "lektionName": "L1", "vokabeln": [
            {"latein": "cogitare", "stammformen": "cogitare", "genus": "", "wortart": "VERB",
             "bedeutungen": ["denken", "nachdenken"], "phrasen": [], "merkhilfe": "EN cogitate",
             "obligatorisch": True},
            {"latein": "dominus", "stammformen": "dominus, -i", "genus": "m", "bedeutungen": ["Herr"]}]}
        out = navigium.parse_vocab(data)
        self.assertEqual(out["anzahl"], 2)
        self.assertEqual(out["vokabeln"][0], {"latein": "cogitare", "wortart": "VERB",
                                              "deutsch": ["denken", "nachdenken"],
                                              "merkhilfe": "EN cogitate", "pflicht": True})
        self.assertEqual(out["vokabeln"][1]["stammformen"], "dominus, -i")


class HttpTest(unittest.TestCase):
    def test_clean_text_truncates(self):
        out = clean_text("<p>" + "x" * 100 + "</p><script>bad()</script>", limit=10)
        self.assertTrue(out.startswith("xxxxxxxxxx"))
        self.assertNotIn("bad", out)


if __name__ == "__main__":
    unittest.main()
