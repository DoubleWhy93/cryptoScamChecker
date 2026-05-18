FROM python:3.11-slim

WORKDIR /app

# Install deps first (layer-cached separately from code)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code — no .env (keys come from Cloud Run env vars)
COPY agent/   ./agent/
COPY core/    ./core/
COPY static/  ./static/
COPY data/testing_labeled_addresses_2026-05-17.csv ./data/testing_labeled_addresses_2026-05-17.csv
COPY server.py .

# Cloud Run injects PORT; default to 8080 for local container runs.
ENV PORT=8080
EXPOSE 8080

CMD exec uvicorn server:app --host 0.0.0.0 --port ${PORT:-8080}
