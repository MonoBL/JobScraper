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
import time
from dataclasses import dataclass
from datetime import datetime, timezone
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


def _llm_headers() -> Dict[str, str]:
    key = _api_key()
    headers: Dict[str, str] = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if "openrouter.ai" in _base_url():
        ref = os.getenv("OPENROUTER_HTTP_REFERER", "").strip()
        if ref:
            headers["Referer"] = ref
        headers["X-Title"] = os.getenv("OPENROUTER_APP_TITLE", "Job Scraper Dashboard")
    return headers


def is_agent_configured() -> bool:
    return bool(_api_key())


def _using_openrouter() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY", "").strip())


# Defaults when no AGENT_*_MODEL is set — OpenRouter uses provider/model slugs; OpenAI.com uses their model names.
# SIMPLIFIED — single "fit" agent; critique/checklist removed.
_DEFAULT_MODELS_OPENROUTER: Dict[str, str] = {
    "fit": "google/gemma-4-26b-a4b-it:free",
}
_DEFAULT_MODELS_OPENAI_COM: Dict[str, str] = {
    "fit": "gpt-4o-mini",
}


def _default_model_for_agent(agent_id: str) -> str:
    if _using_openrouter():
        return _DEFAULT_MODELS_OPENROUTER.get(
            agent_id, "google/gemma-4-26b-a4b-it:free"
        )
    return _DEFAULT_MODELS_OPENAI_COM.get(agent_id, "gpt-4o-mini")


@dataclass(frozen=True)
class AgentSpec:
    id: str
    label: str
    description: str
    system_prompt: str
    response_hint: str


# SIMPLIFIED — only the fit agent remains.
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


def _agent_env_key(agent_id: str) -> str:
    return f"AGENT_{agent_id.upper().replace('-', '_')}_MODEL"


def get_llm_diagnostics() -> Dict[str, Any]:
    """Provider, API base URL, resolved model per agent + resume-profile agent (no network I/O)."""
    if not is_agent_configured():
        return {
            "configured": False,
            "provider": None,
            "base_url": None,
            "agents": [],
            "resume_profile": None,
        }
    provider = "openrouter" if _using_openrouter() else "openai"
    agents: List[Dict[str, Any]] = []
    for spec in _builtin_agents():
        env_key = _agent_env_key(spec.id)
        env_val = os.getenv(env_key, "").strip()
        agents.append(
            {
                "id": spec.id,
                "label": spec.label,
                "model": _agent_env_model(spec.id),
                "model_source": "env" if env_val else "default",
                "env_key": env_key,
            }
        )
    resume_env = os.getenv("AGENT_RESUME_PROFILE_MODEL", "").strip()
    return {
        "configured": True,
        "provider": provider,
        "base_url": _base_url(),
        "agents": agents,
        "resume_profile": {
            "id": "resume_profile",
            "label": "Resume → ranker review",
            "model": _resume_profile_model(),
            "model_source": "env" if resume_env else "default",
            "env_key": "AGENT_RESUME_PROFILE_MODEL",
        },
    }


def test_llm_agent_models() -> Dict[str, Any]:
    """
    Run a tiny completion for each job-card agent model plus the resume-profile model.
    Verifies API key, base URL, and that each model id accepts requests.
    """
    if not is_agent_configured():
        return {
            "configured": False,
            "overall_ok": False,
            "tested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "results": [],
            "error": "No OPENROUTER_API_KEY or OPENAI_API_KEY",
        }

    headers = _llm_headers()
    results: List[Dict[str, Any]] = []

    def ping_one(name: str, agent_id: str, model: str) -> Dict[str, Any]:
        t0 = time.perf_counter()
        try:
            r = requests.post(
                _chat_completions_url(),
                headers=headers,
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Reply with exactly the word OK and nothing else.",
                        },
                        {"role": "user", "content": "ping"},
                    ],
                    "max_tokens": 16,
                    "temperature": 0,
                },
                timeout=75,
            )
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            if r.status_code != 200:
                err_body = r.text[:400] if r.text else ""
                return {
                    "id": agent_id,
                    "name": name,
                    "model": model,
                    "ok": False,
                    "latency_ms": elapsed_ms,
                    "error": f"HTTP {r.status_code}: {err_body}",
                }
            data = r.json()
            content = (
                (data.get("choices") or [{}])[0]
                .get("message", {})
                .get("content", "")
                or ""
            )
            preview = content.strip()[:120]
            return {
                "id": agent_id,
                "name": name,
                "model": model,
                "ok": True,
                "latency_ms": elapsed_ms,
                "response_preview": preview,
            }
        except Exception as e:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            return {
                "id": agent_id,
                "name": name,
                "model": model,
                "ok": False,
                "latency_ms": elapsed_ms,
                "error": str(e)[:400],
            }

    for spec in _builtin_agents():
        results.append(ping_one(spec.label, spec.id, _agent_env_model(spec.id)))
    results.append(
        ping_one(
            "Resume → ranker review",
            "resume_profile",
            _resume_profile_model(),
        )
    )

    overall_ok = all(x.get("ok") for x in results)
    return {
        "configured": True,
        "overall_ok": overall_ok,
        "tested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "results": results,
    }


def _spec_by_id(agent_id: str) -> Optional[AgentSpec]:
    aid = agent_id.strip().lower()
    for s in _builtin_agents():
        if s.id == aid:
            return s
    return None


def _parse_json_response(text: str) -> Dict[str, Any]:
    t = text.strip()
    # Strip Qwen-style <think>…</think> reasoning blocks
    t = re.sub(r"<think>[\s\S]*?</think>", "", t).strip()
    # Strip markdown code fences
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t).strip()
    # Last resort: find first { … } in the response
    if not t.startswith("{"):
        m = re.search(r"\{[\s\S]*\}", t)
        if m:
            t = m.group(0)
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

    headers = _llm_headers()
    payload = {
        "model": resolved_model,
        "messages": [
            {"role": "system", "content": spec.system_prompt},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
        "max_tokens": 600,
    }

    # Retry on 429 (rate limit) with exponential backoff
    max_retries = 3
    last_error: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            r = requests.post(
                _chat_completions_url(),
                headers=headers,
                json=payload,
                timeout=90,
            )
            if r.status_code == 429 and attempt < max_retries - 1:
                wait = int(r.headers.get("retry-after", 2 ** (attempt + 1)))
                logger.info("Agent %s rate-limited (429), retrying in %ds…", agent_id, wait)
                time.sleep(min(wait, 30))
                continue
            r.raise_for_status()
            data = r.json()
            text = data["choices"][0]["message"]["content"].strip()
            parsed = _parse_json_response(text)
            if spec.id == "fit" and "score" in parsed:
                sc = int(parsed.get("score", 0))
                parsed["score"] = max(1, min(10, sc))
            return parsed
        except requests.HTTPError as e:
            body = ""
            try:
                err_json = e.response.json()
                body = err_json.get("error", {}).get("message", "") or e.response.text[:300]
            except Exception:
                body = (e.response.text or "")[:300] if e.response is not None else ""
            status = e.response.status_code if e.response is not None else "?"
            detail = f"HTTP {status} from {resolved_model}: {body}" if body else f"HTTP {status} from {resolved_model}"
            logger.warning("Agent %s HTTP error (model=%s): %s — %s", agent_id, resolved_model, status, body)
            last_error = RuntimeError(detail)
            last_error.__cause__ = e
            if status == 429 and attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            raise last_error from e
        except json.JSONDecodeError as e:
            logger.warning("Agent %s: model returned non-JSON (model=%s): %s", agent_id, resolved_model, e)
            raise RuntimeError(f"Model {resolved_model} returned invalid JSON — try a different model") from e
        except Exception as e:
            logger.warning("Agent %s evaluation failed (model=%s): %s", agent_id, resolved_model, e)
            raise

    if last_error:
        raise last_error
    raise RuntimeError(f"Agent {agent_id} failed after {max_retries} retries")


def auto_evaluate_jobs(
    jobs: List[Any],
    agent_id: str = "fit",
    max_jobs: int = 15,
) -> Dict[str, Any]:
    """
    Run agent_id on up to max_jobs jobs (sync, uses thread pool internally).
    jobs: list of objects with .title, .company, .description, .url attributes OR dicts.
    Returns {url: result_dict} for successful evals.
    """
    import concurrent.futures

    if not is_agent_configured():
        return {}

    def _get(j: Any, field: str, default: str = "") -> str:
        if isinstance(j, dict):
            return j.get(field) or default
        return getattr(j, field, None) or default

    subset = list(jobs)[:max_jobs]

    def _eval_one(j: Any) -> tuple[str, Optional[Dict[str, Any]]]:
        url = _get(j, "url")
        result = evaluate_job(
            _get(j, "title"),
            _get(j, "company"),
            _get(j, "description"),
            agent_id=agent_id,
        )
        return url, result

    results: Dict[str, Any] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        for url, result in ex.map(_eval_one, subset):
            if result and url:
                results[url] = result

    return results


def _resume_profile_model() -> str:
    o = os.getenv("AGENT_RESUME_PROFILE_MODEL", "").strip()
    if o:
        return o
    return _default_model_for_agent("fit")


def review_resume_for_search_profile(resume_text: str) -> Optional[Dict[str, Any]]:
    """
    Compare resume text to built-in JobRanker baselines; return JSON with *_add lists
    to merge into ranker_overrides.json. None if API missing, resume too short, or LLM error.
    """
    key = _api_key()
    if not key:
        return None

    text = (resume_text or "").strip()
    if len(text) < 40:
        return None

    from ranker_profile import baseline_for_llm

    baseline = baseline_for_llm()
    system = (
        "You align a candidate's resume with an automated job-ranking system used by a Python scraper. "
        "Jobs are scored using substring matches on job title and description against phrase lists "
        "(perfect/good titles, keyword groups, optional blacklist title phrases). "
        "Suggest ONLY concise phrases to ADD — lowercase where possible, 2–8 words for titles. "
        "Do not duplicate items already listed in baseline_rules. "
        "Derive suggestions from the resume: technologies, certifications, role titles, domains. "
        "For blacklist_*_add, only add job TITLE phrases the user should not be matched to. "
        "Cruise fields apply to ship/maritime IT jobs only. "
        "Respond ONLY with valid JSON, no markdown or code fences."
    )
    hint = (
        '{"summary":"one paragraph for the user","perfect_titles_add":[],"good_titles_add":[],'
        '"perfect_keywords_add":{"linux":[],"scripting":[],"infrastructure":[],"automation":[]},'
        '"good_keywords_add":[],"strong_title_phrases_add":[],"blacklist_titles_add":[],"blacklist_keywords_title_add":[],'
        '"cruise_perfect_titles_add":[],"cruise_good_titles_add":[],"cruise_it_keywords_add":[],'
        '"notes_for_search":"short technical note"}'
    )
    user = json.dumps(
        {
            "task": f"Output JSON matching this shape exactly: {hint}",
            "baseline_rules": baseline,
            "resume_text": text[:12000],
        },
        ensure_ascii=False,
    )

    headers = _llm_headers()

    resolved_model = _resume_profile_model()
    try:
        r = requests.post(
            _chat_completions_url(),
            headers=headers,
            json={
                "model": resolved_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.15,
                "max_tokens": 2500,
            },
            timeout=120,
        )
        r.raise_for_status()
        data = r.json()
        raw = data["choices"][0]["message"]["content"].strip()
        return _parse_json_response(raw)
    except Exception as e:
        logger.warning("Resume profile review failed: %s", e)
        return None
