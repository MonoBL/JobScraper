"""
Optional LLM helper to score a job for DevOps / SysAdmin / platform-style roles.
Set OPENAI_API_KEY in the environment to enable.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def is_agent_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def evaluate_job(
    title: str,
    company: str,
    description: str,
    *,
    model: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Returns dict with keys: score (1-10), fit (one line), strengths, concerns.
    None if not configured or request failed.
    """
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return None

    model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    snippet = (description or "")[:6000]

    system = (
        "You help a candidate who targets DevOps, SRE, Linux/sysadmin, platform, "
        "and hands-on IT infrastructure roles (including Web3/crypto employers). "
        "Respond ONLY with valid JSON, no markdown."
    )
    user = json.dumps(
        {
            "task": "Rate fit for that candidate profile. Score 1-10 where 10 is ideal.",
            "title": title,
            "company": company,
            "description_excerpt": snippet,
            "schema": {
                "score": "integer 1-10",
                "fit": "one short sentence",
                "strengths": "array of 1-3 short strings",
                "concerns": "array of 0-3 short strings",
            },
        }
    )

    try:
        r = requests.post(
            OPENAI_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.3,
                "max_tokens": 400,
            },
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        text = data["choices"][0]["message"]["content"].strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        parsed = json.loads(text)
        sc = int(parsed.get("score", 0))
        parsed["score"] = max(1, min(10, sc))
        return parsed
    except Exception as e:
        logger.warning("Job agent evaluation failed: %s", e)
        return None
