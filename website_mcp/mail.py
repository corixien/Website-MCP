"""School webmail (Roundcube at mail.gymnasium-babelsberg.de): read-only."""
from __future__ import annotations

import email
import json
import re
from email import policy

from bs4 import BeautifulSoup

from .config import secret
from .http import LoggedOut, LoginFailed, Session, SessionCache, SiteError, clean_text, new_client

BASE = "https://mail.gymnasium-babelsberg.de"
_TOKEN = re.compile(r'"request_token":"([^"]+)"')


def _login() -> Session:
    client = new_client(BASE)
    page = BeautifulSoup(client.get("/").text, "html.parser")
    token = page.find("input", {"name": "_token"})
    data = {
        "_token": token.get("value", "") if token else "",
        "_task": "login",
        "_action": "login",
        "_timezone": "Europe/Berlin",
        "_url": "_task=login",
        "_user": secret("MAIL_USER"),
        "_pass": secret("MAIL_PASS"),
    }
    response = client.post("/?_task=login", data=data)
    match = _TOKEN.search(response.text)
    if "_task=login" in str(response.url) or not match:
        raise LoginFailed("Webmail-Login fehlgeschlagen (Zugangsdaten prüfen).")
    return Session(client, {"token": match.group(1)})


_cache = SessionCache(_login)


def _strip(value: object) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(str(value or ""), "html.parser").get_text(" ")).strip()


def parse_rows(js: str) -> list[dict]:
    """Extract rows from Roundcube's `exec` string (calls to add_message_row)."""
    decoder = json.JSONDecoder()
    key, rows, pos = "add_message_row(", [], 0
    while (start := js.find(key, pos)) != -1:
        begin = start + len(key)
        comma = js.find(",", begin)
        if comma == -1:
            break
        try:
            obj, end = decoder.raw_decode(js, comma + 1)
        except ValueError:
            pos = begin
            continue
        flags: dict = {}
        rest = end
        while rest < len(js) and js[rest] in ", \n":
            rest += 1
        try:
            parsed, rest_end = decoder.raw_decode(js, rest)
            if isinstance(parsed, dict):
                flags, end = parsed, rest_end
        except ValueError:
            pass
        rows.append({
            "uid": js[begin:comma].strip(" '\""),
            "von": _strip(obj.get("fromto")),
            "betreff": _strip(obj.get("subject")),
            "datum": _strip(obj.get("date")),
            "ungelesen": not flags.get("seen", 0),
        })
        pos = end
    return rows


def _list(s: Session, **params: str) -> list[dict]:
    response = s.client.get(
        "/",
        params={"_task": "mail", "_remote": "1", "_unlock": "0", **params},
        headers={"X-Roundcube-Request": s.data["token"]},
    )
    if response.status_code in (401, 403) or "_task=login" in str(response.url):
        raise LoggedOut("Webmail-Session abgelaufen")
    return parse_rows(response.json().get("exec", ""))


def list_messages(folder: str = "INBOX", page: int = 1) -> list[dict]:
    return _cache.run(lambda s: _list(s, _action="list", _mbox=folder, _page=str(page)))


def search(query: str, folder: str = "INBOX") -> list[dict]:
    return _cache.run(lambda s: _list(
        s, _action="search", _mbox=folder, _q=query, _headers="subject,from", _scope="base", _filter="ALL"
    ))


def parse_message(raw: bytes, limit: int = 8000) -> dict:
    msg = email.message_from_bytes(raw, policy=policy.default)
    body = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        content = body.get_content()
        text = clean_text(content, limit) if body.get_content_type() == "text/html" else content[:limit]
    return {
        "von": str(msg.get("From", "")),
        "an": str(msg.get("To", "")),
        "betreff": str(msg.get("Subject", "")),
        "datum": str(msg.get("Date", "")),
        "text": text.strip(),
        "anhaenge": [p.get_filename() or "(ohne Namen)" for p in msg.iter_attachments()],
    }


def read_message(uid: str, folder: str = "INBOX") -> dict:
    if not uid.isdigit():
        raise SiteError("uid muss eine Zahl sein (aus mail_liste).")

    def run(s: Session) -> dict:
        response = s.client.get(
            "/", params={"_task": "mail", "_mbox": folder, "_uid": uid, "_action": "viewsource"}
        )
        if "_task=login" in str(response.url):
            raise LoggedOut("Webmail-Session abgelaufen")
        if response.status_code != 200 or not response.content:
            raise SiteError("Mail nicht gefunden.")
        return parse_message(response.content)

    return _cache.run(run)
