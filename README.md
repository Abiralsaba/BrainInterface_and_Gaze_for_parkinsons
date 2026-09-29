# NeuroWheel camera prototype

Camera gaze, hand-gesture, and ESP32-S3 rover control prototype. The original
communication UI remains laptop-only; the dedicated hand and eye car programs
send fail-safe commands to the rover over its Wi-Fi access point.

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

## Rover control

Upload `carcode.ino` to an ESP32-S3, connect the laptop or phone to
`ESP32-CAR`, and use one controller at a time:

```bash
python hand_car_control.py
python eye_car_control.py
```

The webpage at `http://192.168.4.1` keeps its independent left/right
joysticks. The firmware also accepts the camera programs' throttle/steering
commands, so either interface works without reflashing. Close or stop one
controller before starting another; simultaneous controllers intentionally
compete for the same motors.

## Files

- `camera_control.py` — tested, dependency-free safety and interaction logic.
- `camera_app.py` — OpenCV capture, MediaPipe inference, calibration and UI.
- `test_camera_control.py` — deterministic unit tests for all critical states.
- `car_transport.py` — non-blocking, latest-command-wins ESP32 transport.
- `hand_car_control.py` / `eye_car_control.py` — hardware car controllers.
- `carcode.ino` — dual-protocol ESP32-S3/L298N firmware and web controller.

The ESP32 watchdog stops both motors when fresh commands disappear.

## EEG packet trace and live graph

`thinkgear_eeg.py` reads NeuroSky ThinkGear-compatible packets from the CH343
without transmitting anything. It verifies packet checksums, traces the wire
bytes, decodes signal quality, attention, meditation, blink strength, raw EEG,
and all eight EEG power bands, and displays three scrolling graphs.

Do not place electrodes on a person while the EEG/TTL board is electrically
connected to a USB-powered computer unless the data/power connection uses
appropriate medical-grade galvanic isolation. For a direct CH343 bench test,
leave the electrodes off-body.

First verify the graph and parser without hardware:

```bash
python3 thinkgear_eeg.py --demo --duration 10
```

Then trace the live 9600-baud stream and save decoded packets:

```bash
python3 thinkgear_eeg.py \
  --port /dev/cu.usbmodem5AE71016131 \
  --baud 9600 \
  --trace \
  --csv eeg_capture.csv
```

Close the graph or press `Ctrl-C` to stop. If `bytes=0` remains visible, the
serial port opened correctly but the adapter RX line is idle; check that the
TGM TX goes to CH343 RX, both sides share GND, the module has 3.3 V power, and
the selected baud matches the module mode. `signal=0` is good contact;
non-zero values indicate increasing noise or no contact. Raw samples may not be
present in the module's 9600-baud processed-data mode.

To display the latest candidate threshold on the original scrolling live graph
(visual testing only, with no motor output), run:

```bash
python3 thinkgear_eeg.py \
  --port /dev/cu.usbmodem5AE71016131 \
  --baud 9600 \
  --candidate-calibration eeg_calibration_candidate.json \
  --csv eeg_live_detected.csv
```

The magenta `FOCUS DETECTED` trace is `100` for detected focus and `0` for
rest. Bad contact displays `NO USABLE SIGNAL`. The candidate option is
visualization-only because the current profile failed validation; only a JSON
profile containing `"valid": true` can be loaded with `--calibration`.

For an initial **wheels-raised** rover test using that rejected candidate,
connect the Mac to `ESP32-CAR` and explicitly enable the override:

```bash
python3 thinkgear_eeg.py \
  --port /dev/cu.usbmodem5AE71016131 \
  --baud 9600 \
  --candidate-calibration eeg_calibration_candidate.json \
  --enable-car \
  --allow-candidate-car \
  --focus-hold 5 \
  --car-url http://192.168.4.1
```

The rover stays stopped until the smoothed graph detection remains focused for
five continuous seconds. Focus loss or bad signal stops it immediately;
missing EEG packets stop it after 1.5 seconds, and the ESP32's independent
450 ms command watchdog remains active. Closing the graph or pressing
`Ctrl-C` also sends `STOP`. The current firmware has no speed/PWM setting, so
do not perform the first test with the wheels on the floor.

For a guided preliminary focus/rest calibration, close the live graph first
and run:

```bash
python3 calibrate_eeg.py \
  --port /dev/cu.usbmodem5AE71016131 \
  --baud 9600 \
  --live-plot \
  --show-plot
```

The tool discards a 15-second sensor warm-up, counts down 10 seconds of a
steady focus task and 10 seconds of relaxed wakefulness, and writes
`eeg_calibration.csv`, `eeg_calibration.json`, and `eeg_calibration.png`. It rejects calibration when
contact is unreliable or the two attention distributions overlap too much;
never use a rejected threshold for normal vehicle control.
