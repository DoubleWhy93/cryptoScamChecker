# Bitcoin Scam Warning Agent — Design Notes

## What We Learned (Real-CATS + Live Analysis)

### Dataset summary
| Set | Addresses | Notes |
|-----|-----------|-------|
| CB.tsv — criminal BTC | 40,032 | Ransomware, Phishing, Darknet, Scam |
| BB.tsv — benign BTC | 86,583 | Exchanges, services, normal wallets |
| CE.tsv — criminal ETH | 12,561 | |
| BE.tsv — benign ETH | 15,595 | |

---

## Key Patterns Found

### 1. Full sweep (relay_ratio ≈ 1.0)
Almost every criminal address receives funds from victims and immediately forwards
everything to a broker/cashout address. **Zero or near-zero balance is universal.**

- 92–98% of criminal BTC addresses have relay_ratio > 0.95
- Benign hot wallets (exchanges, services) also have high relay_ratios — so this
  feature **alone** is insufficient and generates false positives.
- Combined with funnel pattern (many senders → few recipients) it becomes strong.

### 2. Funnel pattern (many victims → one criminal → few brokers)
- Criminal addresses typically receive from many victim addresses (high n_senders)
  but forward to only 1–3 broker/cashout addresses (low n_recipients).
- `funnel_ratio = n_senders / n_recipients` — scam wallets often 3–20x.
- Legitimate wallets cluster around 0.5–2.0.
- Threshold: `funnel_ratio > 3` combined with `n_senders >= 3` avoids noise.

### 3. Short lifecycle (burst then dormant)
- Ransomware and phishing wallets are active for a short window (days to weeks),
  receive a single or few large payments, then go quiet.
- `burst_days < 7`: very suspicious for a wallet that has already swept clean.
- `burst_days > 180`: strongly suggests a legitimate long-lived wallet.

### 4. Single dominant payment (spike_ratio)
- One-time ransom / phishing payments produce a spike: one tx much larger than average.
- `spike_ratio = max_recv / avg_recv` — values > 10 suggest a single extorted payment.
- Must be combined with a minimum amount (`max_recv_btc > 0.001`) to avoid noise on
  dust-payment wallets.

### 5. Second-hop cashout chain
From live data tracing (trace_hop2.py), broker behaviour falls into three types:
- **CONSOLIDATION**: receives from 2+ criminal addresses — likely a shared cashout wallet.
- **CASHOUT**: forwards everything to a single final destination (likely an exchange).
- **RELAY**: passes through to several next hops (mixing service or layering step).

Notable: `bc1qr4dl5wa7kl8yu792dceg9z5knl2gkn220lk7a9` appeared as a final cashout
destination receiving from multiple independent criminal chains — likely an exchange
deposit address or mixer entry point.

---

## Feature Vector for the Agent

| Feature | How extracted | Signal direction |
|---------|--------------|-----------------|
| `in_criminal_db` | O(1) set lookup against all 90,597 CB.tsv addresses | +++ |
| `counterparty_hits` | Senders/recipients cross-checked vs criminal DB | ++ |
| `relay_ratio` | `spent_txo_sum / funded_txo_sum` from address summary | + (if > 0.95) |
| `balance_btc` | `funded_txo_sum - spent_txo_sum` | + (if == 0) |
| `funnel_ratio` | `n_unique_senders / n_unique_recipients` (first tx page) | + (if > 3) |
| `n_senders_seen` | Unique addresses that sent to this wallet | + (if >= 3) |
| `spike_ratio` | `max_recv / avg_recv` (first tx page) | + (if > 10) |
| `burst_days` | `(max_timestamp - min_timestamp) / 86400` | + (if < 7), — (if > 180) |
| `tx_count` | From address summary | — (if == 0, dormant) |

**API cost**: 2 calls only — `GET /address/{addr}` + `GET /address/{addr}/txs`
(first page, ≤50 txs). Fast enough for real-time agent use.

---

## Scoring Rules (Current)

Layer 2 scoring answers one question: is this address worth investigating further?
A score ≥ 40 triggers a warning. Rules are independent signals, not compound conditions.
DB lookups (+50 criminal DB, +40 counterparty) are handled in Layer 1, not here.

```
+40   tx_count < 5                         (near-fresh address, little history)
+30   relay_ratio > 0.9 AND tx_count > 0   (90%+ of funds forwarded out)
+25   funnel_ratio > 3 AND n_senders >= 2  (many victims → few brokers)
+20   spike_ratio > 5 AND max_recv > 0.001 BTC  (one dominant payment)
+15   burst_days <= 14                     (all activity within 2 weeks)
-20   burst_days > 365                     (established long-lived wallet)
-15   n_recipients_seen > 10               (sends to many addresses — normal spending)
```

Score is clamped to [0, 100]:
- **HIGH** ≥ 70
- **MEDIUM** ≥ 40  → investigate / warn
- **LOW** ≥ 15
- **CLEAN** < 15

---

## Validated Examples

### Benign hot wallet (`1121tWYMM618hQVKepb6fF1gjr4v3m62E8`)
- 64 txs, relay 100%, funnel 1.3x, **699 days active**
- Score: **CLEAN (0)** — long lifetime and balanced funnel both fire negatively ✓

### Criminal address (`1GH9bkaD3QsZyFU1MRcvpmQLj4SiVpARit`)
- 2,871 txs, 108,504 BTC received, relay 100%, spike 13.7x
- In criminal DB + counterparty in criminal DB + spike rule all fire
- Score: **HIGH (100)** ✓

---

## Agent Architecture (Future)

```
Input: Bitcoin address (from a received transaction)
        │
        ▼
 [Fast path — 2 API calls]
  1. GET /address/{addr}          → relay_ratio, balance, tx_count
  2. GET /address/{addr}/txs      → funnel_ratio, spike_ratio, burst_days
        │
        ▼
  score(extract_features(addr))
        │
        ▼
  if risk_level in ("HIGH", "MEDIUM"):
      emit_warning(addr, score, evidence)
```

### Suggested trigger threshold for warnings
- **HIGH (≥70)**: block or hard-warn — very likely criminal
- **MEDIUM (40–69)**: soft-warn — "this address shows unusual patterns, proceed carefully"
- **LOW (15–39)**: log only — worth monitoring
- **CLEAN (<15)**: no action

### When to use counterparty check (`--check-counterparties`)
- Adds N lookups (all seen senders + recipients vs criminal set)
- Worth doing when real-time latency allows (e.g. user confirms a large send)
- Skip for instant transaction screening (use the 2-call fast path)

---

## Limitations and Edge Cases

1. **First-page bias**: the feature extractor uses only the first 50 transactions
   (newest first). For wallets with thousands of txs the funnel/spike stats may
   not represent the full history.

2. **Mixing services**: heavily mixed coins may break the funnel pattern (many
   recipients) — these would score lower than they deserve. A future hop-2 check
   against known mixer addresses would help.

3. **Dormant new addresses**: fresh addresses with zero transactions score CLEAN
   by default (-20 dormant rule). An address that receives for the first time and
   immediately triggers a warning is not catchable until at least one tx is confirmed.

4. **Exchange deposit addresses**: high-volume exchange deposits look similar to
   criminal sweepers (many senders, zero balance). The long-lifetime rule (-20
   for >180 days) handles established exchanges but won't help for new exchange
   deposit addresses.

5. **Criminal DB coverage**: CB.tsv has 40,032 addresses but the blockchain has
   billions. The `in_criminal_db` rule fires only when the exact address is known.
   Behavioural rules (funnel, spike, burst) cover unknown criminals.
