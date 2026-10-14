"""Public Untis substitution plan embedded on the school website (no login)."""
from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

from .http import SiteError, new_client

URL = "https://www.gymnasium-babelsberg.de/seite/630768/vertretungsplan.html"


def parse(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    head = soup.select_one("table.mon_head")
    stand = None
    if head:
        match = re.search(r"Stand:\s*([\d.]+\s+[\d:]+)", head.get_text(" ", strip=True))
        stand = match.group(1) if match else None

    days = []
    for title in soup.select("div.mon_title"):
        day = {"tag": title.get_text(" ", strip=True), "nachrichten": [], "eintraege": []}
        for sibling in title.find_next_siblings():
            if sibling.name == "div" and "mon_title" in (sibling.get("class") or []):
                break
            if sibling.name == "table" and "info" in (sibling.get("class") or []):
                for cell in sibling.select("td.info"):
                    text = cell.get_text(" ", strip=True)
                    if text:
                        day["nachrichten"].append(text)
            if sibling.name == "table" and "mon_list" in (sibling.get("class") or []):
                cols = [th.get_text(" ", strip=True) for th in sibling.select("tr th")]
                for row in sibling.select("tr.list"):
                    cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                    if cells:
                        day["eintraege"].append(dict(zip(cols, cells)))
        days.append(day)
    return {"stand": stand, "tage": days}


def _matches(entry: dict, klasse: str) -> bool:
    query = klasse.strip().lower()
    for key, value in entry.items():
        if not key.lower().startswith("klasse"):
            continue
        for token in re.split(r"[,\s]+", value.lower()):
            if token and (token == query or (token.isdigit() and query.startswith(token))):
                return True
    return False


def get_plan(klasse: str | None = None) -> dict:
    try:
        with new_client("https://www.gymnasium-babelsberg.de") as client:
            response = client.get(URL)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SiteError(f"Vertretungsplan nicht erreichbar: {exc.__class__.__name__}") from exc
    plan = parse(response.text)
    if klasse:
        for day in plan["tage"]:
            day["eintraege"] = [e for e in day["eintraege"] if _matches(e, klasse)]
    return plan
