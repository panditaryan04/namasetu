#!/bin/sh
# API container entrypoint: migrate, seed SAMPLE data (no-op when unchanged), then serve.
set -e
alembic upgrade head
python -m app.db.seed
# First start only: store an evaluation run in the background so the Evaluation page has real numbers.
python scripts/run_eval.py --use-db --check --if-missing > /tmp/first-eval.log 2>&1 &
# Trust X-Forwarded-For only from the reverse proxy (the frontend's nginx), so rate limits see the browser's IP.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}"
