"""Moodle (lernen.gymnasium-babelsberg.de): web login + AJAX service, read-only."""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from . import documents
from .config import secret
from .http import LoggedOut, LoginFailed, Session, SessionCache, SiteError, clean_text, new_client

HOST = "lernen.gymnasium-babelsberg.de"
BASE = f"https://{HOST}"
MAX_BYTES = 25 * 1024 * 1024
MAX_CHARS = 60_000
_SESSKEY = re.compile(r'"sesskey":"([^"]+)"')
_USERID = re.compile(r'"userId":(\d+)')
_MODTYPE = re.compile(r"modtype_(\w+)")
_MODURL = re.compile(r"/mod/(\w+)/view\.php")
SCAN_TYPES = {"resource", "folder", "assign", "page", "book", "forum", "url"}
PAGE_TYPES = {"assign", "folder", "page", "book"}


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
    user = _USERID.search(response.text)
    return Session(client, {"sesskey": match.group(1), "userid": int(user.group(1)) if user else None})


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


def _stamp(value: int | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(value)) if value else ""


def _plain(html: str | None) -> str:
    return clean_text(html or "", 2000)


# --- courses, deadlines -----------------------------------------------------

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
        return [{
            "name": e.get("name"),
            "faellig": _stamp(e.get("timesort")),
            "kurs": (e.get("course") or {}).get("fullname"),
            "aktion": (e.get("action") or {}).get("name"),
            "url": e.get("url"),
        } for e in data.get("events", [])]

    return _cache.run(run)


# --- course structure and files ---------------------------------------------

def _file_links(root: Any, base: str = BASE) -> list[dict]:
    """All pluginfile.php links below `root` as {name, url, bereich}, de-duplicated."""
    found, seen = [], set()
    for a in root.select('a[href*="/pluginfile.php/"]'):
        url = urljoin(base, a["href"])
        path = urlparse(url).path
        if path in seen or urlparse(url).netloc != HOST:
            continue
        seen.add(path)
        parts = path.split("/pluginfile.php/")[-1].split("/")
        found.append({
            "name": unquote(path.rsplit("/", 1)[-1]),
            "url": url,
            "bereich": "/".join(parts[1:3]) if len(parts) > 2 else None,
        })
    return found


def parse_course(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    sections = []
    for section in soup.select("li.section"):
        name = section.get("aria-label") or ""
        if not name:
            heading = section.select_one(".sectionname")
            name = heading.get_text(" ", strip=True) if heading else ""
        activities = []
        summary = section.select_one(".summarytext")
        if summary is not None:
            text, files = summary.get_text(" ", strip=True), _file_links(summary)
            if text or files:
                activities.append({"name": "Abschnittsbeschreibung", "typ": "text", "url": None,
                                   "text": text[:4000], "dateien": files})
        for act in section.select("li.activity"):
            link = act.select_one("a.aalink, a[href*='/mod/']")
            url = link.get("href") if link else None
            typ = None
            match = _MODTYPE.search(" ".join(act.get("class", []))) or (_MODURL.search(url) if url else None)
            if match:
                typ = match.group(1)
            label = act.select_one(".instancename")
            files = _file_links(act)
            if label is None:
                text = act.get_text(" ", strip=True)
                if text or files:
                    activities.append({"name": text[:80], "typ": typ or "label", "url": None,
                                       "text": text[:4000], "dateien": files})
                continue
            for hidden in label.select(".accesshide"):
                hidden.decompose()
            entry = {"name": label.get_text(" ", strip=True), "typ": typ, "url": url}
            if files:
                entry["dateien"] = files
            activities.append(entry)
        if name or activities:
            sections.append({"abschnitt": name, "aktivitaeten": activities})
    return sections


def course_content(course_id: int) -> list[dict]:
    return _cache.run(lambda s: parse_course(_get(s, "/course/view.php", id=int(course_id))))


# --- downloading ------------------------------------------------------------

def _check_host(url: str) -> None:
    netloc = urlparse(url).netloc
    if netloc and netloc != HOST:
        raise SiteError(f"Externer Link, nicht von Moodle: {url}")


def _absolute(target: str) -> str:
    if target.startswith(("http://", "https://")):
        _check_host(target)
        return target
    if not target.startswith("/") or target.startswith("//"):
        raise SiteError("Pfad muss mit / beginnen oder eine Moodle-URL sein.")
    return BASE + target


def _filename(disposition: str, url: str) -> str:
    match = re.search(r"filename\*=(?:UTF-8|utf-8)''([^;]+)", disposition)
    if match:
        return unquote(match.group(1).strip())
    match = re.search(r'filename="?([^";]+)"?', disposition)
    if match:
        return match.group(1).strip()
    return unquote(urlparse(url).path.rsplit("/", 1)[-1])


def _embedded_file_url(soup: BeautifulSoup, base: str) -> str | None:
    for selector, attr in (("object[data]", "data"), ("iframe[src]", "src"), ("embed[src]", "src"),
                           ("a[href*='/pluginfile.php/']", "href"), ("img[src*='/pluginfile.php/']", "src")):
        for el in soup.select(selector):
            url = urljoin(base, el[attr])
            if "/pluginfile.php/" in url and urlparse(url).netloc == HOST:
                return url
    return None


def _fetch_file(s: Session, target: str, body: bool = True) -> dict:
    """Follow redirects manually (Moodle host only); resolves resource pages to the real file."""
    url = _absolute(target)
    html_hops = 0
    for _ in range(8):
        with s.client.stream("GET", url, follow_redirects=False) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                location = urljoin(url, response.headers.get("location", ""))
                if "/login/index.php" in location:
                    raise LoggedOut("Moodle-Session abgelaufen")
                _check_host(location)
                url = location
                continue
            if response.status_code >= 400:
                raise SiteError(f"Moodle: HTTP {response.status_code} für {urlparse(url).path}")
            ctype = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype == "text/html":
                html = b"".join(response.iter_bytes())[:3_000_000]
                embedded = None if html_hops >= 2 else _embedded_file_url(BeautifulSoup(html, "html.parser"), url)
                if embedded:
                    url, html_hops = embedded, html_hops + 1
                    continue
                return {"url": url, "filename": None, "content_type": ctype, "size": len(html),
                        "data": html if body else None}
            name = _filename(response.headers.get("content-disposition", ""), url)
            size = int(response.headers.get("content-length") or 0)
            if not body:
                return {"url": url, "filename": name, "content_type": ctype, "size": size, "data": None}
            if size > MAX_BYTES:
                raise SiteError(f"Datei zu groß ({size // 1024 // 1024} MB, Limit 25 MB).")
            buffer = bytearray()
            for chunk in response.iter_bytes():
                buffer += chunk
                if len(buffer) > MAX_BYTES:
                    raise SiteError("Datei zu groß (Limit 25 MB).")
            return {"url": url, "filename": name, "content_type": ctype, "size": len(buffer),
                    "data": bytes(buffer)}
    raise SiteError("Zu viele Weiterleitungen.")


def read_file(target: str, max_zeichen: int = 20_000, ab: int = 0) -> dict:
    """Download a Moodle file or resource page and extract its text (PDF, DOC(X), PPT(X), XLS(X), ODF, ...)."""
    max_zeichen = max(1, min(int(max_zeichen), MAX_CHARS))
    ab = max(0, int(ab))
    found = _cache.run(lambda s: _fetch_file(s, target))
    kind, text = documents.extract(found["data"], found["filename"] or "", found["content_type"])
    end = ab + max_zeichen
    return {
        "datei": found["filename"],
        "typ": kind,
        "url": found["url"],
        "zeichen_gesamt": len(text),
        "ab": ab,
        "text": text[ab:end],
        "weiter_ab": end if end < len(text) else None,
    }


def _resolve_link(s: Session, url: str) -> str | None:
    response = s.client.get(url, follow_redirects=False)
    if response.status_code in (301, 302, 303, 307, 308):
        return urljoin(url, response.headers.get("location", ""))
    soup = BeautifulSoup(response.text, "html.parser")
    link = soup.select_one(".urlworkaround a, .generalbox a[href^='http']")
    return link["href"] if link else None


def _activity_files(s: Session, act: dict) -> list[dict]:
    typ, url = act.get("typ"), act.get("url")
    if not url:
        return []
    try:
        if typ == "resource":
            info = _fetch_file(s, url, body=False)
            return [{"name": info["filename"] or act["name"], "mime": info["content_type"],
                     "groesse_kb": round(info["size"] / 1024), "url": url}]
        if typ == "url":
            return [{"name": act["name"], "ziel": _resolve_link(s, url), "url": url}]
        if typ in PAGE_TYPES:
            return _file_links(BeautifulSoup(_get(s, urlparse(url).path + "?" + urlparse(url).query), "html.parser"))
        if typ == "forum":
            page = BeautifulSoup(_get(s, urlparse(url).path + "?" + urlparse(url).query), "html.parser")
            found = _file_links(page)
            threads = []
            for a in page.select("a[href*='/mod/forum/discuss.php?d=']"):
                href = urljoin(url, a["href"]).split("#")[0]
                if href not in threads:
                    threads.append(href)
            for href in threads[:20]:
                thread = BeautifulSoup(_get(s, urlparse(href).path + "?" + urlparse(href).query), "html.parser")
                found.extend(f for f in _file_links(thread) if f["url"] not in {x["url"] for x in found})
            return found
    except LoggedOut:
        raise
    except SiteError as exc:
        return [{"name": act["name"], "hinweis": str(exc), "url": url}]
    return []


def course_files(course_id: int, abschnitt: str | None = None) -> list[dict]:
    """Every file in a course: resources, folders, assignment attachments, page/label/forum files, links."""

    def run(s: Session) -> list[dict]:
        sections = parse_course(_get(s, "/course/view.php", id=int(course_id)))
        result, tasks = [], []
        for sec in sections:
            if abschnitt and abschnitt.lower() not in sec["abschnitt"].lower():
                continue
            for act in sec["aktivitaeten"]:
                for f in act.get("dateien", []):
                    result.append({"abschnitt": sec["abschnitt"], "aktivitaet": act["name"], "typ": act["typ"], **f})
                if act.get("typ") in SCAN_TYPES:
                    tasks.append((sec["abschnitt"], act))
        with ThreadPoolExecutor(max_workers=5) as pool:
            for (sec_name, act), files in zip(tasks, pool.map(lambda t: _activity_files(s, t[1]), tasks)):
                for f in files:
                    result.append({"abschnitt": sec_name, "aktivitaet": act["name"], "typ": act["typ"], **f})
        return result

    return _cache.run(run)


def page(path: str) -> dict:
    if not path.startswith("/") or path.startswith("//"):
        raise SiteError("Pfad muss mit / beginnen (z. B. /my/ oder /mod/assign/view.php?id=1).")

    def run(s: Session) -> dict:
        response = s.client.get(path)
        if "/login/index.php" in str(response.url):
            raise LoggedOut("Moodle-Session abgelaufen")
        soup = BeautifulSoup(response.text, "html.parser")
        region = soup.select_one("#region-main") or soup
        return {"text": clean_text(BeautifulSoup(str(region), "html.parser"), 8000), "dateien": _file_links(region)}

    return _cache.run(run)


# --- messages, notifications ------------------------------------------------

def _userid(s: Session) -> int:
    if not s.data.get("userid"):
        match = _USERID.search(_get(s, "/my/"))
        if not match:
            raise SiteError("Moodle: Benutzer-ID nicht gefunden.")
        s.data["userid"] = int(match.group(1))
    return s.data["userid"]


def conversations(limit: int = 15) -> list[dict]:
    def run(s: Session) -> list[dict]:
        data = _ajax(s, "core_message_get_conversations", {
            "userid": _userid(s), "limitfrom": 0, "limitnum": limit, "type": None,
            "favourites": None, "mergeself": True})
        out = []
        for c in data.get("conversations", []):
            last = (c.get("messages") or [{}])[-1]
            out.append({
                "id": c.get("id"),
                "mit": ", ".join(m.get("fullname", "") for m in c.get("members", [])
                                 if m.get("id") != _userid(s)) or c.get("name"),
                "ungelesen": c.get("unreadcount") or 0,
                "letzte": _plain(last.get("text")),
                "zeit": _stamp(last.get("timecreated")),
            })
        return out

    return _cache.run(run)


def conversation(conversation_id: int, limit: int = 30) -> list[dict]:
    def run(s: Session) -> list[dict]:
        uid = _userid(s)
        data = _ajax(s, "core_message_get_conversation_messages", {
            "currentuserid": uid, "convid": int(conversation_id), "limitfrom": 0,
            "limitnum": limit, "newest": True})
        names = {m["id"]: m.get("fullname", "") for m in data.get("members", [])}
        return [{"von": names.get(m.get("useridfrom"), str(m.get("useridfrom"))),
                 "zeit": _stamp(m.get("timecreated")), "text": _plain(m.get("text"))}
                for m in data.get("messages", [])]

    return _cache.run(run)


def notifications(limit: int = 20) -> list[dict]:
    def run(s: Session) -> list[dict]:
        data = _ajax(s, "message_popup_get_popup_notifications", {
            "useridto": _userid(s), "newestfirst": True, "limit": limit, "offset": 0})
        return [{"betreff": n.get("subject"), "text": _plain(n.get("fullmessagehtml") or n.get("fullmessage")),
                 "zeit": _stamp(n.get("timecreated")), "gelesen": n.get("read"), "url": n.get("contexturl")}
                for n in data.get("notifications", [])]

    return _cache.run(run)
