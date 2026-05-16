# Layer 3 — Gemma Investigation Agent

Autonomous agent that traces cryptocurrency money flow and generates a
plain-language warning for the user. Runs after Layer 2 has flagged an address.

---

## How it works

Criminals always need to convert stolen crypto into real purchasing power. They route
funds through 1-2 relay wallets and deposit into an exchange (Binance, OKX, etc.) to
sell for fiat. The agent traces the full chain to find where the money ends up.

```
Fresh scam wallet  →  [0-2 relay hops]  →  Exchange deposit address
     ↑                                            ↑
 Low tx_count                          Very high tx_count
 Recently created                      Massive total_in_btc
 Sweeps immediately                    Many unrelated senders
                                       Near-zero balance (sweeps to cold storage)
```

**STEP 1 — Assess the target address**
  `get_address_summary(recipient)`
  Is it fresh? (low tx_count, appeared recently, active in last 1-2 weeks)
  Does it sweep immediately? (relay_ratio ≈ 1.0, near-zero balance)
  → Fresh + sweeping = relay wallet, not the final destination

**STEP 2 — Follow the money hop by hop**
  `get_outflows(address)` → find next hop
  `get_address_summary(next_hop)` → classify it:
  - Relay: relay_ratio ≈ 1.0, low balance, moderate tx_count
  - Exchange: very high tx_count + total_in_btc, near-zero balance
  Repeat up to 3 hops.

**STEP 3 — Confirm the exchange endpoint**
  `get_inflows(suspected_exchange)`
  If n_unique_senders is very high (hundreds+) → almost certainly an exchange.
  Chain confirmed: fresh wallet → relay(s) → exchange = scam cashout route.

The 4 tools available:

| Tool | What it returns |
|------|----------------|
| `get_address_summary(address)` | tx_count, relay_ratio, balance, first_seen_days_ago, last_active_days_ago, burst_days |
| `get_outflows(address)` | top 5 addresses this wallet sent money TO (downstream brokers) |
| `get_inflows(address)` | who sent money TO this wallet + n_unique_senders (consolidation check) |
| `score_address(address)` | risk_level, score 0-100, evidence list |

All API calls go to mempool.space (free, no key needed).

---

## Prerequisites

```bash
pip install google-genai requests pandas
```

Set your Google AI Studio API key:

```bash
# Windows PowerShell
$env:GOOGLE_API_KEY = "your-key-here"

# Mac / Linux
export GOOGLE_API_KEY="your-key-here"
```

Get a free key at https://aistudio.google.com/apikey

---

## Calling the agent

```python
from agent.layer3 import investigate

result = investigate(
    address="1GH9bkaD3QsZyFU1MRcvpmQLj4SiVpARit",
    amount_usd=15000,
    token="BTC",
    account={
        "account_age_days": 34,
        "avg_tx_usd": 667,
    },
    layer2_result={
        "score": 75,
        "risk_level": "HIGH",
        "evidence": [
            "[+30] High relay ratio: 90%+ of received funds forwarded out",
            "[+25] Funnel pattern: many senders to few recipients",
        ],
    },
    verbose=True,
)
```

### Minimal call (no layer2 context)

```python
result = investigate(
    address="1ABC...",
    amount_usd=12000,
    token="BTC",
    account={"account_age_days": 30, "avg_tx_usd": 500},
    layer2_result={},
)
```

### Return value

```python
{
    "risk_level":   "HIGH",
    "warning_text": "Before you confirm this transfer...",
    "evidence":     [
        "Recipient address first appeared 8 days ago",
        "All funds are immediately forwarded to a single broker address",
        "That broker is receiving from 34 different wallets simultaneously",
    ],
    "suggestions":  [
        "Wait 24 hours before confirming",
        "Send a small test amount first to verify the recipient",
    ],
}
```

---

## Model selection

```bash
$env:GEMMA_MODEL = "gemma-3-4b-it"      # fastest
$env:GEMMA_MODEL = "gemma-3-27b-it"     # default — good quality
$env:GEMMA_MODEL = "gemini-2.0-flash"   # best tool-use reliability
```

---

## Verbose output

```
[layer3] Starting investigation  model=gemma-3-27b-it
[layer3] Recipient: 1GH9bkaD3QsZyFU1MRcvpmQLj4SiVpARit
[layer3] Amount:    $15,000 BTC  (22.5x user avg)

[layer3]   -> get_address_summary(1GH9bkaD3QsZyFU1MRcvpmQLj4...)
[layer3]      {"tx_count": 4, "relay_ratio": 0.9998, "first_seen_days_ago": 8, ...}

[layer3]   -> get_outflows(1GH9bkaD3QsZyFU1MRcvpmQLj4...)
[layer3]      {"top_recipients": [{"address": "bc1qr4dl5...", "total_btc": 0.182}]}

[layer3]   -> get_inflows(bc1qr4dl5...)
[layer3]      {"n_unique_senders": 34, "top_senders": [...]}

[layer3]   -> score_address(bc1qr4dl5...)
[layer3]      {"risk_level": "HIGH", "score": 85, "evidence": [...]}

[layer3] Final response:
{"risk_level": "HIGH", "warning_text": "Before you confirm...", ...}
```
