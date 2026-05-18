"""
Layer 3 — Gemma 4 powered money flow investigation agent.

Built for the Kaggle Gemma 4 for Good Hackathon.

Supported chains:
  BTC  — mempool.space (no key needed)
  TRX  — Tronscan API (no key needed)
  USDT — TRC20 transfers on TRON via Tronscan
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
import time

import requests

from core.score import extract_features, score

SATOSHI       = 1e-8
SUN           = 1e-6
MEMPOOL_BASE  = "https://mempool.space/api"
TRONSCAN_BASE = "https://apilist.tronscan.org/api"
TRONSCAN_API_BASE = "https://apilist.tronscanapi.com/api"
TRON_USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
DEFAULT_MODEL = "gemma-4-31b-it"


def _asset_key(chain: str, token: str | None = None) -> str:
    normalized = (token or "").strip().upper()
    if chain == "trx" and normalized == "USDT":
        return "usdt_trc20"
    return chain


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

def _fetch_account(address: str, chain: str, token: str | None = None) -> dict:
    """
    Returns {tx_count, balance, unit} for any chain.
    balance is in human units (BTC, TRX, or USDT), not satoshi/SUN.
    """
    asset = _asset_key(chain, token)
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

    if asset == "usdt_trc20":
        tx_data = _http_get(
            f"{TRONSCAN_API_BASE}/transfer/trc20"
            f"?address={address}&trc20Id={TRON_USDT_CONTRACT}&start=0&limit=50"
            f"&direction=0&reverse=true&db_version=1",
            _tronscan_headers(),
        ) or {}
        transfers = tx_data.get("data", []) if isinstance(tx_data, dict) else []
        return {
            "tx_count": tx_data.get("total", tx_data.get("rangeTotal", len(transfers))) if isinstance(tx_data, dict) else len(transfers),
            "balance": None,
            "unit":     "USDT",
        }

    if asset == "trx":
        data = _http_get(f"{TRONSCAN_BASE}/account?address={address}", _tronscan_headers())
        if not data or "address" not in data:
            return {"error": "not_found"}
        return {
            "tx_count": data.get("totalTransactionCount", 0),
            "balance":  round(data.get("balance", 0) * SUN, 2),
            "unit":     "TRX",
        }

    return {"error": f"unsupported chain: {chain}"}


def _fetch_txs(address: str, chain: str, token: str | None = None) -> list[dict]:
    """
    Returns normalized transactions: [{from, to, amount, timestamp}, ...]
    amount is in human units (BTC, TRX, or USDT).
    timestamp is unix seconds.
    """
    asset = _asset_key(chain, token)
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

    if asset == "usdt_trc20":
        data = _http_get(
            f"{TRONSCAN_API_BASE}/transfer/trc20"
            f"?address={address}&trc20Id={TRON_USDT_CONTRACT}&start=0&limit=50"
            f"&direction=0&reverse=true&db_version=1",
            _tronscan_headers(),
        )
        raw = data.get("data", []) if isinstance(data, dict) else []
        txs = []
        for tx in raw:
            decimals = int(tx.get("decimals") or tx.get("tokenInfo", {}).get("tokenDecimal") or 6)
            raw_amount = float(tx.get("amount", 0) or 0)
            amount = round(raw_amount / (10 ** decimals), 6)
            frm = tx.get("from", "")
            to = tx.get("to", "")
            ts = (tx.get("block_timestamp") or tx.get("timestamp") or 0) / 1000
            if frm and to and amount > 0:
                txs.append({"from": frm, "to": to, "amount": amount, "timestamp": ts})
        return txs

    if asset == "trx":
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

def _get_address_summary(address: str, chain: str, token: str | None = None) -> dict:
    account = _fetch_account(address, chain, token)
    if "error" in account:
        return account

    txs  = _fetch_txs(address, chain, token)
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
        "asset":                unit,
        "tx_count":             account["tx_count"],
        "balance":              f"{account['balance']} {unit}" if account.get("balance") is not None else None,
        "total_in":             f"{round(total_in, 8)} {unit}",
        "total_out":            f"{round(total_out, 8)} {unit}",
        "relay_ratio":          round(relay_ratio, 4),
        "n_senders_seen":       len(senders),
        "n_recipients_seen":    len(recipients),
        "first_seen_days_ago":  first_seen_days_ago,
        "last_active_days_ago": last_active_days_ago,
        "burst_days":           burst_days,
    }


def _get_outflows(address: str, chain: str, token: str | None = None) -> dict:
    txs = _fetch_txs(address, chain, token)
    unit = _fetch_account(address, chain, token).get("unit", "")

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


def _get_inflows(address: str, chain: str, token: str | None = None) -> dict:
    txs = _fetch_txs(address, chain, token)
    unit = _fetch_account(address, chain, token).get("unit", "")

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


def _score_address(address: str, chain: str, token: str | None = None) -> dict:
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
    s = _get_address_summary(address, chain, token)
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
        "asset":       s.get("asset"),
        "risk_level":  level,
        "score":       points,
        "evidence":    evidence,
        "tx_count":    s["tx_count"],
        "relay_ratio": s["relay_ratio"],
    }


# ── Tool factory — closures bind chain and verbose ────────────────────────────

def _make_tools(verbose: bool, chain: str, token: str | None = None) -> list:

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
        r = _get_address_summary(address, chain, token)
        _log("get_address_summary", address, r)
        return r

    def get_outflows(address: str) -> dict:
        """
        Get the top 5 addresses this wallet sent money TO, with amounts.
        Use this to follow the money to the next hop in the chain.
        """
        r = _get_outflows(address, chain, token)
        _log("get_outflows", address, r)
        return r

    def get_inflows(address: str) -> dict:
        """
        Get addresses that sent money TO this wallet and n_unique_senders.
        Very high n_unique_senders on a sweeping wallet confirms it is an exchange.
        Returns n_unique_senders and top_senders list.
        """
        r = _get_inflows(address, chain, token)
        _log("get_inflows", address, r)
        return r

    def score_address(address: str) -> dict:
        """
        Run behavioral risk scoring on an address.
        Returns risk_level (HIGH/MEDIUM/LOW/CLEAN), score 0-100, evidence list.
        """
        r = _score_address(address, chain, token)
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

_SYSTEM_PROMPT = """You are a cryptocurrency fraud investigator. A user is about to send money to an address. Your job is to trace where that money will ultimately end up, then explain what you found.

Criminals convert stolen crypto into purchasing power by routing funds through intermediate wallets before depositing into an exchange (Binance, OKX, Kraken etc.) to sell for cash. Follow the money hop by hop until you reach that cashout point.

You have 4 tools. Call them by outputting a tool call block:

<tool_call>
{"name": "tool_name", "args": {"address": "..."}}
</tool_call>

Available tools:

get_address_summary(address)
  Returns: chain, asset, tx_count, balance, total_in, total_out, relay_ratio, n_senders_seen,
           n_recipients_seen, first_seen_days_ago, last_active_days_ago, burst_days
  Use to classify any address. For USDT transfers, amounts are USDT-TRC20 movements on TRON.
  Very high tx_count + large total_in = likely exchange.

get_outflows(address)
  Returns: top 5 addresses this wallet sent money TO, with amounts.
  Follow these to find the next hop.

get_inflows(address)
  Returns: n_unique_senders and top senders.
  Very high n_unique_senders on a large-volume wallet confirms it is an exchange deposit address.

score_address(address)
  Returns: risk_level (HIGH/MEDIUM/LOW/CLEAN), score 0-100, evidence list.

Investigation strategy:
  1. get_address_summary on the recipient. Note the exact values: tx_count, first_seen_days_ago, relay_ratio.
  2. If relay_ratio is high, call get_outflows and follow to the next address. Summarize on that address.
  3. Keep following hops (up to 6) until you reach a high-volume address (tx_count in the thousands).
  4. Call get_inflows on the suspected endpoint to check n_unique_senders.

When you are done investigating, output your final assessment as a JSON object.
The JSON is for the application only; the user will not see raw JSON.
Every customer-facing field must be warm, plain, and specific to THIS address.
Use the exact numbers from the tool results: age in days, transaction counts, amounts, percentages, and unique sender counts.
Do not shame the user. Acknowledge that scams often use hope, fear, urgency, romantic trust, or promises of profit to pressure people.
Include a conditional warning about WhatsApp, Telegram, social media, secrecy, guaranteed returns, taxes/fees to unlock funds, and urgent deadlines.
Do not claim the user used WhatsApp or Telegram unless it is conditional, for example: "If this request came through WhatsApp or Telegram..."
Do not use crypto jargon in customer-facing fields. Forbidden words in headline, user_message, detailed_findings, scam_red_flags, and suggestions: relay, hop, chain, tx_count, relay_ratio, funnel, UTXO, on-chain, node.

{
  "risk_level": "HIGH" | "MEDIUM" | "LOW" | "CLEAN",
  "headline": "A short, calm warning title for a normal customer.",
  "user_message": "4-6 warm sentences. Explain what was found and why it matters. Use exact values from the investigation. Say that if someone is pushing urgency, secrecy, romance, guaranteed returns, or fear of missing out, that pressure is a red flag. Be firm but compassionate.",
  "detailed_findings": [
    "3-5 bullets. Each bullet must include a specific number from the tool results and explain it in everyday language."
  ],
  "scam_red_flags": [
    "2-4 bullets. Include conditional advice such as: If this request came through WhatsApp, Telegram, a dating app, social media, or someone asking you to keep it secret, pause now."
  ],
  "suggestions": [
    "3-5 concrete next steps. Include: do not send yet, verify through a trusted channel, talk to a trusted person, contact the platform/bank/exchange support, and do not pay extra taxes or unlock fees."
  ],
  "reassurance": "One compassionate sentence: scams are designed to create pressure; pausing is the safest move."
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


def _extract_json_object(text: str) -> dict | None:
    """
    Extract the first JSON object from a model response.
    Handles both raw JSON and fenced Markdown blocks like ```json ... ```.
    """
    decoder = json.JSONDecoder()
    candidates = [
        match.group(1)
        for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    ]
    candidates.append(text)

    for candidate in candidates:
        for match in re.finditer(r"\{", candidate):
            try:
                parsed, _ = decoder.raw_decode(candidate[match.start():])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return None


def _normalize_final_response(parsed: dict, layer2_result: dict) -> dict:
    parsed.setdefault("risk_level", layer2_result.get("risk_level", "UNKNOWN"))
    parsed.setdefault("headline", "Review this transfer carefully")
    parsed.setdefault("detailed_findings", parsed.get("evidence") or layer2_result.get("evidence", []))
    parsed.setdefault("evidence", parsed.get("detailed_findings") or layer2_result.get("evidence", []))
    parsed.setdefault("scam_red_flags", [
        "If this request came through WhatsApp, Telegram, a dating app, or someone asking you to keep it private, pause before sending.",
        "Promises of guaranteed profit, urgent deadlines, taxes, or unlock fees are common scam warning signs.",
    ])
    parsed.setdefault("suggestions", [
        "Wait 24 hours before confirming",
        "Verify the recipient through a trusted channel",
        "Talk to someone you trust before sending",
    ])
    parsed.setdefault(
        "reassurance",
        "Scams are designed to create pressure; pausing now is a protective step.",
    )

    for key in ("detailed_findings", "evidence", "scam_red_flags", "suggestions"):
        if not isinstance(parsed.get(key), list):
            parsed[key] = [str(parsed[key])]

    fallback_message = (
        "This address shows unusual patterns. Verify the recipient before confirming."
    )
    parsed.setdefault("user_message", parsed.get("warning_text") or fallback_message)
    parsed.setdefault("warning_text", parsed["user_message"])
    return parsed


def _parse_final_response(text: str, layer2_result: dict) -> dict:
    parsed = _extract_json_object(text)
    if parsed is not None:
        return _normalize_final_response(parsed, layer2_result)

    return _normalize_final_response({
        "risk_level":   layer2_result.get("risk_level", "UNKNOWN"),
        "headline":     "Review this transfer carefully",
        "user_message": text.strip() or "Unusual on-chain patterns detected. Please verify before confirming.",
        "warning_text": text.strip() or "Unusual on-chain patterns detected. Please verify before confirming.",
        "detailed_findings": layer2_result.get("evidence", []),
        "suggestions":  [
            "Wait 24 hours before confirming",
            "Verify the recipient through a trusted channel",
            "Talk to someone you trust before sending",
        ],
    }, layer2_result)


# ── Entry point ───────────────────────────────────────────────────────────────

def investigate(
    address: str,
    amount_usd: float,
    token: str,
    account: dict,
    layer2_result: dict,
    chain: str | None = None,
    verbose: bool = True,
    stage_callback=None,
) -> dict:
    """
    Run the Gemma 4 investigation agent on a flagged address.

    Args:
        address:       recipient address (BTC or TRX)
        amount_usd:    transaction amount in USD
        token:         "BTC" | "TRX" | "USDT"
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

    tools     = _make_tools(verbose, chain, token)
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
            if stage_callback:
                stage_callback(name)
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
