#!/bin/bash
set -e

echo "=== Job Search Automation Setup ==="

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate venv
source .venv/bin/activate

# Install Python deps
echo "Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt

# Playwright browsers
echo "Installing Playwright Chromium..."
playwright install chromium

# Copy env template if .env doesn't exist
if [ ! -f .env ]; then
    cp .env.example .env
    echo ""
    echo "⚠  Created .env — add your ANTHROPIC_API_KEY before running!"
fi

mkdir -p credentials

echo ""
echo "=== Setup complete ==="
echo ""
echo "Activate the environment first:"
echo "  source .venv/bin/activate"
echo ""
echo "Then run:"
echo "  python main.py --dry-run    # test: 1 job, no submission"
echo "  python main.py              # full run with review"
echo "  python main.py --stats      # show statistics"
echo "  python main.py --report     # open HTML report"
echo "  python scheduler.py         # daily auto-run at 09:00"
echo ""
echo "Optional — Google Drive:"
echo "  1. Follow instructions in .env.example (GOOGLE DRIVE section)"
echo "  2. Save credentials/google_credentials.json"
echo "  3. Set GOOGLE_DRIVE_ENABLED=true in .env"
