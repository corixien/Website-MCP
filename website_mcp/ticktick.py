"""TickTick tasks via the official Open API (OAuth access token). Can add tasks and tick items off."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .config import secret
from .http import UA, LoginFailed, SiteError

BASE = "https://api.ticktick.com/open/v1"
TZ_NAME = "Europe/Berlin"
TZ = ZoneInfo(TZ_NAME)
PRIORITY = {"keine": 0, "niedrig": 1, "mittel": 3, "hoch": 5}
PRIORITY_NAME = {value: name for name, value in PRIORITY.items()}
_transport: httpx.BaseTransport | None = None  # tests inject a mock transport


def _request(method: str, path: str, **kwargs: Any) -> Any:
    token = secret("TICKTICK_ACCESS_TOKEN")
    try:
        with httpx.Client(base_url=BASE, timeout=20.0, transport=_transport,
                          headers={"Authorization": f"Bearer {token}", "User-Agent": UA}) as client:
            response = client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise SiteError(f"TickTick nicht erreichbar: {exc.__class__.__name__}") from exc
    if response.status_code == 401:
        raise LoginFailed("TickTick-Token ungültig oder abgelaufen (tools/get_ticktick_token.py erneut ausführen).")
    if response.status_code == 403:
        raise SiteError("TickTick: Zugriff verweigert (Scopes tasks:read und tasks:write nötig).")
    if response.status_code >= 400:
        raise SiteError(f"TickTick: HTTP {response.status_code} {response.text[:200]}")
    return response.json() if response.content else None


def task_dict(task: dict, project_name: str | None = None) -> dict:
    items = [{"titel": i.get("title"), "erledigt": i.get("status") == 1} for i in task.get("items") or []]
    out = {
        "id": task.get("id"),
        "projekt_id": task.get("projectId"),
        "projekt": project_name,
        "titel": task.get("title"),
        "inhalt": task.get("content") or task.get("desc"),
        "faellig": task.get("dueDate"),
        "ganztaegig": task.get("isAllDay") if task.get("dueDate") else None,
        "prioritaet": PRIORITY_NAME.get(task.get("priority", 0)) if task.get("priority") else None,
        "erledigt": task.get("status") == 2,
        "tags": task.get("tags"),
        "punkte": items,
    }
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


def projects() -> list[dict]:
    data = _request("GET", "/project") or []
    return [{"id": p.get("id"), "name": p.get("name"), "geschlossen": p.get("closed") or None} for p in data]


def tasks(projekt_id: str | None = None, nur_offen: bool = True, limit: int = 50) -> dict:
    listing = _request("GET", "/project") or []
    names = {p["id"]: p.get("name") for p in listing}
    ids = [projekt_id] if projekt_id else [p["id"] for p in listing if not p.get("closed")]
    found: list[dict] = []
    for pid in ids:
        data = _request("GET", f"/project/{pid}/data") or {}
        for task in data.get("tasks", []):
            if nur_offen and task.get("status") == 2:
                continue
            found.append(task_dict(task, names.get(pid)))
    found.sort(key=lambda t: (t.get("faellig") is None, t.get("faellig") or ""))
    shown = found[:max(1, int(limit))]
    result: dict[str, Any] = {"gesamt": len(found), "angezeigt": len(shown), "aufgaben": shown}
    if len(found) > len(shown):
        result["hinweis"] = "Liste gekürzt: projekt_id oder limit anpassen."
    return result


def _due(value: str) -> tuple[str, bool]:
    text = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            moment, all_day = datetime.fromisoformat(text).replace(tzinfo=TZ), True
        else:
            moment, all_day = datetime.fromisoformat(text.replace(" ", "T")), False
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=TZ)
    except ValueError as exc:
        raise SiteError("faellig: Format YYYY-MM-DD oder YYYY-MM-DD HH:MM.") from exc
    return moment.strftime("%Y-%m-%dT%H:%M:%S%z"), all_day


def _priority(value: str | int | None) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int) or str(value).isdigit():
        number = int(value)
        if number in PRIORITY_NAME:
            return number
    elif str(value).lower() in PRIORITY:
        return PRIORITY[str(value).lower()]
    raise SiteError("prioritaet: keine, niedrig, mittel oder hoch.")


def add_task(titel: str, projekt_id: str | None = None, inhalt: str | None = None,
             faellig: str | None = None, prioritaet: str | int | None = None,
             punkte: list[str] | None = None) -> dict:
    if not titel.strip():
        raise SiteError("titel darf nicht leer sein.")
    body: dict[str, Any] = {"title": titel.strip(), "timeZone": TZ_NAME}
    if projekt_id:
        body["projectId"] = projekt_id
    if inhalt:
        body["content"] = inhalt
    if faellig:
        body["dueDate"], body["isAllDay"] = _due(faellig)
        body["startDate"] = body["dueDate"]
    priority = _priority(prioritaet)
    if priority is not None:
        body["priority"] = priority
    if punkte:
        body["items"] = [{"title": p.strip(), "status": 0, "sortOrder": i} for i, p in enumerate(punkte) if p.strip()]
    return task_dict(_request("POST", "/task", json=body))


def complete_task(projekt_id: str, aufgabe_id: str) -> dict:
    _request("POST", f"/project/{projekt_id}/task/{aufgabe_id}/complete")
    return {"erledigt": True, "aufgabe_id": aufgabe_id}


def _find_item(items: list[dict], punkt: str | int) -> dict:
    if isinstance(punkt, int) or str(punkt).isdigit():
        index = int(punkt) - 1
        if 0 <= index < len(items):
            return items[index]
        raise SiteError(f"Punkt {punkt} existiert nicht (1 bis {len(items)}).")
    needle = str(punkt).strip().lower()
    exact = [i for i in items if (i.get("title") or "").strip().lower() == needle]
    matches = exact or [i for i in items if needle in (i.get("title") or "").lower()]
    if len(matches) != 1:
        raise SiteError("Punkt nicht eindeutig gefunden." if matches else "Punkt nicht gefunden.")
    return matches[0]


def check_item(projekt_id: str, aufgabe_id: str, punkt: str | int, erledigt: bool = True) -> dict:
    task = _request("GET", f"/project/{projekt_id}/task/{aufgabe_id}")
    item = _find_item(task.get("items") or [], punkt)
    item["status"] = 1 if erledigt else 0
    task["id"], task["projectId"] = aufgabe_id, projekt_id
    return task_dict(_request("POST", f"/task/{aufgabe_id}", json=task))


def add_item(projekt_id: str, aufgabe_id: str, punkt: str) -> dict:
    if not punkt.strip():
        raise SiteError("punkt darf nicht leer sein.")
    task = _request("GET", f"/project/{projekt_id}/task/{aufgabe_id}")
    items = task.get("items") or []
    items.append({"title": punkt.strip(), "status": 0, "sortOrder": len(items)})
    task.update(items=items, id=aufgabe_id, projectId=projekt_id)
    return task_dict(_request("POST", f"/task/{aufgabe_id}", json=task))
