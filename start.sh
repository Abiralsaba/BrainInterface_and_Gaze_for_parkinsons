#!/usr/bin/env bash

# macOS launcher for the gaze keyboard only.
# This does not start EEG reading, the live graph, or rover control.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
APP="$SCRIPT_DIR/camera_app.py"
MODEL="$SCRIPT_DIR/models/face_landmarker.task"
CALIBRATION="$SCRIPT_DIR/calibration.json"

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Error: the project virtual environment is missing." >&2
    echo "Create it with: python3 -m venv $SCRIPT_DIR/.venv" >&2
    echo "Then install: $SCRIPT_DIR/.venv/bin/python -m pip install --no-compile -r $SCRIPT_DIR/requirements.txt" >&2
    exit 1
fi

if [[ ! -f "$APP" ]]; then
    echo "Error: camera_app.py was not found in $SCRIPT_DIR" >&2
    exit 1
fi

if [[ ! -f "$MODEL" ]]; then
    echo "Error: face tracking model is missing: $MODEL" >&2
    exit 1
fi

if [[ -f "$CALIBRATION" ]]; then
    echo "Starting gaze keyboard with saved calibration"
else
    echo "No saved eye calibration found; guided calibration will start"
fi

exec "$PYTHON_BIN" "$APP" \
    --model "$MODEL" \
    --calibration "$CALIBRATION" \
    "$@"
