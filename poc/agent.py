"""
Scam detection agent — proof of concept.

Takes a Bitcoin address and traces its on-chain money flow via two
mempool.space API calls to determine if it shows scam patterns.

Designed for the "brand new high-alert address" scenario: no pre-computed
blacklist is consulted. Only behavioral signals are used:
  - relay_ratio   (funds swept clean)
  - funnel_ratio  (many victims -> few brokers)
  - spike_ratio   (single dominant payment)
  - burst_days    (short active window then dormant)
  - balance       (zero balance after activity)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from score_address import extract_features, score, _load_criminal_db


def detect_chain(address: str) -> str:
    """
    Infer blockchain from address format.
      btc  — starts with 1/3 (legacy/P2SH) or bc1 (bech32)
      eth  — 0x-prefixed, 42 chars (ETH + all ERC-20)
      trx  — T-prefixed, 34 chars (USDT-TRC20)
    """
    a = address.strip()
    if a.startswith(("1", "3")) or a.startswith("bc1"):
        return "btc"
    if a.startswith("0x") and len(a) == 42:
        return "eth"
    if a.startswith("T") and len(a) == 34:
        return "trx"
    return "unknown"


def check_address(
    address: str,
    use_db: bool = False,
    verbose: bool = True,
) -> dict:
    """
    Run the scam detection agent on a single Bitcoin address.

    Args:
        address:  BTC address to analyse
        use_db:   If True, also flag addresses found in CB.tsv (full mode).
                  If False (default), blind mode — simulates a brand-new address
                  not yet in any blacklist.
        verbose:  Print step-by-step API trace and feature values.

    Returns:
        {
            risk_level: "HIGH" | "MEDIUM" | "LOW" | "CLEAN" | "UNKNOWN"
            score:      int 0-100
            evidence:   list[str]   — rules that fired
            features:   dict        — raw extracted features
        }
    """
    chain = detect_chain(address)
    if chain != "btc":
        msg = (
            f"unsupported chain '{chain}' — only BTC is implemented in this PoC"
            if chain != "unknown"
            else "unrecognised address format (not BTC/ETH/TRX)"
        )
        if verbose:
            print(f"[agent] ERROR: {msg}")
        return {
            "risk_level": "UNKNOWN",
            "score": 0,
            "evidence": [msg],
            "features": {"address": address, "error": msg},
        }

    if use_db:
        _load_criminal_db()

    if verbose:
        mode = "FULL (with DB)" if use_db else "BLIND (no DB)"
        print(f"\n[agent] ── {mode} ──")
        print(f"[agent] Analysing: {address}  [chain: {chain.upper()}]")
        print(f"[agent] API call 1: GET /address/{address[:24]}...")

    features = extract_features(address, check_counterparties=False)

    if "error" in features:
        if verbose:
            print(f"[agent] Error: {features['error']}")
        return {
            "risk_level": "UNKNOWN",
            "score": 0,
            "evidence": [features["error"]],
            "features": features,
        }

    if verbose:
        print(f"[agent] API call 2: GET /address/{address[:24]}.../txs "
              f"(sampled {features['txs_sampled']} of {features['tx_count']} txs)")
        print(f"[agent]")
        print(f"[agent]   tx_count     = {features['tx_count']}")
        print(f"[agent]   balance      = {features['balance_btc']:.8f} BTC  "
              f"({'swept clean' if features['balance_btc'] == 0 and features['tx_count'] > 0 else 'has balance'})")
        print(f"[agent]   relay_ratio  = {features['relay_ratio']:.2%}  "
              f"(total out / total in)")
        print(f"[agent]   funnel_ratio = {features['funnel_ratio']:.1f}x  "
              f"({features['n_senders_seen']} senders -> {features['n_recipients_seen']} recipients seen)")
        print(f"[agent]   spike_ratio  = {features['spike_ratio']:.1f}x  "
              f"(max recv {features['max_recv_btc']:.4f} BTC / avg recv {features['avg_recv_btc']:.4f} BTC)")
        print(f"[agent]   burst_days   = {features['burst_days']}  "
              f"(span of observed activity)")
        if use_db:
            print(f"[agent]   in_criminal_db = {features['in_criminal_db']}")

    result = score(features)

    if verbose:
        level_labels = {
            "HIGH":    "HIGH RISK  -- likely criminal",
            "MEDIUM":  "MEDIUM RISK -- suspicious patterns",
            "LOW":     "LOW risk -- some anomalies",
            "CLEAN":   "CLEAN -- no suspicious signals",
            "UNKNOWN": "UNKNOWN -- insufficient data",
        }
        label = level_labels.get(result["risk_level"], result["risk_level"])
        print(f"[agent]")
        print(f"[agent] Verdict: {label}  (score {result['score']}/100)")
        if result["evidence"]:
            print(f"[agent] Evidence:")
            for e in result["evidence"]:
                print(f"[agent]   {e}")
        else:
            print(f"[agent] No suspicious signals detected.")

    return result
