"""
Local API for the job dashboard. Run from repo root:

  uvicorn api.app:app --reload --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import jwt

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
except ImportError:
    pass

from pydantic import BaseModel, Field

# Repo root on path for job_history / job_agent
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from job_history import get_jobs_for_date, list_date_summaries, today_iso
from feedback_store import upsert_feedback, load_all_feedback, list_feedback
from job_agent import (
    evaluate_job,
    get_llm_diagnostics,
    is_agent_configured,
    list_agents,
    review_resume_for_search_profile,
    test_llm_agent_models,
)

logger = logging.getLogger(__name__)

# Manual "Scrape now" from API (single-process; used for UI loading state)
_scrape_state: Dict[str, Any] = {
    "running": False,
    "started_at": None,
    "phase": None,           # "starting"|"loading"|"scrapers"|"ranking"|"saving"|"discord"|"done"|"error"
    "scraper_results": [],   # [{name, category, status, jobs_found, error}]
    "totals": None,          # {scraped, blacklisted, skipped, new}  — filled after ranking
    "error": None,           # fatal error string
}

app = FastAPI(title="Job Scraper Dashboard API", version="1.0.0")

_origins = os.getenv(
    "CORS_ORIGINS",
    "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8765,http://localhost:8765",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _login_required() -> bool:
    return bool(os.getenv("DASHBOARD_ACCESS_CODE", "").strip())


def _jwt_secret_str() -> str:
    j = os.getenv("DASHBOARD_JWT_SECRET", "").strip()
    if j:
        return j
    c = os.getenv("DASHBOARD_ACCESS_CODE", "").strip()
    if c:
        return hashlib.sha256(c.encode()).hexdigest()
    return ""


def _code_matches(provided: str, expected: str) -> bool:
    if len(provided) != len(expected):
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def _issue_token() -> str:
    secret = _jwt_secret_str()
    if not secret:
        raise HTTPException(status_code=500, detail="Dashboard auth is misconfigured.")
    return jwt.encode(
        {"sub": "dashboard", "exp": int(time.time()) + 7 * 24 * 3600},
        secret,
        algorithm="HS256",
    )


def _verify_jwt(token: str) -> bool:
    secret = _jwt_secret_str()
    if not secret:
        return True
    try:
        jwt.decode(token, secret, algorithms=["HS256"])
        return True
    except Exception:
        return False


def require_dashboard(authorization: Optional[str] = Header(None)) -> None:
    if not _login_required():
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization[7:].strip()
    if not _verify_jwt(token):
        raise HTTPException(status_code=401, detail="Invalid or expired session")


public = APIRouter()
protected = APIRouter(prefix="/api", dependencies=[Depends(require_dashboard)])


@public.get("/api/health")
def health() -> Dict[str, Any]:
    llm = is_agent_configured()
    agents = list_agents() if llm else []
    return {
        "ok": True,
        "service": "job-scraper-dashboard",
        "agent": llm,
        "agents_enabled": llm,
        "agents": agents,
        "login_required": _login_required(),
        "llm_agent_count": len(agents),
    }


@public.get("/api/auth/status")
def auth_status() -> Dict[str, Any]:
    return {"login_required": _login_required()}


@public.get("/api/auth/session")
def auth_session(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not _login_required():
        return {"valid": True, "login_required": False}
    if not authorization or not authorization.startswith("Bearer "):
        return {"valid": False, "login_required": True}
    token = authorization[7:].strip()
    return {"valid": _verify_jwt(token), "login_required": True}


class LoginBody(BaseModel):
    code: str = Field(..., min_length=1, max_length=512)


@public.post("/api/auth/login")
def auth_login(body: LoginBody) -> Dict[str, Any]:
    if not _login_required():
        return {"token": None, "login_required": False}
    expected = os.getenv("DASHBOARD_ACCESS_CODE", "").strip()
    if not _code_matches(body.code.strip(), expected):
        raise HTTPException(status_code=401, detail="Invalid code")
    return {"token": _issue_token(), "login_required": True}


@protected.get("/today")
def jobs_today() -> Dict[str, Any]:
    d = today_iso()
    jobs = get_jobs_for_date(d)
    return {"date": d, "jobs": jobs, "count": len(jobs)}


@protected.get("/dates")
def dates() -> Dict[str, Any]:
    return {"dates": list_date_summaries()}


@protected.get("/jobs/{date}")
def jobs_for_date(date: str) -> Dict[str, Any]:
    if len(date) != 10 or date[4] != "-" or date[7] != "-":
        raise HTTPException(status_code=400, detail="Expected YYYY-MM-DD")
    jobs = get_jobs_for_date(date)
    return {"date": date, "jobs": jobs, "count": len(jobs)}


class AgentBody(BaseModel):
    agent_id: str = Field(default="fit", min_length=1)
    title: str = Field(..., min_length=1)
    company: str = ""
    description: str = ""
    url: Optional[str] = None


@protected.get("/agents")
def agents_list() -> Dict[str, Any]:
    if not is_agent_configured():
        return {"agents": [], "configured": False}
    return {"agents": list_agents(), "configured": True}


@protected.get("/agents/diagnostics")
def agents_diagnostics() -> Dict[str, Any]:
    """Resolved provider, base URL, model id per agent (no LLM calls)."""
    return get_llm_diagnostics()


@protected.post("/agents/test-models")
def agents_test_models() -> Dict[str, Any]:
    """Tiny completion per model — verifies connectivity (may take ~10–60s)."""
    if not is_agent_configured():
        raise HTTPException(
            status_code=503,
            detail="Set OPENROUTER_API_KEY or OPENAI_API_KEY to test models.",
        )
    return test_llm_agent_models()


@protected.get("/sources")
def scrape_sources_list() -> Dict[str, Any]:
    """Job boards / sites registered in main.py (same order as concurrent scrape)."""
    from main import get_scrape_sources_metadata

    rows = get_scrape_sources_metadata()
    return {"sources": rows, "count": len(rows)}


@protected.post("/agent/evaluate")
def agent_evaluate(body: AgentBody) -> Dict[str, Any]:
    if not is_agent_configured():
        raise HTTPException(
            status_code=503,
            detail="Set OPENROUTER_API_KEY or OPENAI_API_KEY in .env to enable agents.",
        )
    try:
        result = evaluate_job(
            body.title,
            body.company,
            body.description,
            agent_id=body.agent_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=502, detail="Agent returned no result.")
    return {"agent_id": body.agent_id, "result": result}


@protected.get("/profile")
def profile_get() -> Dict[str, Any]:
    from ranker_profile import profile_status

    return profile_status()


@protected.post("/profile/resume")
async def profile_upload_resume(file: UploadFile = File(...)) -> Dict[str, Any]:
    from ranker_profile import save_resume_pdf

    name = (file.filename or "").lower()
    if not name.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Upload a .pdf file.")
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="PDF exceeds 5 MB.")
    try:
        out = save_resume_pdf(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Resume PDF processing failed")
        raise HTTPException(status_code=500, detail="Could not read PDF.")
    return {"ok": True, **out}


@protected.post("/profile/review-resume")
def profile_review_resume() -> Dict[str, Any]:
    from ranker_profile import apply_llm_overrides, load_resume_text

    if not is_agent_configured():
        raise HTTPException(
            status_code=503,
            detail="Set OPENROUTER_API_KEY or OPENAI_API_KEY to run resume review.",
        )
    text = load_resume_text()
    if len(text) < 40:
        raise HTTPException(status_code=400, detail="Upload a resume PDF first.")
    patch = review_resume_for_search_profile(text)
    if patch is None:
        raise HTTPException(
            status_code=502,
            detail="Resume review failed (LLM error or invalid JSON).",
        )
    summary = str(patch.get("summary") or "")
    apply_llm_overrides(patch, summary=summary)
    preview = {
        "perfect_titles_add": patch.get("perfect_titles_add"),
        "good_titles_add": patch.get("good_titles_add"),
        "notes_for_search": patch.get("notes_for_search"),
    }
    return {"ok": True, "summary": summary, "preview": preview}


@protected.delete("/profile/overrides")
def profile_clear_overrides() -> Dict[str, Any]:
    from ranker_profile import clear_overrides_file

    clear_overrides_file()
    return {"ok": True}


class FeedbackBody(BaseModel):
    url: str = Field(..., min_length=1)
    title: str = ""
    company: str = ""
    feedback: str = Field(..., pattern="^(good|bad)$")


@protected.post("/feedback")
def post_feedback(body: FeedbackBody) -> Dict[str, Any]:
    entry = upsert_feedback(body.url, body.title, body.company, body.feedback)
    return {"ok": True, "entry": entry}


@protected.get("/feedback")
def get_all_feedback() -> Dict[str, Any]:
    return {"feedback": load_all_feedback()}


class NotificationSettingsBody(BaseModel):
    discord_notifications_enabled: bool = Field(..., description="When False, main.py skips all Discord webhook sends.")


@protected.get("/settings/notifications")
def get_notification_settings() -> Dict[str, Any]:
    from app_settings import load_settings

    return load_settings()


@protected.put("/settings/notifications")
def put_notification_settings(body: NotificationSettingsBody) -> Dict[str, Any]:
    from app_settings import set_discord_notifications_enabled

    return set_discord_notifications_enabled(body.discord_notifications_enabled)


def _parse_schedule_time() -> tuple[int, int]:
    raw = os.getenv("SCRAPE_SCHEDULE_TIME", "09:00").strip()
    try:
        parts = raw.replace(".", ":").split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return max(0, min(23, h)), max(0, min(59, m))
    except (ValueError, IndexError):
        return 9, 0


def _next_scheduled_run() -> tuple[datetime, float]:
    """Next daily run in server local time (same as main.py scheduler)."""
    hh, mm = _parse_schedule_time()
    now = datetime.now()
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return target, max(0.0, (target - now).total_seconds())


@protected.get("/schedule")
def schedule_info() -> Dict[str, Any]:
    """Next automatic scrape (from SCRAPE_SCHEDULE_TIME, server clock)."""
    hh, mm = _parse_schedule_time()
    next_at, secs = _next_scheduled_run()
    return {
        "schedule_time": f"{hh:02d}:{mm:02d}",
        "next_run_iso": next_at.isoformat(timespec="seconds"),
        "seconds_until_next": int(secs),
    }


@protected.get("/scrape-status")
def scrape_status() -> Dict[str, Any]:
    """Live status of a manual scrape started from this API."""
    results = _scrape_state.get("scraper_results") or []
    done = sum(1 for s in results if s.get("status") in ("done", "error"))
    return {
        "running": bool(_scrape_state["running"]),
        "started_at": _scrape_state["started_at"],
        "phase": _scrape_state.get("phase"),
        "scrapers_total": len(results),
        "scrapers_done": done,
        "scraper_results": results,
        "totals": _scrape_state.get("totals"),
        "error": _scrape_state.get("error"),
    }


async def _run_scrape_now_task() -> None:
    # Reset progress fields before starting
    _scrape_state["phase"] = "starting"
    _scrape_state["scraper_results"] = []
    _scrape_state["totals"] = None
    _scrape_state["error"] = None
    try:
        from main import run_daily_scrape_async

        await run_daily_scrape_async(is_startup_run=True, progress=_scrape_state)
    except Exception:
        logger.exception("Background scrape-now failed")
        if not _scrape_state.get("error"):
            _scrape_state["error"] = "Unexpected error in scrape task — check server logs."
        _scrape_state["phase"] = "error"
    finally:
        _scrape_state["running"] = False
        _scrape_state["started_at"] = None


@protected.post("/scrape-now")
async def scrape_now(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """Trigger one scrape (dashboard auth). Exclusive while a run is in progress."""
    if _scrape_state["running"]:
        raise HTTPException(
            status_code=409,
            detail="A scrape is already running. Wait for it to finish.",
        )
    _scrape_state["running"] = True
    _scrape_state["started_at"] = time.time()
    background_tasks.add_task(_run_scrape_now_task)
    return {
        "status": "started",
        "detail": "Scrape started in the background.",
        "started_at": _scrape_state["started_at"],
    }


app.include_router(public)
app.include_router(protected)

# Built dashboard (npm run build) — same origin as /api for Cloudflare Tunnel / homelab (single port).
_dist = os.path.join(_ROOT, "web", "dist")
if os.path.isdir(_dist):
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_dist, html=True), name="static")
