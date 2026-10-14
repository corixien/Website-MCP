"""Shared HTTP helpers: client factory, text extraction, login session cache."""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

import httpx
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
T = TypeVar("T")


class SiteError(Exception):
    """Error with a message safe to show to the user."""


class LoggedOut(SiteError):
    """Session expired; retry once with a fresh login."""


class LoginFailed(SiteError):
    """Credentials rejected or login flow changed."""


def new_client(base_url: str) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        follow_redirects=True,
        timeout=20.0,
        headers={"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"},
    )


def clean_text(source: Any, limit: int = 6000) -> str:
    """Visible text of HTML (str or soup), one non-empty line per row, truncated."""
    soup = BeautifulSoup(source, "html.parser") if isinstance(source, str) else source
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = (re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in soup.get_text("\n").splitlines())
    text = "\n".join(ln for ln in lines if ln)
    return text if len(text) <= limit else text[:limit] + "\n[...gekürzt]"


@dataclass
class Session:
    client: httpx.Client
    data: dict[str, Any] = field(default_factory=dict)


class SessionCache:
    """Keeps one logged-in session per site for `ttl` seconds."""

    def __init__(self, login: Callable[[], Session], ttl: float = 600.0) -> None:
        self._login = login
        self._ttl = ttl
        self._session: Session | None = None
        self._stamp = 0.0
        self._lock = threading.Lock()

    def get(self, fresh: bool = False) -> Session:
        with self._lock:
            expired = time.monotonic() - self._stamp > self._ttl
            if fresh or self._session is None or expired:
                if self._session is not None:
                    self._session.client.close()
                self._session = self._login()
                self._stamp = time.monotonic()
            return self._session

    def run(self, fn: Callable[[Session], T]) -> T:
        try:
            return fn(self.get())
        except LoggedOut:
            return fn(self.get(fresh=True))
        except httpx.HTTPError as exc:
            raise SiteError(f"Netzwerkfehler: {exc.__class__.__name__}") from exc
