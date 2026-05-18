# Codex Feedback Agents

These are not runtime product agents and they do not call Gemma. They are Codex
evaluation personas used during development to test the local SwiftX demo in a
browser and produce feedback for improving the frontend, policy rules, and agent
message quality.

This is the primary refinement workflow for the product experience. The demo
workflow explains what SwiftX is simulating; these feedback agents decide
whether the simulation actually feels right to the two users we care about.

Run them after starting the app locally:

```bash
uvicorn server:app --reload --port 8000
```

Target URL:

```text
http://localhost:8000
```

## Shared Rules

- Use the browser like a real customer.
- Do not inspect source code while evaluating the experience.
- Complete the send flow using the assigned scenario.
- Record what the UI showed, what felt clear, what felt wrong, and what should be changed.
- Separate product feedback from algorithm feedback.
- Do not judge whether an address is actually safe or criminal. Judge whether SwiftX behavior matches the scenario.
- Return feedback as structured JSON plus a short plain-English summary.

## How To Run With Codex

Start the local app, then ask Codex to run both evaluators:

```text
Run the two Codex feedback agents from docs/feedback-agents.md against
http://localhost:8000. Use browser interaction only. Do not inspect source code.
Return the normal-customer feedback and the potential-scam-victim feedback as
separate JSON objects, then list the highest-priority product changes.
```

If parallel subagents are available, run the two evaluators at the same time.
If browser automation is unavailable, stop and report that the feedback run was
blocked instead of pretending to have tested the UI.

## Feedback Agent A: Normal Customer

### Persona

You are a legitimate SwiftX customer making a normal transfer to a recipient you
have used before. You are not under pressure. You expect the exchange to be fast,
professional, and minimally intrusive.

### Test Scenario

Use the saved-recipient preset:

```text
Address: 3LQUu4v9z6KNch71j7kbj8GPeAGUo1FW6a
Asset: BTC
USD amount: 250
Expected policy path: trusted_repeat_recipient
Expected agent behavior: no deeper agent investigation
```

### Browser Steps

1. Open `http://localhost:8000`.
2. Select `Saved recipient`.
3. Click `Send`.
4. Observe the result panel.
5. Click `Release transfer` if available.
6. Note whether any warning, hold, or investigation appeared.

### Evaluation Questions

- Did the flow feel like a normal exchange transfer?
- Was the safety check visible but not annoying?
- Did the UI avoid scary language?
- Was there any unnecessary hold or "agent investigation" display?
- Was the transfer release action obvious?
- Did the customer have enough confidence to proceed?

### Scoring

Score each from 1 to 5:

- `friction`: 5 means very low friction.
- `clarity`: 5 means the next action is obvious.
- `trust`: 5 means SwiftX feels professional and customer-friendly.
- `false_positive_risk`: 5 means the UI does not over-warn a normal user.

### Expected Feedback Shape

```json
{
  "agent": "normal_customer",
  "scenario": "saved_recipient_btc",
  "passed": true,
  "scores": {
    "friction": 5,
    "clarity": 5,
    "trust": 5,
    "false_positive_risk": 5
  },
  "observations": [],
  "problems": [],
  "recommended_changes": []
}
```

## Feedback Agent B: Potential Scam Victim

### Persona

You are a customer being pressured to send money to someone you trust online.
The request may involve urgency, secrecy, romance, investment profit, Telegram,
WhatsApp, taxes, or unlock fees. You are anxious and motivated to send, so the
message must be warm, specific, and convincing enough to make you pause.

### Test Scenario

Use the reported-USDT-wallet preset:

```text
Address: TJSoTJ5V8EMnc189ytobnU3QSUyePFq6rC
Asset: USDT
USD amount: 28760
Expected policy path: elevated risk / enhanced review
Expected agent behavior: background investigation, then customer-facing safety report
```

### Browser Steps

1. Open `http://localhost:8000`.
2. Select `Reported USDT wallet`.
3. Click `Send`.
4. Observe the immediate hold message.
5. Wait for the agent investigation report to appear.
6. Read the final warning as a non-technical customer.
7. Decide whether the message would make you pause before sending.

### Evaluation Questions

- Did SwiftX explain that the transfer was held for the customer's protection?
- Did the message feel caring instead of accusatory?
- Did it include specific findings about the address?
- Did it explain scam pressure patterns in plain language?
- Did it mention Telegram, WhatsApp, secrecy, urgency, romance, guaranteed profit, taxes, or unlock fees when appropriate?
- Did it give concrete next steps?
- Was the override action appropriately de-emphasized compared with canceling?
- Would this message realistically make a pressured victim pause?

### Scoring

Score each from 1 to 5:

- `empathy`: 5 means warm and non-shaming.
- `specificity`: 5 means findings are concrete and address-specific.
- `persuasiveness`: 5 means likely to make a victim pause.
- `plain_language`: 5 means no crypto jargon.
- `actionability`: 5 means next steps are concrete.
- `safety_bias`: 5 means cancel/pause is more natural than override.

### Expected Feedback Shape

```json
{
  "agent": "potential_scam_victim",
  "scenario": "reported_usdt_wallet",
  "passed": true,
  "scores": {
    "empathy": 5,
    "specificity": 5,
    "persuasiveness": 5,
    "plain_language": 5,
    "actionability": 5,
    "safety_bias": 5
  },
  "observations": [],
  "problems": [],
  "recommended_changes": []
}
```

## How Codex Should Use The Feedback

After both feedback agents run:

1. Compare the normal-customer and scam-victim results.
2. Preserve low friction for the normal customer.
3. Strengthen warning clarity only where the scam-victim evaluation says it was weak.
4. Avoid broad changes that make normal transfers feel scary.
5. Convert repeated feedback into concrete code changes in `server.py`, `static/app.js`, `static/index.html`, or `agent/layer3.py`.

The important product tradeoff is asymmetric: a normal user should barely notice
SwiftX safety, while a pressured victim should feel seen, protected, and given a
clear reason to pause.

## Pass Criteria

The current build is good enough when:

- Normal customer path does not show agent investigation.
- Normal customer can release transfer quickly.
- Scam-victim path pauses the transfer.
- Scam-victim sees a warm and specific explanation.
- Scam-victim gets at least three practical next steps.
- Override remains possible for demo purposes but is visually and emotionally secondary.
