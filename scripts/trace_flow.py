"""
Trace money flow for criminal Bitcoin or Ethereum addresses.

For each criminal address, this script:
  1. Classifies every transaction as INCOMING (victim→criminal) or OUTGOING (criminal→broker)
  2. Identifies "victim" addresses (sent large amounts to criminal)
  3. Identifies "broker/cashout" addresses (received large amounts from criminal)
  4. Exports a flow graph per address + an aggregated CSV report

Bitcoin model:
  A TX has inputs (spending UTXOs) and outputs (creating UTXOs).
  - If criminal address appears in OUTPUTS → funds are ARRIVING (tx is incoming).
    The input addresses are the senders (victims or relay hops).
  - If criminal address appears in INPUTS → funds are LEAVING (tx is outgoing).
    The output addresses (excluding likely change) are recipients.

Ethereum model:
  - tx.from == criminal → outgoing
  - tx.to   == criminal → incoming

Usage:
  python scripts/trace_flow.py --address 1ABC... --chain btc
  python scripts/trace_flow.py --label Ransomware --top 20 --chain btc
  python scripts/trace_flow.py --label "Phishing Scam" --top 20 --chain eth
"""

import argparse
import json
import sys
from pathlib import Path
from collections import defaultdict

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    CB_TSV, CE_TSV,
    BTC_ADDR_DIR, ETH_ADDR_DIR,
    REPORTS_DIR, GRAPHS_DIR,
    LARGE_TX_SATOSHI, LARGE_TX_WEI, SIGNIFICANT_USD,
)

SATOSHI = 1e-8   # BTC per satoshi
WEI     = 1e-18  # ETH per wei


# ══════════════════════════════════════════════════════════════════════════════
# Bitcoin helpers
# ══════════════════════════════════════════════════════════════════════════════

def _btc_load_txs(address: str, max_txs: int = 0) -> list[dict]:
    path = BTC_ADDR_DIR / f"{address}_txlist.json"
    if not path.exists():
        return []
    size_mb = path.stat().st_size / 1024 / 1024
    if size_mb > 20:
        print(f"  [info] {address[:20]}… txlist is {size_mb:.0f} MB — loading (may take a moment)")
    with open(path) as f:
        txs = json.load(f)
    if max_txs and len(txs) > max_txs:
        # Keep the transactions most likely to contain large flows:
        # sort by max value among inputs/outputs touching the criminal address, desc
        def _max_val(tx):
            out_val = max((o.get("value", 0) for o in tx.get("vout", [])
                           if o.get("scriptpubkey_address") == address), default=0)
            in_val  = max((i.get("prevout", {}).get("value", 0) for i in tx.get("vin", [])
                           if i.get("prevout", {}).get("scriptpubkey_address") == address), default=0)
            return max(out_val, in_val)
        txs.sort(key=_max_val, reverse=True)
        txs = txs[:max_txs]
        print(f"  [info] capped to top {max_txs} txs by value")
    return txs


def _btc_classify(tx: dict, criminal_addr: str) -> tuple[str, list, list, int]:
    """
    Returns (direction, sender_addrs, recipient_addrs, net_value_satoshi).
    direction: 'incoming' | 'outgoing' | 'self' | 'unknown'
    """
    vout_to_criminal = sum(
        o.get("value", 0)
        for o in tx.get("vout", [])
        if o.get("scriptpubkey_address") == criminal_addr
    )
    vin_from_criminal = sum(
        (inp.get("prevout") or {}).get("value", 0)
        for inp in tx.get("vin", [])
        if (inp.get("prevout") or {}).get("scriptpubkey_address") == criminal_addr
    )

    if vout_to_criminal > 0 and vin_from_criminal == 0:
        direction = "incoming"
        senders = list({
            inp["prevout"]["scriptpubkey_address"]
            for inp in tx.get("vin", [])
            if (inp.get("prevout") or {}).get("scriptpubkey_address")
        })
        return direction, senders, [], vout_to_criminal

    if vin_from_criminal > 0 and vout_to_criminal == 0:
        direction = "outgoing"
        recipients = list({
            o["scriptpubkey_address"]
            for o in tx.get("vout", [])
            if o.get("scriptpubkey_address") and o.get("scriptpubkey_address") != criminal_addr
        })
        return direction, [], recipients, vin_from_criminal

    if vin_from_criminal > 0 and vout_to_criminal > 0:
        # Self-churn or partial spend with change
        direction = "outgoing"
        recipients = list({
            o["scriptpubkey_address"]
            for o in tx.get("vout", [])
            if o.get("scriptpubkey_address") and o.get("scriptpubkey_address") != criminal_addr
        })
        net = vin_from_criminal - vout_to_criminal
        return direction, [], recipients, net

    return "unknown", [], [], 0


def trace_btc(address: str, label: str, max_txs: int = 5000) -> dict:
    txs = _btc_load_txs(address, max_txs=max_txs)
    if not txs:
        return {"address": address, "label": label, "error": "no_data"}

    flow = {
        "address": address,
        "label": label,
        "chain": "btc",
        "tx_count": len(txs),
        "incoming": [],   # list of {txid, senders, amount_btc, timestamp}
        "outgoing": [],   # list of {txid, recipients, amount_btc, timestamp}
    }

    for tx in txs:
        direction, senders, recipients, value_sat = _btc_classify(tx, address)
        ts = tx.get("status", {}).get("block_time", 0)
        txid = tx.get("txid", "")

        if direction == "incoming" and value_sat >= LARGE_TX_SATOSHI:
            flow["incoming"].append({
                "txid": txid,
                "senders": senders,
                "amount_btc": round(value_sat * SATOSHI, 8),
                "timestamp": ts,
            })
        elif direction == "outgoing" and value_sat >= LARGE_TX_SATOSHI:
            flow["outgoing"].append({
                "txid": txid,
                "recipients": recipients,
                "amount_btc": round(value_sat * SATOSHI, 8),
                "timestamp": ts,
            })

    # Aggregate: who are the top senders (victims) and top recipients (brokers)?
    sender_totals: dict[str, float] = defaultdict(float)
    for e in flow["incoming"]:
        share = e["amount_btc"] / max(len(e["senders"]), 1)
        for s in e["senders"]:
            sender_totals[s] += share

    recipient_totals: dict[str, float] = defaultdict(float)
    for e in flow["outgoing"]:
        share = e["amount_btc"] / max(len(e["recipients"]), 1)
        for r in e["recipients"]:
            recipient_totals[r] += share

    flow["top_senders"]    = sorted(sender_totals.items(), key=lambda x: -x[1])[:20]
    flow["top_recipients"] = sorted(recipient_totals.items(), key=lambda x: -x[1])[:20]
    flow["total_in_btc"]   = round(sum(e["amount_btc"] for e in flow["incoming"]), 8)
    flow["total_out_btc"]  = round(sum(e["amount_btc"] for e in flow["outgoing"]), 8)

    return flow


# ══════════════════════════════════════════════════════════════════════════════
# Ethereum helpers
# ══════════════════════════════════════════════════════════════════════════════

def _eth_load_txs(address: str) -> dict:
    path = ETH_ADDR_DIR / f"{address.lower()}_txlist.json"
    if not path.exists():
        return {"normal_txs": [], "token_txs": []}
    with open(path) as f:
        return json.load(f)


def trace_eth(address: str, label: str) -> dict:
    data = _eth_load_txs(address)
    normal_txs = data.get("normal_txs", [])
    addr_lower = address.lower()

    flow = {
        "address": address,
        "label": label,
        "chain": "eth",
        "tx_count": len(normal_txs),
        "incoming": [],
        "outgoing": [],
    }

    for tx in normal_txs:
        value_wei = int(tx.get("value", "0"))
        is_error  = tx.get("isError", "0") == "1"
        if is_error or value_wei == 0:
            continue

        frm = tx.get("from", "").lower()
        to  = tx.get("to", "").lower()
        ts  = int(tx.get("timeStamp", 0))
        txid = tx.get("hash", "")

        if to == addr_lower and value_wei >= LARGE_TX_WEI:
            flow["incoming"].append({
                "txid": txid,
                "senders": [frm],
                "amount_eth": round(value_wei * WEI, 6),
                "timestamp": ts,
            })
        elif frm == addr_lower and value_wei >= LARGE_TX_WEI:
            flow["outgoing"].append({
                "txid": txid,
                "recipients": [to],
                "amount_eth": round(value_wei * WEI, 6),
                "timestamp": ts,
            })

    sender_totals: dict[str, float] = defaultdict(float)
    for e in flow["incoming"]:
        for s in e["senders"]:
            sender_totals[s] += e["amount_eth"]

    recipient_totals: dict[str, float] = defaultdict(float)
    for e in flow["outgoing"]:
        for r in e["recipients"]:
            recipient_totals[r] += e["amount_eth"]

    flow["top_senders"]    = sorted(sender_totals.items(), key=lambda x: -x[1])[:20]
    flow["top_recipients"] = sorted(recipient_totals.items(), key=lambda x: -x[1])[:20]
    flow["total_in_eth"]   = round(sum(e["amount_eth"] for e in flow["incoming"]), 6)
    flow["total_out_eth"]  = round(sum(e["amount_eth"] for e in flow["outgoing"]), 6)

    return flow


# ══════════════════════════════════════════════════════════════════════════════
# Batch runner + report
# ══════════════════════════════════════════════════════════════════════════════

def run_batch(addresses_labels: list[tuple[str, str]], chain: str,
              max_txs: int = 5000) -> list[dict]:
    results = []
    for addr, label in addresses_labels:
        if chain == "btc":
            flow = trace_btc(addr, label, max_txs=max_txs)
        else:
            flow = trace_eth(addr, label)
        results.append(flow)
    return results


def save_flow_json(flow: dict, chain: str):
    out = REPORTS_DIR / f"{chain}_{flow['address'][:20]}_flow.json"
    out.write_text(json.dumps(flow, indent=2))


def save_summary_csv(flows: list[dict], chain: str, tag: str):
    """Save a flat CSV summary of all traced addresses."""
    rows = []
    unit = "btc" if chain == "btc" else "eth"
    total_in_key  = f"total_in_{unit}"
    total_out_key = f"total_out_{unit}"

    for f in flows:
        if "error" in f:
            continue
        top_recv = f.get("top_recipients", [])
        top_send = f.get("top_senders", [])
        rows.append({
            "address": f["address"],
            "label": f["label"],
            "tx_count": f["tx_count"],
            "n_large_incoming": len(f.get("incoming", [])),
            "n_large_outgoing": len(f.get("outgoing", [])),
            "total_in": f.get(total_in_key, 0),
            "total_out": f.get(total_out_key, 0),
            "n_unique_senders": len(f.get("top_senders", [])),
            "n_unique_recipients": len(f.get("top_recipients", [])),
            "top_recipient_addr": top_recv[0][0] if top_recv else "",
            "top_recipient_amount": top_recv[0][1] if top_recv else 0,
            "top_sender_addr": top_send[0][0] if top_send else "",
            "top_sender_amount": top_send[0][1] if top_send else 0,
        })

    if not rows:
        print("No data rows to save.")
        return

    df = pd.DataFrame(rows)
    out = REPORTS_DIR / f"{chain}_{tag}_flow_summary.csv"
    df.to_csv(out, index=False)
    print(f"Saved summary: {out}")
    print(df.describe(include="all").to_string())


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Trace money flow for criminal addresses")
    parser.add_argument("--chain", choices=["btc", "eth"], required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--address", help="Single address")
    group.add_argument("--label",   help='Crime label, e.g. "Ransomware"')
    group.add_argument("--all",     action="store_true", help="All available data")

    parser.add_argument("--top",    type=int, default=None)
    parser.add_argument("--save-json", action="store_true",
                        help="Save individual JSON flow file per address")
    parser.add_argument("--max-txs", type=int, default=5000,
                        help="Max transactions to analyse per address (default 5000, 0=all)")
    args = parser.parse_args()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    tsv = CB_TSV if args.chain == "btc" else CE_TSV
    df = pd.read_csv(tsv, sep="\t")

    if args.address:
        row = df[df["address"] == args.address]
        label = row["label"].iloc[0] if not row.empty else "Unknown"
        pairs = [(args.address, label)]
    elif args.label:
        sub = df[df["label"] == args.label]
        if args.top:
            col = "total_received_BTC" if args.chain == "btc" else "total_received_ETH"
            sub = sub.nlargest(args.top, col)
        pairs = list(zip(sub["address"], sub["label"]))
    else:
        col = "total_received_BTC" if args.chain == "btc" else "total_received_ETH"
        if args.top:
            df = df.nlargest(args.top, col)
        pairs = list(zip(df["address"], df["label"]))

    print(f"Tracing {len(pairs)} addresses on {args.chain.upper()}…")
    flows = run_batch(pairs, args.chain, max_txs=args.max_txs)

    if args.save_json:
        for flow in flows:
            save_flow_json(flow, args.chain)

    tag = (args.label or "all").replace(" ", "_")
    save_summary_csv(flows, args.chain, tag)


if __name__ == "__main__":
    main()
