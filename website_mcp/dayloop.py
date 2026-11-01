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


_CHECK = re.compile(r"^- \[( |x(?:@(\d+))?)\] ?")


def parse_notes(notes: str | None) -> tuple[list[str], list[dict]]:
    """Dayloop notes are plain text; lines '- [ ] x' / '- [x@<ms>] x' are checklist items (subtasks)."""
    text, checks = [], []
    for line in (notes or "").split("\n"):
        m = _CHECK.match(line)
        if m:
            checks.append({"titel": line[m.end():], "erledigt": m.group(1)[0] == "x", "at": int(m.group(2)) if m.group(2) else None})
        else:
            text.append(line)
    return text, checks


def build_notes(text: list[str], checks: list[dict]) -> str:
    lines = list(text)
    while lines and not lines[-1].strip():
        lines.pop()
    for c in checks:
        mark = f"x@{c['at']}" if c["erledigt"] and c.get("at") else "x" if c["erledigt"] else " "
        lines.append(f"- [{mark}] {c['titel']}")
    return "\n".join(lines)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _hm(ms: int) -> tuple[str, str]:
    t = datetime.fromtimestamp(ms / 1000, TZ)
    return t.date().isoformat(), t.strftime("%H:%M")


def task_dict(item: dict) -> dict:
    d = item["data"]
    text, checks = parse_notes(d.get("notes"))
    punkte = [{"titel": c["titel"], "erledigt": c["erledigt"], **({"erledigt_um": " ".join(_hm(c["at"]))} if c["at"] else {})} for c in checks]
    punkte += [{"titel": x["title"], "erledigt": x["done"]} for x in d.get("subtasks", [])]  # older tasks
    rep = d.get("repeat")
    out = {
        "id": item["id"],
        "titel": d.get("title"),
        "notizen": "\n".join(text).strip() or None,
        "deadline": " ".join(x for x in (d.get("dueDate"), d.get("dueTime")) if x) or None,
        "prioritaet": PRIORITY_NAME.get(d.get("priority", 0)) if d.get("priority") else None,
        "punkte": punkte,
        "wiederholung": f"{rep['freq']} alle {rep.get('interval', 1)}" if rep else None,
        "erinnerungen_min_vorher": [r["offsetMin"] for r in d.get("reminders", [])] or None,
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


REPEAT = {"taeglich": "daily", "woechentlich": "weekly", "monatlich": "monthly", "jaehrlich": "yearly"}


def _repeat(wiederholung: str | None, intervall: int) -> dict | None:
    if wiederholung in (None, "", "nie", "keine"):
        return None
    if wiederholung not in REPEAT:
        raise SiteError("wiederholung: nie, taeglich, woechentlich, monatlich, jaehrlich.")
    return {"freq": REPEAT[wiederholung], "interval": max(1, min(intervall, 365))}


def add_task(titel: str, notizen: str | None, deadline: str | None, prioritaet: str | None, punkte: list[str] | None,
             wiederholung: str | None = None, intervall: int = 1, erinnerungen: list[int] | None = None) -> dict:
    day, hm = _due(deadline)
    if prioritaet and prioritaet not in PRIORITY:
        raise SiteError("prioritaet: keine, niedrig, mittel, hoch.")
    text = (notizen or "").split("\n") if notizen else []
    notes = build_notes(text, [{"titel": p, "erledigt": False, "at": None} for p in punkte or []])
    data: dict[str, Any] = {"title": titel, "dueDate": day, "dueTime": hm,
                            "priority": PRIORITY.get(prioritaet or "keine", 0), "order": _now_ms()}
    if notes:
        data["notes"] = notes
    rep = _repeat(wiederholung, intervall)
    if rep:
        data["repeat"] = rep
    if erinnerungen is not None:
        data["reminders"] = [{"offsetMin": max(0, int(m))} for m in erinnerungen]
    elif day and hm:
        data["reminders"] = [{"offsetMin": 0}]
    return task_dict(_request("POST", "/api/items/task", json={"data": data}))


def update_task(aufgabe_id: str, titel: str | None, notizen: str | None, deadline: str | None, prioritaet: str | None,
                punkte: list[str] | None = None, wiederholung: str | None = None, intervall: int = 1,
                erinnerungen: list[int] | None = None) -> dict:
    patch: dict[str, Any] = {}
    if titel:
        patch["title"] = titel
    if notizen is not None or punkte is not None:
        current = _request("GET", f"/api/items/task/{aufgabe_id}")["data"]
        text, checks = parse_notes(current.get("notes"))
        if notizen is not None:  # replaces the plain text, keeps the checklist
            text = notizen.split("\n") if notizen else []
        if punkte is not None:  # replaces the checklist, keeps the state of items with the same title
            old = {c["titel"].lower(): c for c in checks}
            checks = [old.get(p.lower(), {"titel": p, "erledigt": False, "at": None}) for p in punkte]
        patch["notes"] = build_notes(text, checks)
        if current.get("subtasks"):
            patch["subtasks"] = []
    if deadline is not None:
        day, hm = _due(deadline or None)
        patch["dueDate"], patch["dueTime"] = day, hm
    if prioritaet:
        if prioritaet not in PRIORITY:
            raise SiteError("prioritaet: keine, niedrig, mittel, hoch.")
        patch["priority"] = PRIORITY[prioritaet]
    if wiederholung is not None:
        patch["repeat"] = _repeat(wiederholung, intervall)
    if erinnerungen is not None:
        patch["reminders"] = [{"offsetMin": max(0, int(m))} for m in erinnerungen]
    if not patch:
        raise SiteError("Nichts zu ändern angegeben.")
    return task_dict(_request("PATCH", f"/api/items/task/{aufgabe_id}", json={"data": patch}))


def complete_task(aufgabe_id: str, erledigt: bool) -> dict:
    current = _request("GET", f"/api/items/task/{aufgabe_id}")
    if erledigt and current["data"].get("repeat"):
        raise SiteError("Wiederkehrende Aufgabe: bitte in der App abhaken, damit der nächste Termin gesetzt wird.")
    patch = {"done": erledigt, "completedAt": _now_ms() if erledigt else None}
    return task_dict(_request("PATCH", f"/api/items/task/{aufgabe_id}", json={"data": patch}))


def trash_task(aufgabe_id: str, aktion: str = "loeschen") -> dict:
    """aktion: loeschen (Papierkorb), wiederherstellen, endgueltig (unwiderruflich)."""
    if aktion == "endgueltig":
        _request("DELETE", f"/api/items/task/{aufgabe_id}")
        return {"id": aufgabe_id, "endgueltig_geloescht": True}
    if aktion not in ("loeschen", "wiederherstellen"):
        raise SiteError("aktion: loeschen, wiederherstellen, endgueltig.")
    _request("PATCH", f"/api/items/task/{aufgabe_id}", json={"data": {"trashedAt": _now_ms() if aktion == "loeschen" else None}})
    return {"id": aufgabe_id, "papierkorb": aktion == "loeschen"}


def _find_check(current: dict, punkt: str) -> tuple[list[str], list[dict], int]:
    text, checks = parse_notes(current.get("notes"))
    idx = int(punkt) - 1 if punkt.isdigit() else next((i for i, c in enumerate(checks) if c["titel"].lower() == punkt.lower()), -1)
    if not 0 <= idx < len(checks):
        raise SiteError(f"Punkt '{punkt}' nicht gefunden.")
    return text, checks, idx


def check_item(aufgabe_id: str, punkt: str, erledigt: bool) -> dict:
    current = _request("GET", f"/api/items/task/{aufgabe_id}")["data"]
    text, checks = parse_notes(current.get("notes"))
    subs = current.get("subtasks", [])  # older tasks still carry a separate subtask list
    titles = [c["titel"].lower() for c in checks] + [x["title"].lower() for x in subs]
    idx = int(punkt) - 1 if punkt.isdigit() else titles.index(punkt.lower()) if punkt.lower() in titles else -1
    if not 0 <= idx < len(titles):
        raise SiteError(f"Punkt '{punkt}' nicht gefunden.")
    patch: dict[str, Any] = {}
    if idx < len(checks):
        checks[idx]["erledigt"] = erledigt
        checks[idx]["at"] = _now_ms() if erledigt else None  # the calendar shows it at this time
        patch["notes"] = build_notes(text, checks)
    else:
        subs[idx - len(checks)]["done"] = erledigt
        patch["subtasks"] = subs
    return task_dict(_request("PATCH", f"/api/items/task/{aufgabe_id}", json={"data": patch}))


def add_item(aufgabe_id: str, titel: str) -> dict:
    current = _request("GET", f"/api/items/task/{aufgabe_id}")["data"]
    text, checks = parse_notes(current.get("notes"))
    checks.append({"titel": titel, "erledigt": False, "at": None})
    return task_dict(_request("PATCH", f"/api/items/task/{aufgabe_id}", json={"data": {"notes": build_notes(text, checks)}}))


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
    data: dict[str, Any] = {"title": titel, "done": True, "completedAt": _now_ms(), "order": _now_ms()}
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
    since = (today - timedelta(days=max(1, tage_zurueck) - 1)).isoformat()
    punkte: dict[str, list[dict]] = {}
    for i in _tasks():
        for c in parse_notes(i["data"].get("notes"))[1]:
            if c["erledigt"] and c["at"]:
                day, hm = _hm(c["at"])
                if day >= since:
                    punkte.setdefault(day, []).append({"aufgabe": i["data"].get("title"), "punkt": c["titel"], "uhrzeit": hm})
    out = {"heute": today.isoformat(), "deadlines": ordered, "erledigt": done_entries(max(1, tage_zurueck)),
           "erledigte_punkte": {d: sorted(v, key=lambda r: r["uhrzeit"]) for d, v in sorted(punkte.items(), reverse=True)}}
    hs = habits(max(1, tage_zurueck))
    done_habits = {d: [h["name"] for h in hs if h["letzte_tage"].get(d, 0) >= h["ziel_pro_tag"]]
                   for d in [(today - timedelta(days=n)).isoformat() for n in range(max(1, tage_zurueck))]}
    out["gewohnheiten_erledigt"] = {d: v for d, v in done_habits.items() if v}
    return out


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


HABIT_COLORS = ("gray", "red", "orange", "yellow", "green", "teal", "blue", "indigo", "purple", "pink")


def add_habit(name: str, ziel_pro_tag: int, einheit: str | None, tage: list[int] | None, farbe: str | None, erinnerung: str | None) -> dict:
    data: dict[str, Any] = {"name": name, "target": max(1, min(ziel_pro_tag, 100)), "unit": einheit or "",
                            "days": tage if tage is not None else [0, 1, 2, 3, 4, 5, 6], "startDate": date.today().isoformat(), "order": _now_ms()}
    if farbe:
        data["color"] = farbe
    if erinnerung:
        data["reminderTime"] = erinnerung
    r = _request("POST", "/api/items/habit", json={"data": data})
    return {"id": r["id"], "name": name}


def update_habit(gewohnheit_id: str, name: str | None, ziel_pro_tag: int | None, einheit: str | None, tage: list[int] | None,
                 farbe: str | None, erinnerung: str | None, archiviert: bool | None) -> dict:
    patch: dict[str, Any] = {}
    for key, value in (("name", name), ("target", ziel_pro_tag), ("unit", einheit), ("days", tage), ("color", farbe),
                       ("reminderTime", erinnerung or None if erinnerung is not None else None), ("archived", archiviert)):
        if value is not None:
            patch[key] = value
    if erinnerung == "":
        patch["reminderTime"] = None
    if not patch:
        raise SiteError("Nichts zu ändern angegeben.")
    _request("PATCH", f"/api/items/habit/{gewohnheit_id}", json={"data": patch})
    return {"id": gewohnheit_id, "geaendert": sorted(patch)}


def delete_habit(gewohnheit_id: str) -> dict:
    _request("DELETE", f"/api/items/habit/{gewohnheit_id}")
    return {"id": gewohnheit_id, "geloescht": True}


def focus(tage: int) -> dict:
    """Pomodoro/focus sessions of the last `tage` days: minutes and sessions per day and per linked todo."""
    since = (datetime.now(TZ).date() - timedelta(days=max(1, min(tage, 90)) - 1)).isoformat()
    titles = {i["id"]: i["data"].get("title") for i in _tasks(include_trash=True)}
    per_day: dict[str, dict] = {}
    per_task: dict[str, int] = {}
    for i in _items("pomodoro"):
        d = i["data"]
        if d.get("kind") != "work" or not d.get("completed"):
            continue
        day, _ = _hm(d["startedAt"])
        if day < since:
            continue
        e = per_day.setdefault(day, {"sessions": 0, "minuten": 0})
        e["sessions"] += 1
        e["minuten"] += round(d.get("actualSec", 0) / 60)
        if d.get("taskId"):
            name = titles.get(d["taskId"]) or d["taskId"]
            per_task[name] = per_task.get(name, 0) + 1
    return {"pro_tag": dict(sorted(per_day.items(), reverse=True)), "pro_aufgabe_sessions": per_task}
