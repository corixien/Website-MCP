"""Dayloop (own tasks/habits app) via its REST API with a personal API token (dl_...). Reads and writes the live data."""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

import httpx

from .config import secret
from .http import UA, LoginFailed, SiteError

TZ = ZoneInfo("Europe/Berlin")
PRIORITY = {"keine": 0, "niedrig": 1, "mittel": 2, "hoch": 3}
PRIORITY_NAME = {value: name for name, value in PRIORITY.items()}
_transport: httpx.BaseTransport | None = None  # tests inject a mock transport


def _request(method: str, path: str, **kwargs: Any) -> Any:
    base = secret("DAYLOOP_URL").rstrip("/")
    token = secret("DAYLOOP_TOKEN")
    try:
        with httpx.Client(base_url=base, timeout=45.0, transport=_transport,  # Render free tier wakes up slowly
                          headers={"Authorization": f"Bearer {token}", "User-Agent": UA}) as client:
            response = client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise SiteError(f"Dayloop nicht erreichbar: {exc.__class__.__name__}") from exc
    if response.status_code == 401:
        raise LoginFailed("Dayloop-Token ungültig oder widerrufen (in den Dayloop-Einstellungen neu erzeugen).")
    if response.status_code == 404:
        raise SiteError("Dayloop: nicht gefunden.")
    if response.status_code >= 400:
        raise SiteError(f"Dayloop: HTTP {response.status_code} {response.text[:200]}")
    return response.json() if response.content else None


def _items(kind: str) -> list[dict]:
    return _request("GET", "/api/items", params={"type": kind}) or []


def _due(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?", value.strip())
    if not m:
        raise SiteError("faellig muss 'YYYY-MM-DD' oder 'YYYY-MM-DD HH:MM' sein.")
    return m.group(1), m.group(2)


def _hm(ms: int) -> tuple[str, str]:
    t = datetime.fromtimestamp(ms / 1000, TZ)
    return t.date().isoformat(), t.strftime("%H:%M")


def task_dict(item: dict) -> dict:
    d = item["data"]
    out = {
        "id": item["id"],
        "titel": d.get("title"),
        "notizen": d.get("notes"),
        "deadline": " ".join(x for x in (d.get("dueDate"), d.get("dueTime")) if x) or None,
        "prioritaet": PRIORITY_NAME.get(d.get("priority", 0)) if d.get("priority") else None,
        "punkte": [{"titel": s["title"], "erledigt": s["done"]} for s in d.get("subtasks", [])],
        "wiederholung": d.get("repeat", {}).get("freq") if d.get("repeat") else None,
        "erledigt": d.get("done"),
    }
    if d.get("done") and d.get("completedAt"):
        out["erledigt_am"], out["erledigt_um"] = _hm(d["completedAt"])
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


def _tasks(include_trash: bool = False) -> list[dict]:
    return [i for i in _items("task") if include_trash or i["data"].get("trashedAt") is None]


def tasks(nur_offen: bool, limit: int) -> dict:
    rows = _tasks()
    if nur_offen:
        rows = [i for i in rows if not i["data"].get("done")]
    rows.sort(key=lambda i: (i["data"].get("dueDate") or "9999", i["data"].get("dueTime") or "", -i["data"].get("priority", 0)))
    return {"anzahl": len(rows), "aufgaben": [task_dict(i) for i in rows[: max(1, min(limit, 200))]]}


def add_task(titel: str, notizen: str | None, deadline: str | None, prioritaet: str | None, punkte: list[str] | None) -> dict:
    day, hm = _due(deadline)
    if prioritaet and prioritaet not in PRIORITY:
        raise SiteError("prioritaet: keine, niedrig, mittel, hoch.")
    data: dict[str, Any] = {"title": titel, "dueDate": day, "dueTime": hm,
                            "priority": PRIORITY.get(prioritaet or "keine", 0), "order": int(time.time() * 1000)}
    if notizen:
        data["notes"] = notizen
    if punkte:
        data["subtasks"] = [{"id": f"mcp{i}-{int(time.time() * 1000)}", "title": p, "done": False} for i, p in enumerate(punkte)]
    if day and hm:
        data["reminders"] = [{"offsetMin": 0}]
    return task_dict(_request("POST", "/api/items/task", json={"data": data}))


def update_task(aufgabe_id: str, titel: str | None, notizen: str | None, deadline: str | None, prioritaet: str | None) -> dict:
    patch: dict[str, Any] = {}
    if titel:
        patch["title"] = titel
    if notizen is not None:
        patch["notes"] = notizen
    if deadline is not None:
        day, hm = _due(deadline or None)
        patch["dueDate"], patch["dueTime"] = day, hm
    if prioritaet:
        if prioritaet not in PRIORITY:
            raise SiteError("prioritaet: keine, niedrig, mittel, hoch.")
        patch["priority"] = PRIORITY[prioritaet]
    if not patch:
        raise SiteError("Nichts zu ändern angegeben.")
    return task_dict(_request("PATCH", f"/api/items/task/{aufgabe_id}", json={"data": patch}))


def complete_task(aufgabe_id: str, erledigt: bool) -> dict:
    current = _request("GET", f"/api/items/task/{aufgabe_id}")
    if erledigt and current["data"].get("repeat"):
        raise SiteError("Wiederkehrende Aufgabe: bitte in der App abhaken, damit der nächste Termin gesetzt wird.")
    patch = {"done": erledigt, "completedAt": int(time.time() * 1000) if erledigt else None}
    return task_dict(_request("PATCH", f"/api/items/task/{aufgabe_id}", json={"data": patch}))


def trash_task(aufgabe_id: str) -> dict:
    _request("PATCH", f"/api/items/task/{aufgabe_id}", json={"data": {"trashedAt": int(time.time() * 1000)}})
    return {"id": aufgabe_id, "papierkorb": True}


def check_item(aufgabe_id: str, punkt: str, erledigt: bool) -> dict:
    current = _request("GET", f"/api/items/task/{aufgabe_id}")["data"]
    subs = current.get("subtasks", [])
    idx = int(punkt) - 1 if punkt.isdigit() else next((i for i, s in enumerate(subs) if s["title"].lower() == punkt.lower()), -1)
    if not 0 <= idx < len(subs):
        raise SiteError(f"Teilaufgabe '{punkt}' nicht gefunden.")
    subs[idx]["done"] = erledigt
    return task_dict(_request("PATCH", f"/api/items/task/{aufgabe_id}", json={"data": {"subtasks": subs}}))


def done_entries(tage: int) -> dict:
    """Have Done list: finished things of the last `tage` days, newest day first."""
    since = (datetime.now(TZ).date() - timedelta(days=max(1, min(tage, 90)) - 1)).isoformat()
    by_day: dict[str, list[dict]] = {}
    for i in _tasks():
        d = i["data"]
        if not d.get("done"):
            continue
        day, hm = _hm(d.get("completedAt") or 0)
        if day >= since:
            by_day.setdefault(day, []).append({"id": i["id"], "titel": d.get("title"), "notizen": d.get("notes") or None, "uhrzeit": hm})
    return {day: sorted(rows, key=lambda r: r["uhrzeit"], reverse=True) for day, rows in sorted(by_day.items(), reverse=True)}


def add_done(titel: str, notizen: str | None) -> dict:
    data: dict[str, Any] = {"title": titel, "done": True, "completedAt": int(time.time() * 1000), "order": int(time.time() * 1000)}
    if notizen:
        data["notes"] = notizen
    return task_dict(_request("POST", "/api/items/task", json={"data": data}))


def calendar(tage_voraus: int, tage_zurueck: int) -> dict:
    """What the Dayloop calendar shows: open deadlines ahead (overdue ones first) and recent completions."""
    today = datetime.now(TZ).date()
    end = (today + timedelta(days=max(0, min(tage_voraus, 120)))).isoformat()
    deadlines: dict[str, list[dict]] = {}
    for i in sorted(_tasks(), key=lambda i: (i["data"].get("dueDate") or "", i["data"].get("dueTime") or "")):
        d = i["data"]
        if d.get("done") or not d.get("dueDate") or d["dueDate"] > end:
            continue
        key = d["dueDate"] if d["dueDate"] >= today.isoformat() else "ueberfaellig"
        deadlines.setdefault(key, []).append({"id": i["id"], "titel": d.get("title"), "uhrzeit": d.get("dueTime"),
                                              "prioritaet": PRIORITY_NAME.get(d.get("priority", 0)) if d.get("priority") else None})
    ordered = {k: deadlines[k] for k in sorted(deadlines, key=lambda k: "" if k == "ueberfaellig" else k)}
    return {"heute": today.isoformat(), "deadlines": ordered, "erledigt": done_entries(max(1, tage_zurueck))}


def habits(tage: int = 7) -> list[dict]:
    today = date.today()
    logs: dict[str, dict[str, int]] = {}
    for i in _items("habitlog"):
        logs.setdefault(i["data"]["habitId"], {})[i["data"]["date"]] = i["data"]["count"]
    out = []
    for h in _items("habit"):
        d = h["data"]
        if d.get("archived"):
            continue
        counts = logs.get(h["id"], {})
        recent = {k: v for k, v in sorted(counts.items()) if (today - date.fromisoformat(k)).days < tage}
        out.append({"id": h["id"], "name": d["name"], "ziel_pro_tag": d.get("target", 1), "einheit": d.get("unit") or None,
                    "heute": counts.get(today.isoformat(), 0), "letzte_tage": recent})
    return out


def log_habit(gewohnheit_id: str, datum: str | None, anzahl: int) -> dict:
    day = datum or date.today().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise SiteError("datum muss 'YYYY-MM-DD' sein.")
    _request("GET", f"/api/items/habit/{gewohnheit_id}")
    log_id = f"{gewohnheit_id}_{day}"
    _request("POST", "/api/items/habitlog", json={"id": log_id, "data": {"habitId": gewohnheit_id, "date": day, "count": max(0, anzahl)}})
    return {"gewohnheit_id": gewohnheit_id, "datum": day, "anzahl": max(0, anzahl)}
