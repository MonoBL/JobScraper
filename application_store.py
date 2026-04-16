"""
Store application decisions on job listings (applied / not_applied).

Data is persisted in data/applications.json (same data dir as jobs_by_date.json),
so it works both locally and when JOB_SCRAPER_STATE_DIR is set (Docker volume).
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, TypedDict

from job_history import get_data_dir

ApplicationStatus = Literal["applied", "not_applied"]


class ApplicationEntry(TypedDict, total=False):
    url: str
    title: str
    company: str
    status: ApplicationStatus
    date: str
    updated: str


def _applications_path() -> str:
    return os.path.join(get_data_dir(), "applications.json")


def _load() -> List[ApplicationEntry]:
    p = _applications_path()
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(entries: List[ApplicationEntry]) -> None:
    p = _applications_path()
    d = get_data_dir()
    fd, tmp = tempfile.mkstemp(prefix="applications_", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        if os.name == "nt" and os.path.exists(p):
            os.remove(p)
        os.rename(tmp, p)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise


def upsert_application(
    url: str,
    title: str,
    company: str,
    status: ApplicationStatus,
) -> ApplicationEntry:
    """Save or update application status for a job URL."""
    url = (url or "").strip()
    if not url:
        raise ValueError("url is required")
    if status not in ("applied", "not_applied"):
        raise ValueError("status must be 'applied' or 'not_applied'")

    entries = _load()
    now = datetime.now().strftime("%Y-%m-%d")
    for e in entries:
        if (e.get("url") or "").strip() == url:
            e["status"] = status
            if title:
                e["title"] = title
            if company:
                e["company"] = company
            e["updated"] = now
            _save(entries)
            return e

    entry: ApplicationEntry = {
        "url": url,
        "title": title or "",
        "company": company or "",
        "status": status,
        "date": now,
        "updated": now,
    }
    entries.append(entry)
    _save(entries)
    return entry


def get_application_status(url: str) -> Optional[ApplicationStatus]:
    url = (url or "").strip()
    if not url:
        return None
    for e in _load():
        if (e.get("url") or "").strip() == url:
            st = e.get("status")
            if st in ("applied", "not_applied"):
                return st
            return None
    return None


def load_application_map() -> Dict[str, ApplicationStatus]:
    """Return {url: status} mapping."""
    out: Dict[str, ApplicationStatus] = {}
    for e in _load():
        u = (e.get("url") or "").strip()
        st = e.get("status")
        if u and st in ("applied", "not_applied"):
            out[u] = st
    return out


def list_applications() -> List[ApplicationEntry]:
    """Return all entries (most recent first)."""
    items = _load()
    # Sort by updated descending (fallback to date)
    def key(e: ApplicationEntry) -> str:
        return str(e.get("updated") or e.get("date") or "")

    return sorted(items, key=key, reverse=True)

