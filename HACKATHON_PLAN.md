# Crypto Scam Warning Agent — Hackathon Plan
## Google Gemma Hackathon

---

## 1. Pitch

A broker-side transaction guard that intercepts large cryptocurrency sends before they
are confirmed. When a user attempts to send more than $10,000 to an address that shows
signs of scam activity — fresh address, known criminal, funnel/sweep behaviour — the
agent pauses the transaction, explains the risk in plain language, and suggests safer
alternatives (wait 24 hours, send a small test amount first).

The Gemma model runs via Google AI Studio API. The broker never needs to host a model.
No sensitive user data leaves the platform beyond what is already sent to the API.

Target scam type: **pig butchering / romance investment scams** — victim is
psychologically manipulated into voluntarily sending a large lump sum to a scammer.

Supported chains: **Bitcoin (BTC), Ethereum (ETH), USDT/USDC (ERC-20)**

---

## 2. Architecture

```
User clicks "Send"  →  amount > $10,000 USD?
                               │ No  → allow silently
                               │ Yes
                               ▼
┌────────────────────────────────────────────────────┐
│  LAYER 1 — Instant checks  (no external API calls) │
│                                                    │
│  A. Destination in pre-computed blacklist?         │
│     (criminal addresses + known relay/cashout)     │
│                                         → WARN     │
│  B. Account anomaly?                               │
│     - Account age < 90 days                        │
│     - Amount > 3× user's historical average        │
│     - First transaction above $5,000               │
│     Any yes                             → WARN     │
└────────────────────────────────────────────────────┘
                               │ No flags → allow with mild unverified-address note
                               │ Any flag
                               ▼
┌────────────────────────────────────────────────────┐
│  LAYER 2 — Real-time address scoring               │
│                                                    │
│  BTC  →  2 calls to mempool.space (no key)         │
│  ETH  →  2 calls to Etherscan (free key)           │
│    1. Address summary  → balance, tx_count         │
│    2. First 50 txs     → behaviour features        │
│                                                    │
│  Fresh address (0 prior txs)?           → WARN     │
│  Rule-based score ≥ 40 (MEDIUM/HIGH)?   → WARN     │
│                                                    │
│  Features:                                         │
│    relay_ratio, funnel_ratio, spike_ratio,         │
│    burst_days, balance, in_criminal_db             │
└────────────────────────────────────────────────────┘
                               │ Score < 40 → allow
                               │ Score ≥ 40
                               ▼
┌────────────────────────────────────────────────────┐
│  LAYER 3 — Gemma warning generation                │
│                                                    │
│  Input:  transaction amount + token                │
│          account context (age, avg tx, history)    │
│          risk score + evidence list                │
│          address age + triggered rules             │
│                                                    │
│  Output: warm, plain-language warning paragraph    │
│          + 1–2 concrete suggested actions          │
│                                                    │
│  API: Google AI Studio (gemma-3-27b-it)           │
└────────────────────────────────────────────────────┘
                               │
                               ▼
         Warning modal shown — user must actively dismiss or cancel
```

### Background deep trace (async, not in hot path)
```
Triggered after any Layer 2 flag:
  → fetch full tx history for flagged address  (trace_flow.py)
  → trace hop-2 broker/cashout addresses       (trace_hop2.py)
  → add newly discovered relay addresses to blacklist
  → if escalation found → push stronger follow-up warning to session
```

---

## 3. Pre-computed Blacklist (offline, run nightly)

The fastest check is O(1) set membership. Expand beyond raw CB.tsv / CE.tsv by
running hop-2 tracing on all known criminals to capture their relay and cashout nodes:

```
CB.tsv  (40,032 criminal BTC addresses)
CE.tsv  (12,561 criminal ETH addresses)
    → trace_flow.py   → top recipients per criminal  (hop-1 brokers)
    → trace_hop2.py   → top cashout destinations     (hop-2 cashout)
    → union → expanded_blacklist.json
```

Expected size: 40k–200k addresses. All checked at O(1) per transaction.
Run as a nightly cron job so newly discovered addresses are absorbed quickly.

---

## 4. Chain and Token Detection

```python
def detect_chain(address: str) -> str:
    if address.startswith(("1", "3")) or address.startswith("bc1"):
        return "btc"
    elif address.startswith("0x") and len(address) == 42:
        return "eth"          # ETH + all ERC-20 (USDT, USDC, DAI…)
    elif address.startswith("T") and len(address) == 34:
        return "trx"          # USDT-TRC20 (future)
    return "unknown"
```

**ETH / ERC-20 data fetching (Etherscan):**
- `action=txlist`  — native ETH transfers
- `action=tokentx` — all ERC-20 token transfers (USDT, USDC, etc.), no contract filter
- Combine both lists; feature extraction is identical to BTC (relay ratio, funnel, burst)
- USDT and USDC both have 6 decimals; normalise to USD using a spot price at query time

---

## 5. Gemma Prompt Design

```
System:
  You are a financial safety assistant embedded in a cryptocurrency platform.
  Your job is to protect users from scams by warning them before they confirm
  large transactions. Be warm, clear, and never accusatory. The user may
  genuinely trust the recipient — your role is to introduce healthy caution,
  not to alarm. Keep the warning to 3–4 sentences. End with one or two
  specific, actionable suggestions.

User:
  A user is about to send {amount_usd} USD worth of {token}
  to address {recipient_address}.

  About this user:
  - Account opened {account_age_days} days ago
  - Their typical transaction size is ${avg_tx_usd}
  - This transaction is {multiplier}× their usual amount
  - {first_large_tx_note}   e.g. "This is their first transaction over $5,000."

  About the recipient address:
  - Prior transactions on-chain: {tx_count}
  - Address first seen: {address_age_note}   e.g. "2 days ago" or "brand new"
  - Risk score: {score}/100  ({risk_level})
  - Signals detected: {evidence_list}

  Write a friendly warning and suggest what the user should do before confirming.
```

### Target output example
> "Before you confirm, we noticed a couple of things that are worth a moment of
> thought. This recipient address is brand new — it has no transaction history —
> and the amount you're sending is much larger than your usual transfers. This
> pattern is common in investment scams where victims are asked to send funds to
> a freshly created wallet. We'd recommend waiting 24 hours and sending a small
> test amount first to make sure the recipient is who they say they are."

---

## 6. Demo Site — Mock Crypto Platform

A small hosted web app that simulates a user logged into a crypto exchange.
All account data is static and pre-set. The only live component is the
address-checking pipeline and the Gemma API call.

### Page layout

```
┌──────────────────────────────────────────────────────────┐
│  CryptoGuard Exchange          [Alex Chen ▾]  [Logout]   │
├──────────────────────────────────────────────────────────┤
│  Portfolio                                               │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐     │
│  │  BTC         │ │  ETH         │ │  USDT        │     │
│  │  0.42 BTC    │ │  2.8 ETH     │ │  14,200 USDT │     │
│  │  ~$28,000    │ │  ~$9,800     │ │              │     │
│  └──────────────┘ └──────────────┘ └──────────────┘     │
├──────────────────────────────────────────────────────────┤
│  Send                                                    │
│                                                          │
│  Token      [BTC ▾]                                      │
│  Amount     [____________] USD  ≈ 0.000 BTC              │
│  Recipient  [____________________________________________]│
│                                                          │
│             [  Review Transaction  ]                     │
└──────────────────────────────────────────────────────────┘
```

### Mock user profile (static)
```json
{
  "name": "Alex Chen",
  "account_age_days": 34,
  "portfolio": {
    "BTC":  0.42,
    "ETH":  2.8,
    "USDT": 14200
  },
  "transaction_history": [
    {"to": "self-wallet",  "amount_usd": 500,  "days_ago": 30},
    {"to": "coinbase",     "amount_usd": 1200, "days_ago": 20},
    {"to": "friend-addr",  "amount_usd": 300,  "days_ago": 10}
  ],
  "avg_tx_usd": 667,
  "funding_source": "bank_wire"
}
```

### Warning modal (shown on Layer 2/3 trigger)

```
┌──────────────────────────────────────────────────────────┐
│  ⚠  Hold on, Alex                                        │
│─────────────────────────────────────────────────────────│
│  [Gemma-generated warning text here]                     │
│                                                          │
│  What we found:                                          │
│  • Recipient address has 0 prior transactions            │
│  • This is 18× your typical transaction size             │
│  • Address not found in any verified registry            │
│                                                          │
│  Suggestions:                                            │
│  → Wait 24 hours before confirming                       │
│  → Send $50 first to verify the recipient                │
│                                                          │
│  [ Cancel Transaction ]   [ I understand, proceed ]      │
└──────────────────────────────────────────────────────────┘
```

"I understand, proceed" requires the user to type a confirmation phrase to
add friction: **"I have verified this recipient"**

### Pre-staged demo addresses
| Scenario | Address to enter | Amount | Expected result |
|----------|-----------------|--------|----------------|
| Known criminal | Pick from CB.tsv | $15,000 BTC | Layer 1 hit → HIGH warning |
| Fresh address | Any 0-tx address | $12,000 ETH | Layer 2: fresh → warning |
| Relay/cashout | From hop-2 output | $10,500 USDT | Expanded blacklist hit |
| Benign address | From BB.tsv | $500 BTC | No warning, transaction proceeds |

---

## 7. Files to Copy from Analysis Repo

### Bring as-is
| File | Purpose |
|------|---------|
| `scripts/config.py` | Paths, API URLs, thresholds |
| `scripts/score_address.py` | Real-time rule-based scorer (Layer 2 core) |
| `scripts/fetch_btc.py` | BTC address + tx fetcher (mempool.space) |
| `scripts/fetch_eth.py` | ETH address + tx fetcher (Etherscan) |
| `scripts/trace_flow.py` | Hop-1 flow tracer |
| `scripts/trace_hop2.py` | Hop-2 broker tracer |

### Reference
| File | Purpose |
|------|---------|
| `AGENT_DESIGN_NOTES.md` | Feature definitions, scoring rules, validated examples |
| `requirements.txt` | Python dependencies |

### Data (store outside git — use object storage or env-injected path)
| File | Size | Purpose |
|------|------|---------|
| `Real-CATS/CB.tsv` | ~5 MB | 40,032 criminal BTC addresses |
| `Real-CATS/CE.tsv` | ~1 MB | 12,561 criminal ETH addresses |

---

## 8. New Files to Build

### `agent/blacklist.py`
Loads CB.tsv + CE.tsv + `expanded_blacklist.json` into a single in-memory set at
startup. Exposes `is_flagged(address: str) → bool`. Updated by background tracer.

### `agent/layer1.py`
- `check_blacklist(address)` — O(1) set lookup
- `check_fresh_address(address, chain)` — 1 API call, returns True if tx_count == 0
- `check_account_anomaly(amount_usd, account)` — pure logic, no API
- Returns list of triggered flags with human-readable descriptions

### `agent/layer2.py`
Thin wrapper around `score_address.py`:
- Detects chain from address format
- Calls `extract_features(address)` + `score(features)`
- Returns structured result: score, risk_level, evidence list, key features

### `agent/layer3.py`
- `build_prompt(transaction, account, layer2_result) → str`
- `generate_warning(prompt) → dict`  — calls Gemma via Google AI Studio API
- Returns `{warning_text, suggestions}`

### `agent/check_transaction.py`
Main orchestrator. Called by the demo site on every "Review Transaction" click.
```
check_transaction(recipient, amount_usd, token, account) → {
    should_warn: bool,
    risk_level: str,
    score: int,
    evidence: list,
    layer1_flags: list,
    warning_text: str,     # only if should_warn
    suggestions: list,     # only if should_warn
}
```

### `agent/expand_blacklist.py`
Offline nightly job. Iterates CB.tsv, runs trace_flow + trace_hop2, writes
`data/expanded_blacklist.json`. Designed to run independently of the web app.

### `demo/app.py`
Hosted web app (Flask + plain HTML/CSS or lightweight React).
- Static mock user profile loaded at startup
- `/api/check` POST endpoint → calls `check_transaction.py` → returns JSON
- Frontend renders the send form, calls `/api/check` on submit, shows warning modal

---

## 9. Tech Stack

| Component | Choice |
|-----------|--------|
| Language | Python 3.10+ |
| BTC data | mempool.space API — free, no key |
| ETH / ERC-20 data | Etherscan API — free key, 5 calls/sec |
| LLM | Gemma via Google AI Studio API |
| Gemma model | `gemma-3-27b-it` (quality) or `gemma-3-4b-it` (speed) |
| Gemma SDK | `google-generativeai` Python package |
| Demo frontend | Flask backend + HTML/CSS/JS (no framework needed for a demo) |
| Blacklist storage | In-memory Python set (loaded from JSON at startup) |

### Dependencies to add to requirements.txt
```
google-generativeai
flask
```

---

## 10. Build Order

| Priority | Task | Est. time |
|----------|------|-----------|
| 1 | `agent/blacklist.py` + load CB/CE data | 1 hr |
| 2 | `agent/layer1.py` — blacklist + fresh + account checks | 2 hr |
| 3 | `agent/layer2.py` — scorer wrapper + chain detection | 1 hr |
| 4 | `agent/layer3.py` — Gemma prompt + API call | 2 hr |
| 5 | `agent/check_transaction.py` — orchestrator | 1 hr |
| 6 | `demo/app.py` — Flask backend + `/api/check` | 2 hr |
| 7 | `demo/static/` — mock exchange UI (HTML/CSS/JS) | 3 hr |
| 8 | `agent/expand_blacklist.py` — offline job | 2 hr |
| 9 | ETH / ERC-20 feature extraction in layer2 | 2 hr |
| 10 | Background async deep trace | 3 hr |

Items 1–7 are the minimum viable demo.
Items 8–10 strengthen the pitch but are not required for a working demo.
