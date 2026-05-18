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


# Mock customer history for the demo exchange account. In production this would
# come from the exchange ledger, not public chain data.
_TRUSTED_RECIPIENTS = {
    ("BTC", "BTC", "3LQUu4v9z6KNch71j7kbj8GPeAGUo1FW6a"): {
        "count": 8,
        "last_sent_days_ago": 12,
        "total_sent_usd": 42000,
        "nickname": "Saved Binance cold-storage recipient",
    },
    ("BTC", "BTC", "bc1qvy0sp8cdj3cv2wwh05scucxw6vxqpdlhfjvqn8"): {
        "count": 5,
        "last_sent_days_ago": 28,
        "total_sent_usd": 18000,
        "nickname": "Saved Kraken recipient",
    },
}


# ── Request model ─────────────────────────────────────────────────────────────

class ReviewRequest(BaseModel):
    address: str
    amount_usd: float = 15000.0
    token: str = "BTC"


# ── Layer 2: quick heuristic (no agent, just two API calls) ───────────────────

def _normalize_token(token: str) -> str:
    return token.strip().upper()


def _layer2(address: str, chain: str, token: str) -> dict:
    summary = _get_address_summary(address, chain, token)
    if "error" in summary:
        return {
            "risk_level": "UNKNOWN",
            "score": 0,
            "evidence": [f"Could not fetch address data: {summary['error']}"],
        }
    result = _score_address(address, chain, token)
    return {
        "risk_level": result.get("risk_level", "UNKNOWN"),
        "score":      result.get("score", 0),
        "evidence":   result.get("evidence", []),
        "asset":      summary.get("asset"),
        "tx_count":   summary.get("tx_count"),
        "first_seen_days_ago": summary.get("first_seen_days_ago"),
        "balance":    summary.get("balance"),
    }


def _recipient_history(address: str, chain: str, token: str) -> dict | None:
    return _TRUSTED_RECIPIENTS.get((chain.upper(), token, address))


def _trusted_recipient_policy(history: dict) -> dict:
    return {
        "decision": "pass",
        "reason": "trusted_repeat_recipient",
        "headline": "Ready to send",
        "message": (
            f"You have sent to this saved recipient {history['count']} times before, "
            f"most recently {history['last_sent_days_ago']} days ago. SwiftX did not find "
            "a reason to slow down this transfer."
        ),
        "details": [
            f"Saved recipient: {history['nickname']}",
            f"Prior sends: {history['count']} totaling about ${history['total_sent_usd']:,.0f}",
        ],
        "allow_release": True,
        "needs_agent": False,
    }


def _policy_check(address: str, chain: str, token: str, amount_usd: float, quick: dict) -> dict:
    history = _recipient_history(address, chain, token)
    if history and history["count"] >= 3:
        return _trusted_recipient_policy(history)

    tx_count = quick.get("tx_count")
    first_seen_days = quick.get("first_seen_days_ago")
    evidence = list(quick.get("evidence", []))

    if tx_count == 0:
        return {
            "decision": "warn",
            "reason": "no_public_history",
            "headline": "This recipient has no visible transfer history",
            "message": (
                "This may be a brand-new address. Make sure it belongs to the person or account "
                "you intended before sending the full amount. A small test transfer is recommended."
            ),
            "details": ["No prior public transfers were found for this recipient."],
            "allow_release": True,
            "needs_agent": False,
        }

    if first_seen_days is not None and first_seen_days < 14:
        return {
            "decision": "warn",
            "reason": "fresh_address",
            "headline": "This recipient address is very new",
            "message": (
                f"This address first appeared about {round(first_seen_days, 1)} days ago. "
                "Fresh addresses can be legitimate, but scammers often use new addresses to receive funds. "
                "Confirm it is yours or send a small test amount first."
            ),
            "details": [
                f"First seen: {round(first_seen_days, 1)} days ago",
                f"Observed transactions: {tx_count}",
            ] + evidence[:2],
            "allow_release": True,
            "needs_agent": quick.get("risk_level") in ("MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"),
        }

    if amount_usd >= 10000 and not history:
        return {
            "decision": "warn",
            "reason": "large_first_time_recipient",
            "headline": "Large transfer to a new recipient",
            "message": (
                "This is a large transfer to a recipient that is not in your saved history. "
                "Please verify the address through a trusted channel before releasing funds."
            ),
            "details": [
                f"Transfer amount: ${amount_usd:,.0f}",
                "No prior SwiftX sends to this recipient were found.",
            ],
            "allow_release": True,
            "needs_agent": quick.get("risk_level") in ("MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"),
        }

    return {
        "decision": "pass",
        "reason": "standard_check_passed",
        "headline": "Ready to send",
        "message": "SwiftX completed the check and did not find a reason to slow down this transfer.",
        "details": evidence[:2],
        "allow_release": True,
        "needs_agent": quick.get("risk_level") in ("MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"),
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
    token = _normalize_token(req.token)

    # Layer 1: chain detection
    chain = detect_chain(address)
    if chain == "unknown":
        return JSONResponse(
            {"error": "Cannot detect blockchain from this address. Supported: BTC (1.../3.../bc1...) and TRX (T...)."},
            status_code=400,
        )
    if chain == "eth":
        return JSONResponse({"error": "ETH is not yet supported in this demo."}, status_code=400)
    if token == "BTC" and chain != "btc":
        return JSONResponse({"error": "BTC transfers require a Bitcoin address."}, status_code=400)
    if token == "TRX" and chain != "trx":
        return JSONResponse({"error": "TRX transfers require a TRON address that starts with T."}, status_code=400)
    if token == "USDT" and chain != "trx":
        return JSONResponse({"error": "USDT is supported as TRC20 in this demo. Use a TRON address that starts with T."}, status_code=400)
    if token not in ("BTC", "TRX", "USDT"):
        return JSONResponse({"error": "Unsupported asset. Supported: BTC, TRX, and USDT-TRC20."}, status_code=400)

    history = _recipient_history(address, chain, token)
    if history and history["count"] >= 3:
        policy = _trusted_recipient_policy(history)
        quick = {
            "risk_level": "CLEAN",
            "score": 0,
            "evidence": policy["details"],
            "asset": token,
            "tx_count": None,
            "first_seen_days_ago": None,
            "balance": None,
        }
        job_id = str(uuid.uuid4())[:8].upper()
        _jobs[job_id] = {
            "status": "complete",
            "stage": "Done",
            "quick": quick,
            "policy": policy,
            "result": {
                "risk_level": "CLEAN",
                "headline": policy["headline"],
                "user_message": policy["message"],
                "detailed_findings": policy["details"],
                "evidence": policy["details"],
                "suggestions": ["Verify the recipient details before releasing the transfer."],
            },
            "error": None,
        }
        return {
            "job_id": job_id,
            "chain": chain.upper(),
            "asset": token,
            "quick": quick,
            "policy": policy,
            "investigating": False,
        }

    # Layer 2: quick heuristic (sync, takes ~3-8s)
    quick = _layer2(address, chain, token)
    policy = _policy_check(address, chain, token, req.amount_usd, quick)

    job_id = str(uuid.uuid4())[:8].upper()
    needs_deep = policy["needs_agent"]

    _jobs[job_id] = {
        "status": "pending" if needs_deep else "complete",
        "stage":  "Queued"  if needs_deep else "Done",
        "quick":  quick,
        "policy": policy,
        "result": None,
        "error":  None,
    }

    if needs_deep:
        # Layer 3: full agent — runs in background
        threading.Thread(
            target=_run_investigation,
            args=(job_id, address, req.amount_usd, token, chain, quick),
            daemon=True,
        ).start()
    else:
        # LOW — quick result is sufficient
        _jobs[job_id]["result"] = {
            "risk_level":   quick["risk_level"],
            "headline":     policy["headline"],
            "user_message": policy["message"],
            "detailed_findings": policy.get("details", []),
            "evidence":     policy.get("details", quick.get("evidence", [])),
            "suggestions":  ["Verify the recipient details before releasing the transfer."],
        }

    return {
        "job_id":        job_id,
        "chain":         chain.upper(),
        "asset":         quick.get("asset") or token,
        "quick":         quick,
        "policy":        policy,
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
