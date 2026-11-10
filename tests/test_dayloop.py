import json
import os
import time
import unittest

import httpx

from website_mcp import dayloop
from website_mcp.http import LoginFailed, SiteError

NOW = int(time.time() * 1000)
TASK = {"id": "t1", "type": "task", "data": {"title": "Referat", "priority": 3, "dueDate": "2999-11-02",
        "dueTime": "09:00", "subtasks": [{"id": "s1", "title": "Quellen", "done": False}], "done": False, "trashedAt": None}}
OVERDUE = {"id": "t3", "type": "task", "data": {"title": "Alt", "dueDate": "2000-01-01", "done": False, "trashedAt": None}}
DONE = {"id": "d1", "type": "task", "data": {"title": "Sport", "notes": "5 km", "done": True, "trashedAt": None, "completedAt": NOW}}
TRASHED = {"id": "t2", "type": "task", "data": {"title": "weg", "trashedAt": 1}}


class DayloopTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        os.environ.update(DAYLOOP_URL="https://dl.test", DAYLOOP_TOKEN="dl_secret")
        dayloop._transport = httpx.MockTransport(self.handler)

    def tearDown(self):
        dayloop._transport = None
        os.environ.pop("DAYLOOP_URL", None)
        os.environ.pop("DAYLOOP_TOKEN", None)

    def handler(self, request):
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body, request.headers.get("authorization")))
        if "bad" in str(request.headers.get("authorization")):
            return httpx.Response(401)
        path, kind = request.url.path, request.url.params.get("type")
        if path == "/api/items" and kind == "task":
            return httpx.Response(200, json=[TASK, OVERDUE, DONE, TRASHED])
        if path == "/api/items" and kind in ("habit", "habitlog"):
            return httpx.Response(200, json=[])
        if path == "/api/items/task" and request.method == "POST":
            return httpx.Response(201, json={"id": "new", "type": "task", "data": body["data"]})
        if path == "/api/items/task/t1" and request.method == "GET":
            return httpx.Response(200, json=TASK)
        if path == "/api/items/task/t1" and request.method == "PATCH":
            return httpx.Response(200, json={**TASK, "data": {**TASK["data"], **body["data"]}})
        if path == "/api/items/habit/h1":
            return httpx.Response(200, json={"id": "h1", "type": "habit", "data": {}})
        if path == "/api/items/habitlog":
            return httpx.Response(201, json={"id": body["id"]})
        return httpx.Response(404)

    def test_tasks_sorted_by_deadline_without_trash(self):
        out = dayloop.tasks(True, 50)
        self.assertEqual(out["anzahl"], 2)
        self.assertEqual([t["titel"] for t in out["aufgaben"]], ["Alt", "Referat"])
        t = out["aufgaben"][1]
        self.assertEqual((t["prioritaet"], t["deadline"]), ("hoch", "2999-11-02 09:00"))
        self.assertNotIn("liste", t)
        self.assertTrue(all(c[3] == "Bearer dl_secret" for c in self.calls))
        self.assertEqual(dayloop.tasks(False, 50)["anzahl"], 3)

    def test_add_sets_reminder_for_timed_deadline(self):
        dayloop.add_task("Neu", "Notiz", "2026-11-05 18:30", "mittel", ["a"])
        post = next(c for c in self.calls if c[0] == "POST")
        d = post[2]["data"]
        self.assertEqual((d["priority"], d["dueDate"], d["dueTime"], d["notes"]), (2, "2026-11-05", "18:30", "Notiz\n- [ ] a"))
        self.assertEqual(d["reminders"], [{"offsetMin": 0}])
        self.assertNotIn("subtasks", d)
        self.assertNotIn("listId", d)

    def test_validation_errors(self):
        with self.assertRaises(SiteError):
            dayloop.add_task("x", None, "morgen", None, None)
        with self.assertRaises(SiteError):
            dayloop.add_task("x", None, None, "extrem", None)
        with self.assertRaises(SiteError):
            dayloop.update_task("t1", None, None, None, None)

    def test_complete_and_subtask(self):
        done = dayloop.complete_task("t1", True)
        self.assertTrue(done["erledigt"])
        patch = [c for c in self.calls if c[0] == "PATCH"][-1]
        self.assertTrue(patch[2]["data"]["done"] and patch[2]["data"]["completedAt"] > 0)
        out = dayloop.check_item("t1", "1", True)
        self.assertTrue(out["punkte"][0]["erledigt"])
        with self.assertRaises(SiteError):
            dayloop.check_item("t1", "Gibtsnicht", True)

    def test_have_done_list_and_manual_entry(self):
        by_day = dayloop.done_entries(7)
        day = next(iter(by_day))
        self.assertEqual(by_day[day][0]["titel"], "Sport")
        self.assertEqual(by_day[day][0]["notizen"], "5 km")
        dayloop.add_done("Mathe gelernt", "Kapitel 4")
        post = [c for c in self.calls if c[0] == "POST"][-1]
        self.assertTrue(post[2]["data"]["done"] and post[2]["data"]["completedAt"] > 0)
        self.assertEqual(post[2]["data"]["title"], "Mathe gelernt")

    def test_calendar_groups_deadlines_and_completions(self):
        cal = dayloop.calendar(14, 7)
        self.assertIn("ueberfaellig", cal["deadlines"])
        self.assertEqual(cal["deadlines"]["ueberfaellig"][0]["titel"], "Alt")
        self.assertNotIn("2999-11-02", cal["deadlines"])  # beyond the horizon
        self.assertEqual(next(iter(cal["erledigt"].values()))[0]["titel"], "Sport")

    def test_habit_log_uses_deterministic_id(self):
        r = dayloop.log_habit("h1", "2026-10-30", 2)
        post = self.calls[-1]
        self.assertEqual((post[2]["id"], post[2]["data"]["count"], r["anzahl"]), ("h1_2026-10-30", 2, 2))

    def test_bad_token(self):
        os.environ["DAYLOOP_TOKEN"] = "bad"
        with self.assertRaises(LoginFailed):
            dayloop.tasks(True, 5)

class PlanEntriesTest(unittest.TestCase):
    """Plan blocks reference real todos and their points (ids), read live; open work can be carried to the next day."""

    def setUp(self):
        self.calls = []
        self.task = {"id": "t1", "type": "task", "data": {"title": "Referat", "done": False, "trashedAt": None,
                     "notes": "- [ ] Quellen\n- [x@5#bb22] Gliederung\n- [ #aa11] Text"}}
        self.old = {"id": "p1", "type": "plan", "data": {"title": "Schule", "date": "2999-11-08", "start": "15:00", "end": "17:00",
                    "entries": [{"taskId": "t1", "points": None}]}}
        os.environ.update(DAYLOOP_URL="https://dl.test", DAYLOOP_TOKEN="dl_secret")
        dayloop._transport = httpx.MockTransport(self.handler)

    def tearDown(self):
        dayloop._transport = None

    def handler(self, request):
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        path, kind = request.url.path, request.url.params.get("type")
        if path == "/api/items" and kind == "task":
            return httpx.Response(200, json=[self.task])
        if path == "/api/items" and kind == "plan":
            return httpx.Response(200, json=[self.old])
        if path == "/api/items/task/t1" and request.method == "GET":
            return httpx.Response(200, json=self.task)
        if path == "/api/items/task/t1" and request.method == "PATCH":
            self.task["data"].update(body["data"])
            return httpx.Response(200, json=self.task)
        if path == "/api/items/plan" and request.method == "POST":
            return httpx.Response(201, json={"id": "new", "type": "plan", "data": body["data"]})
        return httpx.Response(404)

    def test_notes_keep_point_ids(self):
        text, checks = dayloop.parse_notes(self.task["data"]["notes"])
        self.assertEqual([c.get("id") for c in checks], [None, "bb22", "aa11"])
        self.assertEqual(dayloop.build_notes(text, checks), self.task["data"]["notes"])

    def test_linking_gives_every_point_an_id_and_selects_by_title(self):
        dayloop.add_plan("Schule", "15:00", "17:00", "2999-11-09", None, None, [{"aufgabe_id": "t1", "punkte": ["Quellen", "aa11"]}])
        created = [c for c in self.calls if c[0] == "POST"][-1][2]["data"]
        ids = [c["id"] for c in dayloop.parse_notes(self.task["data"]["notes"])[1]]
        self.assertTrue(all(ids))
        self.assertEqual(created["entries"], [{"taskId": "t1", "points": [ids[0], "aa11"]}])

    def test_plan_shows_live_points_and_check_by_id(self):
        out = dayloop.plan("2999-11-08", 1)["tage"][0]["bloecke"][0]
        e = out["eintraege"][0]
        self.assertEqual((e["aufgabe_id"], e["titel"], len(e["punkte"])), ("t1", "Referat", 3))
        self.assertEqual(e["punkte"][1], {"punkt_id": "bb22", "titel": "Gliederung", "erledigt": True})
        dayloop.check_item("t1", "aa11", True)
        self.assertIn("- [x@", self.task["data"]["notes"].split("\n")[2])
        self.assertIn("#aa11] Text", self.task["data"]["notes"])

    def test_carry_over_moves_only_open_work(self):
        r = dayloop.carry_over_plan("2999-11-08", "2999-11-09")
        self.assertEqual((r["bloecke_angelegt"], r["aufgaben_uebertragen"]), (1, 1))
        body = [c for c in self.calls if c[0] == "POST"][-1][2]["data"]
        self.assertEqual((body["title"], body["date"], body["start"]), ("Schule", "2999-11-09", "15:00"))
        self.task["data"]["done"] = True
        self.calls.clear()
        self.assertEqual(dayloop.carry_over_plan("2999-11-08", "2999-11-09")["aufgaben_uebertragen"], 0)

class InfoTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        os.environ.update(DAYLOOP_URL="https://dl.test", DAYLOOP_TOKEN="dl_secret")
        dayloop._transport = httpx.MockTransport(self.handler)

    def tearDown(self):
        dayloop._transport = None

    def handler(self, request):
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        if request.url.path == "/api/items" and request.url.params.get("type") == "info":
            return httpx.Response(200, json=[{"id": "i1", "type": "info", "data": {"title": "Klausur", "date": "2999-01-05", "endDate": None}}])
        if request.url.path == "/api/items/info" and request.method == "POST":
            return httpx.Response(201, json={"id": "new", "type": "info", "data": body["data"]})
        return httpx.Response(204)

    def test_list_add_delete(self):
        self.assertEqual(dayloop.info_list("2999-01-01", 30)["eintraege"], [{"id": "i1", "titel": "Klausur", "datum": "2999-01-05"}])
        r = dayloop.add_info(" Ferien ", "2999-02-01", "2999-02-06", "x")
        self.assertEqual(self.calls[-1][2]["data"], {"title": "Ferien", "date": "2999-02-01", "endDate": "2999-02-06", "note": "x"})
        self.assertEqual(r["bis"], "2999-02-06")
        with self.assertRaises(SiteError):
            dayloop.add_info("x", "2999-02-06", "2999-02-01", None)
        self.assertTrue(dayloop.delete_info("i1")["geloescht"])


if __name__ == "__main__":
    unittest.main()


class NotesChecklistTest(unittest.TestCase):
    def test_parse_and_build_roundtrip(self):
        notes = "Intro\n- [ ] a\n- [x@1730000000000] b\n- [x] alt"
        text, checks = dayloop.parse_notes(notes)
        self.assertEqual(text, ["Intro"])
        self.assertEqual([(c["titel"], c["erledigt"], c["at"]) for c in checks], [("a", False, None), ("b", True, 1730000000000), ("alt", True, None)])
        self.assertEqual(dayloop.build_notes(text, checks), notes)

    def test_task_dict_splits_notes_and_points(self):
        d = dayloop.task_dict({"id": "x", "data": {"title": "T", "notes": "Text\n- [x@1730000000000] b", "repeat": {"freq": "weekly", "interval": 2}}})
        self.assertEqual(d["notizen"], "Text")
        self.assertEqual(d["punkte"][0]["titel"], "b")
        self.assertIn("erledigt_um", d["punkte"][0])
        self.assertEqual(d["wiederholung"], "weekly alle 2")


class ManageTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        os.environ.update(DAYLOOP_URL="https://dl.test", DAYLOOP_TOKEN="dl_secret")
        self.task = {"id": "t1", "type": "task", "data": {"title": "T", "notes": "Text\n- [ ] a\n- [ ] b", "trashedAt": None}}
        dayloop._transport = httpx.MockTransport(self.handler)

    def tearDown(self):
        dayloop._transport = None

    def handler(self, request):
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        p = request.url.path
        if p == "/api/items/task/t1" and request.method == "GET":
            return httpx.Response(200, json=self.task)
        if p == "/api/items/task/t1" and request.method == "PATCH":
            return httpx.Response(200, json={**self.task, "data": {**self.task["data"], **body["data"]}})
        if p == "/api/items/task/t1" and request.method == "DELETE":
            return httpx.Response(200, json={"ok": True})
        if p == "/api/items/habit" and request.method == "POST":
            return httpx.Response(201, json={"id": "h9", "data": body["data"]})
        if p.startswith("/api/items/habit/"):
            return httpx.Response(200, json={"ok": True})
        if p == "/api/items" and request.url.params.get("type") == "pomodoro":
            now = NOW
            return httpx.Response(200, json=[{"id": "p1", "data": {"kind": "work", "completed": True, "startedAt": now, "actualSec": 1500, "taskId": "t1"}}])
        if p == "/api/items":
            return httpx.Response(200, json=[self.task] if request.url.params.get("type") == "task" else [])
        return httpx.Response(404)

    def patch_body(self):
        return [c for c in self.calls if c[0] == "PATCH"][-1][2]["data"]

    def test_tick_by_number_and_title_stamps_time(self):
        out = dayloop.check_item("t1", "2", True)
        self.assertRegex(self.patch_body()["notes"], r"- \[x@\d+\] b")
        self.assertEqual(out["punkte"][1]["erledigt"], True)
        dayloop.check_item("t1", "A", True)
        self.assertRegex(self.patch_body()["notes"], r"- \[x@\d+\] a")
        with self.assertRaises(SiteError):
            dayloop.check_item("t1", "nix", True)

    def test_add_item_and_replace_points_keep_state(self):
        dayloop.add_item("t1", "c")
        self.assertTrue(self.patch_body()["notes"].endswith("- [ ] c"))
        self.task["data"]["notes"] = "Text\n- [x@1730000000000] a\n- [ ] b"
        dayloop.update_task("t1", None, None, None, None, punkte=["a", "neu"])
        self.assertEqual(self.patch_body()["notes"], "Text\n- [x@1730000000000] a\n- [ ] neu")
        dayloop.update_task("t1", None, "Nur Text", None, None)
        self.assertEqual(self.patch_body()["notes"], "Nur Text\n- [x@1730000000000] a\n- [ ] b")

    def test_repeat_reminders_and_trash_actions(self):
        dayloop.update_task("t1", None, None, None, None, wiederholung="woechentlich", intervall=2, erinnerungen=[10, 60])
        d = self.patch_body()
        self.assertEqual((d["repeat"], d["reminders"]), ({"freq": "weekly", "interval": 2}, [{"offsetMin": 10}, {"offsetMin": 60}]))
        dayloop.update_task("t1", None, None, None, None, wiederholung="nie")
        self.assertIsNone(self.patch_body()["repeat"])
        dayloop.trash_task("t1", "wiederherstellen")
        self.assertIsNone(self.patch_body()["trashedAt"])
        dayloop.trash_task("t1", "endgueltig")
        self.assertEqual(self.calls[-1][0], "DELETE")
        with self.assertRaises(SiteError):
            dayloop.trash_task("t1", "kaputt")

    def test_appointment_lead_time(self):
        dayloop.update_task("t1", None, None, None, None, in_todo_ab=120)
        self.assertEqual(self.patch_body()["showBeforeMin"], 120)
        dayloop.update_task("t1", None, None, None, None, in_todo_ab=-1)
        self.assertIsNone(self.patch_body()["showBeforeMin"])
        with self.assertRaises(SiteError):
            dayloop.add_task("x", None, None, None, None, in_todo_ab=30)
        self.assertEqual(dayloop.task_dict({"id": "a", "data": {"title": "x", "showBeforeMin": 60}})["in_todo_ab_min_vorher"], 60)

    def test_habit_management_and_focus(self):
        r = dayloop.add_habit("Lesen", 2, "Seiten", [1, 2], "blue", "07:30")
        post = next(c for c in self.calls if c[0] == "POST")[2]["data"]
        self.assertEqual((r["id"], post["target"], post["days"], post["reminderTime"]), ("h9", 2, [1, 2], "07:30"))
        dayloop.update_habit("h1", None, None, None, None, None, "", True)
        self.assertEqual(self.patch_body(), {"archived": True, "reminderTime": None})
        self.assertTrue(dayloop.delete_habit("h1")["geloescht"])
        f = dayloop.focus(7)
        self.assertEqual(list(f["pro_tag"].values())[0], {"sessions": 1, "minuten": 25})
        self.assertEqual(f["pro_aufgabe_sessions"], {"T": 1})


class PlanTest(unittest.TestCase):
    P1 = {"id": "p1", "type": "plan", "data": {"title": "Lernen", "date": "2999-11-09", "start": "09:00", "end": "10:30", "notes": "Kapitel 3\n- [ ] Vokabeln\n- [x] Text"}}
    P2 = {"id": "p2", "type": "plan", "data": {"title": "Sport", "date": "2999-11-09", "start": "08:00", "end": "08:45"}}
    P3 = {"id": "p3", "type": "plan", "data": {"title": "Später", "date": "2999-11-10", "start": "12:00", "end": "13:00"}}

    def setUp(self):
        self.calls = []
        os.environ.update(DAYLOOP_URL="https://dl.test", DAYLOOP_TOKEN="dl_secret")
        dayloop._transport = httpx.MockTransport(self.handler)

    def tearDown(self):
        dayloop._transport = None

    def handler(self, request):
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        path = request.url.path
        if path == "/api/items" and request.url.params.get("type") == "plan":
            return httpx.Response(200, json=[self.P1, self.P2, self.P3])
        if path == "/api/items/plan" and request.method == "POST":
            return httpx.Response(201, json={"id": "new", "type": "plan", "data": body["data"]})
        if path == "/api/items/plan/p1" and request.method == "GET":
            return httpx.Response(200, json=self.P1)
        if path == "/api/items/plan/p1" and request.method == "PATCH":
            return httpx.Response(200, json={**self.P1, "data": {**self.P1["data"], **body["data"]}})
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(404)

    def test_plan_lists_days_chronologically_with_empty_days(self):
        out = dayloop.plan("2999-11-09", 3)
        self.assertEqual([d["datum"] for d in out["tage"]], ["2999-11-09", "2999-11-10", "2999-11-11"])
        self.assertEqual([b["titel"] for b in out["tage"][0]["bloecke"]], ["Sport", "Lernen"])
        self.assertEqual(out["tage"][2]["bloecke"], [])
        lern = out["tage"][0]["bloecke"][1]
        self.assertEqual((lern["notizen"], lern["punkte"]), ("Kapitel 3", [{"titel": "Vokabeln", "erledigt": False}, {"titel": "Text", "erledigt": True}]))

    def test_add_defaults_and_validation(self):
        r = dayloop.add_plan("Mathe", "9:30", None, "2999-11-09", "Aufgaben", ["A1", "A2"])
        body = self.calls[-1][2]["data"]
        self.assertEqual((body["start"], body["end"], body["date"]), ("09:30", "10:30", "2999-11-09"))
        self.assertEqual(body["notes"], "Aufgaben\n- [ ] A1\n- [ ] A2")
        self.assertEqual(r["ende"], "10:30")
        for bad in (("x", "10:00", "09:00"), ("x", "25:00", None), ("", "09:00", "10:00")):
            with self.assertRaises(SiteError):
                dayloop.add_plan(bad[0], bad[1], bad[2], None, None, None)

    def test_update_move_keeps_length_and_checks_items(self):
        dayloop.update_plan("p1", None, "13:00", None, None, None, None)
        d = self.calls[-1][2]["data"]
        self.assertEqual((d["start"], d["end"]), ("13:00", "14:30"))
        dayloop.update_plan("p1", "Neu", None, "11:00", "2999-11-12", None, ["Text", "Neu"])
        d = self.calls[-1][2]["data"]
        self.assertEqual((d["title"], d["end"], d["date"]), ("Neu", "11:00", "2999-11-12"))
        self.assertEqual(d["notes"], "Kapitel 3\n- [x] Text\n- [ ] Neu")
        dayloop.check_plan_item("p1", "vokabeln", True)
        self.assertIn("- [x@", self.calls[-1][2]["data"]["notes"])
        with self.assertRaises(SiteError):
            dayloop.update_plan("p1", None, "11:00", "10:00", None, None, None)
        with self.assertRaises(SiteError):
            dayloop.update_plan("p1", None, None, None, None, None, None)

    def test_set_day_validates_before_writing_and_replaces(self):
        with self.assertRaises(SiteError):
            dayloop.set_plan_day("2999-11-09", [{"titel": "a", "start": "09:00"}, {"titel": "b", "start": "11:00", "ende": "10:00"}], True)
        self.assertFalse([c for c in self.calls if c[0] in ("POST", "DELETE")])
        out = dayloop.set_plan_day("2999-11-09", [{"titel": "a", "start": "09:00", "ende": "10:00", "punkte": ["x"]}], True)
        self.assertEqual((out["entfernt"], out["angelegt"]), (2, 1))
        self.assertEqual([c[0] for c in self.calls if c[0] in ("POST", "DELETE")], ["DELETE", "DELETE", "POST"])

    def test_delete(self):
        self.assertTrue(dayloop.delete_plan("p1")["geloescht"])
