#!/bin/bash
# ─────────────────────────────────────────────────────────────
# BTC DCA Strategy — double-click this file to run
# ─────────────────────────────────────────────────────────────
# First run: installs Python dependencies automatically.
# Every run after: just opens and runs.
# Requires Python 3 to be installed (https://www.python.org).
# ─────────────────────────────────────────────────────────────

cd "$(dirname "$0")"

# ── Check Python is installed ─────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo ""
    echo "  Python 3 is not installed."
    echo "  Download it from https://www.python.org/downloads/"
    echo "  Then double-click this file again."
    echo ""
    exec bash
    exit 1
fi

# ── Create virtual environment if it doesn't exist ────────────
if [ ! -d "venv" ]; then
    echo ""
    echo "  Setting up for the first time..."
    python3 -m venv venv
fi

source venv/bin/activate

# ── Install / update dependencies if needed ───────────────────
python3 -c "import requests, yfinance" 2>/dev/null
if [ $? -ne 0 ]; then
    echo ""
    echo "  Installing dependencies (one-time, takes ~30 seconds)..."
    pip install -q -r requirements.txt
    echo "  Done."
fi

# ── Run ───────────────────────────────────────────────────────
echo ""
python3 btc_dca.py

exec bash
