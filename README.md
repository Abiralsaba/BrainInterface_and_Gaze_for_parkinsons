# NeuroWheel camera prototype

Minimal laptop-only implementation of camera gaze steering and eye-controlled
communication. It does not connect to the EEG board, ESP32 or motors yet.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python camera_app.py --check
python camera_app.py
```

The first run downloads the official MediaPipe face model to `models/` and
starts guided calibration. Keep the head still and follow the centre, left,
right and closed-eye prompts. Calibration is saved locally; press `C` to redo
it. The program does not record or save camera images.

If macOS blocks the camera, allow the terminal or Python under **System
Settings → Privacy & Security → Camera**, then reopen the program.

## Use

Communication mode is the safe default:

- Look left/right for 0.25 seconds; navigation repeats while you keep looking.
- A short two-eye blink moves the selection down by one keyboard row.
- Close both eyes for 0.8 seconds to select the highlighted key.
- `HELP` and `WATER` select and speak quick phrases.
- Select `MOBILITY` to enter the on-screen steering simulator.

Mobility mode:

- Look left/right continuously to maintain the steering intent.
- A single blink is ignored in mobility mode.
- Double blink, eyes closed for 0.8 seconds, or face loss for 0.3 seconds sends
  `STOP` and returns to communication mode.

Operator keys: `C` recalibrates, `M` changes mode, `I` reverses horizontal
direction, `R` clears text, and `Q` or Escape exits.

## Files

- `camera_control.py` — tested, dependency-free safety and interaction logic.
- `camera_app.py` — OpenCV capture, MediaPipe inference, calibration and UI.
- `test_camera_control.py` — deterministic unit tests for all critical states.

The application produces camera intent only. Hardware networking must later
consume this intent through a separate fail-safe command-fusion layer.
