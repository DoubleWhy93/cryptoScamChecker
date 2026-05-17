"""
Backend — FastAPI server wrapping the layer3 investigation agent.

Run from the project root:
    uvicorn server:app --reload --port 8000

Then open http://localhost:8000 in your browser.
"""

import os
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Load .env (no-op in Cloud Run where env vars are set directly)
_env = Path(__file__).parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from agent.layer3 import investigate, detect_chain, _get_address_summary, _score_address

app = FastAPI(title="CryptoScamChecker Demo API")

# job_id -> {status, stage, quick, result, error}
_jobs: dict[str, dict] = {}

# Human-readable stage names for each tool call
_STAGE_LABELS = {
    "get_address_summary": "Fetching address history",
    "get_outflows":        "Tracing outgoing funds",
    "get_inflows":         "Analyzing incoming sources",
    "score_address":       "Running risk assessment",
}


# ── Request model ─────────────────────────────────────────────────────────────

class ReviewRequest(BaseModel):
    address: str
    amount_usd: float = 15000.0
    token: str = "BTC"


# ── Layer 2: quick heuristic (no agent, just two API calls) ───────────────────

def _layer2(address: str, chain: str) -> dict:
    summary = _get_address_summary(address, chain)
    if "error" in summary:
        return {
            "risk_level": "UNKNOWN",
            "score": 0,
            "evidence": [f"Could not fetch address data: {summary['error']}"],
        }
    result = _score_address(address, chain)
    return {
        "risk_level": result.get("risk_level", "UNKNOWN"),
        "score":      result.get("score", 0),
        "evidence":   result.get("evidence", []),
        "tx_count":   summary.get("tx_count"),
        "first_seen_days_ago": summary.get("first_seen_days_ago"),
        "balance":    summary.get("balance"),
    }


# ── Layer 3: full agent investigation (background thread) ─────────────────────

def _run_investigation(job_id: str, address: str, amount_usd: float, token: str, chain: str, quick: dict):
    def on_stage(tool_name: str):
        _jobs[job_id]["stage"] = _STAGE_LABELS.get(tool_name, tool_name)

    _jobs[job_id].update({"status": "running", "stage": "Starting investigation"})
    try:
        result = investigate(
            address=address,
            amount_usd=amount_usd,
            token=token,
            account={"account_age_days": 30, "avg_tx_usd": amount_usd},
            layer2_result=quick,
            chain=chain,
            verbose=True,
            stage_callback=on_stage,
        )
        _jobs[job_id].update({"status": "complete", "result": result, "stage": "Complete"})
    except Exception as e:
        _jobs[job_id].update({"status": "error", "error": str(e), "stage": "Error"})


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.get("/healthz")
def healthz():
    """Health check endpoint for Cloud Run and local smoke tests."""
    return {"status": "ok"}


@app.post("/api/review")
def review(req: ReviewRequest):
    """
    Layer 1 + 2: validate address and run quick heuristic scoring.
    Kicks off Layer 3 agent investigation in the background if risk is MEDIUM+.
    Returns immediately with job_id and quick result.
    """
    address = req.address.strip()

    # Layer 1: chain detection
    chain = detect_chain(address)
    if chain == "unknown":
        return JSONResponse(
            {"error": "Cannot detect blockchain from this address. Supported: BTC (1.../3.../bc1...) and TRX (T...)."},
            status_code=400,
        )
    if chain == "eth":
        return JSONResponse({"error": "ETH is not yet supported in this demo."}, status_code=400)

    # Layer 2: quick heuristic (sync, takes ~3-8s)
    quick = _layer2(address, chain)

    job_id = str(uuid.uuid4())[:8].upper()
    needs_deep = quick["risk_level"] in ("MEDIUM", "HIGH", "CRITICAL", "UNKNOWN")

    _jobs[job_id] = {
        "status": "pending" if needs_deep else "complete",
        "stage":  "Queued"  if needs_deep else "Done",
        "quick":  quick,
        "result": None,
        "error":  None,
    }

    if needs_deep:
        # Layer 3: full agent — runs in background
        threading.Thread(
            target=_run_investigation,
            args=(job_id, address, req.amount_usd, req.token, chain, quick),
            daemon=True,
        ).start()
    else:
        # LOW — quick result is sufficient
        _jobs[job_id]["result"] = {
            "risk_level":   quick["risk_level"],
            "user_message": "This address looks normal. No unusual patterns were detected in its recent history.",
            "evidence":     quick.get("evidence", []),
            "suggestions":  ["This transfer appears safe to proceed."],
        }

    return {
        "job_id":        job_id,
        "chain":         chain.upper(),
        "quick":         quick,
        "investigating": needs_deep,
    }


@app.get("/api/result/{job_id}")
def get_result(job_id: str):
    """Poll this endpoint for the Layer 3 investigation status and result."""
    job = _jobs.get(job_id.upper())
    if not job:
        return JSONResponse({"status": "not_found"}, status_code=404)
    return {
        "status": job["status"],
        "stage":  job["stage"],
        "result": job.get("result"),
        "error":  job.get("error"),
    }


# ── Serve the frontend ────────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory=str(Path(__file__).parent / "static"), html=True), name="static")



if __name__ == "__main__":
    import uvicorn
    port = max(1, min(65535, int(os.environ.get("PORT", 8000))))
    uvicorn.run("server:app", host="0.0.0.0", port=port)
