import unittest

import gkeepapi

from website_mcp import keep


class KeepTest(unittest.TestCase):
    def setUp(self):
        self.client = gkeepapi.Keep()
        self.note = self.client.createNote("Plan", "x" * 400)
        self.todo = self.client.createList("Einkauf", [("Milch", False), ("Brot", True)])
        self.label = self.client.createLabel("Schule")
        self.note.labels.add(self.label)
        self.original = keep._client
        keep._client = lambda: self.client

    def tearDown(self):
        keep._client = self.original

    def test_note_preview_and_full(self):
        short = keep.note_dict(self.note)
        self.assertEqual((short["typ"], short["labels"]), ("notiz", ["Schule"]))
        self.assertTrue(short["text"].endswith("..."))
        self.assertEqual(len(keep.note_dict(self.note, full=True)["text"]), 400)

    def test_checklist(self):
        self.assertEqual(keep.note_dict(self.todo, full=True)["punkte"], ["[ ] Milch", "[x] Brot"])

    def test_list_filters(self):
        self.assertEqual(keep.list_notes()["gesamt"], 2)
        self.assertEqual([n["titel"] for n in keep.list_notes(label="Schule")["notizen"]], ["Plan"])
        self.assertEqual([n["titel"] for n in keep.list_notes(suche="Milch")["notizen"]], ["Einkauf"])
        self.assertEqual(keep.list_notes(label="gibt es nicht")["gesamt"], 0)
        self.assertEqual(keep.list_notes(archiviert=True)["gesamt"], 0)

    def test_read_note(self):
        self.assertEqual(keep.read_note(self.todo.id)["titel"], "Einkauf")
        with self.assertRaises(keep.SiteError):
            keep.read_note("unbekannt")

    def test_trashed_notes_hidden(self):
        self.todo.trash()
        self.assertEqual(keep.list_notes()["gesamt"], 1)
        with self.assertRaises(keep.SiteError):
            keep.read_note(self.todo.id)

    def test_labels(self):
        self.assertEqual(keep.labels(), ["Schule"])


if __name__ == "__main__":
    unittest.main()
