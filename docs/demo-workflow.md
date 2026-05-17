# Demo Workflow

This project is built as a hackathon demo for a crypto scam warning agent. The
site should feel like a familiar exchange or broker send-crypto screen because
the best moment to prevent a scam is before the user confirms the transfer.

## Demo Story

A user is about to send crypto to a recipient address. In a normal wallet or
exchange product, that action would move directly to confirmation. In this demo,
the exchange inserts a protection step:

```text
User enters address and amount inside a mock exchange
  -> Exchange runs a quick backend risk screen before releasing funds
  -> User sees a warning or safe-to-proceed result in the send flow
  -> Suspicious addresses trigger a deeper agent investigation
  -> User receives plain-language evidence and safer next steps
```

The agent is the important part. The website exists to make that agent easy to
understand in a live demo: it shows how the investigation could fit into an
existing consumer crypto product without requiring the user to understand block
explorers or transaction graphs.

## What The Backend Checks

The quick screen looks at public address behavior:

- whether the chain can be detected
- how many transactions the address has
- whether the address is new or short-lived
- whether funds are quickly moved out
- whether funds appear to collect from many senders and move to few recipients
- whether the behavior score crosses a warning threshold

When the quick score is elevated, the Layer 3 agent investigates the address in
more detail by calling tools for summaries, inflows, outflows, and risk scoring.
The final report is written for a non-technical user.

## What The Demo Does Not Do

- It does not send real transactions.
- It does not block funds inside a real wallet or exchange.
- It does not guarantee that an address is safe or malicious.
- It does not replace compliance tooling or law-enforcement investigation.

The practical goal is narrower: show a user-friendly intervention that can slow
down high-risk transfers and give potential victims a chance to reconsider.

## Hackathon Positioning

For the Gemma 4 Good Hackathon, the social-good angle is scam prevention. Crypto
scams often rely on urgency and trust. A small delay plus a clear explanation can
be useful because victims may still be able to stop before irreversible payment.

The strongest demo path is:

1. Enter a normal-looking address.
2. Show the quick assessment.
3. Enter a suspicious demo address.
4. Show the immediate warning.
5. Confirm in the demo UI.
6. Show the background investigation report appearing in Activity.
