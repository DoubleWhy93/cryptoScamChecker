# Layer 3 — Gemma Investigation Agent

Autonomous agent that traces cryptocurrency money flow and generates a
plain-language warning for the user. Runs after Layer 2 has flagged an address.

---

## How it works

The agent is given an address and a transaction context. It has 3 tools it can
call in any order, as many times as it needs (up to 8 calls total):

```
get_address_summary(address)
  → tx_count, balance_btc, relay_ratio, funnel_ratio,
    spike_ratio, burst_days, total_in_btc, total_out_btc

trace_recipients(address)
  → top 5 addresses this wallet forwarded money to, with amounts

score_recipient(address)
  → risk_level, score, evidence for any address (1-hop deeper)
```

The model decides what to investigate. A typical run looks like:

```
1. get_address_summary(recipient)          ← always starts here
2. trace_recipients(recipient)             ← if relay_ratio > 0.9 or tx_count < 5
3. score_recipient(top_recipient_addr)     ← if broker address looks suspicious
4. Final JSON response
```

All API calls go to mempool.space (free, no key). The model reasons over the
results and outputs a structured warning.

---

## Prerequisites

```bash
pip install google-genai requests pandas
```

Set your Google AI Studio API key:

```bash
# Windows
$env:GOOGLE_API_KEY = "your-key-here"

# Mac/Linux
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
    verbose=True,   # prints each tool call and result
)
```

### Return value

```python
{
    "risk_level":   "HIGH",          # HIGH | MEDIUM | LOW | CLEAN
    "warning_text": "Before you confirm...",   # 2-3 sentence user-facing warning
    "evidence":     ["finding 1", "finding 2"],
    "suggestions":  ["Wait 24 hours", "Send a small test amount first"],
}
```

### Minimal call (no layer2 context)

```python
result = investigate(
    address="1ABC...",
    amount_usd=12000,
    token="BTC",
    account={"account_age_days": 30, "avg_tx_usd": 500},
    layer2_result={},   # pass empty dict if called standalone
)
```

---

## Model selection

Default model is `gemma-3-27b-it`. Override via environment variable:

```bash
# Fastest
$env:GEMMA_MODEL = "gemma-3-4b-it"

# Best tool-use reliability
$env:GEMMA_MODEL = "gemini-2.0-flash"
```

| Model | Speed | Tool-use quality |
|-------|-------|-----------------|
| `gemma-3-4b-it` | Fast | Good |
| `gemma-3-27b-it` | Moderate | Better |
| `gemini-2.0-flash` | Fast | Best |

---

## Verbose output

With `verbose=True` you see every tool call the model makes:

```
[layer3] Starting investigation  model=gemma-3-27b-it
[layer3] Recipient: 1GH9bkaD3QsZyFU1MRcvpmQLj4SiVpARit
[layer3] Amount: $15,000 BTC  (22.5x user avg)

[layer3]   -> get_address_summary(1GH9bkaD3QsZyFU1...)
[layer3]      {"tx_count": 2871, "relay_ratio": 1.0, "funnel_ratio": 5.3, ...}

[layer3]   -> trace_recipients(1GH9bkaD3QsZyFU1...)
[layer3]      {"top_recipients": [{"address": "bc1q...", "total_btc": 108.4}, ...]}

[layer3]   -> score_recipient(bc1q...)
[layer3]      {"risk_level": "HIGH", "score": 75, "evidence": [...]}

[layer3] Final response:
{"risk_level": "HIGH", "warning_text": "Before you confirm...", ...}
```

Set `verbose=False` for production use.
