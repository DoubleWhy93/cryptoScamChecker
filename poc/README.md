# Crypto Scam Detection — Proof of Concept

Tests whether on-chain behavioral signals alone can identify known criminal
Bitcoin addresses — without any pre-computed blacklist.

---

## How it works

Given a Bitcoin address, the agent makes **2 API calls** to mempool.space (no key needed):

```
GET /address/{addr}       → balance, tx count, relay ratio
GET /address/{addr}/txs   → first 50 transactions → funnel, spike, burst
```

It then scores the address against 7 rules:

| Points | Signal |
|--------|--------|
| +40 | Fewer than 5 transactions on chain (near-fresh address) |
| +30 | 90%+ of received funds forwarded out (relay sweep) |
| +25 | Many senders → few recipients, ratio > 3 (victim funnel) |
| +20 | One payment is 5× the average (single dominant spike) |
| +15 | All activity within 14 days (short burst lifecycle) |
| −20 | Active for over 365 days (established wallet) |
| −15 | Sends to more than 10 distinct recipients (normal spending) |

Score is clamped to [0, 100]:
- **HIGH** ≥ 70 — very likely criminal
- **MEDIUM** ≥ 40 — suspicious, worth investigating
- **LOW** ≥ 15 — some anomaly, monitor
- **CLEAN** < 15 — no signals

No blacklist is consulted. The test simulates encountering a brand-new address
that has never been seen in any database before.

---

## Prerequisites

```bash
pip install requests pandas
```

No API keys required.

---

## Usage

### Test a single address (your own input)

```bash
python poc/run_poc.py 1GH9bkaD3QsZyFU1MRcvpmQLj4SiVpARit
```

or with the flag form:

```bash
python poc/run_poc.py --address 1GH9bkaD3QsZyFU1MRcvpmQLj4SiVpARit
```

### Run against a random sample from the known criminal dataset (CB.tsv)

```bash
python poc/run_poc.py                        # 20 addresses, default seed
python poc/run_poc.py --n 10 --seed 7        # 10 addresses, different sample
```

### Filter to a specific scam type

```bash
python poc/run_poc.py --labels "Pigbutchering Scam" "Romance Scam" --n 15
python poc/run_poc.py --labels Ransomware --n 10
```

Available labels in CB.tsv include: `Ransomware`, `Blackmail Scam`, `Phishing Scam`,
`Pigbutchering Scam`, `Romance Scam`, `Investment Scam`, `Sextortion Scam`, and more.

### Compare blind mode vs with-DB mode

```bash
python poc/run_poc.py --n 10 --with-db
```

Adds a DB score column showing what score the address would get if the CB.tsv
blacklist lookup was also active. Useful for measuring how much the blacklist adds
on top of pure behavioral detection.

---

## Output

The script prints a step-by-step trace for each address:

```
[1/20] 1GH9bkaD3QsZyFU1MRcvpmQLj4SiVpARit  (known: Ransomware)

[agent] ── BLIND (no DB) ──
[agent] Analysing: 1GH9bkaD3QsZyFU1MRcvpmQLj4SiVpARit  [chain: BTC]
[agent] API call 1: GET /address/1GH9bkaD3QsZyFU1...
[agent] API call 2: GET /address/1GH9bkaD3QsZyFU1.../txs (sampled 50 of 2871 txs)
[agent]
[agent]   tx_count     = 2871
[agent]   balance      = 0.00000000 BTC  (swept clean)
[agent]   relay_ratio  = 100.00%  (total out / total in)
[agent]   funnel_ratio = 5.3x  (12 senders -> 2 recipients seen)
[agent]   spike_ratio  = 13.7x  (max recv 0.2941 BTC / avg recv 0.0214 BTC)
[agent]   burst_days   = 42.3  (span of observed activity)
[agent]
[agent] Verdict: HIGH RISK  -- likely criminal  (score 75/100)
[agent] Evidence:
[agent]   [+30] High relay ratio: 90%+ of received funds forwarded out
[agent]   [+25] Funnel pattern: many senders to few recipients (ratio > 3, senders >= 2)
[agent]   [+20] Single dominant incoming payment: one payment is 5x the average
```

Followed by a summary table:

```
======================================================================
  SUMMARY
======================================================================

  Addresses tested : 20
  Valid (data found): 19

  Detection rate (HIGH+MEDIUM, blind): 14/19 = 74%

    HIGH    :  8  ########
    MEDIUM  :  6  ######
    LOW     :  3  ###
    CLEAN   :  2  ##
    UNKNOWN :  1  (address not found on chain)
```

Results are also saved to `output/reports/poc_results.csv`.

---

## Files

```
poc/
  agent.py      — core agent: chain detection, API calls, feature extraction, scoring
  run_poc.py    — evaluation harness: sampling, batch run, summary report
  README.md     — this file

scripts/
  score_address.py  — feature extraction + scoring rules
  config.py         — API base URLs, paths, rate limits
  fetch_btc.py      — full tx pagination fetcher (used for offline analysis)

data/
  CB.tsv        — 90,597 known criminal BTC addresses with labels
  CE.tsv        — 12,561 known criminal ETH addresses
```
