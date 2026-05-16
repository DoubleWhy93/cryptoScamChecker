"""
Fetch Bitcoin transaction data for criminal addresses from mempool.space.

Usage examples:
  # Fetch top-50 Ransomware addresses (by total received):
  python scripts/fetch_btc.py --label Ransomware --top 50

  # Fetch a specific address:
  python scripts/fetch_btc.py --address 1A1zP1eP5QGefi2DMPTfTL5SLmv7Divf

  # Fetch all Blackmail Scam addresses (slow):
  python scripts/fetch_btc.py --label "Blackmail Scam"

Data is cached under data/btc/addresses/<addr>.json and data/btc/txs/<txid>.json
Re-running skips already-downloaded files.
"""

import argparse
import json
import time
import sys
from pathlib import Path

import requests
import pandas as pd
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    CB_TSV, BTC_ADDR_DIR, BTC_TX_DIR,
    MEMPOOL_BASE, BTC_DELAY, MAX_TX_PAGES,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get(url: str, retries: int = 3) -> dict | list | None:
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 429:
                print(f"  [rate-limit] sleeping 10s…")
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


def fetch_address_summary(address: str) -> dict | None:
    """Return address stats from mempool.space."""
    path = BTC_ADDR_DIR / f"{address}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)

    data = _get(f"{MEMPOOL_BASE}/address/{address}")
    if data:
        path.write_text(json.dumps(data, indent=2))
    time.sleep(BTC_DELAY)
    return data


def fetch_address_txs(address: str) -> list[dict]:
    """
    Fetch all confirmed transactions for an address via mempool.space pagination.
    Each page returns up to 25 transactions. We paginate using the last txid.
    """
    tx_list_path = BTC_ADDR_DIR / f"{address}_txlist.json"
    if tx_list_path.exists():
        with open(tx_list_path) as f:
            return json.load(f)

    all_txs = []
    last_txid = None

    for page in range(MAX_TX_PAGES):
        if last_txid:
            url = f"{MEMPOOL_BASE}/address/{address}/txs/chain/{last_txid}"
        else:
            url = f"{MEMPOOL_BASE}/address/{address}/txs"

        txs = _get(url)
        time.sleep(BTC_DELAY)

        if not txs:
            break

        all_txs.extend(txs)

        if len(txs) < 25:
            break  # last page
        last_txid = txs[-1]["txid"]

    # Cache the tx list
    tx_list_path.write_text(json.dumps(all_txs, indent=2))
    return all_txs


def fetch_tx_detail(txid: str) -> dict | None:
    """Fetch a single transaction by txid."""
    path = BTC_TX_DIR / f"{txid}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)

    data = _get(f"{MEMPOOL_BASE}/tx/{txid}")
    if data:
        path.write_text(json.dumps(data, indent=2))
    time.sleep(BTC_DELAY)
    return data


# ── Main ───────────────────────────────────────────────────────────────────────

def load_cb(label: str | None = None, top: int | None = None) -> pd.DataFrame:
    df = pd.read_csv(CB_TSV, sep="\t")
    if label:
        df = df[df["label"] == label]
    if top:
        df = df.nlargest(top, "total_received_BTC")
    return df.reset_index(drop=True)


def run(addresses: list[str], fetch_tx_details: bool = False):
    BTC_ADDR_DIR.mkdir(parents=True, exist_ok=True)
    BTC_TX_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Fetching data for {len(addresses)} addresses…")
    for addr in tqdm(addresses):
        # 1. Address summary
        summary = fetch_address_summary(addr)
        if not summary:
            continue

        # 2. Transaction list
        txs = fetch_address_txs(addr)

        # 3. Optionally fetch full tx detail for each transaction
        if fetch_tx_details:
            for tx in txs:
                fetch_tx_detail(tx["txid"])

    print("Done.")


def main():
    parser = argparse.ArgumentParser(description="Fetch BTC tx data from mempool.space")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--label",   help='Crime label, e.g. "Ransomware"')
    group.add_argument("--address", help="Single address to fetch")
    group.add_argument("--file",    help="Text file with one address per line")

    parser.add_argument("--top",      type=int, default=None,
                        help="Limit to top N addresses by total_received_BTC")
    parser.add_argument("--tx-details", action="store_true",
                        help="Also download full JSON for each transaction")
    args = parser.parse_args()

    if args.address:
        addresses = [args.address]
    elif args.file:
        addresses = Path(args.file).read_text().splitlines()
    else:
        df = load_cb(label=args.label, top=args.top)
        print(f"Loaded {len(df)} addresses (label={args.label!r})")
        addresses = df["address"].tolist()

    run(addresses, fetch_tx_details=args.tx_details)


if __name__ == "__main__":
    main()
