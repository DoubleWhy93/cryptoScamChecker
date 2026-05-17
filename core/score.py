"""
Behavioral risk scorer for Bitcoin addresses.

Provides extract_features(address) and score(features) used by agent/layer3.py.
BTC only — TRX scoring is handled inline in layer3.py using the same RULES.
"""

import time
from typing import Optional

import requests

MEMPOOL_BASE = "https://mempool.space/api"
SATOSHI      = 1e-8
_BTC_DELAY   = 0.3  # seconds between mempool.space requests


def _get(url: str) -> Optional[dict]:
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def extract_features(address: str, check_counterparties: bool = False) -> dict:
    """
    Fetch BTC address stats and return a feature dict ready for score().
    Makes 2 API calls: address summary + first page of transactions.
    """
    summary = _get(f"{MEMPOOL_BASE}/address/{address}")
    if not summary:
        return {"address": address, "error": "not_found"}

    stats        = summary.get("chain_stats", {})
    tx_count     = stats.get("tx_count", 0)
    funded_sum   = stats.get("funded_txo_sum", 0)
    spent_sum    = stats.get("spent_txo_sum", 0)
    total_in     = funded_sum * SATOSHI
    total_out    = spent_sum  * SATOSHI
    relay_ratio  = spent_sum / funded_sum if funded_sum > 0 else 0.0

    time.sleep(_BTC_DELAY)

    txs = (_get(f"{MEMPOOL_BASE}/address/{address}/txs") or []) if tx_count > 0 else []
    time.sleep(_BTC_DELAY)

    senders:    dict[str, float] = {}
    recipients: dict[str, float] = {}
    amounts_in  = []
    amounts_out = []
    timestamps  = []

    for tx in txs:
        vout_to_us  = sum(o.get("value", 0) for o in tx.get("vout", [])
                          if o.get("scriptpubkey_address") == address)
        vin_from_us = sum((i.get("prevout") or {}).get("value", 0)
                          for i in tx.get("vin", [])
                          if (i.get("prevout") or {}).get("scriptpubkey_address") == address)
        ts = tx.get("status", {}).get("block_time", 0)
        if ts:
            timestamps.append(ts)

        if vout_to_us > 0 and vin_from_us == 0:
            amounts_in.append(vout_to_us)
            for inp in tx.get("vin", []):
                sa = (inp.get("prevout") or {}).get("scriptpubkey_address")
                if sa and sa != address:
                    senders[sa] = senders.get(sa, 0) + vout_to_us

        elif vin_from_us > 0 and vout_to_us == 0:
            amounts_out.append(vin_from_us)
            for out in tx.get("vout", []):
                ra = out.get("scriptpubkey_address")
                if ra and ra != address:
                    recipients[ra] = recipients.get(ra, 0) + out.get("value", 0)

    n_senders    = len(senders)
    n_recipients = len(recipients)
    funnel_ratio = n_senders / max(n_recipients, 1)
    max_in       = max(amounts_in,  default=0) * SATOSHI
    avg_in       = (sum(amounts_in) / len(amounts_in) * SATOSHI) if amounts_in else 0
    spike_ratio  = max_in / avg_in if avg_in > 0 else 1.0

    now = time.time()
    burst_days           = round((max(timestamps) - min(timestamps)) / 86400, 1) if len(timestamps) >= 2 else None
    last_active_days_ago = round((now - max(timestamps)) / 86400, 1) if timestamps else None
    first_seen_days_ago  = round((now - min(timestamps)) / 86400, 1) if timestamps else None

    return {
        "address":              address,
        "tx_count":             tx_count,
        "total_in_btc":         round(total_in,  8),
        "total_out_btc":        round(total_out, 8),
        "balance_btc":          round((funded_sum - spent_sum) * SATOSHI, 8),
        "relay_ratio":          round(relay_ratio, 4),
        "n_senders_seen":       n_senders,
        "n_recipients_seen":    n_recipients,
        "funnel_ratio":         round(funnel_ratio, 2),
        "max_recv_btc":         round(max_in,  8),
        "avg_recv_btc":         round(avg_in,  8),
        "spike_ratio":          round(spike_ratio, 2),
        "burst_days":           burst_days,
        "last_active_days_ago": last_active_days_ago,
        "first_seen_days_ago":  first_seen_days_ago,
    }


RULES = [
    ("Near-fresh address: fewer than 5 transactions on chain",              40, lambda f: 0 < f.get("tx_count", 0) < 5),
    ("High relay ratio: 90%+ of received funds forwarded out",              30, lambda f: f.get("relay_ratio", 0) > 0.9 and f.get("tx_count", 0) > 0),
    ("Funnel pattern: many senders to few recipients (ratio > 3)",          25, lambda f: f.get("funnel_ratio", 0) > 3 and f.get("n_senders_seen", 0) >= 2),
    ("Single dominant incoming payment: one payment is 5x the average",     20, lambda f: f.get("spike_ratio", 0) > 5 and f.get("max_recv_btc", 0) > 0.001),
    ("Short burst lifecycle: all activity within 14 days",                  15, lambda f: f.get("burst_days") is not None and f.get("burst_days", 999) <= 14),
    ("Established wallet: activity spans over 365 days",                   -20, lambda f: f.get("burst_days") is not None and f.get("burst_days", 0) > 365),
    ("Normal spending behaviour: sends to more than 10 distinct recipients",-15, lambda f: f.get("n_recipients_seen", 0) > 10),
]


def score(features: dict) -> dict:
    if "error" in features:
        return {"risk_level": "UNKNOWN", "score": 0, "evidence": [features["error"]]}

    total    = 0
    evidence = []
    for desc, pts, condition in RULES:
        try:
            if condition(features):
                total += pts
                evidence.append(f"[{'+' if pts >= 0 else ''}{pts:+d}] {desc}")
        except Exception:
            pass

    total = max(0, min(100, total))
    level = "HIGH" if total >= 70 else "MEDIUM" if total >= 40 else "LOW" if total >= 15 else "CLEAN"

    return {"risk_level": level, "score": total, "evidence": evidence}
