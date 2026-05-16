"""
Layer 3 — Gemma 4 powered money flow investigation agent.

Built for the Kaggle Gemma 4 for Good Hackathon.

Supported chains:
  BTC  — mempool.space (no key needed)
  TRX  — Tronscan API (no key needed)
  ETH  — planned (needs Etherscan key)

Chain-specific code is limited to two fetcher functions per chain:
  _fetch_account(address, chain)  →  {tx_count, balance, unit}
  _fetch_txs(address, chain)      →  [{from, to, amount, timestamp}, ...]

All analysis (relay_ratio, funnel, inflows, outflows, scoring) runs
on the normalized output — no duplication.

Uses a manual ReAct loop so it works with any Gemma instruction-tuned model.
Set GOOGLE_API_KEY and GEMMA_MODEL in .env at the project root.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from score_address import extract_features, score

SATOSHI       = 1e-8
SUN           = 1e-6
MEMPOOL_BASE  = "https://mempool.space/api"
TRONSCAN_BASE = "https://apilist.tronscan.org/api"
DEFAULT_MODEL = "gemma-4-31b-it"


# ── Chain detection ───────────────────────────────────────────────────────────

def detect_chain(address: str) -> str:
    a = address.strip()
    if a.startswith(("1", "3")) or a.startswith("bc1"):
        return "btc"
    if a.startswith("0x") and len(a) == 42:
        return "eth"
    if a.startswith("T") and len(a) == 34:
        return "trx"
    return "unknown"


# ── Shared HTTP helper ────────────────────────────────────────────────────────

def _http_get(url: str, headers: dict | None = None) -> dict | list | None:
    try:
        r = requests.get(url, headers=headers or {}, timeout=12)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _tronscan_headers() -> dict:
    key = os.getenv("TRONSCAN_API_KEY", "")
    return {"TRON-PRO-API-KEY": key} if key else {}


# ── Chain-specific fetchers (the ONLY chain-specific code) ────────────────────

def _fetch_account(address: str, chain: str) -> dict:
    """
    Returns {tx_count, balance, unit} for any chain.
    balance is in human units (BTC or TRX), not satoshi/SUN.
    """
    if chain == "btc":
        data = _http_get(f"{MEMPOOL_BASE}/address/{address}")
        if not data:
            return {"error": "not_found"}
        stats = data.get("chain_stats", {})
        funded = stats.get("funded_txo_sum", 0)
        spent  = stats.get("spent_txo_sum", 0)
        return {
            "tx_count": stats.get("tx_count", 0),
            "balance":  round((funded - spent) * SATOSHI, 8),
            "unit":     "BTC",
        }

    if chain == "trx":
        data = _http_get(f"{TRONSCAN_BASE}/account?address={address}", _tronscan_headers())
        if not data or "address" not in data:
            return {"error": "not_found"}
        return {
            "tx_count": data.get("totalTransactionCount", 0),
            "balance":  round(data.get("balance", 0) * SUN, 2),
            "unit":     "TRX",
        }

    return {"error": f"unsupported chain: {chain}"}


def _fetch_txs(address: str, chain: str) -> list[dict]:
    """
    Returns normalized transactions: [{from, to, amount, timestamp}, ...]
    amount is in human units (BTC or TRX).
    timestamp is unix seconds.
    """
    if chain == "btc":
        raw = _http_get(f"{MEMPOOL_BASE}/address/{address}/txs") or []
        txs = []
        for tx in raw:
            ts = tx.get("status", {}).get("block_time", 0)
            vout_to_us = sum(
                o.get("value", 0) for o in tx.get("vout", [])
                if o.get("scriptpubkey_address") == address
            )
            vin_from_us = sum(
                (i.get("prevout") or {}).get("value", 0)
                for i in tx.get("vin", [])
                if (i.get("prevout") or {}).get("scriptpubkey_address") == address
            )

            if vout_to_us > 0 and vin_from_us == 0:         # incoming
                senders = [
                    (i.get("prevout") or {}).get("scriptpubkey_address")
                    for i in tx.get("vin", [])
                    if (i.get("prevout") or {}).get("scriptpubkey_address")
                ]
                for sender in senders:
                    txs.append({"from": sender, "to": address,
                                "amount": round(vout_to_us * SATOSHI, 8), "timestamp": ts})

            elif vin_from_us > 0 and vout_to_us == 0:        # outgoing
                for out in tx.get("vout", []):
                    ra = out.get("scriptpubkey_address")
                    if ra and ra != address:
                        txs.append({"from": address, "to": ra,
                                    "amount": round(out.get("value", 0) * SATOSHI, 8),
                                    "timestamp": ts})
        return txs

    if chain == "trx":
        data = _http_get(
            f"{TRONSCAN_BASE}/transaction?address={address}&limit=50&sort=-timestamp",
            _tronscan_headers()
        )
        raw = data.get("data", []) if data else []
        txs = []
        for tx in raw:
            ts     = tx.get("timestamp", 0) / 1000
            frm    = tx.get("ownerAddress", "")
            to     = tx.get("toAddress", "")
            amount = round(float(tx.get("amount", 0) or 0) * SUN, 2)
            if frm and to and amount > 0:
                txs.append({"from": frm, "to": to, "amount": amount, "timestamp": ts})
        return txs

    return []


# ── Shared analysis — runs on normalized data for any chain ──────────────────

def _get_address_summary(address: str, chain: str) -> dict:
    account = _fetch_account(address, chain)
    if "error" in account:
        return account

    txs  = _fetch_txs(address, chain)
    unit = account["unit"]

    total_in   = sum(t["amount"] for t in txs if t["to"]   == address)
    total_out  = sum(t["amount"] for t in txs if t["from"] == address)
    relay_ratio = total_out / total_in if total_in > 0 else 0.0

    senders    = {t["from"] for t in txs if t["to"]   == address}
    recipients = {t["to"]   for t in txs if t["from"] == address}
    timestamps = [t["timestamp"] for t in txs if t["timestamp"]]

    now = time.time()
    last_active_days_ago  = round((now - max(timestamps)) / 86400, 1) if timestamps else None
    first_seen_days_ago   = round((now - min(timestamps)) / 86400, 1) if timestamps else None
    burst_days = round((max(timestamps) - min(timestamps)) / 86400, 1) if len(timestamps) >= 2 else None

    return {
        "chain":                chain.upper(),
        "tx_count":             account["tx_count"],
        "balance":              f"{account['balance']} {unit}",
        "total_in":             f"{round(total_in, 8)} {unit}",
        "total_out":            f"{round(total_out, 8)} {unit}",
        "relay_ratio":          round(relay_ratio, 4),
        "n_senders_seen":       len(senders),
        "n_recipients_seen":    len(recipients),
        "first_seen_days_ago":  first_seen_days_ago,
        "last_active_days_ago": last_active_days_ago,
        "burst_days":           burst_days,
    }


def _get_outflows(address: str, chain: str) -> dict:
    txs = _fetch_txs(address, chain)
    unit = _fetch_account(address, chain).get("unit", "")

    totals: dict[str, float] = {}
    for t in txs:
        if t["from"] == address and t["to"] != address:
            totals[t["to"]] = totals.get(t["to"], 0) + t["amount"]

    top = sorted(totals.items(), key=lambda x: -x[1])[:5]
    return {
        "top_recipients": [
            {"address": addr, "amount": f"{round(amt, 8)} {unit}"}
            for addr, amt in top
        ]
    }


def _get_inflows(address: str, chain: str) -> dict:
    txs = _fetch_txs(address, chain)
    unit = _fetch_account(address, chain).get("unit", "")

    totals: dict[str, float] = {}
    for t in txs:
        if t["to"] == address and t["from"] != address:
            totals[t["from"]] = totals.get(t["from"], 0) + t["amount"]

    top = sorted(totals.items(), key=lambda x: -x[1])[:10]
    return {
        "n_unique_senders": len(totals),
        "top_senders": [
            {"address": addr, "amount": f"{round(amt, 8)} {unit}"}
            for addr, amt in top
        ],
    }


def _score_address(address: str, chain: str) -> dict:
    # BTC uses the full rule-based scorer from score_address.py
    if chain == "btc":
        features = extract_features(address, check_counterparties=False)
        if "error" in features:
            return features
        result = score(features)
        return {
            "chain":       "BTC",
            "risk_level":  result["risk_level"],
            "score":       result["score"],
            "evidence":    result["evidence"],
            "tx_count":    features["tx_count"],
            "relay_ratio": round(features["relay_ratio"], 4),
        }

    # Other chains use the shared summary + same rule logic
    s = _get_address_summary(address, chain)
    if "error" in s:
        return s

    points   = 0
    evidence = []

    if 0 < s["tx_count"] < 5:
        points += 40; evidence.append("[+40] Near-fresh address: fewer than 5 transactions")
    if s["relay_ratio"] > 0.9 and s["tx_count"] > 0:
        points += 30; evidence.append("[+30] High relay ratio: 90%+ forwarded out")
    if s["n_senders_seen"] >= 2 and s["n_recipients_seen"] <= 2:
        points += 25; evidence.append("[+25] Funnel pattern: many senders to few recipients")
    if s["burst_days"] is not None and s["burst_days"] <= 14:
        points += 15; evidence.append("[+15] Short burst lifecycle: all activity within 14 days")
    if s["burst_days"] is not None and s["burst_days"] > 365:
        points -= 20; evidence.append("[-20] Established wallet: active for over a year")
    if s["n_recipients_seen"] > 10:
        points -= 15; evidence.append("[-15] Many recipients: normal spending behaviour")

    points = max(0, min(100, points))
    level  = "HIGH" if points >= 70 else "MEDIUM" if points >= 40 else "LOW" if points >= 15 else "CLEAN"

    return {
        "chain":       s["chain"],
        "risk_level":  level,
        "score":       points,
        "evidence":    evidence,
        "tx_count":    s["tx_count"],
        "relay_ratio": s["relay_ratio"],
    }


# ── Tool factory — closures bind chain and verbose ────────────────────────────

def _make_tools(verbose: bool, chain: str) -> list:

    def _log(name: str, address: str, result: dict):
        if verbose:
            print(f"[layer3]   -> {name}({address[:34]})")
            s = json.dumps(result, default=str)
            print(f"[layer3]      {s[:200]}{'...' if len(s) > 200 else ''}")

    def get_address_summary(address: str) -> dict:
        """
        Fetch on-chain statistics for an address.
        Returns: chain, tx_count, balance, total_in, total_out, relay_ratio,
        n_senders_seen, n_recipients_seen, first_seen_days_ago,
        last_active_days_ago, burst_days.
        Use on every address to classify it: fresh scam wallet, relay hop,
        or exchange endpoint (very high tx_count + large total_in).
        """
        r = _get_address_summary(address, chain)
        _log("get_address_summary", address, r)
        return r

    def get_outflows(address: str) -> dict:
        """
        Get the top 5 addresses this wallet sent money TO, with amounts.
        Use this to follow the money to the next hop in the chain.
        """
        r = _get_outflows(address, chain)
        _log("get_outflows", address, r)
        return r

    def get_inflows(address: str) -> dict:
        """
        Get addresses that sent money TO this wallet and n_unique_senders.
        Very high n_unique_senders on a sweeping wallet confirms it is an exchange.
        Returns n_unique_senders and top_senders list.
        """
        r = _get_inflows(address, chain)
        _log("get_inflows", address, r)
        return r

    def score_address(address: str) -> dict:
        """
        Run behavioral risk scoring on an address.
        Returns risk_level (HIGH/MEDIUM/LOW/CLEAN), score 0-100, evidence list.
        """
        r = _score_address(address, chain)
        _log("score_address", address, r)
        return r

    return [get_address_summary, get_outflows, get_inflows, score_address]


# ── LLM providers ────────────────────────────────────────────────────────────

def _resolve_model() -> tuple[str, str]:
    """
    Returns (provider, model_name) based on what keys are set in .env.
    Prefers OpenRouter if OPENROUTER_API_KEY is set.
    Model name priority:
      OpenRouter: OPENROUTER_MODEL → GEMMA_MODEL → default
      Google:     GEMMA_MODEL → default
    """
    if os.getenv("OPENROUTER_API_KEY"):
        model = os.getenv("OPENROUTER_MODEL") or os.getenv("GEMMA_MODEL", DEFAULT_MODEL)
        return "openrouter", model
    if os.getenv("GOOGLE_API_KEY"):
        model = os.getenv("GEMMA_MODEL", DEFAULT_MODEL)
        return "google", model
    raise ValueError(
        "No API key found. Set OPENROUTER_API_KEY or GOOGLE_API_KEY in .env"
    )


def _call_llm(messages: list[dict], model: str, system_prompt: str, provider: str) -> str:
    """
    Send messages to the given provider.
    messages: list of {"role": "user"|"assistant", "content": str}
    Returns the model reply as a string.
    """
    if provider == "openrouter":
        return _call_openrouter(messages, model, system_prompt)
    return _call_google(messages, model, system_prompt)


def _call_openrouter(messages: list[dict], model: str, system_prompt: str) -> str:
    from openai import OpenAI
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY"),
        default_headers={"HTTP-Referer": "https://github.com/cryptoScamChecker"},
    )
    full_messages = [{"role": "system", "content": system_prompt}] + messages
    response = client.chat.completions.create(model=model, messages=full_messages)
    return response.choices[0].message.content or ""


def _call_google(messages: list[dict], model: str, system_prompt: str) -> str:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    contents = [
        types.Content(
            role="model" if m["role"] == "assistant" else "user",
            parts=[types.Part(text=m["content"])]
        )
        for m in messages
    ]
    config   = types.GenerateContentConfig(system_instruction=system_prompt)
    response = client.models.generate_content(model=model, contents=contents, config=config)
    return response.text.strip() if response.text else ""


# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a cryptocurrency fraud investigator. A user is about to send a large amount to an address. Trace where that money will ultimately end up.

Criminals always need to convert stolen crypto into real purchasing power. They route funds through relay wallets and deposit into an exchange (Binance, OKX, Kraken etc.) to sell for fiat. Your goal is to follow the money chain hop by hop until you find the cashout point.

You have 4 tools. Call them by responding with a tool call block:

<tool_call>
{"name": "tool_name", "args": {"address": "..."}}
</tool_call>

Available tools:

get_address_summary(address)
  Returns: tx_count, balance, relay_ratio, n_senders_seen, n_recipients_seen,
           first_seen_days_ago, last_active_days_ago, burst_days
  Use this to classify any address. Very high tx_count + large total_in = likely exchange.

get_outflows(address)
  Returns: top 5 addresses this wallet sent money TO with amounts
  Use this to follow the money to the next hop.

get_inflows(address)
  Returns: n_unique_senders + top senders to this wallet
  Use this on suspected exchange endpoints — very high n_unique_senders confirms it is an exchange.

score_address(address)
  Returns: risk_level (HIGH/MEDIUM/LOW/CLEAN), score 0-100, evidence list

Investigation strategy:
  STEP 1 — Assess the target. Call get_address_summary. Is it fresh (low tx_count, appeared recently)? Does it sweep immediately (relay_ratio close to 1.0)?
  STEP 2 — Follow the money. Call get_outflows to find the next hop. Then get_address_summary on that address to classify it.
  STEP 3 — Keep following hops (up to 6) until you reach a high-volume address (tx_count in thousands, massive total_in) — that is the exchange cashout point.
  STEP 4 — Confirm the exchange. Call get_inflows on it. Very high n_unique_senders = exchange confirmed.

The chain: fresh wallet → relay(s) → exchange = strong evidence of a scam cashout route.

Once you have traced the chain, respond with your final assessment as a JSON object:
{
  "risk_level": "HIGH" | "MEDIUM" | "LOW" | "CLEAN",
  "user_message": "3-5 sentences written for someone with no crypto knowledge. Explain what you found using everyday words. Forbidden words: relay, hop, chain, tx_count, relay_ratio, funnel, UTXO, on-chain, node, wallet address. Instead say things like: 'this account was created just 8 days ago', 'the money was immediately moved to another account within hours', 'that account collects money from dozens of different people at the same time — a pattern commonly used by scammers to pool stolen funds before cashing out'. Be warm and specific about what you actually found.",
  "evidence": ["Each bullet must describe a concrete finding in plain language. Bad: 'The money follows a chain: addr1 -> addr2'. Good: 'The recipient account was created 8 days ago and has only been used 4 times'. Good: 'Every dollar received was moved out within hours, leaving a zero balance'. Good: 'The next account this money flows to collects from 47 different people simultaneously'."],
  "suggestions": ["concrete action 1", "concrete action 2"]
}"""


# ── ReAct loop ────────────────────────────────────────────────────────────────

def _parse_tool_call(text: str) -> dict | None:
    match = re.search(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _parse_final_response(text: str, layer2_result: dict) -> dict:
    start = text.rfind('{"risk_level"')
    end   = text.rfind("}") + 1
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start:end])
            parsed.setdefault("risk_level",   layer2_result.get("risk_level", "UNKNOWN"))
            parsed.setdefault("evidence",     layer2_result.get("evidence", []))
            parsed.setdefault("warning_text",
                "This address shows unusual patterns. Verify the recipient before confirming.")
            parsed.setdefault("user_message",
                "We noticed some unusual activity linked to this account. "
                "We recommend verifying who you are sending money to before confirming.")
            parsed.setdefault("suggestions", [
                "Wait 24 hours before confirming",
                "Send a small test amount first to verify the recipient",
            ])
            return parsed
        except json.JSONDecodeError:
            pass
    return {
        "risk_level":   layer2_result.get("risk_level", "UNKNOWN"),
        "warning_text": text.strip() or "Unusual on-chain patterns detected. Please verify before confirming.",
        "evidence":     layer2_result.get("evidence", []),
        "suggestions":  [
            "Wait 24 hours before confirming",
            "Send a small test amount first to verify the recipient",
        ],
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def investigate(
    address: str,
    amount_usd: float,
    token: str,
    account: dict,
    layer2_result: dict,
    chain: str | None = None,
    verbose: bool = True,
) -> dict:
    """
    Run the Gemma 4 investigation agent on a flagged address.

    Args:
        address:       recipient address (BTC or TRX)
        amount_usd:    transaction amount in USD
        token:         "BTC" | "TRX" | "USDT" etc.
        account:       {account_age_days, avg_tx_usd}
        layer2_result: {score, risk_level, evidence} from layer2
        chain:         "btc" | "trx" — auto-detected from address if None
        verbose:       print each tool call and result

    Returns:
        {risk_level, warning_text, evidence, suggestions}
    """
    if chain is None:
        chain = detect_chain(address)
    if chain == "unknown":
        raise ValueError(f"Cannot detect chain for address: {address}")
    if chain == "eth":
        raise NotImplementedError("ETH support coming soon — needs Etherscan API key")

    avg_tx            = account.get("avg_tx_usd", amount_usd) or amount_usd
    multiplier        = amount_usd / avg_tx
    provider, model_name = _resolve_model()

    initial_message = (
        f"Investigate this pending transaction.\n\n"
        f"Transaction:\n"
        f"  Recipient: {address}  (chain: {chain.upper()})\n"
        f"  Amount: ${amount_usd:,.0f} USD in {token}\n\n"
        f"User account:\n"
        f"  Account age: {account.get('account_age_days', '?')} days old\n"
        f"  Typical send size: ${avg_tx:,.0f} USD\n"
        f"  This send is {multiplier:.1f}x their usual amount\n\n"
        f"Layer 2 pre-screen:\n"
        f"  Score: {layer2_result.get('score', 0)}/100  "
        f"Level: {layer2_result.get('risk_level', 'UNKNOWN')}\n"
        f"  Signals: {', '.join(layer2_result.get('evidence', [])) or 'none triggered'}\n\n"
        f"Please investigate the recipient address and trace where the money goes."
    )

    if verbose:
        print(f"\n[layer3] Starting investigation  provider={provider}  model={model_name}  chain={chain.upper()}")
        print(f"[layer3] Recipient: {address}")
        print(f"[layer3] Amount:    ${amount_usd:,.0f} {token}  ({multiplier:.1f}x user avg)")

    tools     = _make_tools(verbose, chain)
    tools_map = {fn.__name__: fn for fn in tools}

    # Conversation history as plain dicts — provider-agnostic
    messages = [{"role": "user", "content": initial_message}]

    for _ in range(20):
        reply = None
        for attempt in range(3):
            try:
                reply = _call_llm(messages, model_name, _SYSTEM_PROMPT, provider)
                break
            except Exception as e:
                if attempt == 2:
                    raise
                if verbose:
                    print(f"[layer3] API error (attempt {attempt+1}/3): {e} — retrying...")
                time.sleep(2 ** attempt)

        reply = reply or ""
        messages.append({"role": "assistant", "content": reply})

        tool_call = _parse_tool_call(reply)
        if tool_call:
            name   = tool_call.get("name", "")
            args   = tool_call.get("args", {})
            result = tools_map[name](**args) if name in tools_map else {"error": f"unknown tool: {name}"}
            messages.append({
                "role":    "user",
                "content": f"<tool_result>\n{json.dumps(result, default=str)}\n</tool_result>",
            })
        else:
            if verbose:
                print(f"\n[layer3] Final response:\n{reply}\n")
            return _parse_final_response(reply, layer2_result)

    last_reply = next(
        (m["content"] for m in reversed(messages) if m["role"] == "assistant"), ""
    )
    return _parse_final_response(last_reply, layer2_result)
