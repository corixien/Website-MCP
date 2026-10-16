"""Google Keep notes via the unofficial gkeepapi (master token login), read-only."""
from __future__ import annotations

import threading
import time
from typing import Any

import gkeepapi
from gkeepapi import exception as keep_exc
from gkeepapi.node import NodeType

from .config import secret
from .http import LoginFailed, SiteError

SYNC_AFTER = 60.0
LOGIN_AFTER = 3600.0
_lock = threading.Lock()
_state: dict[str, Any] = {"keep": None, "login": 0.0, "sync": 0.0}


def _client() -> gkeepapi.Keep:
    """Logged-in client; incremental sync at most once a minute, fresh login hourly."""
    with _lock:
        now = time.monotonic()
        keep = _state["keep"]
        try:
            if keep is None or now - _state["login"] > LOGIN_AFTER:
                keep = gkeepapi.Keep()
                keep.authenticate(secret("GKEEP_EMAIL"), secret("GKEEP_MASTER_TOKEN"), sync=True)
                _state.update(keep=keep, login=now, sync=now)
            elif now - _state["sync"] > SYNC_AFTER:
                keep.sync()
                _state["sync"] = now
        except keep_exc.BrowserLoginRequiredException as exc:
            raise LoginFailed("Keep: Google verlangt eine Browser-Bestätigung für diese Server-IP.") from exc
        except keep_exc.LoginException as exc:
            raise LoginFailed("Keep-Login fehlgeschlagen (Master-Token ungültig oder Server blockiert).") from exc
        except keep_exc.KeepException as exc:
            raise SiteError(f"Keep: {exc.__class__.__name__}") from exc
        return keep


def _stamp(value: Any) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M") if value else ""


def note_dict(note: Any, full: bool = False, preview: int = 300) -> dict:
    is_list = note.type == NodeType.List
    out: dict[str, Any] = {
        "id": note.id,
        "titel": note.title,
        "typ": "liste" if is_list else "notiz",
        "labels": [label.name for label in note.labels.all()],
        "angepinnt": note.pinned,
        "archiviert": note.archived,
        "geaendert": _stamp(note.timestamps.updated),
    }
    if is_list:
        items = [f"[{'x' if i.checked else ' '}] {i.text}" for i in note.items]
        out["punkte"] = items if full else items[:10]
        if not full and len(items) > 10:
            out["weitere_punkte"] = len(items) - 10
    else:
        text = note.text or ""
        out["text"] = text if full else text[:preview] + ("..." if len(text) > preview else "")
    return out


def list_notes(suche: str | None = None, label: str | None = None, archiviert: bool = False,
               limit: int = 30) -> dict:
    keep = _client()
    label_ids = None
    if label:
        found = keep.findLabel(label)
        if found is None:
            return {"gesamt": 0, "notizen": [], "hinweis": f"Label '{label}' nicht gefunden."}
        label_ids = [found.id]
    notes = sorted(keep.find(query=suche or None, labels=label_ids, archived=archiviert, trashed=False),
                   key=lambda n: n.timestamps.updated, reverse=True)
    shown = [note_dict(n) for n in notes[:max(1, int(limit))]]
    result: dict[str, Any] = {"gesamt": len(notes), "angezeigt": len(shown), "notizen": shown}
    if len(notes) > len(shown):
        result["hinweis"] = "Liste gekürzt: suche, label oder limit anpassen."
    return result


def read_note(note_id: str) -> dict:
    note = _client().get(note_id)
    if note is None or getattr(note, "trashed", False):
        raise SiteError("Notiz nicht gefunden (id aus keep_liste).")
    return note_dict(note, full=True)


def labels() -> list[str]:
    return sorted(label.name for label in _client().labels())
