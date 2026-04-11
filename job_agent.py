"""
Multi-agent LLM helpers for the job dashboard.

Uses one API key and base URL (OpenAI or OpenRouter — OpenAI-compatible chat completions).

Default models differ per agent (e.g. fast scorer vs. stronger critic). With OpenRouter you
get cross-provider IDs like anthropic/claude-3.5-haiku; with api.openai.com use short names
(gpt-4o-mini). Override any agent with AGENT_<ID>_MODEL (ID uppercased: AGENT_FIT_MODEL, …).
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


def _api_key() -> str:
    return (
        os.getenv("OPENROUTER_API_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )


def _base_url() -> str:
    if os.getenv("OPENROUTER_API_KEY", "").strip():
        return os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    return os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")


def _chat_completions_url() -> str:
    return f"{_base_url()}/chat/completions"


def is_agent_configured() -> bool:
    return bool(_api_key())


def _using_openrouter() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY", "").strip())


# Defaults when no AGENT_*_MODEL is set — OpenRouter uses provider/model slugs; OpenAI.com uses their model names.
# OpenRouter defaults (free-tier slugs; override with AGENT_*_MODEL if a slug changes)
_DEFAULT_MODELS_OPENROUTER: Dict[str, str] = {
    "fit": "qwen/qwen3-next-80b-a3b-instruct:free",
    "critique": "meta-llama/llama-3.3-70b-instruct:free",
    "checklist": "google/gemma-4-26b-a4b-it:free",
}
_DEFAULT_MODELS_OPENAI_COM: Dict[str, str] = {
    "fit": "gpt-4o-mini",
    "critique": "gpt-4o",
    "checklist": "gpt-4o-mini",
}


def _default_model_for_agent(agent_id: str) -> str:
    if _using_openrouter():
        return _DEFAULT_MODELS_OPENROUTER.get(
            agent_id, "qwen/qwen3-next-80b-a3b-instruct:free"
        )
    return _DEFAULT_MODELS_OPENAI_COM.get(agent_id, "gpt-4o-mini")


@dataclass(frozen=True)
class AgentSpec:
    id: str
    label: str
    description: str
    system_prompt: str
    response_hint: str


def _builtin_agents() -> List[AgentSpec]:
    """Built-in agents; resolved model = env AGENT_<ID>_MODEL or provider-specific defaults."""
    return [
        AgentSpec(
            id="fit",
            label="Role fit",
            description="Scores 1-10 vs DevOps / SRE / platform / infra targets (incl. Web3).",
            system_prompt=(
                "You help a candidate who targets DevOps, SRE, Linux/sysadmin, platform, "
                "and hands-on IT infrastructure roles (including Web3/crypto employers). "
                "Respond ONLY with valid JSON, no markdown or code fences."
            ),
            response_hint=(
                '{"score": 1-10, "fit": "one short sentence", '
                '"strengths": ["..."], "concerns": ["..."]}'
            ),
        ),
        AgentSpec(
            id="critique",
            label="Posting critique",
            description="Skeptical read: hype, vague scope, red flags in the listing.",
            system_prompt=(
                "You critically read job postings for inflated titles, vague responsibilities, "
                "unrealistic stacks, or mismatch with infra/DevOps work. "
                "Respond ONLY with valid JSON, no markdown."
            ),
            response_hint=(
                '{"summary": "one sentence", "suspicious_or_vague": ["..."], '
                '"possible_red_flags": ["..."], "things_to_verify": ["..."]}'
            ),
        ),
        AgentSpec(
            id="checklist",
            label="Before you apply",
            description="Concrete questions and prep before spending time on this role.",
            system_prompt=(
                "You help a candidate decide whether to apply and what to clarify with the employer. "
                "Respond ONLY with valid JSON, no markdown."
            ),
            response_hint=(
                '{"recommendation": "apply|maybe|pass", "confidence_1_to_10": 7, '
                '"questions_to_ask": ["..."], "prep_notes": "short paragraph"}'
            ),
        ),
    ]


def _agent_env_model(agent_id: str) -> str:
    env_key = f"AGENT_{agent_id.upper().replace('-', '_')}_MODEL"
    override = os.getenv(env_key, "").strip()
    if override:
        return override
    return _default_model_for_agent(agent_id)


def list_agents() -> List[Dict[str, Any]]:
    """Metadata for the UI (no secrets)."""
    out: List[Dict[str, Any]] = []
    for spec in _builtin_agents():
        model = _agent_env_model(spec.id)
        out.append(
            {
                "id": spec.id,
                "label": spec.label,
                "description": spec.description,
                "model": model,
            }
        )
    return out


def _spec_by_id(agent_id: str) -> Optional[AgentSpec]:
    aid = agent_id.strip().lower()
    for s in _builtin_agents():
        if s.id == aid:
            return s
    return None


def _parse_json_response(text: str) -> Dict[str, Any]:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t).strip()
    return json.loads(t)


def evaluate_job(
    title: str,
    company: str,
    description: str,
    *,
    agent_id: str = "fit",
    model_override: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Run one agent. Returns parsed JSON dict from the model, or None on failure / misconfiguration.
    """
    key = _api_key()
    if not key:
        return None

    spec = _spec_by_id(agent_id)
    if spec is None:
        logger.warning("Unknown agent_id: %s", agent_id)
        return None

    resolved_model = model_override or _agent_env_model(spec.id)
    snippet = (description or "")[:6000]

    user = json.dumps(
        {
            "task": f"Follow your role and output JSON matching this shape: {spec.response_hint}",
            "title": title,
            "company": company,
            "description_excerpt": snippet,
        },
        ensure_ascii=False,
    )

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    # OpenRouter optional attribution (helps with rankings on their side)
    if "openrouter.ai" in _base_url():
        ref = os.getenv("OPENROUTER_HTTP_REFERER", "").strip()
        if ref:
            headers["Referer"] = ref
        headers["X-Title"] = os.getenv("OPENROUTER_APP_TITLE", "Job Scraper Dashboard")

    try:
        r = requests.post(
            _chat_completions_url(),
            headers=headers,
            json={
                "model": resolved_model,
                "messages": [
                    {"role": "system", "content": spec.system_prompt},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.3,
                "max_tokens": 600,
            },
            timeout=90,
        )
        r.raise_for_status()
        data = r.json()
        text = data["choices"][0]["message"]["content"].strip()
        parsed = _parse_json_response(text)
        if spec.id == "fit" and "score" in parsed:
            sc = int(parsed.get("score", 0))
            parsed["score"] = max(1, min(10, sc))
        return parsed
    except Exception as e:
        logger.warning("Agent %s evaluation failed: %s", agent_id, e)
        return None
