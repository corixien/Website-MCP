import json
import os
import unittest

import httpx

from website_mcp import ticktick
from website_mcp.http import LoginFailed, SiteError

TASK = {"id": "t1", "projectId": "p1", "title": "Referat", "status": 0, "priority": 5,
        "dueDate": "2026-10-20T00:00:00+0200", "isAllDay": True,
        "items": [{"title": "Quellen suchen", "status": 0}, {"title": "Gliederung", "status": 1}]}


class TickTickTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        os.environ["TICKTICK_ACCESS_TOKEN"] = "test-token"
        ticktick._transport = httpx.MockTransport(self.handler)

    def tearDown(self):
        ticktick._transport = None
        os.environ.pop("TICKTICK_ACCESS_TOKEN", None)

    def handler(self, request):
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body, request.headers.get("authorization")))
        path = request.url.path
        if path.endswith("/open/v1/project"):
            return httpx.Response(200, json=[{"id": "p1", "name": "Schule"}, {"id": "p2", "name": "Alt", "closed": True}])
        if path.endswith("/project/p1/data"):
            done = dict(TASK, id="t2", status=2)
            return httpx.Response(200, json={"tasks": [TASK, done]})
        if path.endswith("/project/p1/task/t1") and request.method == "GET":
            return httpx.Response(200, json=json.loads(json.dumps(TASK)))
        if path.endswith("/complete"):
            return httpx.Response(200)
        if path.endswith("/task") or path.endswith("/task/t1"):
            return httpx.Response(200, json=dict(body, id="t1", projectId=body.get("projectId", "inbox1")))
        return httpx.Response(404)

    def test_tasks_skip_closed_projects_and_done_tasks(self):
        result = ticktick.tasks()
        self.assertEqual([t["titel"] for t in result["aufgaben"]], ["Referat"])
        task = result["aufgaben"][0]
        self.assertEqual((task["projekt"], task["prioritaet"]), ("Schule", "hoch"))
        self.assertEqual(task["punkte"], [{"titel": "Quellen suchen", "erledigt": False},
                                          {"titel": "Gliederung", "erledigt": True}])
        self.assertEqual(self.calls[0][3], "Bearer test-token")

    def test_add_task_body(self):
        ticktick.add_task("Mathe", projekt_id="p1", faellig="2026-10-20", prioritaet="hoch", punkte=["a", "b"])
        _, path, body, _ = self.calls[-1]
        self.assertEqual(path, "/open/v1/task")
        self.assertEqual((body["title"], body["projectId"], body["priority"], body["isAllDay"]), ("Mathe", "p1", 5, True))
        self.assertEqual(body["dueDate"], "2026-10-20T00:00:00+0200")
        self.assertEqual([i["title"] for i in body["items"]], ["a", "b"])

    def test_add_task_with_time_and_inbox(self):
        ticktick.add_task("Arzt", faellig="2026-10-20 14:30")
        body = self.calls[-1][2]
        self.assertNotIn("projectId", body)
        self.assertEqual((body["dueDate"], body["isAllDay"]), ("2026-10-20T14:30:00+0200", False))

    def test_invalid_input(self):
        for kwargs in ({"titel": " "}, {"titel": "x", "faellig": "morgen"}, {"titel": "x", "prioritaet": "dringend"}):
            with self.assertRaises(SiteError, msg=kwargs):
                ticktick.add_task(**kwargs)

    def test_complete_task(self):
        ticktick.complete_task("p1", "t1")
        self.assertEqual(self.calls[-1][:2], ("POST", "/open/v1/project/p1/task/t1/complete"))

    def test_check_item_by_title_and_number(self):
        ticktick.check_item("p1", "t1", "quellen")
        sent = self.calls[-1][2]
        self.assertEqual([i["status"] for i in sent["items"]], [1, 1])
        ticktick.check_item("p1", "t1", 2, erledigt=False)
        self.assertEqual([i["status"] for i in self.calls[-1][2]["items"]], [0, 0])
        for bad in ("gibt es nicht", 9):
            with self.assertRaises(SiteError):
                ticktick.check_item("p1", "t1", bad)

    def test_add_item(self):
        ticktick.add_item("p1", "t1", "Fazit")
        titles = [i["title"] for i in self.calls[-1][2]["items"]]
        self.assertEqual(titles, ["Quellen suchen", "Gliederung", "Fazit"])

    def test_unauthorized_token(self):
        ticktick._transport = httpx.MockTransport(lambda r: httpx.Response(401))
        with self.assertRaises(LoginFailed):
            ticktick.projects()

    def test_missing_secret(self):
        os.environ.pop("TICKTICK_ACCESS_TOKEN")
        with self.assertRaises(SiteError):
            ticktick.projects()


if __name__ == "__main__":
    unittest.main()
