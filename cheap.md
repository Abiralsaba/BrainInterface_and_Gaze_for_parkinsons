# NeuroWheel — Alternative Low-Cost Bangladesh Build

**Price research date:** 1 September 2026  
**Prototype:** Unloaded 2WD demonstration model  
**Import required:** No  
**Estimated total:** **৳11,027**, excluding delivery and an existing laptop  
**Cost saved compared with the balanced build:** **৳20,372**

This version keeps the safety hardware and reliable robot power system. It
saves money by using a bare Taurus single-channel EEG module and the laptop's
existing camera for steering and communication. It has more setup
risk than the MindWave version and is not a five-command thought classifier.

## Working architecture

```text
Forehead EEG pads -> Taurus TGM --3.3 V UART--> wearable ESP32
                                      |
                                      +--Wi-Fi--> laptop command fusion

Laptop built-in camera -------------------------> eye keyboard and TTS

Laptop --Wi-Fi + heartbeat--> wheel ESP32 --> TB6612 --> two motors
                                      ^             |
                              HC-SR04 Plus     hardware E-stop
```

The wearable ESP32 receives processed EEG packets at 9600 baud and sends only
timestamped features to the laptop. Camera gaze provides left/right steering;
double blink or a long eye closure stops motion.

## Bangladesh-market component list

| Subsystem | Component and local link | Qty | Unit price | Subtotal | Why it is included |
|---|---|---:|---:|---:|---|
| EEG | [Taurus TGM single-channel EEG module — RoboticsBD](https://store.roboticsbd.com/biometrics-skin/3142-taurus-tgm-single-channel-eeg-module-tgam-brain-wave-sensor-development-kit-robotics-bangladesh.html) | 1 | ৳3,980 | **৳3,980** | Lowest-cost local board found that explicitly provides raw EEG, signal bands, focus/relaxation and UART output. Use the current price supplied by the seller page. |
| EEG lead | [Three-conductor biomedical electrode cable — TechShopBD](https://techshopbd.com/product/sensor-cable-electrode-pads-3-connector-china) | 1 | ৳510 | **৳510** | Provides three snap leads. Its 3.5 mm plug is not guaranteed to fit the Taurus board; map the conductors and adapt them only after obtaining the Taurus pinout. |
| EEG signal shield | [Dual-track microphone cable, 1 m — ElectronicsBD](https://www.electronics.com.bd/lolihgt-cable-3-in-1-slim-mic-wire-1-meter) | 1 | ৳52 | **৳52** | Candidate lead for the forehead signal. Buy it only after confirming that the delivered cable has a separate outer braid/foil shield; that shield connects to the board's forehead-shield pad. |
| EEG electrodes | [Biomedical sensor pads, 10-pack — TechShopBD](https://techshopbd.com/product/biomedical-sensor-pad-china-10-pack) | 1 | ৳608 | **৳608** | Wet adhesive electrodes for forehead signal, reference and ground. |
| Body power | [Awei P5K 10000 mAh 5 V power bank — Star Tech](https://www.startech.com.bd/awei-p5k-10000mah-fast-charging-power-bank) | 1 | ৳790 | **৳790** | Isolated 5 V USB power for the body ESP32; never charge it while electrodes are worn. |
| Wearable controller | [ESP32 V1.3 CH340C NodeMCU-32 — RoboticsBD](https://store.roboticsbd.com/development-boards/2268-esp32-v13-dev-board-ch340c-nodemcu-32-robotics-bangladesh.html) | 1 | ৳580 | **৳580** | Reads Taurus UART and sends EEG features to the laptop by Wi-Fi. |
| Safety pull-down | [10 kΩ resistor pack — RoboticsBD](https://store.roboticsbd.com/components/245-kiloohm-kω-14w-resistors-pack-of-5-robotics-bangladesh.html) | 1 pack | ৳5 | **৳5** | Pulls TB6612 `STBY` low so the motors remain disabled during boot/reset. |
| Wearable enclosure | [ABS project box, about 115×70×30 mm — ElectronicsBD](https://electronics.com.bd/black-plastic-project-box-115x70x30mm) | 1 | ৳200 | **৳200** | Covers all body-connected electronics. |
| Wheel controller | [ESP32 V1.3 CH340C NodeMCU-32 — RoboticsBD](https://store.roboticsbd.com/development-boards/2268-esp32-v13-dev-board-ch340c-nodemcu-32-robotics-bangladesh.html) | 1 | ৳580 | **৳580** | Receives commands and independently stops on a stale heartbeat or obstacle. |
| Robot base | [2WD mobile robot platform — RoboticsBD](https://store.roboticsbd.com/robot-platform-chassis-Bangladesh/259-2wd-wheel-drive-mobile-robot-platform-chassis-robotics-bangladesh.html) | 1 | ৳650 | **৳650** | Includes two 3–6 V motors, wheels, caster and chassis. |
| Motor driver | [TB6612FNG dual motor driver — RoboticsBD](https://store.roboticsbd.com/robotics-parts/684-motor-driver-dual-tb6612fng-1a-robotics-bangladesh.html) | 1 | ৳219 | **৳219** | Correct 3.3 V logic and current capacity for the selected motors. |
| Front obstacle sensor | [HC-SR04 Plus 3.3–5 V — RoboticsBD](https://store.roboticsbd.com/sensors/2446-hc-sr04-plus-ultrasonic-sonar-sensor-2021-33v-5v-robotics-bangladesh.html) | 1 | ৳93 | **৳93** | This Plus version is compatible with ESP32 3.3 V logic. |
| Emergency stop | [AB6-V 16 mm emergency-stop button — RoboticsBD](https://store.roboticsbd.com/electronic-switches/2649-16mm-emergency-stop-push-button-ab6-v-robotics-bangladesh.html) | 1 | ৳99 | **৳99** | Physically interrupts the motor-power branch. Verify NC contacts and DC rating. |
| Robot battery | [2S 7.4 V 1500 mAh LiPo — RoboticsBD](https://store.roboticsbd.com/battery/1259-lipo-battery-1500mah-74v-2s-robotics-bangladesh.html) | 1 | ৳1,650 | **৳1,650** | Reliable current source for the tabletop model. |
| Battery connector | [T-connector male/female pair — RoboticsBD](https://store.roboticsbd.com/quadcopter/825-t-connectors-male-and-female-pair-robotics-bangladesh.html) | 1 | ৳40 | **৳40** | Matches the selected LiPo output. |
| LiPo charger | [iMax B3 2S/3S balance charger — ElectronicsBD](https://electronics.com.bd/imax-b3-rc-battery-balance-charger) | 1 | ৳450 | **৳450** | Matches the battery's 2S JST-XH balance lead. |
| Robot regulators | [HW-411A LM2596 buck converter — RoboticsBD](https://store.roboticsbd.com/power-module-adapter/1855-hw-411a-lm2596-dc-to-dc-buck-converter-step-down-module-power-supply-robotics-bangladesh.html) | 2 | ৳99 | **৳198** | Separate measured 5.0 V logic and approximately 5.5 V motor rails. |
| Fuse holder | [BLX-A covered 5×20 mm fuse holder — RoboticsBD](https://store.roboticsbd.com/components/2055-blx-a-cover-with-fuse-holder-robotics-bangladesh.html) | 1 | ৳30 | **৳30** | Protects motor wiring. |
| Fuses | [2 A fast-acting 5×20 mm fuse — ElectronicsBD](https://electronics.com.bd/fast-acting-glass-tube-fuse-2a-5x20) | 2 | ৳15 | **৳30** | One fitted and one spare; confirm the rating after measuring stall current. |
| Camera | Existing laptop camera | 1 | ৳0 | **৳0** | Use only if eye-tracking calibration is stable. Add the A4Tech camera from the balanced BOM if it is not. |
| Prototyping | [400-point breadboard — RoboticsBD](https://store.roboticsbd.com/robotics-parts/120-breadboard-half-size-bare-400-tie-points-robotics-bangladesh.html) | 1 | ৳88 | **৳88** | Bench testing only. |
| Wiring | [40-piece jumper-wire set — RoboticsBD](https://store.roboticsbd.com/robotics-parts/31-jumper-wire-40-pcs-set-20cm-robotics-bangladesh.html) | 1 | ৳100 | **৳100** | Low-current signal wiring. Use thicker wire for motor power. |
| Final assembly | [Veroboard dot type — RoboticsBD](https://store.roboticsbd.com/robotics-parts/840-veroboard-dot-type-robotics-bangladesh.html) | 1 | ৳35 | **৳35** | Final soldered low-current wiring. |
| Insulation | [Heat-shrink tube — RoboticsBD](https://store.roboticsbd.com/assorted-kit/984--heat-shrink-tube-robotics-bangladesh.html) | 20 | ৳2 | **৳40** | Insulates soldered joints. |
|  | **Estimated total** |  |  | **৳11,027** | Delivery and laptop excluded. |

## Will the cheap EEG board work?

### Taurus TGM: yes, with strict limitations

The Taurus listing specifies one EEG channel, three skin contacts, 3.3 V power,
processed focus/relaxation/band values and UART at 9600 or 115200 baud. It
advertises raw output at 512 samples/s, although a later parameter table on the
same page says 250 Hz. Either rate is sufficient for the intended attention and
band-power demonstration, but the firmware must measure the real packet rate
instead of assuming 512 Hz. The available outputs are enough for:

- detecting electrode contact quality;
- displaying real EEG/raw-band data;
- using a calibrated attention threshold to arm short forward pulses;
- detecting strong blink events as an optional input.

It is **not** enough for five dependable imagined commands. A frontal
single-channel sensor also cannot implement the four-electrode occipital SSVEP
architecture described in the original `brain.md`. For this cheap build, EEG
does only `ARM/FORWARD` intent and loss of signal means `STOP`.

There is more integration risk than with MindWave. The seller lists only one
Taurus module in the package, and the generic electrode cable is not a
guaranteed plug-in match.

The supplied V6.1 picture confirms four electrode-side solder pads. From left
to right, the Chinese labels translate as:

1. `Reference electrode` — skin reference, normally at an ear/earlobe;
2. `Forehead electrode` — active EEG signal on clean forehead skin;
3. `Forehead shield layer` — cable shield only, **not a skin electrode**;
4. `Reference ground` — the third skin contact.

Use a short shielded lead for the forehead signal. Its inner conductor goes to
the forehead-electrode pad and snap electrode; its outer shield goes only to
the forehead-shield pad. Do not join the shield to a skin electrode unless the
manufacturer's V6.1 wiring document explicitly requires it.

The serial/power side of the picture is unsafe to follow by itself. It labels
the four pads, top to bottom, as `VCC, RX, TX, VCC`, leaving no labelled GND.
One of the two VCC labels is almost certainly a drawing error, but guessing
which one could destroy the board. Obtain the V6.1 schematic/pinout or have
RoboticsBD identify 3.3 V and GND in writing before applying power.

The page also contains these internal conflicts:

- sampling rate is stated as both **512 Hz** and **250 Hz**;
- current is stated as both **5 mA typical** and **15 mA at 3.3 V**; the
  [Sichiray manufacturer page](https://www.sichiray.com/eeg) currently says
  **18 mA**;
- raw UART speed is given as **115200 baud** by the seller, while the
  manufacturer page lists output speeds of **9600 and 57600 baud**;
- Bluetooth is advertised, but no separate Bluetooth adapter, battery,
  electrodes or cable appears in `Package Includes`.

Design power for at least 20 mA and use the documented UART connection for the
first prototype. Before paying, ask RoboticsBD for:

1. the exact electrode, reference and ground pinout;
2. the UART packet/protocol document and voltage level;
3. confirmation of whether Bluetooth hardware is physically included on this
   exact ৳3,980 board, not merely supported by an optional adapter;
4. confirmation whether its actual raw sample rate is 250 or 512 Hz;
5. confirmation whether raw UART is 57600 or 115200 baud for V6.1;
6. identification of GND and 3.3 V on the duplicated-`VCC` header image;
7. a power-on or output test, because the page warns that specifications may
   not be completely accurate.

If the seller cannot provide the electrode pinout and packet document, **do
not buy this board**. Spend more for the complete MindWave headset.

## Camera steering

OpenCV captures frames and MediaPipe Face Landmarker supplies gaze and blink
values. Hold left/right gaze for 600 ms to steer the next 300 ms forward pulse.
Double blink or keep both eyes closed for 800 ms to stop. A normal single blink
never moves the robot. Camera loss for 300 ms also stops it.

## Required wiring and compatibility

| Connection | Required implementation |
|---|---|
| Taurus → ESP32 | Only after written pin confirmation: Taurus `3.3 V` to ESP32 `3.3 V`, common GND, and Taurus TX to ESP32 UART2 RX such as GPIO16. Do not power it from the duplicated-`VCC` picture alone. |
| Taurus electrode pads | Left-to-right in the supplied picture: reference electrode, forehead electrode, forehead shield, reference ground. Only three connect to skin; shield is the outer conductor around the forehead-signal wire. |
| Body power | Power the body ESP32 through USB from the disconnected power bank; its 3.3 V pin powers Taurus. Never charge the bank while worn. |
| ESP32 → laptop | Send EEG quality, attention and timestamp over 2.4 GHz Wi-Fi at least 10 times/s. |
| Camera → laptop | Built-in USB camera through OpenCV; MediaPipe performs gaze/blink detection. |
| Laptop → wheel | Command packet must include sequence and timestamp; wheel ESP32 stops after 300 ms without a fresh valid packet. |
| LiPo → robot | Never connect the 7.4–8.4 V battery directly to ESP32 or motors. Use the two measured buck-converter rails. |

## Buy-and-test order

1. Buy only the Taurus board, cables and pads after receiving its V6.1 pinout
   and protocol document. It must identify GND despite the duplicated `VCC`
   picture and explain the shield pad. Use UART first; Bluetooth is optional until its presence
   is confirmed. With USB disconnected and battery power only, prove
   signal-quality, attention and raw/band packets for 15 minutes and measure
   the actual raw samples received per second.
2. Validate EEG with at least 20 randomized focus/rest trials. If performance
   is not meaningfully above chance, use it only as a demonstration display;
   do not let it move the robot.
3. Calibrate centre/left/right gaze and stop gestures, then pass 20 randomized
   camera trials in stable lighting before enabling motors.
4. Test wheel commands, obstacle stop, E-stop and heartbeat timeout with the
   wheels raised before integrating any body signal.

## Safety boundary

- All EEG electronics must run only from the power bank while attached to a
  person. Disconnect USB, chargers, programmers and mains-powered instruments.
- The E-stop must break motor power physically.
- Reverse stays disabled and the prototype must not carry a person.
- This is an educational demonstration, not a medical device.
