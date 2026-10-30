"""Dayloop (own tasks/habits app) via its REST API with a personal API token (dl_...). Reads and writes the live data."""
from __future__ import annotations

import re
import time
from datetime import date
from typing import Any

import httpx

from .config import secret
from .http import UA, LoginFailed, SiteError

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


def task_dict(item: dict, lists: dict[str, str], tags: dict[str, str]) -> dict:
    d = item["data"]
    out = {
        "id": item["id"],
        "titel": d.get("title"),
        "liste": lists.get(d.get("listId")) if d.get("listId") else "Eingang",
        "notizen": d.get("notes"),
        "faellig": " ".join(x for x in (d.get("dueDate"), d.get("dueTime")) if x) or None,
        "prioritaet": PRIORITY_NAME.get(d.get("priority", 0)) if d.get("priority") else None,
                "punkte": [{"titel": s["title"], "erledigt": s["done"]} for s in d.get("subtasks", [])],
        "wiederholung": d.get("repeat", {}).get("freq") if d.get("repeat") else None,
        "erledigt": d.get("done"),
    }
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


def lists() -> list[dict]:
    return [{"id": i["id"], "name": i["data"]["name"]} for i in _items("list") if not i["data"].get("archived")]


def _lookup() -> tuple[dict[str, str], dict[str, str]]:
    return ({i["id"]: i["data"]["name"] for i in _items("list")}, {i["id"]: i["data"]["name"] for i in _items("tag")})


def _resolve_list(name_or_id: str | None) -> str | None:
    if not name_or_id or name_or_id.lower() in ("eingang", "inbox"):
        return None
    for item in _items("list"):
        if item["id"] == name_or_id or item["data"]["name"].lower() == name_or_id.lower():
            return item["id"]
    raise SiteError(f"Liste '{name_or_id}' nicht gefunden (dayloop_listen).")


def _resolve_tags(names: list[str] | None) -> list[str]:
    ids = []
    existing = {i["data"]["name"].lower(): i["id"] for i in _items("tag")}
    for name in names or []:
        if name.lower() in existing:
            ids.append(existing[name.lower()])
        else:
            created = _request("POST", "/api/items/tag", json={"data": {"name": name}})
            existing[name.lower()] = created["id"]
            ids.append(created["id"])
    return ids


def tasks(liste: str | None, nur_offen: bool, limit: int) -> dict:
    ls, tg = _lookup()
    list_id = _resolve_list(liste) if liste else None
    rows = [i for i in _items("task") if i["data"].get("trashedAt") is None]
    if liste:
        rows = [i for i in rows if i["data"].get("listId") == list_id]
    if nur_offen:
        rows = [i for i in rows if not i["data"].get("done")]
    rows.sort(key=lambda i: (i["data"].get("dueDate") or "9999", -i["data"].get("priority", 0)))
    return {"anzahl": len(rows), "aufgaben": [task_dict(i, ls, tg) for i in rows[: max(1, min(limit, 200))]]}


def add_task(titel: str, liste: str | None, notizen: str | None, faellig: str | None, prioritaet: str | None,
             punkte: list[str] | None) -> dict:
    day, hm = _due(faellig)
    if prioritaet and prioritaet not in PRIORITY:
        raise SiteError("prioritaet: keine, niedrig, mittel, hoch.")
    data: dict[str, Any] = {"title": titel, "listId": _resolve_list(liste), "dueDate": day, "dueTime": hm,
                            "priority": PRIORITY.get(prioritaet or "keine", 0),
                            "order": int(time.time() * 1000)}
    if notizen:
        data["notes"] = notizen
    if punkte:
        data["subtasks"] = [{"id": f"mcp{i}-{int(time.time() * 1000)}", "title": p, "done": False} for i, p in enumerate(punkte)]
    if day and hm:
        data["reminders"] = [{"offsetMin": 0}]
    created = _request("POST", "/api/items/task", json={"data": data})
    ls, tg = _lookup()
    return task_dict(created, ls, tg)


def update_task(aufgabe_id: str, titel: str | None, notizen: str | None, faellig: str | None,
                prioritaet: str | None, liste: str | None) -> dict:
    patch: dict[str, Any] = {}
    if titel:
        patch["title"] = titel
    if notizen is not None:
        patch["notes"] = notizen
    if faellig is not None:
        day, hm = _due(faellig or None)
        patch["dueDate"], patch["dueTime"] = day, hm
    if prioritaet:
        if prioritaet not in PRIORITY:
            raise SiteError("prioritaet: keine, niedrig, mittel, hoch.")
        patch["priority"] = PRIORITY[prioritaet]
    if liste is not None:
        patch["listId"] = _resolve_list(liste)
        patch["columnId"] = None
    if not patch:
        raise SiteError("Nichts zu ändern angegeben.")
    updated = _request("PATCH", f"/api/items/task/{aufgabe_id}", json={"data": patch})
    ls, tg = _lookup()
    return task_dict(updated, ls, tg)


def complete_task(aufgabe_id: str, erledigt: bool) -> dict:
    current = _request("GET", f"/api/items/task/{aufgabe_id}")
    if erledigt and current["data"].get("repeat"):
        raise SiteError("Wiederkehrende Aufgabe: bitte in der App abhaken, damit der nächste Termin gesetzt wird.")
    patch = {"done": erledigt, "completedAt": int(time.time() * 1000) if erledigt else None}
    updated = _request("PATCH", f"/api/items/task/{aufgabe_id}", json={"data": patch})
    ls, tg = _lookup()
    return task_dict(updated, ls, tg)


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
    updated = _request("PATCH", f"/api/items/task/{aufgabe_id}", json={"data": {"subtasks": subs}})
    ls, tg = _lookup()
    return task_dict(updated, ls, tg)


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
