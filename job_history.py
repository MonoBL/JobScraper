"""
Persist scraped jobs by calendar date for the web UI and analytics.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

HISTORY_FILENAME = "jobs_by_date.json"
MAX_DAYS_RETAINED = 400


def get_data_dir() -> str:
    """Job calendar JSON lives here. If JOB_SCRAPER_STATE_DIR is set, use <state>/data (Docker-friendly)."""
    state = os.getenv("JOB_SCRAPER_STATE_DIR", "").strip()
    if state:
        p = os.path.join(state, "data")
    else:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(p, exist_ok=True)
    return p


def _history_path() -> str:
    return os.path.join(get_data_dir(), HISTORY_FILENAME)


def _ensure_data_dir() -> None:
    get_data_dir()


def load_all_history() -> Dict[str, List[Dict[str, Any]]]:
    path = _history_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception as e:
        logger.warning("Could not load job history: %s", e)
        return {}


def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    _ensure_data_dir()
    fd, tmp = tempfile.mkstemp(prefix="jobs_hist_", suffix=".tmp", dir=get_data_dir())
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        if os.name == "nt" and os.path.exists(path):
            os.remove(path)
        os.rename(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise


def _prune_old_dates(store: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    cutoff = (datetime.now() - timedelta(days=MAX_DAYS_RETAINED)).strftime("%Y-%m-%d")
    return {d: jobs for d, jobs in store.items() if d >= cutoff}


def append_jobs_for_date(date_str: str, jobs: List[Dict[str, Any]]) -> None:
    """Append job dicts (e.g. from Job.to_dict()) to the given ISO date bucket."""
    if not jobs:
        return
    store = load_all_history()
    existing = store.get(date_str, [])
    # Dedupe by normalized url within the file
    seen = {j.get("url", "").lower().strip() for j in existing}
    added = 0
    for j in jobs:
        u = (j.get("url") or "").lower().strip()
        if u and u not in seen:
            seen.add(u)
            existing.append(j)
            added += 1
    if not added:
        return
    store[date_str] = existing
    store = _prune_old_dates(store)
    try:
        _atomic_write_json(_history_path(), store)
        logger.info("Saved %s new job(s) to history for %s", added, date_str)
    except Exception as e:
        logger.error("Failed to write job history: %s", e)


def get_jobs_for_date(date_str: str) -> List[Dict[str, Any]]:
    store = load_all_history()
    return list(store.get(date_str, []))


def list_date_summaries() -> List[Dict[str, Any]]:
    """For calendar: [{ date, count, perfect, good, weak }, ...] sorted desc by date."""
    store = load_all_history()
    out: List[Dict[str, Any]] = []
    for date_str, jobs in sorted(store.items(), reverse=True):
        perfect = sum(1 for j in jobs if j.get("priority") == "PERFECT_MATCH")
        good = sum(1 for j in jobs if j.get("priority") == "GOOD_MATCH")
        weak = sum(1 for j in jobs if j.get("priority") == "WEAK_MATCH")
        out.append(
            {
                "date": date_str,
                "count": len(jobs),
                "perfect": perfect,
                "good": good,
                "weak": weak,
            }
        )
    return out


def today_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")
