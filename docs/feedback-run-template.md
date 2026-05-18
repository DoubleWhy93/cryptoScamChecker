# Feedback Run Template

Use this file to record one browser-based Codex feedback run. Copy the relevant
section for each evaluator.

## Normal Customer Feedback

```json
{
  "agent": "normal_customer",
  "scenario": "saved_recipient_btc",
  "date": "",
  "app_url": "http://localhost:8000",
  "passed": false,
  "scores": {
    "friction": 0,
    "clarity": 0,
    "trust": 0,
    "false_positive_risk": 0
  },
  "observations": [],
  "problems": [],
  "recommended_changes": []
}
```

## Potential Scam Victim Feedback

```json
{
  "agent": "potential_scam_victim",
  "scenario": "reported_usdt_wallet",
  "date": "",
  "app_url": "http://localhost:8000",
  "passed": false,
  "scores": {
    "empathy": 0,
    "specificity": 0,
    "persuasiveness": 0,
    "plain_language": 0,
    "actionability": 0,
    "safety_bias": 0
  },
  "observations": [],
  "problems": [],
  "recommended_changes": []
}
```
