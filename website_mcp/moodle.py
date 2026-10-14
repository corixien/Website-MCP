"""Moodle (lernen.gymnasium-babelsberg.de): web login + AJAX service, read-only."""
from __future__ import annotations

import re
import time
from typing import Any

from bs4 import BeautifulSoup

from .config import secret
from .http import LoggedOut, LoginFailed, Session, SessionCache, SiteError, clean_text, new_client

BASE = "https://lernen.gymnasium-babelsberg.de"
_SESSKEY = re.compile(r'"sesskey":"([^"]+)"')


def _login() -> Session:
    client = new_client(BASE)
    page = BeautifulSoup(client.get("/login/index.php").text, "html.parser")
    data = {"username": secret("MOODLE_USER"), "password": secret("MOODLE_PASS"), "anchor": ""}
    token = page.find("input", {"name": "logintoken"})
    if token:
        data["logintoken"] = token.get("value", "")
    response = client.post("/login/index.php", data=data)
    if "/login/index.php" in str(response.url):
        raise LoginFailed("Moodle-Login fehlgeschlagen (Zugangsdaten prüfen).")
    match = _SESSKEY.search(response.text)
    if not match:
        raise LoginFailed("Moodle: sesskey nicht gefunden (Login-Ablauf geändert?).")
    return Session(client, {"sesskey": match.group(1)})


_cache = SessionCache(_login)


def _ajax(s: Session, method: str, args: dict[str, Any]) -> Any:
    response = s.client.post(
        "/lib/ajax/service.php",
        params={"sesskey": s.data["sesskey"], "info": method},
        json=[{"index": 0, "methodname": method, "args": args}],
    )
    result = response.json()[0]
    if result.get("error"):
        exc = result.get("exception", {})
        if exc.get("errorcode") in ("servicerequireslogin", "invalidsesskey"):
            raise LoggedOut("Moodle-Session abgelaufen")
        raise SiteError(f"Moodle: {exc.get('message', 'Fehler')}")
    return result["data"]


def _get(s: Session, path: str, **params: Any) -> str:
    response = s.client.get(path, params=params or None)
    if "/login/index.php" in str(response.url):
        raise LoggedOut("Moodle-Session abgelaufen")
    return response.text


def courses() -> list[dict]:
    def run(s: Session) -> list[dict]:
        data = _ajax(
            s,
            "core_course_get_enrolled_courses_by_timeline_classification",
            {"offset": 0, "limit": 0, "classification": "inprogress", "sort": "fullname",
             "customfieldname": "", "customfieldvalue": ""},
        )
        return [
            {"id": c["id"], "name": c["fullname"], "kurz": c.get("shortname"), "url": c.get("viewurl")}
            for c in data.get("courses", [])
        ]

    return _cache.run(run)


def deadlines(days: int = 14) -> list[dict]:
    def run(s: Session) -> list[dict]:
        now = int(time.time())
        data = _ajax(
            s,
            "core_calendar_get_action_events_by_timesort",
            {"limitnum": 50, "timesortfrom": now - 86400, "timesortto": now + days * 86400,
             "limittononsuspendedevents": True},
        )
        events = []
        for e in data.get("events", []):
            events.append({
                "name": e.get("name"),
                "faellig": time.strftime("%Y-%m-%d %H:%M", time.localtime(e.get("timesort", 0))),
                "kurs": (e.get("course") or {}).get("fullname"),
                "aktion": (e.get("action") or {}).get("name"),
                "url": e.get("url"),
            })
        return events

    return _cache.run(run)


def parse_course(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    sections = []
    for section in soup.select("li.section"):
        name = section.get("aria-label") or ""
        if not name:
            heading = section.select_one(".sectionname")
            name = heading.get_text(" ", strip=True) if heading else ""
        activities = []
        for act in section.select("li.activity"):
            label = act.select_one(".instancename")
            if label is None:
                continue
            for hidden in label.select(".accesshide"):
                hidden.decompose()
            link = act.select_one("a.aalink, a[href]")
            activities.append({"name": label.get_text(" ", strip=True),
                               "url": link.get("href") if link else None})
        if name or activities:
            sections.append({"abschnitt": name, "aktivitaeten": activities})
    return sections


def course_content(course_id: int) -> list[dict]:
    return _cache.run(lambda s: parse_course(_get(s, "/course/view.php", id=int(course_id))))


def page(path: str) -> str:
    if not path.startswith("/") or path.startswith("//"):
        raise SiteError("Pfad muss mit / beginnen (z. B. /my/ oder /mod/assign/view.php?id=1).")

    def run(s: Session) -> str:
        response = s.client.get(path)
        if "/login/index.php" in str(response.url):
            raise LoggedOut("Moodle-Session abgelaufen")
        soup = BeautifulSoup(response.text, "html.parser")
        return clean_text(soup.select_one("#region-main") or soup)

    return _cache.run(run)
