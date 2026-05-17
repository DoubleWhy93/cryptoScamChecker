# CryptoScamChecker

CryptoScamChecker is a Gemma-powered crypto scam prevention demo for the
[Gemma 4 Good Hackathon](https://www.kaggle.com/competitions/gemma-4-good-hackathon/overview).
The core idea is an investigation agent that reviews a destination crypto
address before a user sends money. The website is intentionally framed as a
mock exchange send flow, not a standalone address scanner. It shows how an
exchange, wallet, or broker could insert a friendly safety check into the normal
send flow and warn users before funds leave their account.

This app does not send real crypto. It simulates the final review step of a
transfer, runs a fast address-risk screen, and uses an LLM-backed agent to
investigate suspicious addresses in more detail.

## User Workflow

1. The user acts normally inside a mock exchange account and starts an external transfer.
2. The frontend calls `POST /api/review` before the transfer is released.
3. The backend detects the chain, currently BTC or TRX.
4. Layer 2 runs a quick behavioral risk score from public address activity.
5. The exchange UI pauses or allows the transfer based on the quick result.
6. Medium and higher risk addresses trigger the Layer 3 investigation agent.
7. The agent follows money-flow clues and returns a warm plain-language report.
8. The activity panel updates with deeper findings and suggested next steps.

The goal is a user-friendly pipeline that can interrupt scam payments at the
moment they are still preventable.

## Architecture

```text
Website form
  -> FastAPI /api/review
    -> Chain detection
    -> Layer 2 quick score
      -> Return immediate user warning
      -> Start Layer 3 agent when risk is elevated
        -> Fetch address summary, inflows, outflows
        -> Ask Gemma/OpenRouter model for an investigation report
  -> Browser polls /api/result/{job_id}
```

## Project Structure

```text
.
|-- agent/                 # Layer 3 investigation agent and chain fetchers
|-- core/                  # Layer 2 scoring rules and BTC feature extraction
|-- static/                # Website UI served by FastAPI
|   |-- index.html
|   |-- styles.css
|   `-- app.js
|-- poc/                   # Offline proof-of-concept scripts and experiments
|-- data/                  # Local research datasets, excluded from deploys
|-- docs/
|   |-- demo-workflow.md
|   `-- google-cloud-run.md
|-- server.py              # FastAPI app, API routes, static file serving
|-- Dockerfile             # Cloud Run-compatible container
|-- requirements.txt       # Production runtime dependencies
`-- requirements-poc.txt   # Research and proof-of-concept dependencies
```

## Run Locally

```bash
pip install -r requirements.txt
uvicorn server:app --reload --port 8000
```

Open `http://localhost:8000`.

Optional environment variables:

```bash
GOOGLE_API_KEY=...
GEMMA_MODEL=gemma-4-31b-it
OPENROUTER_API_KEY=...
OPENROUTER_MODEL=...
TRONSCAN_API_KEY=...
```

## API

- `GET /healthz` returns a simple health response.
- `POST /api/review` validates an address, runs quick scoring, and returns a job id.
- `GET /api/result/{job_id}` polls the background investigation.

## POC Scripts

The proof-of-concept scripts use heavier research dependencies that are kept out
of the production Cloud Run image.

```bash
pip install -r requirements-poc.txt
python poc/run_poc.py --n 10
```

## Deploy

The current target is Cloud Run because this app is an HTTP service with a
container-ready FastAPI backend and static assets. See
[docs/google-cloud-run.md](docs/google-cloud-run.md) for deploy commands and
production notes.

## Python vs Node.js

Keep the backend in Python for now. The project already depends on Python data
and ML/LLM tooling, and FastAPI deploys cleanly to Cloud Run. Converting to
Node.js would mostly rewrite working chain-analysis code without improving the
deployment path. A Node or React frontend can still be added later if the UI
needs a larger component system.
