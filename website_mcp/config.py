"""Secrets from environment variables (Horizon secrets); optional local .env."""
from __future__ import annotations

import os
from pathlib import Path

from .http import SiteError


def _load_dotenv() -> None:
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_dotenv()


def secret(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SiteError(f"Secret {name} ist nicht gesetzt (in Horizon als Secret anlegen).")
    return value
