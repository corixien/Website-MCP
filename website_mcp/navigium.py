"""Navigium Online (school licence BVS_POTSDAM): form login, read-only."""
from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

from .config import secret
from .http import LoggedOut, LoginFailed, Session, SessionCache, SiteError, clean_text, new_client

BASE = "https://www.navigium.de"
SCHOOL = "BVS_POTSDAM"


def _login() -> Session:
    client = new_client(BASE)
    client.get(f"/login/schule/{SCHOOL}")
    response = client.post(
        "/schule/login2",
        data={
            "name": secret("NAVIGIUM_USER"),
            "school": SCHOOL,
            "password": secret("NAVIGIUM_PASS"),
            "permanent": "false",
            "forward": "",
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    try:
        result = response.json()
    except ValueError as exc:
        raise LoginFailed("Navigium: unerwartete Login-Antwort.") from exc
    if not result.get("authenticated"):
        message = clean_text(str(result.get("message") or ""), 200)
        raise LoginFailed(f"Navigium-Login fehlgeschlagen. {message}".strip())
    return Session(client, {"role": result.get("role"), "user": (result.get("name") or "").strip()})


_cache = SessionCache(_login)


def _fetch(s: Session, path: str) -> dict:
    response = s.client.get(path)
    if "/login/schule" in str(response.url) and "/loggedin" not in str(response.url):
        raise LoggedOut("Navigium-Session abgelaufen")
    soup = BeautifulSoup(response.text, "html.parser")
    links = []
    for a in soup.select("a[href]"):
        label = a.get_text(" ", strip=True)
        href = a["href"]
        if label and not href.startswith(("#", "javascript:")):
            links.append({"text": label, "pfad": href})
    return {"text": clean_text(soup, 5000), "links": links[:40]}


def overview() -> dict:
    def run(s: Session) -> dict:
        path = "/schule/login/mainmenu-L.html" if s.data.get("role") == "L" else "/schule/login/mainmenu.html"
        return _fetch(s, path)

    return _cache.run(run)


def page(path: str) -> dict:
    if not path.startswith("/") or path.startswith("//"):
        raise SiteError("Pfad muss mit / beginnen.")
    return _cache.run(lambda s: _fetch(s, path))


# --- vocabulary lessons (JSON API behind the Angular app) --------------------

def _json(s: Session, path: str, **params: str) -> Any:
    response = s.client.get(path, params=params, headers={"Accept": "application/json"})
    if "/login/schule" in str(response.url) and "/loggedin" not in str(response.url):
        raise LoggedOut("Navigium-Session abgelaufen")
    try:
        return response.json()
    except ValueError as exc:
        raise SiteError(f"Navigium: keine Daten für {path.split('?')[0]} (HTTP {response.status_code}).") from exc


def parse_lessons(items: list[dict]) -> list[dict]:
    lessons: dict[str, dict] = {}
    for item in items:
        lesson_id = str((item.get("key") or {}).get("id") or "")
        if not lesson_id or lesson_id in lessons:
            continue
        sub = (item.get("subcat") or {}).get("name")
        lessons[lesson_id] = {
            "id": int(lesson_id),
            "name": item.get("name"),
            "eigner": item.get("eigner"),
            "gruppe": item.get("cat"),
            "unterkategorie": sub if sub and sub != "-" else None,
            "sprache": item.get("lang"),
        }
    return list(lessons.values())


def lessons(suche: str | None = None, sprache: str = "LA") -> list[dict]:
    def run(s: Session) -> list[dict]:
        user = s.data["user"]
        merged: list[dict] = []
        for path, extra in (("/schule/vokabellektionen/inklPontes", {}),
                            ("/schule/vokabellektionen", {"lang": sprache})):
            try:
                merged.extend(_json(s, path, schule=SCHOOL, user=user, **extra))
            except SiteError:
                continue
        found = parse_lessons(merged)
        if sprache:
            found = [x for x in found if not x["sprache"] or x["sprache"] == sprache]
        if suche:
            found = [x for x in found if suche.lower() in (x["name"] or "").lower()]
        return found

    return _cache.run(run)


def parse_vocab(data: dict) -> dict:
    words = []
    for v in data.get("vokabeln", []):
        entry = {
            "latein": v.get("latein"),
            "stammformen": v.get("stammformen") if v.get("stammformen") != v.get("latein") else None,
            "genus": v.get("genus"),
            "wortart": v.get("wortart"),
            "deutsch": v.get("bedeutungen"),
            "phrasen": v.get("phrasen"),
            "minikontexte": v.get("minikontexte"),
            "merkhilfe": v.get("merkhilfe"),
            "anmerkung": v.get("anmerkung"),
            "aussprache": v.get("aussprache"),
            "querverweise": v.get("querverweise"),
            "pflicht": v.get("obligatorisch"),
        }
        words.append({k: val for k, val in entry.items() if val not in (None, "", [], {})})
    return {"id": data.get("lektionId"), "name": data.get("lektionName"), "anzahl": len(words), "vokabeln": words}


def vocab(lesson_id: int) -> dict:
    return _cache.run(lambda s: parse_vocab(
        _json(s, f"/schule/rest/vokabellektion/{int(lesson_id)}", schule=SCHOOL, user=s.data["user"])))


def assignments() -> dict:
    def run(s: Session) -> dict:
        user = s.data["user"]
        group = _json(s, f"/schule/{SCHOOL}/benutzer/{user}/auftraege")
        preset = _json(s, "/schule/rest/vokabeltraining/vorgabe", schule=SCHOOL, user=user, zukuenftige="true")
        return {
            "gruppe": group.get("gruppeName"),
            "auftrag_gruppe": group.get("auftragGruppe") or None,
            "auftrag_benutzer": group.get("auftragBenutzer") or None,
            "vorgabe": preset.get("vorgabe"),
            "abfragesprache": preset.get("abfragesprache"),
        }

    return _cache.run(run)
