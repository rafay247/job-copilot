"""Job Copilot — single local FastAPI app serving the SPA + JSON API.

Run:  uvicorn app:app --reload --port 8787   (then open http://localhost:8787)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from copilot import ai, jobs, store

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"

# Non-secret config keys the Settings screen can read back verbatim.
PUBLIC_SETTINGS = ["search_queries", "candidate_profile", "AI_PROVIDER", "min_score"]
# Secret keys — never returned to the browser, only a "configured" boolean.
SECRET_SETTINGS = ["AI_API_KEY", "JSEARCH_API_KEY"]

app = FastAPI(title="Job Copilot", version="2.0.0")

store.init_db()


# ── API: settings ────────────────────────────────────────────────────────────
@app.get("/api/settings")
def read_settings() -> dict[str, Any]:
    data = {k: store.get_setting(k) for k in PUBLIC_SETTINGS}
    data["configured"] = {k: bool(store.get_setting(k)) for k in SECRET_SETTINGS}
    data["has_profile"] = bool(ai.profile_text())
    data["detected_provider"] = ai.provider_label() if store.get_setting("AI_API_KEY") else ""
    return data


@app.post("/api/settings")
async def write_settings(payload: dict[str, Any]) -> dict[str, Any]:
    for key in [*PUBLIC_SETTINGS, *SECRET_SETTINGS]:
        if key in payload and payload[key] is not None:
            # Blank secret fields mean "leave as-is" (UI never sends the stored value back).
            if key in SECRET_SETTINGS and str(payload[key]).strip() == "":
                continue
            store.set_setting(key, str(payload[key]))
    return {"ok": True}


@app.post("/api/resume")
async def upload_resume(file: UploadFile = File(...)) -> dict[str, Any]:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file.")
    try:
        text = jobs.parse_resume_pdf(data)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not read PDF: {e}") from e
    if not text:
        raise HTTPException(400, "No text found in PDF (it may be scanned).")
    store.set_setting("resume_text", text)
    if not store.get_setting("candidate_profile"):
        store.set_setting("candidate_profile", text)
    return {"ok": True, "chars": len(text), "text": text}


# ── API: scout ───────────────────────────────────────────────────────────────
@app.post("/api/scout/refresh")
def scout_refresh() -> dict[str, Any]:
    try:
        raw = jobs.fetch_jobs()
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e

    fresh = jobs.new_jobs_only(raw)
    for job in fresh:
        job.update(ai.score_job(job))
        store.upsert_job(job)

    return {
        "ok": True,
        "fetched": len(raw),
        "new": len(fresh),
        "counts": store.counts(),
    }


@app.get("/api/jobs")
def api_jobs(min_score: int = 0, q: str = "", status: str = "new,saved,applied,interview,offer") -> dict[str, Any]:
    statuses = [s for s in status.split(",") if s]
    return {"jobs": store.list_jobs(min_score=min_score, search=q, statuses=statuses)}


@app.post("/api/jobs/{job_id}/status")
def api_job_status(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    status = str(payload.get("status", ""))
    if not store.set_job_status(job_id, status):
        raise HTTPException(400, "Invalid job id or status.")
    return {"ok": True, "counts": store.counts()}


@app.get("/api/tracker")
def api_tracker() -> dict[str, Any]:
    return {"board": store.tracker_board(), "counts": store.counts()}


# ── API: tailor + coach ──────────────────────────────────────────────────────
@app.post("/api/tailor")
def api_tailor(payload: dict[str, Any]) -> dict[str, Any]:
    jd = str(payload.get("job_description", "")).strip()
    if not jd:
        raise HTTPException(400, "Paste a job description first.")
    try:
        return ai.tailor_resume(jd)
    except ai.AIError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/coach")
def api_coach(payload: dict[str, Any]) -> dict[str, Any]:
    role = str(payload.get("role", "")).strip()
    if not role:
        raise HTTPException(400, "Enter a target role first.")
    try:
        return ai.interview_prep(
            role=role,
            company=str(payload.get("company", "")),
            job_description=str(payload.get("job_description", "")),
        )
    except ai.AIError as e:
        raise HTTPException(400, str(e)) from e


@app.exception_handler(ai.AIError)
async def ai_error_handler(_request, exc: ai.AIError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ── Static SPA ───────────────────────────────────────────────────────────────
app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")
