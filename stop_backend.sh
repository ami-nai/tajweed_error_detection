#!/usr/bin/env bash
# Stop the tajweed backend and ngrok tunnel on this laptop.
set -uo pipefail

if pgrep -f "ngrok http 8000" > /dev/null; then
  echo "Stopping ngrok tunnel..."
  pkill -f "ngrok http 8000"
else
  echo "ngrok not running."
fi

if pgrep -f "uvicorn main:app" > /dev/null; then
  echo "Stopping backend..."
  pkill -f "uvicorn main:app"
else
  echo "Backend not running."
fi

sleep 1
if pgrep -f "ngrok http 8000" > /dev/null || pgrep -f "uvicorn main:app" > /dev/null; then
  echo "Some processes still alive; forcing..."
  pkill -9 -f "ngrok http 8000" || true
  pkill -9 -f "uvicorn main:app" || true
fi

echo "Done. Everything stopped."