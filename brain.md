# NeuroWheel — Final Working Prototype Architecture

**Purpose:** unloaded 2WD demonstration robot, not a person-carrying wheelchair  
**Control:** Taurus EEG for forward intent, laptop camera for steering, eye UI
for communication, and hardware/software safety stopping  
**Removed:** EMG, Raspberry Pi, separate display, reverse motion and extra sensors

## 1. Complete architecture

```text
3 EEG skin pads -> Taurus EEG V6.1 -> UART -> body ESP32 -> Wi-Fi --+
                                                                  |
Laptop camera -> OpenCV -> MediaPipe gaze/blink -------------------+-> Laptop
                                                                       |
                                    command fusion + keyboard + TTS ---+
                                                                       |
                                            Wi-Fi command + heartbeat  |
                                                                       v
Front HC-SR04 Plus -> wheel ESP32 -> TB6612 -> left/right 2WD motors
                           |              ^
                    300 ms timeout        |
                                  physical E-stop in motor supply
```

All body electronics run from a disconnected 5 V power bank. The robot uses
its own 2S LiPo. Only wireless data crosses between them.

## 2. Commands

| User action | Result |
|---|---|
| Good EEG contact + calibrated attention held for 800 ms | One 300 ms slow forward pulse |
| Look left steadily for 600 ms | Next movement pulse turns left |
| Look right steadily for 600 ms | Next movement pulse turns right |
| Double blink or both eyes closed for 800 ms | Immediate latched `STOP` |
| Face/EEG/packet lost, obstacle detected or E-stop pressed | Immediate `STOP` |

Single blinks never create motion because natural blinking would cause false
commands. Reverse is disabled because there is no rear obstacle sensor.

## 3. EEG wearing and wiring

The four lower Taurus pads in the supplied V6.1 picture are, left to right:

| Taurus pad | Connection |
|---|---|
| Reference electrode | Snap pad on one earlobe/mastoid |
| Forehead electrode | Inner conductor of shielded lead to pad above eyebrow |
| Forehead shield layer | Outer cable shield only; never attach it to skin |
| Reference ground | Snap pad on the other earlobe/mastoid |

Use clean, dry, hairless and unbroken skin. Confirm every lead with a
multimeter because biomedical-cable colours are not standardized.

The board image incorrectly labels its serial header `VCC, RX, TX, VCC` and
does not identify GND. **Do not guess.** Obtain the V6.1 pinout from RoboticsBD
before power is applied. After written confirmation, wire:

```text
Confirmed Taurus 3.3V  -> body ESP32 3V3
Confirmed Taurus GND   -> body ESP32 GND
Taurus TX              -> body ESP32 GPIO16 / UART2 RX
Taurus RX              -> body ESP32 GPIO17 / UART2 TX (optional)
Power bank 5V USB      -> body ESP32 USB connector
```

Start UART at 9600 baud for processed attention, relaxation, bands, blink and
signal-quality data. Determine the V6.1 raw-data baud rate from its protocol;
seller and manufacturer values conflict. Do not assume NeuroSky packet format.

## 4. Camera processing

- OpenCV reads the built-in webcam at 720p or 1080p and 30 fps.
- MediaPipe Face Landmarker supplies left/right eye-look and blink values.
- Calibrate centre, left, right, open and closed eyes for the actual user.
- Accept gaze only after 600 ms of stable direction across multiple frames.
- Camera should be centred 50–80 cm from the face with steady front lighting.
- Losing face tracking for 300 ms sends `STOP`.

The program has two exclusive modes:

- `MOBILITY`: EEG and gaze may control the robot; keyboard selection is off.
- `COMMUNICATION`: gaze keyboard and TTS work; motor output is forced off.

Default mode is `COMMUNICATION/STOP`. Changing mode requires a two-second dwell
on a large on-screen button followed by visual/audio confirmation.

## 5. Robot power wiring

Set both buck converters with a multimeter **before** connecting electronics.

```text
2S LiPo positive
  -> 2 A fuse
      +-> logic LM2596 set to 5.0 V -> wheel ESP32 VIN/5V
      |
      +-> E-stop NC contacts -> motor LM2596 set to 5.5 V -> TB6612 VM

2S LiPo negative
  -> both LM2596 IN-
  -> wheel ESP32 GND
  -> TB6612 GND
```

The E-stop cuts motor power but leaves the controller alive. This design does
not claim that software can read the E-stop state; the switch is an independent
hardware override.
Never connect raw 7.4–8.4 V LiPo voltage to an ESP32 or motor.

## 6. Wheel ESP32 signal wiring

| Wheel ESP32 | Connect to |
|---|---|
| `3V3` | TB6612 `VCC` and HC-SR04 Plus `VCC` |
| `GND` | TB6612, HC-SR04 Plus and both buck grounds |
| GPIO25 | TB6612 `PWMA` — left motor speed |
| GPIO26 | TB6612 `AIN1` |
| GPIO27 | TB6612 `AIN2` |
| GPIO13 | TB6612 `PWMB` — right motor speed |
| GPIO32 | TB6612 `BIN1` |
| GPIO33 | TB6612 `BIN2` |
| GPIO23 | TB6612 `STBY`; add 10 kΩ from `STBY` to GND |
| GPIO18 | HC-SR04 Plus `TRIG` |
| GPIO19 | HC-SR04 Plus `ECHO` |

```text
TB6612 A01/A02 -> left chassis motor
TB6612 BO1/BO2 -> right chassis motor
TB6612 VM      -> regulated 5.5 V after E-stop
```

This wiring applies only to the **HC-SR04 Plus 3.3–5 V** model. A normal 5 V
HC-SR04 Echo pin would require a voltage divider.

## 7. Software safety logic

The laptop sends `{sequence, timestamp, mode, speed, turn}` at least 10 times/s.
The wheel ESP32 accepts a movement command only when all conditions are true:

```text
mobility mode
AND EEG signal quality valid
AND current attention confirmation valid
AND face/eye tracking valid
AND command age < 300 ms
AND front distance >= 25 cm
```

Any false or unknown condition sets both PWM outputs to zero and drives
`STBY` low. On boot, Wi-Fi loss, program crash or sequence error, the robot
remains stopped. The hardware E-stop overrides this logic by removing `VM`.
Use approximately 25% PWM during initial tests.

## 8. Build order

1. Obtain the Taurus V6.1 power/electrode pinout and UART protocol.
2. Test EEG packets on battery power with motors absent.
3. Calibrate camera gaze/blink and test an on-screen simulator.
4. Wire robot power, E-stop and motors; test with wheels raised.
5. Verify packet timeout and obstacle stop before enabling EEG movement.
6. Integrate using 300 ms movement pulses at low speed.

## 9. Non-negotiable safety

- While electrodes touch a person, the body ESP32 must use only its power bank;
  disconnect USB from the laptop, chargers, programmers and oscilloscopes.
- Never charge the body power bank or robot LiPo while the system is worn.
- E-stop must physically interrupt the motor branch.
- This educational prototype must never transport a person.
