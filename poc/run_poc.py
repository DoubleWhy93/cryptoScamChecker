"""
PoC evaluation: behavioral scam detection against known criminal addresses.

Samples N addresses from CB.tsv and checks each one in BLIND mode —
no blacklist lookup, only on-chain behavioral signals. This simulates
the real-world scenario of encountering a brand-new high-alert address
that has never been seen in any database before.

Detection counts as a hit if the address scores HIGH or MEDIUM.

Usage:
  python poc/run_poc.py                     # 20 random addresses
  python poc/run_poc.py --n 10 --seed 7
  python poc/run_poc.py --labels Ransomware "Phishing Scam"
  python poc/run_poc.py --n 5 --with-db     # compare blind vs DB mode
  python poc/run_poc.py --address 1GH9bka...  # single address
"""

import sys
import argparse
from pathlib import Path

import pandas as pd

# Make scripts/ importable
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).parent))

from agent import check_address

DATA_DIR = ROOT / "data"
OUT_DIR  = ROOT / "output" / "reports"


def load_sample(
    n: int,
    seed: int,
    labels: list[str] | None = None,
) -> pd.DataFrame:
    cb = pd.read_csv(DATA_DIR / "CB.tsv", sep="\t", usecols=["address", "label"])
    if labels:
        cb = cb[cb["label"].isin(labels)]
    if len(cb) == 0:
        raise ValueError(f"No addresses found for labels: {labels}")
    return cb.sample(n=min(n, len(cb)), random_state=seed).reset_index(drop=True)


def run(
    addresses: list[tuple[str, str]] | None = None,
    n: int = 20,
    seed: int = 42,
    labels: list[str] | None = None,
    with_db: bool = False,
) -> list[dict]:
    """
    addresses: optional list of (address, label) pairs — bypasses CSV sampling
    n:         number of random addresses to sample from CB.tsv
    seed:      random seed for reproducibility
    labels:    filter CB.tsv to specific crime categories
    with_db:   also run with DB lookup and print comparison column
    """
    if addresses is None:
        sample = load_sample(n, seed, labels)
        addresses = list(zip(sample["address"].str.strip(), sample["label"]))

    print("=" * 70)
    print("  CRYPTO SCAM DETECTION — PROOF OF CONCEPT")
    print("=" * 70)
    print(f"  Mode: BLIND (no blacklist lookup) — pure behavioral detection")
    print(f"  Test set: {len(addresses)} known criminal BTC addresses from CB.tsv")
    label_counts: dict[str, int] = {}
    for _, lbl in addresses:
        label_counts[lbl] = label_counts.get(lbl, 0) + 1
    for lbl, cnt in sorted(label_counts.items(), key=lambda x: -x[1]):
        print(f"    {cnt:3d}  {lbl}")
    print()

    results = []
    for i, (addr, true_label) in enumerate(addresses, 1):
        print(f"[{i}/{len(addresses)}] {addr}  (known: {true_label})")
        blind_result = check_address(addr, use_db=False, verbose=True)

        row: dict = {
            "address":       addr,
            "true_label":    true_label,
            "risk_level":    blind_result["risk_level"],
            "score":         blind_result["score"],
            "tx_count":      blind_result["features"].get("tx_count", 0),
            "relay_ratio":   blind_result["features"].get("relay_ratio", 0),
            "funnel_ratio":  blind_result["features"].get("funnel_ratio", 0),
            "burst_days":    blind_result["features"].get("burst_days"),
            "balance_btc":   blind_result["features"].get("balance_btc", 0),
            "evidence":      " | ".join(blind_result.get("evidence", [])),
        }

        if with_db:
            full_result = check_address(addr, use_db=True, verbose=False)
            row["score_with_db"]  = full_result["score"]
            row["level_with_db"]  = full_result["risk_level"]
            row["in_criminal_db"] = full_result["features"].get("in_criminal_db", False)

        results.append(row)

    _print_summary(results, with_db=with_db)
    _save_results(results)
    return results


def _print_summary(results: list[dict], with_db: bool = False):
    valid   = [r for r in results if r["risk_level"] != "UNKNOWN"]
    detected = [r for r in valid if r["risk_level"] in ("HIGH", "MEDIUM")]
    missed   = [r for r in valid if r["risk_level"] in ("LOW", "CLEAN")]
    unknown  = [r for r in results if r["risk_level"] == "UNKNOWN"]

    print()
    print("=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    det_pct = len(detected) / len(valid) * 100 if valid else 0
    print(f"\n  Addresses tested : {len(results)}")
    print(f"  Valid (data found): {len(valid)}")
    print(f"\n  Detection rate (HIGH+MEDIUM, blind): "
          f"{len(detected)}/{len(valid)} = {det_pct:.0f}%")
    print()
    for level in ("HIGH", "MEDIUM", "LOW", "CLEAN"):
        count = sum(1 for r in results if r["risk_level"] == level)
        bar   = "#" * count
        print(f"    {level:8s}: {count:3d}  {bar}")
    if unknown:
        print(f"    UNKNOWN  : {len(unknown):3d}  (address not found on chain)")

    # Per-address table
    print()
    w_db = "  DB-score" if with_db else ""
    print(f"  {'Address':45s} {'Label':20s} {'Level':8s} {'Score':5s} {'TxCnt':6s}{w_db}")
    print("  " + "-" * (80 + (10 if with_db else 0)))
    for r in sorted(results, key=lambda x: (-x["score"], x["true_label"])):
        db_col = f"  {r.get('score_with_db', ''):>5}" if with_db else ""
        print(f"  {r['address']:45s} {r['true_label']:20s} "
              f"{r['risk_level']:8s} {r['score']:5d} {r['tx_count']:6d}{db_col}")

    # Missed addresses — useful for debugging false negatives
    if missed:
        print(f"\n  Missed addresses ({len(missed)} — scored LOW or CLEAN in blind mode):")
        for r in missed:
            print(f"    {r['address']}  {r['true_label']}  "
                  f"score={r['score']}  txs={r['tx_count']}  "
                  f"relay={r['relay_ratio']:.0%}  funnel={r['funnel_ratio']:.1f}x  "
                  f"burst={r['burst_days']}d")
            if r.get("evidence"):
                print(f"      evidence: {r['evidence'][:120]}")
            else:
                print(f"      evidence: (none)")


def _save_results(results: list[dict]):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "poc_results.csv"
    pd.DataFrame(results).to_csv(out, index=False)
    print(f"\n  Results saved: {out}")


def main():
    parser = argparse.ArgumentParser(
        description="PoC: behavioral scam detection against CB.tsv test set",
        epilog=(
            "Examples:\n"
            "  python poc/run_poc.py                        # 20 random from CB.tsv\n"
            "  python poc/run_poc.py 1GH9bkaD3QsZy...      # test your own address\n"
            "  python poc/run_poc.py --n 10 --seed 7\n"
            "  python poc/run_poc.py --labels Ransomware\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Positional address — optional, lets user just type: python run_poc.py <addr>
    parser.add_argument(
        "address_pos", nargs="?", metavar="ADDRESS",
        help="Single Bitcoin address to check (positional shorthand)"
    )
    parser.add_argument(
        "--address", "-a",
        help="Single Bitcoin address to check (flag form)"
    )
    parser.add_argument(
        "--n", type=int, default=20,
        help="Number of random criminal addresses to sample from CB.tsv (default: 20)"
    )
    parser.add_argument("--seed",    type=int, default=42, help="Random seed")
    parser.add_argument(
        "--labels", nargs="+",
        help='Filter to specific crime labels, e.g. --labels Ransomware "Phishing Scam"'
    )
    parser.add_argument(
        "--with-db", action="store_true",
        help="Also score with CB.tsv blacklist lookup and show comparison column"
    )
    args = parser.parse_args()

    # Positional arg takes priority, then --address flag
    single = args.address_pos or args.address
    if single:
        run(
            addresses=[(single.strip(), "manual")],
            with_db=args.with_db,
        )
    else:
        run(
            n=args.n,
            seed=args.seed,
            labels=args.labels,
            with_db=args.with_db,
        )


if __name__ == "__main__":
    main()
