#!/bin/bash
# Double-click this file in Finder to start MindChart.
# Keep the Terminal window open while using the app; close it to stop the app (this also locks your data).
cd "$(dirname "$0")"

if ! curl -s http://localhost:11434/api/version > /dev/null; then
  echo "Starting Ollama..."
  open -a Ollama
  sleep 3
fi

if lsof -nP -iTCP:8000 -sTCP:LISTEN > /dev/null; then
  echo "MindChart is already running."
  open http://localhost:8000
  exit 0
fi

python3 updater.py  # installs a newer version first, if one has been published

(sleep 1 && open http://localhost:8000) &
python3 server.py
