"""
Local API for the job dashboard. Run from repo root:

  uvicorn api.app:app --reload --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

import os
import sys

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
except ImportError:
    pass
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# Repo root on path for job_history / job_agent
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from job_history import get_jobs_for_date, list_date_summaries, today_iso
from job_agent import evaluate_job, is_agent_configured

app = FastAPI(title="Job Scraper Dashboard API", version="1.0.0")

_origins = os.getenv("CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "agent": is_agent_configured()}


@app.get("/api/today")
def jobs_today() -> Dict[str, Any]:
    d = today_iso()
    jobs = get_jobs_for_date(d)
    return {"date": d, "jobs": jobs, "count": len(jobs)}


@app.get("/api/dates")
def dates() -> Dict[str, Any]:
    return {"dates": list_date_summaries()}


@app.get("/api/jobs/{date}")
def jobs_for_date(date: str) -> Dict[str, Any]:
    # Basic ISO date validation
    if len(date) != 10 or date[4] != "-" or date[7] != "-":
        raise HTTPException(status_code=400, detail="Expected YYYY-MM-DD")
    jobs = get_jobs_for_date(date)
    return {"date": date, "jobs": jobs, "count": len(jobs)}


class AgentBody(BaseModel):
    title: str = Field(..., min_length=1)
    company: str = ""
    description: str = ""
    url: Optional[str] = None


@app.post("/api/agent/evaluate")
def agent_evaluate(body: AgentBody) -> Dict[str, Any]:
    if not is_agent_configured():
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY is not set. Add it to .env to enable the agent.",
        )
    result = evaluate_job(body.title, body.company, body.description)
    if result is None:
        raise HTTPException(status_code=502, detail="Agent request failed")
    return {"result": result}
