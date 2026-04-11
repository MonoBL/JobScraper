"""
Persist resume text + LLM-derived ranker overrides for JobRanker / CruiseJobRanker.

Files under data/ (gitignored): resume.pdf, resume.txt, ranker_overrides.json
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(_ROOT, "data")
_RESUME_PDF = os.path.join(_DATA, "resume.pdf")
_RESUME_TXT = os.path.join(_DATA, "resume.txt")
_OVERRIDES_JSON = os.path.join(_DATA, "ranker_overrides.json")

_MAX_PDF_BYTES = 5 * 1024 * 1024
_MAX_RESUME_CHARS = 200_000

# Caps per list after merge (safety)
_MAX_LIST = 48

_CACHE: Dict[str, Any] = {"mtime": None, "extras": None}

_DEFAULT_EXTRAS: Dict[str, Any] = {
    "perfect_titles": [],
    "good_titles": [],
    "perfect_keywords": {"linux": [], "scripting": [], "infrastructure": [], "automation": []},
    "good_keywords": [],
    "strong_title_phrases": [],
    "blacklist_titles": [],
    "blacklist_keywords_title": [],
    "cruise_perfect_titles": [],
    "cruise_good_titles": [],
    "cruise_it_keywords": [],
}


def ensure_data_dir() -> None:
    os.makedirs(_DATA, mode=0o700, exist_ok=True)


def _normalize_phrase(s: str) -> str:
    t = (s or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


def _dedupe_cap(items: List[str], cap: int = _MAX_LIST) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for raw in items:
        n = _normalize_phrase(raw)
        if not n or len(n) > 120:
            continue
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
        if len(out) >= cap:
            break
    return out


def clear_extras_cache() -> None:
    _CACHE["mtime"] = None
    _CACHE["extras"] = None


def _empty_store() -> Dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "last_summary": "",
        **{k: (dict((sk, []) for sk in _DEFAULT_EXTRAS["perfect_keywords"]) if k == "perfect_keywords" else [])
           for k in _DEFAULT_EXTRAS},
    }


def _read_overrides_raw() -> Dict[str, Any]:
    ensure_data_dir()
    if not os.path.isfile(_OVERRIDES_JSON):
        return _empty_store()
    try:
        with open(_OVERRIDES_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not read ranker_overrides.json: %s", e)
        return _empty_store()
    out = _empty_store()
    out["version"] = data.get("version", 1)
    out["updated_at"] = data.get("updated_at")
    out["last_summary"] = str(data.get("last_summary") or "")[:2000]
    for k in _DEFAULT_EXTRAS:
        if k == "perfect_keywords":
            pk_in = data.get("perfect_keywords")
            if isinstance(pk_in, dict):
                for sub in _DEFAULT_EXTRAS["perfect_keywords"]:
                    out["perfect_keywords"][sub] = _dedupe_cap(list(pk_in.get(sub, [])))
        else:
            out[k] = _dedupe_cap(list(data.get(k, [])))
    return out


def load_ranker_extras() -> Dict[str, Any]:
    """Merged extras for JobRanker (cached by mtime)."""
    ensure_data_dir()
    mtime = os.path.getmtime(_OVERRIDES_JSON) if os.path.isfile(_OVERRIDES_JSON) else -1.0
    if _CACHE["extras"] is not None and _CACHE["mtime"] == mtime:
        return _CACHE["extras"]

    raw = _read_overrides_raw()
    extras = {
        "perfect_titles": _dedupe_cap(raw.get("perfect_titles") or []),
        "good_titles": _dedupe_cap(raw.get("good_titles") or []),
        "perfect_keywords": {
            k: _dedupe_cap(list(raw.get("perfect_keywords", {}).get(k, [])))
            for k in _DEFAULT_EXTRAS["perfect_keywords"]
        },
        "good_keywords": _dedupe_cap(raw.get("good_keywords") or []),
        "strong_title_phrases": _dedupe_cap(raw.get("strong_title_phrases") or []),
        "blacklist_titles": _dedupe_cap(raw.get("blacklist_titles") or []),
        "blacklist_keywords_title": _dedupe_cap(raw.get("blacklist_keywords_title") or []),
        "cruise_perfect_titles": _dedupe_cap(raw.get("cruise_perfect_titles") or []),
        "cruise_good_titles": _dedupe_cap(raw.get("cruise_good_titles") or []),
        "cruise_it_keywords": _dedupe_cap(raw.get("cruise_it_keywords") or []),
        "last_summary": (raw.get("last_summary") or "")[:2000],
        "updated_at": raw.get("updated_at"),
    }
    _CACHE["mtime"] = mtime
    _CACHE["extras"] = extras
    return extras


def extract_text_from_pdf(file_bytes: bytes) -> str:
    from pypdf import PdfReader
    from io import BytesIO

    if len(file_bytes) > _MAX_PDF_BYTES:
        raise ValueError("PDF exceeds maximum size (5 MB).")
    reader = PdfReader(BytesIO(file_bytes))
    parts: List[str] = []
    for page in reader.pages:
        try:
            t = page.extract_text()
            if t:
                parts.append(t)
        except Exception as e:
            logger.warning("PDF page extract failed: %s", e)
    return "\n".join(parts).strip()


def save_resume_pdf(file_bytes: bytes) -> Dict[str, Any]:
    """Write resume.pdf + resume.txt; returns char count and short preview."""
    ensure_data_dir()
    text = extract_text_from_pdf(file_bytes)
    if len(text) > _MAX_RESUME_CHARS:
        text = text[:_MAX_RESUME_CHARS]
    with open(_RESUME_PDF, "wb") as f:
        f.write(file_bytes)
    with open(_RESUME_TXT, "w", encoding="utf-8") as f:
        f.write(text)
    preview = text[:400].replace("\n", " ")
    return {"char_count": len(text), "preview": preview}


def load_resume_text() -> str:
    if not os.path.isfile(_RESUME_TXT):
        return ""
    try:
        with open(_RESUME_TXT, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def profile_status() -> Dict[str, Any]:
    ensure_data_dir()
    has_pdf = os.path.isfile(_RESUME_PDF)
    has_txt = os.path.isfile(_RESUME_TXT)
    ex = load_ranker_extras()
    raw = _read_overrides_raw()
    return {
        "has_resume": has_pdf and has_txt,
        "resume_chars": len(load_resume_text()) if has_txt else 0,
        "overrides_updated_at": raw.get("updated_at"),
        "last_summary": (raw.get("last_summary") or "")[:800],
        "override_counts": {
            "perfect_titles": len(ex["perfect_titles"]),
            "good_titles": len(ex["good_titles"]),
            "good_keywords": len(ex["good_keywords"]),
            "blacklist_titles": len(ex["blacklist_titles"]),
            "cruise_perfect_titles": len(ex["cruise_perfect_titles"]),
        },
    }


def baseline_for_llm() -> Dict[str, Any]:
    """Snapshot of built-in ranker rules (for resume review agent)."""
    import main as m

    return {
        "job_ranker": {
            "perfect_titles": list(m.JobRanker.PERFECT_TITLES),
            "good_titles": list(m.JobRanker.GOOD_TITLES),
            "perfect_keywords": {k: list(v) for k, v in m.JobRanker.PERFECT_KEYWORDS.items()},
            "good_keywords": list(m.JobRanker.GOOD_KEYWORDS),
            "blacklist_titles": list(m.JobRanker.BLACKLIST_TITLES),
            "blacklist_keywords_title": list(m.JobRanker.BLACKLIST_KEYWORDS_TITLE),
        },
        "cruise_job_ranker": {
            "perfect_titles": list(m.CruiseJobRanker.PERFECT_TITLES),
            "good_titles": list(m.CruiseJobRanker.GOOD_TITLES),
            "it_keywords": list(m.CruiseJobRanker.IT_KEYWORDS),
        },
    }


def apply_llm_overrides(
    patch: Dict[str, Any],
    *,
    summary: str = "",
) -> Dict[str, Any]:
    """
    Merge *_add fields from LLM into ranker_overrides.json (additive).
    """
    ensure_data_dir()
    current = _read_overrides_raw()
    base: Dict[str, Any] = {k: list(current[k]) for k in _DEFAULT_EXTRAS if k != "perfect_keywords"}
    base["perfect_keywords"] = {
        sub: list(current["perfect_keywords"][sub]) for sub in _DEFAULT_EXTRAS["perfect_keywords"]
    }

    def merge_list(key: str, add_key: str) -> None:
        adds = patch.get(add_key)
        if not isinstance(adds, list):
            return
        base[key] = _dedupe_cap(list(base[key]) + [str(x) for x in adds])

    merge_list("perfect_titles", "perfect_titles_add")
    merge_list("good_titles", "good_titles_add")
    merge_list("good_keywords", "good_keywords_add")
    merge_list("strong_title_phrases", "strong_title_phrases_add")
    merge_list("blacklist_titles", "blacklist_titles_add")
    merge_list("blacklist_keywords_title", "blacklist_keywords_title_add")
    merge_list("cruise_perfect_titles", "cruise_perfect_titles_add")
    merge_list("cruise_good_titles", "cruise_good_titles_add")
    merge_list("cruise_it_keywords", "cruise_it_keywords_add")

    pk_add = patch.get("perfect_keywords_add")
    if isinstance(pk_add, dict):
        for sub in _DEFAULT_EXTRAS["perfect_keywords"]:
            extra = pk_add.get(sub)
            if isinstance(extra, list):
                base["perfect_keywords"][sub] = _dedupe_cap(
                    list(base["perfect_keywords"][sub]) + [str(x) for x in extra]
                )

    from datetime import datetime, timezone

    summary_text = (summary or patch.get("summary") or "")[:2000]
    out = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_summary": summary_text,
        **{k: base[k] for k in _DEFAULT_EXTRAS if k != "perfect_keywords"},
        "perfect_keywords": base["perfect_keywords"],
    }
    with open(_OVERRIDES_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    clear_extras_cache()
    return out


def clear_overrides_file() -> None:
    ensure_data_dir()
    if os.path.isfile(_OVERRIDES_JSON):
        os.remove(_OVERRIDES_JSON)
    clear_extras_cache()
