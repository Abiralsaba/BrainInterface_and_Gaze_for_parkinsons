#!/usr/bin/env bash
# ──────────────────────────────────────────────
#  NeuroWheel — Start Script
#  Activates the virtual environment, installs
#  dependencies if needed, and launches the app.
# ──────────────────────────────────────────────

set -e

# Resolve the project root (directory where this script lives)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/.venv"

# Create venv if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "🔧 Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# Activate venv
source "$VENV_DIR/bin/activate"

# Install dependencies if requirements.txt exists
if [ -f "requirements.txt" ]; then
    echo "📦 Checking dependencies..."
    pip install -q -r requirements.txt
fi

# Launch the app (pass any CLI args through)
echo "🚀 Starting NeuroWheel camera app..."
python camera_app.py "$@"
