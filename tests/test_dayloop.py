import json
import os
import unittest

import httpx

from website_mcp import dayloop
from website_mcp.http import LoginFailed, SiteError

LIST = {"id": "l1", "type": "list", "data": {"name": "Uni"}}
TASK = {"id": "t1", "type": "task", "data": {"title": "Referat", "listId": "l1", "priority": 3, "dueDate": "2026-11-02",
        "dueTime": "09:00", "tags": ["g1"], "subtasks": [{"id": "s1", "title": "Quellen", "done": False}], "done": False, "trashedAt": None}}


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
        if path == "/api/items" and kind == "list":
            return httpx.Response(200, json=[LIST])
        if path == "/api/items" and kind == "tag":
            return httpx.Response(200, json=[{"id": "g1", "type": "tag", "data": {"name": "wichtig"}}])
        if path == "/api/items" and kind == "task":
            return httpx.Response(200, json=[TASK, {"id": "t2", "type": "task", "data": {"title": "weg", "trashedAt": 1}}])
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

    def test_tasks_skip_trash_and_map_names(self):
        out = dayloop.tasks(None, True, 50)
        self.assertEqual(out["anzahl"], 1)
        t = out["aufgaben"][0]
        self.assertEqual((t["titel"], t["liste"], t["prioritaet"], t["faellig"], t.get("tags")), ("Referat", "Uni", "hoch", "2026-11-02 09:00", None))
        self.assertTrue(all(c[3] == "Bearer dl_secret" for c in self.calls))

    def test_add_resolves_list_and_sets_reminder(self):
        dayloop.add_task("Neu", "uni", None, "2026-11-05 18:30", "mittel", ["a"])
        post = next(c for c in self.calls if c[0] == "POST")
        self.assertEqual((post[2]["data"]["listId"], post[2]["data"]["priority"], post[2]["data"]["dueTime"]), ("l1", 2, "18:30"))
        self.assertEqual(post[2]["data"]["reminders"], [{"offsetMin": 0}])
        self.assertEqual(post[2]["data"]["subtasks"][0]["title"], "a")

    def test_validation_errors(self):
        with self.assertRaises(SiteError):
            dayloop.add_task("x", None, None, "morgen", None, None)
        with self.assertRaises(SiteError):
            dayloop.add_task("x", "gibtsnicht", None, None, None, None)
        with self.assertRaises(SiteError):
            dayloop.update_task("t1", None, None, None, None, None)

    def test_complete_and_subtask(self):
        done = dayloop.complete_task("t1", True)
        self.assertTrue(done["erledigt"])
        patch = [c for c in self.calls if c[0] == "PATCH"][-1]
        self.assertTrue(patch[2]["data"]["done"] and patch[2]["data"]["completedAt"] > 0)
        out = dayloop.check_item("t1", "1", True)
        self.assertTrue(out["punkte"][0]["erledigt"])
        with self.assertRaises(SiteError):
            dayloop.check_item("t1", "Gibtsnicht", True)

    def test_habit_log_uses_deterministic_id(self):
        r = dayloop.log_habit("h1", "2026-10-30", 2)
        post = self.calls[-1]
        self.assertEqual((post[2]["id"], post[2]["data"]["count"], r["anzahl"]), ("h1_2026-10-30", 2, 2))

    def test_bad_token(self):
        os.environ["DAYLOOP_TOKEN"] = "bad"
        with self.assertRaises(LoginFailed):
            dayloop.lists()


if __name__ == "__main__":
    unittest.main()
