"""Job sourcing (RapidAPI JSearch) and resume PDF parsing."""
from __future__ import annotations

import io
from typing import Any

import requests

from . import store

DEFAULT_QUERIES = [
    "software engineer remote",
    "full stack developer remote",
    "backend developer node.js remote",
    "react developer remote",
]


def get_queries() -> list[str]:
    raw = store.get_setting("search_queries")
    queries = [q.strip() for q in raw.split("\n") if q.strip()] if raw else []
    return queries or DEFAULT_QUERIES


def fetch_jobs() -> list[dict[str, Any]]:
    """Fetch fresh remote jobs for each configured query. Returns deduped list."""
    key = store.get_setting("JSEARCH_API_KEY")
    if not key:
        raise RuntimeError("No JSearch API key set. Add JSEARCH_API_KEY in Settings.")

    headers = {
        "X-RapidAPI-Key": key,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    }
    all_jobs: list[dict[str, Any]] = []
    seen: set[str] = set()

    for query in get_queries():
        try:
            resp = requests.get(
                "https://jsearch.p.rapidapi.com/search",
                headers=headers,
                params={
                    "query": query,
                    "page": "1",
                    "num_pages": "1",
                    "date_posted": "week",
                    "remote_jobs_only": "true",
                },
                timeout=20,
            )
            resp.raise_for_status()
            for job in resp.json().get("data", []):
                jid = job.get("job_id")
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                all_jobs.append({
                    "id": jid,
                    "title": job.get("job_title", ""),
                    "company": job.get("employer_name", ""),
                    "location": job.get("job_city") or "Remote",
                    "description": (job.get("job_description") or "")[:1500],
                    "apply_link": job.get("job_apply_link", ""),
                    "posted": job.get("job_posted_at_datetime_utc", ""),
                    "salary_min": job.get("job_min_salary"),
                    "salary_max": job.get("job_max_salary"),
                    "employment_type": job.get("job_employment_type", ""),
                    "query": query,
                })
        except Exception as e:  # noqa: BLE001 - report per-query, keep going
            print(f"[WARN] Query '{query}' failed: {e}")

    return all_jobs


def new_jobs_only(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = store.get_seen_ids()
    return [j for j in jobs if j["id"] not in seen]


def parse_resume_pdf(data: bytes) -> str:
    """Extract text from an uploaded resume PDF."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages: list[str] = []
    for page in reader.pages[:15]:
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            pages.append("")
    text = "\n".join(pages).replace("\x00", "").strip()
    return text[:30_000]
