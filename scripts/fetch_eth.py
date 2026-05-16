"""
Fetch Ethereum transaction data for criminal addresses from Etherscan.

Requires a free Etherscan API key in config.py → ETHERSCAN_API_KEY.
Get one at: https://etherscan.io/register

Usage examples:
  python scripts/fetch_eth.py --label "Phishing Scam" --top 50
  python scripts/fetch_eth.py --address 0xABC...
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
    CE_TSV, ETH_ADDR_DIR, ETH_TX_DIR,
    ETHERSCAN_BASE, ETHERSCAN_API_KEY, ETH_DELAY, ETH_TX_OFFSET,
)


def _etherscan(params: dict, retries: int = 3) -> list | None:
    params["apikey"] = ETHERSCAN_API_KEY or "YourApiKeyToken"
    for attempt in range(retries):
        try:
            r = requests.get(ETHERSCAN_BASE, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
            if data.get("status") == "0" and data.get("message") == "No transactions found":
                return []
            if data.get("status") == "0":
                # Rate limited or error
                msg = data.get("message", "")
                if "rate" in msg.lower() or "Max rate" in msg:
                    print(f"  [rate-limit] sleeping 5s…")
                    time.sleep(5)
                    continue
                return None
            return data.get("result", [])
        except Exception as exc:
            if attempt == retries - 1:
                print(f"  [error] {exc}")
                return None
            time.sleep(2 ** attempt)


def fetch_address_summary(address: str) -> dict | None:
    path = ETH_ADDR_DIR / f"{address.lower()}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)

    # Balance
    bal_data = _etherscan({"module": "account", "action": "balance",
                            "address": address, "tag": "latest"})
    time.sleep(ETH_DELAY)

    summary = {"address": address, "balance_wei": bal_data[0] if isinstance(bal_data, list) else bal_data}
    path.write_text(json.dumps(summary, indent=2))
    return summary


def fetch_address_txs(address: str) -> list[dict]:
    """
    Fetch normal (external) ETH transactions for an address, paginated.
    Also fetches ERC-20 token transfers.
    """
    tx_list_path = ETH_ADDR_DIR / f"{address.lower()}_txlist.json"
    if tx_list_path.exists():
        with open(tx_list_path) as f:
            return json.load(f)

    all_txs = []
    page = 1
    while True:
        txs = _etherscan({
            "module": "account",
            "action": "txlist",
            "address": address,
            "startblock": 0,
            "endblock": 99999999,
            "page": page,
            "offset": ETH_TX_OFFSET,
            "sort": "asc",
        })
        time.sleep(ETH_DELAY)

        if txs is None or len(txs) == 0:
            break

        all_txs.extend(txs)

        if len(txs) < ETH_TX_OFFSET:
            break
        page += 1

    # Token transfers (ERC-20)
    token_txs = _etherscan({
        "module": "account",
        "action": "tokentx",
        "address": address,
        "startblock": 0,
        "endblock": 99999999,
        "page": 1,
        "offset": ETH_TX_OFFSET,
        "sort": "asc",
    })
    time.sleep(ETH_DELAY)

    result = {
        "normal_txs": all_txs,
        "token_txs": token_txs or [],
    }
    tx_list_path.write_text(json.dumps(result, indent=2))
    return result


# ── Main ───────────────────────────────────────────────────────────────────────

def load_ce(label: str | None = None, top: int | None = None) -> pd.DataFrame:
    df = pd.read_csv(CE_TSV, sep="\t")
    if label:
        df = df[df["label"] == label]
    if top:
        df = df.nlargest(top, "total_received_ETH")
    return df.reset_index(drop=True)


def run(addresses: list[str]):
    ETH_ADDR_DIR.mkdir(parents=True, exist_ok=True)
    ETH_TX_DIR.mkdir(parents=True, exist_ok=True)

    if not ETHERSCAN_API_KEY:
        print("WARNING: No Etherscan API key set in config.py.")
        print("         Rate limit is 1 req/5sec without a key (very slow).")
        print("         Get a free key at https://etherscan.io/register\n")

    print(f"Fetching Ethereum data for {len(addresses)} addresses…")
    for addr in tqdm(addresses):
        fetch_address_summary(addr)
        fetch_address_txs(addr)
    print("Done.")


def main():
    parser = argparse.ArgumentParser(description="Fetch ETH tx data from Etherscan")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--label",   help='Crime label, e.g. "Phishing Scam"')
    group.add_argument("--address", help="Single address to fetch")
    group.add_argument("--file",    help="Text file with one address per line")

    parser.add_argument("--top",  type=int, default=None,
                        help="Limit to top N addresses by total_received_ETH")
    args = parser.parse_args()

    if args.address:
        addresses = [args.address]
    elif args.file:
        addresses = Path(args.file).read_text().splitlines()
    else:
        df = load_ce(label=args.label, top=args.top)
        print(f"Loaded {len(df)} addresses (label={args.label!r})")
        addresses = df["address"].tolist()

    run(addresses)


if __name__ == "__main__":
    main()
