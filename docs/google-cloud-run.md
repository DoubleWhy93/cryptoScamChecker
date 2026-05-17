# Deploying CryptoScamChecker to Google Cloud Run

Cloud Run is a good fit for this project because the app is a single HTTP
service: FastAPI handles `/api/*`, serves the static website, and can run in a
standard container.

## One-command Source Deploy

From the repository root:

```bash
gcloud run deploy crypto-scam-checker \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars GEMMA_MODEL=gemma-4-31b-it
```

Set secrets separately instead of committing `.env`:

```bash
gcloud secrets create google-api-key --replication-policy=automatic
gcloud secrets versions add google-api-key --data-file=-
gcloud run services update crypto-scam-checker \
  --region us-central1 \
  --set-secrets GOOGLE_API_KEY=google-api-key:latest
```

If you use OpenRouter instead of Google AI Studio, store
`OPENROUTER_API_KEY` as a secret and set `OPENROUTER_MODEL` as a normal
environment variable.

## Container Notes

- The Dockerfile listens on Cloud Run's `PORT` environment variable and defaults
  to `8080` for local container runs.
- `.gcloudignore` keeps local datasets, POC scripts, `.env`, and output files
  out of the Cloud Build upload.
- `/healthz` is available for a lightweight service check.

## Production Gaps To Close

- Move `_jobs` out of in-memory process state if users need reliable background
  results across instance restarts or multiple Cloud Run instances. Firestore,
  Memorystore, Cloud SQL, or a task queue plus persistent result store would all
  be better than a module-level dict.
- Avoid long-running investigations inside request-serving instances if they
  become slow or expensive. A stronger production shape is: API creates a job,
  Cloud Tasks triggers a worker endpoint, results are stored, and the browser
  polls the result store.
- Add request limits, address validation, and abuse controls before making the
  service public.
- Consider splitting the static frontend into a separate build only after the UI
  needs routing, authentication, or a component framework.

## Should This Be Converted To Node.js?

Not now. FastAPI on Cloud Run is already a normal deployment path, and the
current backend is mostly chain-data fetching, Python scoring, and LLM calls.
Node.js would make sense if the product becomes a full React/Next.js app with a
large frontend team, or if the backend needs to share TypeScript contracts with
other services. For the current project, conversion cost is higher than the
benefit.
