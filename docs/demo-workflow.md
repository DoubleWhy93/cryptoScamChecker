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

## Experience Target

The platform should distinguish between two customer experiences:

- Legitimate users should feel almost no warning. The exchange runs the check,
  clears the transfer, and lets them continue with minimal friction.
- Potential victims should receive a warm safety hold. The message should be
  specific, compassionate, and practical: explain what looked unusual, mention
  scam pressure patterns such as WhatsApp, Telegram, secrecy, urgency, romance,
  guaranteed profit, taxes, or unlock fees, and suggest safer next steps.

This is the reason the agent is triggered by exchange behavior instead of asking
the user to run a separate scanner. The exchange is protecting customer trust
inside the normal send flow.

## What The Backend Checks

The quick screen looks at public address behavior:

- whether the chain can be detected
- whether the selected asset matches the address type
- whether the address appears in a local reported scam-address database
- whether this customer has frequently sent to the same recipient before
- how many transactions the address has
- whether the address is new or short-lived
- whether funds are quickly moved out
- whether funds appear to collect from many senders and move to few recipients
- whether the behavior score crosses a warning threshold

The demo supports BTC, native TRX, and USDT on TRON/TRC20.

Basic policy filters run before the agent:

- reported scam-address database match: start the deeper agent review immediately
- frequent saved recipient + clean/low public risk: pass with light friction
- brand-new/no-history recipient: warn and recommend verifying ownership or a small test amount
- very fresh recipient: warn directly with address-age context and recommend verification or a small test amount
- large first-time recipient: warn and ask the user to verify through a trusted channel
- medium/high/unknown risk: start the deeper agent review in the background

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
