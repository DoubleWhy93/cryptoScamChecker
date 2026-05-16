"""
Risk scorer for a Bitcoin address — the core logic of the future warning agent.

Given a single address, this script:
  1. Fetches its summary and first page of transactions from mempool.space
     (only 2 API calls — fast enough for real-time agent use)
  2. Extracts a feature vector based on patterns found in Real-CATS analysis
  3. Applies a rule-based risk score
  4. Outputs: risk level (HIGH / MEDIUM / LOW / UNKNOWN), score, and evidence

Scoring rules — purpose is to flag addresses worth investigating, not final verdict:
  +40  tx_count < 5            (near-fresh address)
  +30  relay_ratio > 0.9       (90%+ of funds forwarded out)
  +25  funnel_ratio > 3        (many senders → few recipients)
  +20  spike_ratio > 5         (one payment dominates)
  +15  burst_days <= 14        (all activity within 2 weeks)
  -20  burst_days > 365        (established long-lived wallet)
  -15  n_recipients > 10       (normal spending behaviour)

Usage:
  python scripts/score_address.py --address 1ABC...
  python scripts/score_address.py --address 1ABC... --check-counterparties
  python scripts/score_address.py --batch-file addresses.txt
"""

import argparse
import json
import sys
import time
from pathlib import Path

import requests
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    CB_TSV, MEMPOOL_BASE, BTC_DELAY,
    BTC_ADDR_DIR, REPORTS_DIR,
)

SATOSHI = 1e-8

# Load known criminal addresses into a set at startup for O(1) lookup
_CRIMINAL_SET: set[str] = set()

def _load_criminal_db():
    global _CRIMINAL_SET
    if _CRIMINAL_SET:
        return
    try:
        df = pd.read_csv(CB_TSV, sep="\t", usecols=["address", "label"])
        _CRIMINAL_SET = set(df["address"].str.strip())
        print(f"  [db] Loaded {len(_CRIMINAL_SET):,} known criminal BTC addresses")
    except Exception as e:
        print(f"  [db] Could not load criminal DB: {e}")


# ── API helpers ───────────────────────────────────────────────────────────────

def _get(url: str):
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [fetch error] {e}")
        return None


def fetch_summary(address: str) -> dict | None:
    """Address stats: tx_count, funded_txo_sum, spent_txo_sum, balance."""
    cache = BTC_ADDR_DIR / f"{address}.json"
    if cache.exists():
        with open(cache) as f:
            return json.load(f)
    data = _get(f"{MEMPOOL_BASE}/address/{address}")
    if data:
        BTC_ADDR_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data, indent=2))
    time.sleep(BTC_DELAY)
    return data


def fetch_first_page(address: str) -> list[dict]:
    """First page of transactions (up to 50, newest first)."""
    data = _get(f"{MEMPOOL_BASE}/address/{address}/txs")
    time.sleep(BTC_DELAY)
    return data or []


# ── Feature extraction ────────────────────────────────────────────────────────

def extract_features(address: str, check_counterparties: bool = False) -> dict:
    """
    Returns a feature dict ready for scoring.
    Only 2 API calls (summary + first tx page) unless check_counterparties=True.
    """
    summary = fetch_summary(address)
    if not summary:
        return {"address": address, "error": "not_found"}

    stats = summary.get("chain_stats", {})
    tx_count    = stats.get("tx_count", 0)
    funded_sum  = stats.get("funded_txo_sum", 0)    # total received (satoshi)
    spent_sum   = stats.get("spent_txo_sum", 0)     # total sent (satoshi)
    balance     = funded_sum - spent_sum             # current balance

    total_in_btc  = funded_sum * SATOSHI
    total_out_btc = spent_sum  * SATOSHI
    relay_ratio   = spent_sum / funded_sum if funded_sum > 0 else 0.0

    # Fetch first page of transactions for behavioural features
    txs = fetch_first_page(address) if tx_count > 0 else []

    # --- Per-transaction classification ---
    senders:    dict[str, float] = {}
    recipients: dict[str, float] = {}
    tx_amounts_in  = []
    tx_amounts_out = []
    timestamps     = []

    for tx in txs:
        vout_to_us  = sum(o.get("value", 0) for o in tx.get("vout", [])
                          if o.get("scriptpubkey_address") == address)
        vin_from_us = sum((i.get("prevout") or {}).get("value", 0)
                          for i in tx.get("vin", [])
                          if (i.get("prevout") or {}).get("scriptpubkey_address") == address)

        ts = tx.get("status", {}).get("block_time", 0)
        if ts:
            timestamps.append(ts)

        if vout_to_us > 0 and vin_from_us == 0:       # incoming
            tx_amounts_in.append(vout_to_us)
            for inp in tx.get("vin", []):
                sa = inp.get("prevout", {}).get("scriptpubkey_address")
                if sa and sa != address:
                    senders[sa] = senders.get(sa, 0) + vout_to_us / max(
                        len([i for i in tx.get("vin", [])
                             if (i.get("prevout") or {}).get("scriptpubkey_address")]), 1)

        elif vin_from_us > 0 and vout_to_us == 0:     # outgoing
            tx_amounts_out.append(vin_from_us)
            for out in tx.get("vout", []):
                ra = out.get("scriptpubkey_address")
                if ra and ra != address:
                    recipients[ra] = recipients.get(ra, 0) + out.get("value", 0)

    # --- Derived features ---
    n_senders     = len(senders)
    n_recipients  = len(recipients)
    funnel_ratio  = n_senders / max(n_recipients, 1)   # high = many-to-few

    max_in   = max(tx_amounts_in,  default=0) * SATOSHI
    avg_in   = (sum(tx_amounts_in)  / len(tx_amounts_in)  * SATOSHI) if tx_amounts_in  else 0
    max_out  = max(tx_amounts_out, default=0) * SATOSHI
    spike_ratio = max_in / avg_in if avg_in > 0 else 1.0  # dominant single payment

    # Burst detection: all activity within N days
    burst_days = None
    if len(timestamps) >= 2:
        span = (max(timestamps) - min(timestamps)) / 86400
        burst_days = round(span, 1)

    # Counterparty check against criminal DB
    counterparty_hits = []
    if check_counterparties:
        _load_criminal_db()
        all_counterparties = set(senders) | set(recipients)
        counterparty_hits = [a for a in all_counterparties if a in _CRIMINAL_SET]

    return {
        "address":           address,
        "tx_count":          tx_count,
        "total_in_btc":      round(total_in_btc, 8),
        "total_out_btc":     round(total_out_btc, 8),
        "balance_btc":       round(balance * SATOSHI, 8),
        "relay_ratio":       round(relay_ratio, 4),
        "n_senders_seen":    n_senders,
        "n_recipients_seen": n_recipients,
        "funnel_ratio":      round(funnel_ratio, 2),
        "max_recv_btc":      round(max_in, 8),
        "avg_recv_btc":      round(avg_in, 8),
        "spike_ratio":       round(spike_ratio, 2),
        "burst_days":        burst_days,
        "counterparty_hits": counterparty_hits,
        "in_criminal_db":    address in _CRIMINAL_SET,
        "txs_sampled":       len(txs),
    }


# ── Risk scoring ──────────────────────────────────────────────────────────────

RULES = [
    # ── Positive signals — each alone is enough reason to investigate ─────────
    (
        "Near-fresh address: fewer than 5 transactions on chain",
        40,
        lambda f: 0 < f.get("tx_count", 0) < 5,
    ),
    (
        "High relay ratio: 90%+ of received funds forwarded out",
        30,
        lambda f: f.get("relay_ratio", 0) > 0.9 and f.get("tx_count", 0) > 0,
    ),
    (
        "Funnel pattern: many senders to few recipients (ratio > 3, senders >= 2)",
        25,
        lambda f: f.get("funnel_ratio", 0) > 3 and f.get("n_senders_seen", 0) >= 2,
    ),
    (
        "Single dominant incoming payment: one payment is 5x the average",
        20,
        lambda f: f.get("spike_ratio", 0) > 5 and f.get("max_recv_btc", 0) > 0.001,
    ),
    (
        "Short burst lifecycle: all activity within 14 days",
        15,
        lambda f: f.get("burst_days") is not None and f.get("burst_days", 999) <= 14,
    ),
    # ── Negative signals — reduce suspicion ──────────────────────────────────
    (
        "Established wallet: activity spans over 365 days",
        -20,
        lambda f: f.get("burst_days") is not None and f.get("burst_days", 0) > 365,
    ),
    (
        "Normal spending behaviour: sends to more than 10 distinct recipients",
        -15,
        lambda f: f.get("n_recipients_seen", 0) > 10,
    ),
]


def score(features: dict) -> dict:
    if "error" in features:
        return {"risk_level": "UNKNOWN", "score": 0,
                "evidence": [features["error"]], "features": features}

    total  = 0
    evidence = []
    for desc, pts, condition in RULES:
        try:
            if condition(features):
                total += pts
                sign = "+" if pts >= 0 else ""
                evidence.append(f"[{sign}{pts:+d}] {desc}")
        except Exception:
            pass

    total = max(0, min(100, total))

    if total >= 70:
        level = "HIGH"
    elif total >= 40:
        level = "MEDIUM"
    elif total >= 15:
        level = "LOW"
    else:
        level = "CLEAN"

    return {
        "risk_level": level,
        "score":      total,
        "evidence":   evidence,
        "features":   features,
    }


# ── Report printer ────────────────────────────────────────────────────────────

def print_report(result: dict):
    f = result["features"]
    addr = f.get("address", "?")
    print(f"\n{'='*60}")
    print(f"  Address : {addr}")
    print(f"  Risk    : {result['risk_level']}  (score {result['score']}/100)")
    print(f"{'='*60}")
    print(f"  Transactions : {f.get('tx_count', '?')}")
    print(f"  Total in     : {f.get('total_in_btc', 0):.6f} BTC")
    print(f"  Total out    : {f.get('total_out_btc', 0):.6f} BTC")
    print(f"  Balance      : {f.get('balance_btc', 0):.8f} BTC")
    print(f"  Relay ratio  : {f.get('relay_ratio', 0):.2%}")
    print(f"  Funnel ratio : {f.get('funnel_ratio', 0):.1f}x  "
          f"({f.get('n_senders_seen',0)} senders → {f.get('n_recipients_seen',0)} recipients seen)")
    print(f"  Burst span   : {f.get('burst_days', 'N/A')} days")
    print(f"  Spike ratio  : {f.get('spike_ratio', 1):.1f}x  (max recv / avg recv)")
    if f.get("counterparty_hits"):
        print(f"  !! Counterparty hits: {f['counterparty_hits'][:3]}")
    print(f"\n  Evidence:")
    for e in result["evidence"]:
        print(f"    {e}")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    _load_criminal_db()

    parser = argparse.ArgumentParser(description="Risk-score a Bitcoin address")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--address",    help="Single address to score")
    group.add_argument("--batch-file", help="Text file with one address per line")
    parser.add_argument("--check-counterparties", action="store_true",
                        help="Cross-check all seen counterparties against criminal DB "
                             "(slower — adds N counterparty lookups)")
    parser.add_argument("--save-json", action="store_true",
                        help="Save full result JSON to output/reports/")
    args = parser.parse_args()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    BTC_ADDR_DIR.mkdir(parents=True, exist_ok=True)

    addresses = [args.address] if args.address else \
                Path(args.batch_file).read_text().splitlines()

    all_results = []
    for addr in addresses:
        addr = addr.strip()
        if not addr:
            continue
        print(f"Scoring {addr}…")
        features = extract_features(addr, check_counterparties=args.check_counterparties)
        result   = score(features)
        print_report(result)
        all_results.append(result)

        if args.save_json:
            out = REPORTS_DIR / f"btc_{addr[:20]}_score.json"
            out.write_text(json.dumps(result, indent=2))

    # Batch summary CSV
    if len(all_results) > 1:
        rows = []
        for r in all_results:
            f = r["features"]
            rows.append({
                "address":     f.get("address"),
                "risk_level":  r["risk_level"],
                "score":       r["score"],
                "tx_count":    f.get("tx_count"),
                "relay_ratio": f.get("relay_ratio"),
                "funnel_ratio": f.get("funnel_ratio"),
                "balance_btc": f.get("balance_btc"),
                "in_criminal_db": f.get("in_criminal_db"),
                "counterparty_hits": len(f.get("counterparty_hits", [])),
                "evidence":    " | ".join(r["evidence"]),
            })
        df = pd.DataFrame(rows)
        out_csv = REPORTS_DIR / "btc_batch_scores.csv"
        df.to_csv(out_csv, index=False)
        print(f"Batch summary saved: {out_csv}")
        print(df[["address", "risk_level", "score", "relay_ratio",
                  "funnel_ratio", "balance_btc"]].to_string(index=False))


if __name__ == "__main__":
    main()
