# r/CryptoScams Research Notes - 2026-05-16

Source scope: public posts visible from `https://www.reddit.com/r/CryptoScams/`, `https://www.reddit.com/r/CryptoScams/new/`, and Reddit search results for related r/CryptoScams posts. Usernames are intentionally omitted. These notes are for product testing and scam-pattern modeling, not for identifying individual victims.

## High-Signal Patterns

1. Fake exchange/Ponzi groups dominate recent reports.
   The DSJ/BG Wealth/FCIG pattern appears repeatedly: a professor or assistant persona, group chats, Zoom meetings, copy-paste trading signals, fake AI trading, and family/community recruiting. The "crypto" part is often just the deposit rail; the platform UI can fabricate balances, profits, losses, and withdrawal rules.

2. Withdrawals are the control point.
   Scams commonly allow small early withdrawals, then block larger withdrawals using taxes, missed-trade rules, verification, recruitment levels, frozen accounts, or broken domains. A safety agent should treat "pay a fee/tax to withdraw" and "recruit to unlock withdrawal/signal" as severe warnings.

3. Affinity pressure makes warnings harder.
   Many reports involve parents, churches, immigrant communities, friends, or family. The warning copy should not say "your family member is scamming you." It should focus on verifiable facts: unknown recipient, unusual amount, guaranteed returns, off-platform messenger, withdrawal conditions, and lack of regulated exchange identity.

4. The smart-contract surface is mixed.
   Many user-facing scams do not require a malicious contract. They use fake exchange ledgers and collect deposits to externally owned addresses. Smart-contract-specific scams still show up through presales, token projects, bridge exploits, token admin powers, fake airdrops, and approval drains.

5. Domain and communication churn are useful off-chain signals.
   Several reports mention sites being flagged, disappearing, changing domains, or moving users from Telegram/WhatsApp to BonChat/Zoom/Facebook groups. These are not on-chain features, but they are valuable context for an LLM warning layer.

## Detection Features To Add To The Agent

- `social_source`: user says the recipient came from WhatsApp, Telegram, BonChat, Facebook, Zoom, dating apps, or a random message.
- `authority_persona`: professor, assistant, teacher, mentor, analyst, celebrity, AI trader, recovery specialist.
- `withdrawal_fee_or_tax`: any demand for more money before withdrawal.
- `recruitment_gate`: unlock returns, signals, level, or bonus by inviting others.
- `fake_profit_claim`: guaranteed daily return, high win rate, copy-paste signals, no-risk AI trading.
- `first_large_deposit`: first transfer above normal user baseline, especially $1000+ to $10000+.
- `fresh_or_sweeping_destination`: destination has no history or quickly forwards funds onward.
- `presale_lock`: funds committed to token not independently tradable or launch keeps moving.
- `contract_admin_risk`: upgradeable/admin-controlled token, mint authority, transfer tax, blacklist/whitelist, paused withdrawals, or unverifiable bridge logic.
- `brand_impersonation`: claims to be owned by a famous person, AI company, exchange, regulator, or celebrity without verification.

## Suggested Warning Copy Patterns

For fake exchange/Ponzi deposits:

> Before you send this, pause and verify the recipient outside the group that introduced the investment. The setup you described - trading signals, a professor or assistant, guaranteed profits, and withdrawal limits - matches patterns often reported in fake crypto exchange scams. Consider waiting 24 hours and testing whether you can withdraw through a regulated exchange before sending more.

For withdrawal fee/tax requests:

> A request to pay more crypto before you can withdraw existing funds is a major scam signal. Legitimate exchanges deduct fees from the balance or show regulated tax documents; they do not require a separate payment to unlock your money.

For presale/token projects:

> This token appears to depend on future promises rather than something you can independently verify now. Before buying, confirm that the contract is verified, liquidity is real, admin privileges are limited, and the token can be transferred or sold without special approval.

For family/community recruitment:

> This may have come from someone you trust, but the recipient address and platform still need independent verification. Scams often spread through trusted communities because early participants see fake profits or small withdrawals before larger deposits are blocked.

## Smart-Contract Scam Checks

Use these checks when the user is about to buy a token, approve a contract, or bridge funds:

- Is the contract verified on the relevant explorer?
- Can the owner mint unlimited supply?
- Can the owner pause transfers, blacklist wallets, change fees, or redirect liquidity?
- Is liquidity locked, and for how long?
- Are buy/sell taxes unusually high or changeable?
- Is the user being asked for unlimited token approval?
- Does the bridge bind messages/proofs to chain, request, nonce, and replay protection?
- Are presale proceeds visible on-chain, or only claimed in marketing?
- Does the project have public tests, audits, and a functioning product, not only AMAs and ads?

## Local Dataset Comparison

The existing repo datasets support a useful chain split:

- BTC criminal labels are dominated by blackmail, ransomware, sextortion, investment scams, tumblers, giveaway scams, impersonation, and romance scams.
- ETH criminal labels are dominated by phishing and impersonation, with a meaningful smart-contract tail: contract exploits, rug pulls, NFT airdrop scams, fake projects, liquidity scams, and metamorphic contracts.
- Reddit's recent examples skew toward fake exchange/Ponzi and affinity scams. That means the product should combine on-chain address behavior with conversational/off-chain context, because the scam may look like a normal deposit until the user explains the social setup.

## Notes For Test Dataset Use

The TSV in `data/reddit_cryptoscams_testing_data_2026-05-16.tsv` is intentionally scenario-level. It should be used to build warning prompts, classifiers, and red-team cases. It should not be treated as verified evidence that every named project is criminal; many rows are user reports from Reddit and need independent verification before enforcement actions.
