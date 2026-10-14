"""Navigium Online (school licence BVS_POTSDAM): form login, read-only."""
from __future__ import annotations

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
    return Session(client, {"role": result.get("role")})


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
