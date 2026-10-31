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
        self.assertEqual((d["priority"], d["dueDate"], d["dueTime"], d["notes"]), (2, "2026-11-05", "18:30", "Notiz"))
        self.assertEqual(d["reminders"], [{"offsetMin": 0}])
        self.assertEqual(d["subtasks"][0]["title"], "a")
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


if __name__ == "__main__":
    unittest.main()
