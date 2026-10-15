#!/bin/bash
set -e

echo "Starting FastAPI backend on internal port 8000..."
python -m uvicorn src.api_server:app --host 127.0.0.1 --port 8000 &
FASTAPI_PID=$!

echo "Waiting for FastAPI healthcheck..."
for i in {1..30}; do
  if curl -s http://127.0.0.1:8000/api/health > /dev/null; then
    echo "FastAPI is healthy and online."
    break
  fi
  sleep 1
done

echo "Starting Next.js frontend on public PORT ${PORT:-3000}..."
cd /app/frontend
exec npx next start -p "${PORT:-3000}" -H 0.0.0.0
