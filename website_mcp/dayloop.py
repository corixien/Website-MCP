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


_CHECK = re.compile(r"^- \[( |x)(?:@(\d+))?(?:#([a-z0-9]{3,12}))?\] ?")


def parse_notes(notes: str | None) -> tuple[list[str], list[dict]]:
    """Dayloop notes are plain text; lines '- [ ] x' / '- [x@<ms>#id] x' are checklist items (subtasks). The id is optional."""
    text, checks = [], []
    for line in (notes or "").split("\n"):
        m = _CHECK.match(line)
        if m:
            checks.append({"titel": line[m.end():], "erledigt": m.group(1) == "x", "at": int(m.group(2)) if m.group(2) else None, "id": m.group(3)})
        else:
            text.append(line)
    return text, checks


def build_notes(text: list[str], checks: list[dict]) -> str:
    lines = list(text)
    while lines and not lines[-1].strip():
        lines.pop()
    for c in checks:
        mark = f"x@{c['at']}" if c["erledigt"] and c.get("at") else "x" if c["erledigt"] else " "
        lines.append(f"- [{mark}{'#' + c['id'] if c.get('id') else ''}] {c['titel']}")
    return "\n".join(lines)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _hm(ms: int) -> tuple[str, str]:
    t = datetime.fromtimestamp(ms / 1000, TZ)
    return t.date().isoformat(), t.strftime("%H:%M")


def task_dict(item: dict) -> dict:
    d = item["data"]
    text, checks = parse_notes(d.get("notes"))
    punkte = [{"titel": c["titel"], "erledigt": c["erledigt"], **({"punkt_id": c["id"]} if c.get("id") else {}), **({"erledigt_um": " ".join(_hm(c["at"]))} if c["at"] else {})} for c in checks]
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
        "in_todo_ab_min_vorher": d.get("showBeforeMin"),
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


def _lead(minuten: int | None) -> int | None:
    """Appointment lead time: minutes before the deadline from which the task shows in the Todo list; -1 = ordinary task."""
    if minuten is None:
        return None
    if minuten < 0:
        return None
    return min(int(minuten), 525600)


def add_task(titel: str, notizen: str | None, deadline: str | None, prioritaet: str | None, punkte: list[str] | None,
             wiederholung: str | None = None, intervall: int = 1, erinnerungen: list[int] | None = None,
             in_todo_ab: int | None = None) -> dict:
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
    if in_todo_ab is not None and in_todo_ab >= 0:
        if not day:
            raise SiteError("in_todo_ab_min_vorher braucht eine deadline.")
        data["showBeforeMin"] = _lead(in_todo_ab)
    if erinnerungen is not None:
        data["reminders"] = [{"offsetMin": max(0, int(m))} for m in erinnerungen]
    elif day and hm:
        data["reminders"] = [{"offsetMin": 0}]
    return task_dict(_request("POST", "/api/items/task", json={"data": data}))


def update_task(aufgabe_id: str, titel: str | None, notizen: str | None, deadline: str | None, prioritaet: str | None,
                punkte: list[str] | None = None, wiederholung: str | None = None, intervall: int = 1,
                erinnerungen: list[int] | None = None, in_todo_ab: int | None = None) -> dict:
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
    if in_todo_ab is not None:
        patch["showBeforeMin"] = _lead(in_todo_ab)  # -1 turns it back into an ordinary task
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
    ids = [c.get("id") for c in checks]
    idx = ids.index(punkt) if punkt in ids else int(punkt) - 1 if punkt.isdigit() else titles.index(punkt.lower()) if punkt.lower() in titles else -1
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


# ---- Plan page: time blocks of a day (made by Claude, editable by the user in the app) ----

_HM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def _today() -> str:
    return datetime.now(TZ).date().isoformat()


def _plan_date(datum: str | None) -> str:
    day = (datum or "").strip() or _today()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise SiteError("datum muss 'YYYY-MM-DD' sein.")
    return day


def _plan_time(value: str, name: str) -> str:
    v = (value or "").strip()
    if re.fullmatch(r"\d:[0-5]\d", v):
        v = "0" + v
    if not _HM.fullmatch(v):
        raise SiteError(f"{name} muss 'HH:MM' sein (00:00 bis 23:59).")
    return v


def _plan_range(start: str, ende: str | None) -> tuple[str, str]:
    s = _plan_time(start, "start")
    if ende in (None, ""):
        m = min(int(s[:2]) * 60 + int(s[3:]) + 60, 1439)
        e = f"{m // 60:02d}:{m % 60:02d}"
    else:
        e = _plan_time(ende, "ende")
    if e <= s:
        raise SiteError("ende muss nach start liegen (am selben Tag, spätestens 23:59).")
    return s, e


def _task_map() -> dict[str, dict]:
    return {i["id"]: i["data"] for i in _items("task")}


def _entry_view(entry: dict, task: dict) -> dict:
    """One todo of a plan block with its checklist points, read live from the todo (the plan only stores references)."""
    _, checks = parse_notes(task.get("notes"))
    pick = [c for c in checks if entry.get("points") is None or (c.get("id") and c["id"] in entry["points"])]
    out = {"aufgabe_id": entry["taskId"], "titel": task.get("title"), "erledigt": bool(task.get("done")),
           "punkte": [{**({"punkt_id": c["id"]} if c.get("id") else {}), "titel": c["titel"], "erledigt": c["erledigt"]} for c in pick]}
    return {k: v for k, v in out.items() if v not in (None, [], "")} | {"erledigt": out["erledigt"]}


def plan_dict(item: dict, tasks: dict[str, dict] | None = None) -> dict:
    d = item["data"]
    text, checks = parse_notes(d.get("notes"))
    entries = d.get("entries") or []
    if entries and tasks is None:
        tasks = _task_map()
    views = [_entry_view(e, tasks[e["taskId"]]) for e in entries if tasks and e["taskId"] in tasks and tasks[e["taskId"]].get("trashedAt") is None]
    out = {
        "id": item["id"], "datum": d.get("date"), "start": d.get("start"), "ende": d.get("end"), "titel": d.get("title"),
        "notizen": "\n".join(text).strip() or None,
        "punkte": [{"titel": c["titel"], "erledigt": c["erledigt"]} for c in checks],
        "eintraege": views,
    }
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


def _plan_rows(day: str | None = None) -> list[dict]:
    rows = _items("plan")
    if day:
        rows = [i for i in rows if i["data"].get("date") == day]
    return sorted(rows, key=lambda i: (i["data"].get("date", ""), i["data"].get("start", ""), i["data"].get("end", "")))


def plan(datum: str | None, tage: int) -> dict:
    """Plan blocks from `datum` (default today) for `tage` days, chronological; days without blocks are listed empty."""
    first = date.fromisoformat(_plan_date(datum))
    n = max(1, min(tage, 31))
    wanted = [(first + timedelta(days=i)).isoformat() for i in range(n)]
    by_day: dict[str, list[dict]] = {d: [] for d in wanted}
    rows = [i for i in _plan_rows() if i["data"].get("date") in by_day]
    tasks = _task_map() if any(i["data"].get("entries") for i in rows) else {}
    for i in rows:
        by_day[i["data"]["date"]].append(plan_dict(i, tasks))
    return {"tage": [{"datum": d, "bloecke": by_day[d]} for d in wanted]}


def _plan_notes(notizen: str | None, punkte: list[str] | None) -> str:
    text = (notizen or "").split("\n") if notizen else []
    return build_notes(text, [{"titel": p, "erledigt": False, "at": None} for p in punkte or []])


_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def _new_point_id(used: set[str]) -> str:
    import random
    while True:
        pid = "".join(random.choice(_ALPHABET) for _ in range(4))
        if pid not in used:
            return pid


def _link_task(task_id: str) -> tuple[dict, list[dict]]:
    """The todo and its checklist; every point gets an id (written back once), so plan blocks can address it."""
    task = _request("GET", f"/api/items/task/{task_id}")["data"]
    text, checks = parse_notes(task.get("notes"))
    if any(not c.get("id") for c in checks):
        used = {c["id"] for c in checks if c.get("id")}
        for c in checks:
            if not c.get("id"):
                c["id"] = _new_point_id(used)
                used.add(c["id"])
        task = _request("PATCH", f"/api/items/task/{task_id}", json={"data": {"notes": build_notes(text, checks)}})["data"]
    return task, checks


def _plan_entries(aufgaben: list[dict] | None) -> list[dict]:
    """[{aufgabe_id, punkte?}] -> stored entries. punkte: ids, numbers (from 1) or titles of that todo's points; omitted = all."""
    out, seen = [], set()
    for a in aufgaben or []:
        tid = str(a.get("aufgabe_id", "")).strip()
        if not tid:
            raise SiteError("aufgaben: aufgabe_id fehlt.")
        if tid in seen:
            continue
        seen.add(tid)
        task, checks = _link_task(tid)
        refs = a.get("punkte")
        if refs is None:
            out.append({"taskId": tid, "points": None})
            continue
        ids = []
        for r in refs:
            r = str(r)
            hit = next((c for c in checks if c["id"] == r), None) or (checks[int(r) - 1] if r.isdigit() and 0 < int(r) <= len(checks) else None) \
                or next((c for c in checks if c["titel"].lower() == r.lower()), None)
            if not hit:
                raise SiteError(f"Punkt '{r}' in Aufgabe {tid} nicht gefunden.")
            ids.append(hit["id"])
        out.append({"taskId": tid, "points": ids})
    return out


def add_plan(titel: str, start: str, ende: str | None, datum: str | None, notizen: str | None, punkte: list[str] | None,
             aufgaben: list[dict] | None = None) -> dict:
    if not (titel or "").strip():
        raise SiteError("titel fehlt.")
    s, e = _plan_range(start, ende)
    data = {"title": titel.strip(), "date": _plan_date(datum), "start": s, "end": e}
    notes = _plan_notes(notizen, punkte)
    if notes:
        data["notes"] = notes
    if aufgaben:
        data["entries"] = _plan_entries(aufgaben)
    return plan_dict(_request("POST", "/api/items/plan", json={"data": data}))


def update_plan(plan_id: str, titel: str | None, start: str | None, ende: str | None, datum: str | None,
                notizen: str | None, punkte: list[str] | None, aufgaben: list[dict] | None = None) -> dict:
    current = _request("GET", f"/api/items/plan/{plan_id}")["data"]
    patch: dict[str, Any] = {}
    if titel is not None:
        if not titel.strip():
            raise SiteError("titel darf nicht leer sein.")
        patch["title"] = titel.strip()
    if datum is not None:
        patch["date"] = _plan_date(datum)
    if start is not None or ende is not None:
        s = _plan_time(start, "start") if start else current["start"]
        e = _plan_time(ende, "ende") if ende else current["end"]
        if start and not ende and e <= s:  # moving the start keeps the length
            m = min(int(s[:2]) * 60 + int(s[3:]) + (int(current["end"][:2]) * 60 + int(current["end"][3:]) - int(current["start"][:2]) * 60 - int(current["start"][3:])), 1439)
            e = f"{m // 60:02d}:{m % 60:02d}"
        s, e = _plan_range(s, e)
        patch["start"], patch["end"] = s, e
    if notizen is not None or punkte is not None:
        text, checks = parse_notes(current.get("notes"))
        if notizen is not None:
            text = notizen.split("\n") if notizen else []
        if punkte is not None:  # same titles keep their state
            old = {c["titel"].lower(): c for c in checks}
            checks = [old.get(p.lower(), {"titel": p, "erledigt": False, "at": None}) for p in punkte]
        patch["notes"] = build_notes(text, checks)
    if aufgaben is not None:  # replaces the linked todos of the block
        patch["entries"] = _plan_entries(aufgaben)
    if not patch:
        raise SiteError("Nichts zu ändern angegeben.")
    return plan_dict(_request("PATCH", f"/api/items/plan/{plan_id}", json={"data": patch}))


def delete_plan(plan_id: str) -> dict:
    _request("DELETE", f"/api/items/plan/{plan_id}")
    return {"id": plan_id, "geloescht": True}


def check_plan_item(plan_id: str, punkt: str, erledigt: bool) -> dict:
    current = _request("GET", f"/api/items/plan/{plan_id}")["data"]
    text, checks = parse_notes(current.get("notes"))
    titles = [c["titel"].lower() for c in checks]
    idx = int(punkt) - 1 if punkt.isdigit() else titles.index(punkt.lower()) if punkt.lower() in titles else -1
    if not 0 <= idx < len(checks):
        raise SiteError(f"Punkt '{punkt}' nicht gefunden.")
    checks[idx]["erledigt"] = erledigt
    checks[idx]["at"] = _now_ms() if erledigt else None
    return plan_dict(_request("PATCH", f"/api/items/plan/{plan_id}", json={"data": {"notes": build_notes(text, checks)}}))


def set_plan_day(datum: str, bloecke: list[dict], ersetzen: bool) -> dict:
    """Write a whole day at once. Everything is validated first; with ersetzen the day's existing blocks are removed."""
    day = _plan_date(datum)
    prepared = []
    for b in bloecke:
        if not str(b.get("titel", "")).strip():
            raise SiteError("Jeder Block braucht einen titel.")
        s, e = _plan_range(b.get("start", ""), b.get("ende"))
        data = {"title": str(b["titel"]).strip(), "date": day, "start": s, "end": e}
        notes = _plan_notes(b.get("notizen"), b.get("punkte"))
        if notes:
            data["notes"] = notes
        if b.get("aufgaben"):
            data["entries"] = _plan_entries(b["aufgaben"])
        prepared.append(data)
    removed = 0
    if ersetzen:
        for i in _plan_rows(day):
            _request("DELETE", f"/api/items/plan/{i['id']}")
            removed += 1
    created = [plan_dict(_request("POST", "/api/items/plan", json={"data": d})) for d in prepared]
    return {"datum": day, "entfernt": removed, "angelegt": len(created), "bloecke": created}


def _open_rest(entry: dict, task: dict | None) -> dict | None:
    """What is still open of a linked todo (None = nothing left): an open todo, with a point subset only its unfinished points."""
    if not task or task.get("trashedAt") is not None or task.get("done"):
        return None
    if entry.get("points") is None:
        return entry
    _, checks = parse_notes(task.get("notes"))
    open_ids = {c["id"] for c in checks if c.get("id") and not c["erledigt"]}
    rest = [p for p in entry["points"] if p in open_ids]
    if entry["points"] and not rest:
        return None
    return {**entry, "points": rest}


def carry_over_plan(von: str | None, nach: str | None) -> dict:
    """Open work of day `von` (default yesterday) into the blocks of `nach` (default today): blocks with the same title get the
    open todos added, missing blocks are created with the same times. Todos already in the target block are not repeated."""
    to_day = _plan_date(nach)
    from_day = (date.fromisoformat(to_day) - timedelta(days=1)).isoformat() if not von else _plan_date(von)
    tasks = _task_map()
    target = {i["data"].get("title", "").strip().lower(): i for i in _plan_rows(to_day)}
    created = updated = moved = 0
    for src in _plan_rows(from_day):
        d = src["data"]
        rest = [r for r in (_open_rest(e, tasks.get(e["taskId"])) for e in d.get("entries") or []) if r]
        if not rest:
            continue
        hit = target.get(d.get("title", "").strip().lower())
        if hit is None:
            row = _request("POST", "/api/items/plan", json={"data": {"title": d["title"], "date": to_day, "start": d["start"], "end": d["end"], "entries": rest}})
            target[d["title"].strip().lower()] = row
            created += 1
            moved += len(rest)
            continue
        have = {e["taskId"] for e in hit["data"].get("entries") or []}
        add = [r for r in rest if r["taskId"] not in have]
        if add:
            row = _request("PATCH", f"/api/items/plan/{hit['id']}", json={"data": {"entries": (hit["data"].get("entries") or []) + add}})
            target[d["title"].strip().lower()] = row
            updated += 1
            moved += len(add)
    return {"von": from_day, "nach": to_day, "bloecke_angelegt": created, "bloecke_ergaenzt": updated, "aufgaben_uebertragen": moved}
