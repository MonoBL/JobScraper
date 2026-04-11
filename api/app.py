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

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from job_history import get_jobs_for_date, list_date_summaries, today_iso
from job_agent import evaluate_job, is_agent_configured, list_agents

logger = logging.getLogger(__name__)

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
    agents = list_agents() if is_agent_configured() else []
    return {
        "ok": True,
        "agent": is_agent_configured(),
        "agents_enabled": is_agent_configured(),
        "agents": agents,
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


@protected.post("/agent/evaluate")
def agent_evaluate(body: AgentBody) -> Dict[str, Any]:
    if not is_agent_configured():
        raise HTTPException(
            status_code=503,
            detail="Set OPENROUTER_API_KEY or OPENAI_API_KEY in .env to enable agents.",
        )
    result = evaluate_job(
        body.title,
        body.company,
        body.description,
        agent_id=body.agent_id,
    )
    if result is None:
        raise HTTPException(
            status_code=502,
            detail="Agent request failed (unknown agent_id or LLM error).",
        )
    return {"agent_id": body.agent_id, "result": result}


async def _run_scrape_now_task() -> None:
    try:
        from main import run_daily_scrape_async

        await run_daily_scrape_async(is_startup_run=True)
    except Exception:
        logger.exception("Background scrape-now failed")


@protected.post("/scrape-now")
async def scrape_now(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """Trigger one scrape (no extra code — dashboard login is enough)."""
    background_tasks.add_task(_run_scrape_now_task)
    return {
        "status": "started",
        "detail": "Scrape started in the background. Wait a few minutes, then refresh or pick today on the calendar.",
    }


app.include_router(public)
app.include_router(protected)

# Built dashboard (npm run build) — same origin as /api for Cloudflare Tunnel / homelab (single port).
_dist = os.path.join(_ROOT, "web", "dist")
if os.path.isdir(_dist):
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_dist, html=True), name="static")
