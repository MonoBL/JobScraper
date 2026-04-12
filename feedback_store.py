"""
Store good/bad feedback on job listings.
Feedback lives in data/feedback.json (same dir as jobs_by_date.json).
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional

from job_history import get_data_dir


def _feedback_path() -> str:
    return os.path.join(get_data_dir(), "feedback.json")


def _load() -> List[Dict[str, Any]]:
    p = _feedback_path()
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(entries: List[Dict[str, Any]]) -> None:
    p = _feedback_path()
    d = get_data_dir()
    fd, tmp = tempfile.mkstemp(prefix="feedback_", suffix=".tmp", dir=d)
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


def upsert_feedback(url: str, title: str, company: str, feedback: str) -> Dict[str, Any]:
    """Save or update feedback for a job URL. feedback = 'good' | 'bad' | 'none'."""
    entries = _load()
    now = datetime.now().strftime("%Y-%m-%d")
    
    if feedback == "none":
        original_len = len(entries)
        entries = [e for e in entries if e.get("url") != url]
        if len(entries) != original_len:
            _save(entries)
        return {"url": url, "feedback": "none"}

    for e in entries:
        if e.get("url") == url:
            e["feedback"] = feedback
            e["updated"] = now
            _save(entries)
            return e
    entry = {"url": url, "title": title, "company": company, "feedback": feedback, "date": now}
    entries.append(entry)
    _save(entries)
    return entry


def get_feedback(url: str) -> Optional[str]:
    """Return 'good', 'bad', or None."""
    for e in _load():
        if e.get("url") == url:
            return e.get("feedback")
    return None


def load_all_feedback() -> Dict[str, str]:
    """Return {url: feedback} mapping."""
    return {e["url"]: e["feedback"] for e in _load() if e.get("url") and e.get("feedback")}


def list_feedback() -> List[Dict[str, Any]]:
    return _load()
