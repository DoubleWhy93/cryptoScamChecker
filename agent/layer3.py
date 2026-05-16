"""
Layer 3 — Gemma-powered money flow investigation agent.

Uses google-genai (the current SDK) with automatic function calling.
The agent receives 3 tools, decides which addresses to investigate,
calls them in a loop, then outputs a structured warning.

Tools available to the agent:
  get_address_summary  — relay ratio, balance, tx count, funnel/spike/burst
  trace_recipients     — top addresses this wallet forwarded money to
  score_recipient      — run behavioral scoring 1 hop deeper

Set GOOGLE_API_KEY in your environment before running.
Control the model via GEMMA_MODEL env var (default: gemma-3-27b-it).
Gemini 2.0 Flash has better tool-use reliability: set GEMMA_MODEL=gemini-2.0-flash
"""

import json
import os
import sys
from pathlib import Path

from google import genai
from google.genai import types

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from score_address import extract_features, score, fetch_first_page

SATOSHI = 1e-8

DEFAULT_MODEL = "gemma-3-27b-it"   # swap to "gemini-2.0-flash" for best tool-use


# ── Core tool logic (pure Python, no SDK dependency) ─────────────────────────

def _get_address_summary(address: str) -> dict:
    features = extract_features(address, check_counterparties=False)
    if "error" in features:
        return {"error": features["error"]}
    return {
        "tx_count":          features["tx_count"],
        "balance_btc":       features["balance_btc"],
        "relay_ratio":       round(features["relay_ratio"], 4),
        "funnel_ratio":      round(features["funnel_ratio"], 2),
        "n_senders_seen":    features["n_senders_seen"],
        "n_recipients_seen": features["n_recipients_seen"],
        "spike_ratio":       round(features["spike_ratio"], 2),
        "burst_days":        features["burst_days"],
        "max_recv_btc":      features["max_recv_btc"],
        "total_in_btc":      features["total_in_btc"],
        "total_out_btc":     features["total_out_btc"],
    }


def _trace_recipients(address: str) -> dict:
    txs = fetch_first_page(address)
    if not txs:
        return {"top_recipients": [], "note": "no transactions found"}

    totals: dict[str, float] = {}
    for tx in txs:
        vin_from_us = sum(
            (i.get("prevout") or {}).get("value", 0)
            for i in tx.get("vin", [])
            if (i.get("prevout") or {}).get("scriptpubkey_address") == address
        )
        if vin_from_us > 0:
            for out in tx.get("vout", []):
                ra = out.get("scriptpubkey_address")
                if ra and ra != address:
                    totals[ra] = totals.get(ra, 0) + out.get("value", 0)

    top = sorted(totals.items(), key=lambda x: -x[1])[:5]
    return {
        "top_recipients": [
            {"address": addr, "total_btc": round(amt * SATOSHI, 8)}
            for addr, amt in top
        ]
    }


def _score_recipient(address: str) -> dict:
    features = extract_features(address, check_counterparties=False)
    if "error" in features:
        return {"error": features["error"]}
    result = score(features)
    return {
        "address":     address,
        "risk_level":  result["risk_level"],
        "score":       result["score"],
        "evidence":    result["evidence"],
        "tx_count":    features["tx_count"],
        "relay_ratio": round(features["relay_ratio"], 4),
        "burst_days":  features["burst_days"],
    }


# ── Tool factory — wraps core logic with logging and correct docstrings ───────
# The new google-genai SDK reads the function signature + docstring to
# auto-generate the JSON schema it sends to the model.

def _make_tools(verbose: bool) -> list:
    """Return tool functions with logging baked in via closure."""

    def get_address_summary(address: str) -> dict:
        """
        Fetch on-chain statistics for a Bitcoin address via mempool.space.
        Returns tx_count, balance_btc, relay_ratio (fraction of funds forwarded out),
        funnel_ratio (senders / recipients), spike_ratio (max / avg incoming payment),
        burst_days (length of active window), total_in_btc, total_out_btc.
        """
        if verbose:
            print(f"[layer3]   -> get_address_summary({address[:32]}...)")
        result = _get_address_summary(address)
        if verbose:
            print(f"[layer3]      {_preview(result)}")
        return result

    def trace_recipients(address: str) -> dict:
        """
        Get the top 5 addresses that received money FROM this Bitcoin address.
        Reveals broker or cashout addresses one hop downstream from the wallet.
        Returns a list of {address, total_btc} pairs sorted by amount.
        """
        if verbose:
            print(f"[layer3]   -> trace_recipients({address[:32]}...)")
        result = _trace_recipients(address)
        if verbose:
            print(f"[layer3]      {_preview(result)}")
        return result

    def score_recipient(address: str) -> dict:
        """
        Run behavioral risk scoring on any Bitcoin address.
        Returns risk_level (HIGH/MEDIUM/LOW/CLEAN), score 0-100, evidence list,
        tx_count, relay_ratio, and burst_days.
        Use this on suspicious recipient addresses to investigate one hop deeper.
        """
        if verbose:
            print(f"[layer3]   -> score_recipient({address[:32]}...)")
        result = _score_recipient(address)
        if verbose:
            print(f"[layer3]      {_preview(result)}")
        return result

    return [get_address_summary, trace_recipients, score_recipient]


def _preview(obj: dict) -> str:
    s = json.dumps(obj, default=str)
    return s[:180] + ("..." if len(s) > 180 else "")


# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a cryptocurrency fraud investigator embedded in a financial platform.
A user is about to confirm a large transaction. Investigate the recipient address and decide whether to warn the user.

You have 3 tools:
  get_address_summary  — fetch relay ratio, balance, tx count, funnel/spike/burst stats
  trace_recipients     — see the top addresses this wallet forwarded money to (hop-1 brokers)
  score_recipient      — run risk scoring on any address (investigate 1 hop deeper)

Investigation strategy:
  1. Start with get_address_summary on the recipient.
  2. If relay_ratio > 0.9 or tx_count < 5, call trace_recipients to follow the money.
  3. Call score_recipient on 1-2 suspicious recipients to check if it is a broker chain.
  4. Stop once you have enough evidence. Do not exceed 6 tool calls total.

After investigating, respond ONLY with a valid JSON object — no extra text before or after:
{
  "risk_level": "HIGH" | "MEDIUM" | "LOW" | "CLEAN",
  "warning_text": "2-3 sentence plain-language warning. Warm, not accusatory — the user may genuinely trust this person.",
  "evidence": ["specific finding 1", "specific finding 2"],
  "suggestions": ["concrete action 1", "concrete action 2"]
}"""


# ── Agent entry point ─────────────────────────────────────────────────────────

def investigate(
    address: str,
    amount_usd: float,
    token: str,
    account: dict,
    layer2_result: dict,
    verbose: bool = True,
) -> dict:
    """
    Run the Gemma investigation agent on a flagged address.

    Args:
        address:       recipient Bitcoin address
        amount_usd:    transaction amount in USD
        token:         "BTC" | "ETH" | "USDT"
        account:       {account_age_days, avg_tx_usd, ...}
        layer2_result: output dict from layer2 scoring
        verbose:       print tool calls and reasoning steps

    Returns:
        {risk_level, warning_text, evidence, suggestions}
    """
    api_key = os.getenv("GOOGLE_API_KEY", "")
    if not api_key:
        raise ValueError(
            "GOOGLE_API_KEY environment variable is not set. "
            "Get a free key at https://aistudio.google.com/apikey"
        )

    model_name = os.getenv("GEMMA_MODEL", DEFAULT_MODEL)
    client = genai.Client(api_key=api_key)

    avg_tx     = account.get("avg_tx_usd", amount_usd) or amount_usd
    multiplier = amount_usd / avg_tx

    initial_message = (
        f"Investigate this pending transaction.\n\n"
        f"Transaction:\n"
        f"  Recipient: {address}\n"
        f"  Amount: ${amount_usd:,.0f} USD in {token}\n\n"
        f"User account:\n"
        f"  Account age: {account.get('account_age_days', '?')} days old\n"
        f"  Typical send size: ${avg_tx:,.0f} USD\n"
        f"  This send is {multiplier:.1f}x their usual amount\n\n"
        f"Layer 2 pre-screen:\n"
        f"  Score: {layer2_result.get('score', 0)}/100  "
        f"Level: {layer2_result.get('risk_level', 'UNKNOWN')}\n"
        f"  Signals: {', '.join(layer2_result.get('evidence', [])) or 'none triggered'}\n\n"
        f"Please investigate the recipient address and provide your assessment."
    )

    if verbose:
        print(f"\n[layer3] Starting investigation  model={model_name}")
        print(f"[layer3] Recipient: {address}")
        print(f"[layer3] Amount: ${amount_usd:,.0f} {token}  ({multiplier:.1f}x user avg)")

    tools = _make_tools(verbose)

    config = types.GenerateContentConfig(
        system_instruction=_SYSTEM_PROMPT,
        tools=tools,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(
            maximum_remote_calls=8,
        ),
    )

    response = client.models.generate_content(
        model=model_name,
        contents=initial_message,
        config=config,
    )

    final_text = response.text.strip() if response.text else ""

    if verbose:
        print(f"\n[layer3] Final response:\n{final_text}\n")

    return _parse_response(final_text, layer2_result)


def _parse_response(text: str, layer2_result: dict) -> dict:
    """Parse JSON from model output. Falls back gracefully on failure."""
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start:end])
            parsed.setdefault("risk_level",   layer2_result.get("risk_level", "UNKNOWN"))
            parsed.setdefault("evidence",     layer2_result.get("evidence", []))
            parsed.setdefault("warning_text",
                "This address shows unusual patterns. Verify the recipient before confirming.")
            parsed.setdefault("suggestions", [
                "Wait 24 hours before confirming",
                "Send a small test amount first to verify the recipient",
            ])
            return parsed
        except json.JSONDecodeError:
            pass

    return {
        "risk_level":   layer2_result.get("risk_level", "UNKNOWN"),
        "warning_text": text or "This address shows unusual on-chain patterns. Please verify before confirming.",
        "evidence":     layer2_result.get("evidence", []),
        "suggestions":  [
            "Wait 24 hours before confirming",
            "Send a small test amount first to verify the recipient",
        ],
    }
