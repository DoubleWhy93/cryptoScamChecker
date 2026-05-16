"""
Second-hop tracer: fetch and trace the broker/cashout addresses identified
in hop-1 flow JSONs. This reveals where criminal money ultimately lands
(exchanges, mixers, further relays) — the final leg of the cash-out chain.

For each criminal address's top recipients (brokers), this script:
  1. Collects broker addresses from existing flow JSONs
  2. Fetches their transaction lists (same mempool.space API)
  3. Traces their outgoing flows to find final destinations
  4. Flags likely exchange / mixer / relay behaviour

Usage:
  python scripts/trace_hop2.py --chain btc
  python scripts/trace_hop2.py --chain btc --top-brokers 5
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    REPORTS_DIR, BTC_ADDR_DIR,
    MEMPOOL_BASE, BTC_DELAY, MAX_TX_PAGES,
    LARGE_TX_SATOSHI,
)

SATOSHI = 1e-8


# ── Collect broker addresses from hop-1 results ───────────────────────────────

def collect_brokers(chain: str, top_n: int = 5) -> dict[str, dict]:
    """
    Returns {broker_addr: {origin_criminal, label, total_received_btc}}.
    Deduplicates brokers that appear across multiple criminal addresses.
    """
    files = sorted(REPORTS_DIR.glob(f"{chain}_*_flow.json"))
    brokers: dict[str, dict] = {}

    for fp in files:
        with open(fp) as f:
            flow = json.load(f)

        criminal = flow["address"]
        label    = flow["label"]
        unit     = "btc" if chain == "btc" else "eth"

        for addr, amount in flow.get("top_recipients", [])[:top_n]:
            if addr not in brokers:
                brokers[addr] = {
                    "origins":        [],
                    "total_received": 0.0,
                    "labels":         set(),
                }
            brokers[addr]["origins"].append(criminal)
            brokers[addr]["total_received"] += amount
            brokers[addr]["labels"].add(label)

    # Convert sets to lists for JSON serialisation
    for v in brokers.values():
        v["labels"] = list(v["labels"])

    return brokers


# ── Fetch helpers (reuse same mempool.space logic as fetch_btc.py) ────────────

def _get(url: str, retries: int = 3):
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 429:
                print("  [rate-limit] sleeping 10s…")
                time.sleep(10)
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            if attempt == retries - 1:
                print(f"  [error] {url}: {exc}")
                return None
            time.sleep(2 ** attempt)


def fetch_broker_txs(address: str) -> list[dict]:
    """Fetch up to MAX_TX_PAGES pages of transactions for a broker address."""
    cache = BTC_ADDR_DIR / f"{address}_txlist.json"
    if cache.exists():
        with open(cache) as f:
            return json.load(f)

    all_txs = []
    last_txid = None
    for _ in range(MAX_TX_PAGES):
        url = (f"{MEMPOOL_BASE}/address/{address}/txs/chain/{last_txid}"
               if last_txid else f"{MEMPOOL_BASE}/address/{address}/txs")
        txs = _get(url)
        time.sleep(BTC_DELAY)
        if not txs:
            break
        all_txs.extend(txs)
        if len(txs) < 25:
            break
        last_txid = txs[-1]["txid"]

    cache.write_text(json.dumps(all_txs, indent=2))
    return all_txs


# ── Classify broker transactions ──────────────────────────────────────────────

def trace_broker(address: str, origin_criminals: list[str]) -> dict:
    """
    For a broker address, classify each transaction:
      - CONSOLIDATION : receives from multiple criminal addresses
      - CASHOUT       : large outgoing to a single destination (likely exchange)
      - RELAY         : passes through to another intermediary
    """
    txs = fetch_broker_txs(address)
    criminal_set = set(origin_criminals)

    incoming_from_criminal = []
    outgoing = []

    for tx in txs:
        vout_to_broker = sum(
            o.get("value", 0) for o in tx.get("vout", [])
            if o.get("scriptpubkey_address") == address
        )
        vin_from_broker = sum(
            (i.get("prevout") or {}).get("value", 0)
            for i in tx.get("vin", [])
            if (i.get("prevout") or {}).get("scriptpubkey_address") == address
        )

        # Incoming to broker
        if vout_to_broker > 0 and vin_from_broker == 0:
            senders = [
                (i.get("prevout") or {}).get("scriptpubkey_address")
                for i in tx.get("vin", [])
                if (i.get("prevout") or {}).get("scriptpubkey_address")
            ]
            from_criminal = [s for s in senders if s in criminal_set]
            if from_criminal and vout_to_broker >= LARGE_TX_SATOSHI:
                incoming_from_criminal.append({
                    "txid":            tx.get("txid"),
                    "amount_btc":      round(vout_to_broker * SATOSHI, 8),
                    "criminal_senders": from_criminal,
                    "timestamp":       tx.get("status", {}).get("block_time", 0),
                })

        # Outgoing from broker
        if vin_from_broker > 0 and vout_to_broker == 0 and vin_from_broker >= LARGE_TX_SATOSHI:
            recipients = list({
                o["scriptpubkey_address"]
                for o in tx.get("vout", [])
                if o.get("scriptpubkey_address") and o["scriptpubkey_address"] != address
            })
            outgoing.append({
                "txid":       tx.get("txid"),
                "amount_btc": round(vin_from_broker * SATOSHI, 8),
                "recipients": recipients,
                "n_outputs":  len(tx.get("vout", [])),
                "timestamp":  tx.get("status", {}).get("block_time", 0),
            })

    # Classify behaviour
    out_counts = defaultdict(float)
    for e in outgoing:
        share = e["amount_btc"] / max(len(e["recipients"]), 1)
        for r in e["recipients"]:
            out_counts[r] += share

    top_cashout = sorted(out_counts.items(), key=lambda x: -x[1])[:10]

    # Heuristics
    n_origins  = len({s for e in incoming_from_criminal for s in e["criminal_senders"]})
    n_cashout  = len(top_cashout)
    total_in   = sum(e["amount_btc"] for e in incoming_from_criminal)
    total_out  = sum(e["amount_btc"] for e in outgoing)

    if n_origins >= 2:
        behaviour = "CONSOLIDATION"   # aggregates multiple criminal sources
    elif n_cashout == 1:
        behaviour = "CASHOUT"          # funnels to a single final destination
    else:
        behaviour = "RELAY"            # passes through to several next hops

    return {
        "address":                address,
        "behaviour":              behaviour,
        "n_criminal_origins":     n_origins,
        "total_in_from_criminal": round(total_in, 8),
        "total_out_btc":          round(total_out, 8),
        "incoming_from_criminal": incoming_from_criminal[:20],
        "top_cashout_destinations": top_cashout,
        "tx_count":               len(txs),
    }


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Second-hop broker tracer")
    parser.add_argument("--chain", choices=["btc"], default="btc")
    parser.add_argument("--top-brokers", type=int, default=5,
                        help="Top N broker addresses per criminal to trace (default 5)")
    args = parser.parse_args()

    print(f"Collecting broker addresses from hop-1 flow files…")
    brokers = collect_brokers(args.chain, top_n=args.top_brokers)
    print(f"Found {len(brokers)} unique broker addresses\n")

    rows = []
    for addr, meta in brokers.items():
        print(f"Tracing broker: {addr[:24]}…  "
              f"(from {len(meta['origins'])} criminal(s), "
              f"{meta['total_received']:.4f} BTC received)")
        result = trace_broker(addr, meta["origins"])
        result["origin_labels"]   = meta["labels"]
        result["origin_criminals"] = meta["origins"]

        # Save per-broker JSON
        out = REPORTS_DIR / f"btc_broker_{addr[:20]}_hop2.json"
        out.write_text(json.dumps(result, indent=2))

        rows.append({
            "broker_address":       addr,
            "behaviour":            result["behaviour"],
            "n_criminal_origins":   result["n_criminal_origins"],
            "total_in_criminal_btc": result["total_in_from_criminal"],
            "total_out_btc":        result["total_out_btc"],
            "tx_count":             result["tx_count"],
            "top_cashout_dest":     result["top_cashout_destinations"][0][0]
                                    if result["top_cashout_destinations"] else "",
            "top_cashout_btc":      result["top_cashout_destinations"][0][1]
                                    if result["top_cashout_destinations"] else 0,
            "crime_labels":         ", ".join(result["origin_labels"]),
        })

    df = pd.DataFrame(rows)
    out_csv = REPORTS_DIR / f"{args.chain}_hop2_brokers.csv"
    df.to_csv(out_csv, index=False)

    print(f"\n{'='*70}")
    print("  Second-hop broker summary")
    print(f"{'='*70}")
    for _, row in df.iterrows():
        print(f"\n  {row['broker_address']}")
        print(f"  Behaviour : {row['behaviour']}")
        print(f"  Origins   : {row['n_criminal_origins']} criminal address(es)")
        print(f"  In (criminal) : {row['total_in_criminal_btc']:.4f} BTC")
        print(f"  Out total     : {row['total_out_btc']:.4f} BTC")
        if row["top_cashout_dest"]:
            print(f"  Top cashout → {row['top_cashout_dest']}  "
                  f"({row['top_cashout_btc']:.4f} BTC)")
    print(f"\nSaved: {out_csv}\n")


if __name__ == "__main__":
    main()
