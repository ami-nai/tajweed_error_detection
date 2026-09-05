#!/usr/bin/env bash
# Start the tajweed backend + ngrok tunnel on this laptop.
# Usage: ./start_backend.sh   (from the repo root)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT/td_backend"
LOG_BACKEND=/tmp/opencode/uvicorn.log
LOG_NGROK=/tmp/opencode/ngrok.log
mkdir -p /tmp/opencode

# Kill any stale instances before starting (safe to re-run anytime).
if pgrep -f "uvicorn main:app" > /dev/null; then
  echo "Stopping stale backend..."
  pkill -f "uvicorn main:app"
  sleep 1
fi
if pgrep -f "ngrok http 8000" > /dev/null; then
  echo "Stopping stale ngrok..."
  pkill -f "ngrok http 8000"
  sleep 1
fi

if pgrep -f "uvicorn main:app" > /dev/null; then
  echo "Backend already running (pid $(pgrep -f 'uvicorn main:app'))."
else
  echo "Starting backend..."
  cd "$BACKEND_DIR"
  setsid nohup "$BACKEND_DIR/fastapienv/bin/python" -m uvicorn main:app \
      --host 0.0.0.0 --port 8000 > "$LOG_BACKEND" 2>&1 < /dev/null &
  disown || true
  echo "Backend starting (logs: $LOG_BACKEND). Waiting for port 8000..."
  for _ in $(seq 1 120); do
    if ss -tlnp 2>/dev/null | grep -q ":8000 "; then
      echo "Backend is UP on port 8000."
      break
    fi
    sleep 1
  done
fi

if pgrep -f "ngrok http 8000" > /dev/null; then
  echo "ngrok already running."
else
  echo "Starting ngrok tunnel..."
  setsid nohup ngrok http 8000 --log stdout > "$LOG_NGROK" 2>&1 < /dev/null &
  disown || true
  echo "ngrok starting (logs: $LOG_NGROK). Waiting for tunnel..."
  for _ in $(seq 1 30); do
    if curl -s http://localhost:4040/api/tunnels 2>/dev/null | grep -q public_url; then
      break
    fi
    sleep 1
  done
fi

echo "=========================================================="
echo "Backend status: $(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/docs 2>/dev/null) on :8000"
HTML_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null | grep -o 'https://[a-z0-9.-]*\.ngrok-free\.dev' | head -1)
echo "ngrok URL:       $HTML_URL"
echo "Flutter build:   flutter build apk --release --dart-define=BACKEND_URL=${HTML_URL/https/wss}"
echo "WebSocket test:  ${HTML_URL/https/wss}/ws/recite"
echo "=========================================================="