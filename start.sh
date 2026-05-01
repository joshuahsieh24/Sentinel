#!/bin/bash
cd "$(dirname "$0")"
pkill -f "uvicorn main:app" 2>/dev/null
lsof -ti:8000 | xargs kill -9 2>/dev/null
sleep 0.3
python3 -m uvicorn main:app --port 8000 --log-level warning
