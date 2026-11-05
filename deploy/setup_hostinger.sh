#!/usr/bin/env bash
# Deploy / setup script for Hostinger Ubuntu/Debian VPS
set -euo pipefail

APP_DIR="/opt/uzex_agent"
cd "$APP_DIR"

echo "=== 1. Creating Python venv and installing requirements ==="
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo "=== 2. Building DuckDB database if not already uploaded ==="
if [ ! -f "data/uzex.duckdb" ]; then
    echo "Building data/uzex.duckdb from data/raw/*.csv ..."
    .venv/bin/python -X utf8 etl/build_db.py
else
    echo "data/uzex.duckdb already exists ($(du -h data/uzex.duckdb | cut -f1))"
fi

echo "=== 3. Checking LLM models ==="
.venv/bin/python -X utf8 scripts/check_models.py

echo "=== 4. Installing and starting systemd service ==="
mkdir -p logs
sudo cp deploy/uzex-bot.service /etc/systemd/system/uzex-bot.service
sudo systemctl daemon-reload
sudo systemctl enable uzex-bot.service
sudo systemctl restart uzex-bot.service
sudo systemctl status uzex-bot.service --no-pager

