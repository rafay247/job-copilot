"""AI layer for Job Copilot — one swappable module over multiple providers.

Paste *any* API key and Job Copilot detects the provider automatically:

  - Google Gemini     (keys starting with `AIza...`)
  - Anthropic Claude  (keys starting with `sk-ant-...`)
  - OpenAI            (keys starting with `sk-...`)
  - DeepSeek          (`sk-...`, selected via the provider override)

Every model call goes through `_chat()`, so switching or adding a provider is a
one-file change. No model has to be configured — each provider ships a sensible
default. All calls use plain HTTP (`requests`) to stay dependency-light.
"""
from __future__ import annotations

import json
from typing import Any

import requests

from . import store

# Per-provider defaults: endpoint style + a good, inexpensive default model.
PROVIDERS: dict[str, dict[str, str]] = {
    "gemini": {"model": "gemini-2.5-flash"},
    "openai": {"model": "gpt-4o-mini", "base": "https://api.openai.com/v1"},
    "deepseek": {"model": "deepseek-chat", "base": "https://api.deepseek.com/v1"},
    "anthropic": {"model": "claude-opus-4-8"},
}

PROVIDER_LABELS = {
    "gemini": "Google Gemini",
    "openai": "OpenAI",
    "deepseek": "DeepSeek",
    "anthropic": "Anthropic Claude",
}


class AIError(RuntimeError):
    """Raised when the AI provider is not configured or the call fails."""


def _api_key() -> str:
    # AI_API_KEY is the new generic key; GOOGLE_API_KEY kept for back-compat.
    return store.get_setting("AI_API_KEY") or store.get_setting("GOOGLE_API_KEY")


def detect_provider(key: str = "", override: str = "") -> str:
    """Figure out which provider a key belongs to.

    An explicit override (from Settings) always wins — needed to tell OpenAI and
    DeepSeek apart, since both use `sk-...` keys.
    """
    override = (override or store.get_setting("AI_PROVIDER") or "auto").lower()
    if override in PROVIDERS:
        return override

    key = key or _api_key()
    if key.startswith("sk-ant-"):
        return "anthropic"
    if key.startswith("AIza"):
        return "gemini"
    if key.startswith("sk-"):
        return "openai"
    return "gemini"


def provider_label(key: str = "") -> str:
    return PROVIDER_LABELS.get(detect_provider(key), "Unknown")


def _model(provider: str) -> str:
    return store.get_setting("AI_MODEL") or PROVIDERS[provider]["model"]


# ── Provider dispatch ────────────────────────────────────────────────────────
def _chat(prompt: str) -> str:
    key = _api_key()
    if not key:
        raise AIError("No AI API key set. Paste any Gemini / OpenAI / Anthropic / DeepSeek key in Settings.")

    provider = detect_provider(key)
    try:
        if provider == "gemini":
            return _gemini(key, prompt)
        if provider == "anthropic":
            return _anthropic(key, prompt)
        return _openai_compatible(provider, key, prompt)  # openai + deepseek
    except AIError:
        raise
    except requests.HTTPError as e:
        body = e.response.text[:300] if e.response is not None else ""
        raise AIError(f"{PROVIDER_LABELS[provider]} request failed: {e} {body}") from e
    except Exception as e:  # noqa: BLE001
        raise AIError(f"{PROVIDER_LABELS[provider]} request failed: {e}") from e


def _gemini(key: str, prompt: str) -> str:
    model = _model("gemini")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    resp = requests.post(
        url,
        params={"key": key},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _openai_compatible(provider: str, key: str, prompt: str) -> str:
    base = PROVIDERS[provider]["base"]
    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": _model(provider),
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _anthropic(key: str, prompt: str) -> str:
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": _model("anthropic"),
            "max_tokens": 1500,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    blocks = resp.json().get("content", [])
    for block in blocks:
        if block.get("type") == "text":
            return block.get("text", "")
    return ""


def _parse_json(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def profile_text() -> str:
    return store.get_setting("candidate_profile") or store.get_setting("resume_text") or ""


# ── 1. Scout scoring ─────────────────────────────────────────────────────────
_SCORE_PROMPT = """You are a job-fit scoring assistant. Score how well the job fits the candidate.

CANDIDATE PROFILE:
{profile}

JOB POSTING:
Title: {title}
Company: {company}
Location: {location}
Employment Type: {employment_type}
Description:
{description}

Respond ONLY with a JSON object in this exact format (no markdown, no prose):
{{
  "score": <integer 0-100>,
  "match_reasons": "<2-3 sentence explanation of fit>",
  "key_matches": ["skill1", "skill2", "skill3"],
  "red_flags": "<concerns or deal-breakers, or 'None'>"
}}"""


def score_job(job: dict[str, Any]) -> dict[str, Any]:
    prompt = _SCORE_PROMPT.format(
        profile=profile_text() or "(No profile provided — score on general remote software roles.)",
        title=job.get("title", ""),
        company=job.get("company", ""),
        location=job.get("location", ""),
        employment_type=job.get("employment_type", ""),
        description=job.get("description", ""),
    )
    try:
        result = _parse_json(_chat(prompt))
        return {
            "score": int(result.get("score", 0)),
            "match_reasons": str(result.get("match_reasons", "")),
            "key_matches": list(result.get("key_matches", []))[:6],
            "red_flags": str(result.get("red_flags", "None")),
        }
    except (AIError, json.JSONDecodeError, ValueError, TypeError) as e:
        return {
            "score": 0,
            "match_reasons": f"Scoring unavailable ({e})",
            "key_matches": [],
            "red_flags": "N/A",
        }


# ── 2. Resume Tailor ─────────────────────────────────────────────────────────
_TAILOR_PROMPT = """You are an expert technical resume writer. Using the candidate's
background, tailor their materials to the target job. Be truthful — only use
skills/experience present in the profile; never invent employers or credentials.

CANDIDATE PROFILE:
{profile}

TARGET JOB DESCRIPTION:
{job_description}

Respond ONLY with a JSON object in this exact format (no markdown, no prose):
{{
  "headline": "<one-line professional headline tuned to this role>",
  "summary": "<2-3 sentence professional summary tuned to this role>",
  "bullets": ["<achievement bullet reworded for this role>", "... 4-6 bullets total"],
  "highlight_skills": ["<skill>", "... 6-10 skills the job cares about that the candidate has"],
  "cover_letter": "<a concise, warm 3-paragraph cover letter addressed to the hiring team>"
}}"""


def tailor_resume(job_description: str) -> dict[str, Any]:
    if not profile_text():
        raise AIError("Add your resume/profile in Settings first so I can tailor it.")
    prompt = _TAILOR_PROMPT.format(profile=profile_text(), job_description=job_description)
    result = _parse_json(_chat(prompt))
    return {
        "headline": str(result.get("headline", "")),
        "summary": str(result.get("summary", "")),
        "bullets": [str(b) for b in result.get("bullets", [])],
        "highlight_skills": [str(s) for s in result.get("highlight_skills", [])],
        "cover_letter": str(result.get("cover_letter", "")),
    }


# ── 3. Interview Prep Coach ──────────────────────────────────────────────────
_COACH_PROMPT = """You are an interview coach. Given a target role and the candidate's
background, produce likely interview questions AND a strong model answer for each,
grounded in the candidate's real projects and experience.

CANDIDATE PROFILE:
{profile}

TARGET ROLE: {role}
COMPANY: {company}
JOB DESCRIPTION (optional):
{job_description}

Produce a mix of technical, system-design, and behavioral questions.
Respond ONLY with a JSON object in this exact format (no markdown, no prose):
{{
  "questions": [
    {{
      "question": "<the interview question>",
      "category": "<Technical | System Design | Behavioral | AI/LLM>",
      "answer": "<a strong 3-5 sentence model answer that draws on the candidate's real projects>"
    }}
    // 6-8 questions total
  ]
}}"""


def interview_prep(role: str, company: str = "", job_description: str = "") -> dict[str, Any]:
    if not profile_text():
        raise AIError("Add your resume/profile in Settings first so answers use your background.")
    prompt = _COACH_PROMPT.format(
        profile=profile_text(),
        role=role or "Software Engineer",
        company=company or "(unspecified)",
        job_description=job_description or "(none provided)",
    )
    result = _parse_json(_chat(prompt))
    questions = []
    for q in result.get("questions", []):
        questions.append({
            "question": str(q.get("question", "")),
            "category": str(q.get("category", "General")),
            "answer": str(q.get("answer", "")),
        })
    return {"questions": questions}
