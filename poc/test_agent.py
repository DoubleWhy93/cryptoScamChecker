"""
Quick test script for the Layer 3 investigation agent.
Paste in any Bitcoin or TRX address and see the full trace.

Run from the project root:
    python poc/test_agent.py 1GH9bkaD3QsZyFU1MRcvpmQLj4SiVpARit
    python poc/test_agent.py TApEYDGz8eH9JywtaTWkTwczwPJH368aD3 --amount 25000
"""

import argparse
import os
import sys
from pathlib import Path

# Load .env from project root
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from agent.layer3 import investigate, detect_chain


def main():
    parser = argparse.ArgumentParser(description="Test the Layer 3 investigation agent")
    parser.add_argument("address", help="Address to investigate (BTC or TRX)")
    parser.add_argument("--amount", type=float, default=15000,
                        help="Transaction amount in USD (default: 15000)")
    parser.add_argument("--token", default=None,
                        help="Token being sent — auto-detected if not set (BTC, TRX, USDT)")
    parser.add_argument("--chain", choices=["btc", "trx"], default=None,
                        help="Force chain — auto-detected from address format if not set")
    args = parser.parse_args()

    # Auto-detect chain and token if not provided
    chain = args.chain or detect_chain(args.address)
    if chain == "unknown":
        print(f"ERROR: Cannot detect chain for address: {args.address}")
        print("Supported formats: BTC (1.../3.../bc1...), TRX (T...)")
        sys.exit(1)

    token_defaults = {"btc": "BTC", "trx": "TRX", "eth": "ETH"}
    token = args.token or token_defaults.get(chain, chain.upper())

    if not os.getenv("GOOGLE_API_KEY"):
        print("ERROR: GOOGLE_API_KEY is not set.")
        print("Get a free key at https://aistudio.google.com/apikey")
        print("Then run: $env:GOOGLE_API_KEY = 'your-key-here'")
        sys.exit(1)

    # Generic test account context
    account = {
        "account_age_days": 1000,
        "avg_tx_usd": 15000,
    }

    # Minimal layer2 result — the agent will do its own full investigation
    layer2_result = {
        "score": 0,
        "risk_level": "UNKNOWN",
        "evidence": [],
    }

    print(f"\nAddress : {args.address}")
    print(f"Chain   : {chain.upper()}")
    print(f"Amount  : ${args.amount:,.0f} {token}")
    print(f"Model   : {os.getenv('GEMMA_MODEL', 'gemma-4-31b-it')}")
    print("-" * 60)

    result = investigate(
        address=args.address,
        amount_usd=args.amount,
        token=token,
        account=account,
        layer2_result=layer2_result,
        chain=chain,
        verbose=True,
    )

    print("=" * 60)
    print(f"  RISK LEVEL : {result['risk_level']}")
    print()
    print(f"  MESSAGE TO USER:")
    print(f"  {result.get('user_message', '')}")
    print()
    print(f"  EVIDENCE:")
    for e in result.get("evidence", []):
        print(f"    - {e}")
    print()
    print(f"  SUGGESTIONS:")
    for s in result.get("suggestions", []):
        print(f"    -> {s}")
    print("=" * 60)


if __name__ == "__main__":
    main()
