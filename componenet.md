# NeuroWheel — Balanced Bangladesh-Market Component List

**Price research date:** 1 September 2026  
**Build:** Reliable, reasonably priced, unloaded 2WD working prototype  
**Import required:** No  
**Estimated component total:** **৳31,399**, excluding delivery and an existing laptop

This is a balanced design rather than the absolute cheapest design. More money
is spent on the EEG headset, EMG acquisition, eye camera and power system—the
parts that directly affect signal quality, safety and demonstration reliability.
Simple robot mechanics remain economical.

## Final control architecture

```text
NeuroSky MindWave 2 --Bluetooth--> Laptop BCI/command-fusion program

Left and right EMG sensors -> voltage dividers -> ADS1115 -> wearable ESP32
                                                        |
                                                        +--Wi-Fi--> Laptop

1080p USB webcam ------------------------------USB-----> Laptop eye keyboard/TTS

Laptop --Wi-Fi command + heartbeat--> wheel ESP32 -> TB6612 -> left/right motors
                                                  ^
                                     HC-SR04 + physical E-stop
```

Recommended MVP commands:

- Sustained EEG attention above a calibrated threshold enables short forward
  movement pulses.
- Left EMG steers left; right EMG steers right; both EMG signals mean `STOP`.
- Loss of EEG quality, low attention, stale communication, front obstacle or
  E-stop activation means `STOP`.
- The webcam is used only for the communication keyboard during the first
  version, keeping mobility and communication modes separate.
- Reverse motion is disabled initially. A single-channel forehead EEG headset
  cannot reliably identify five independent imagined commands, so the report
  must not claim that it does.

## Recommended purchase list

| Subsystem | Component and Bangladesh link | Qty | Unit price | Subtotal | Selection reason |
|---|---|---:|---:|---:|---|
| EEG | [NeuroSky MindWave Mobile 2 — TechBazar](https://techbazar.com.bd/product/neurosky-mindwave-mobile-2/) | 1 | ৳14,700 | **৳14,700** | Complete wearable headset with dry forehead sensor, ear reference/ground, battery power and Bluetooth. Safer and much easier to integrate than a bare EEG board. |
| EEG battery | [Rangs Power AAA battery, 2-pack — ElectronicsBD](https://electronics.com.bd/sunlight-aaa-battery-2pcs) | 1 pack | ৳70 | **৳70** | MindWave requires one AAA cell; the second is a spare. |
| EMG | [High-quality EMG Muscle Sensor V3 with cable and electrodes — RoboticsBD](https://store.roboticsbd.com/sensors/2918-emg-muscle-sensor-with-cable-and-electrodes-controller-detects-muscle-activity-robotics-bangladesh.html) | 2 | ৳2,550 | **৳5,100** | Complete left/right muscle channels with rectified analog outputs, adjustable gain, cables and initial electrodes. |
| EMG electrodes | [Biomedical sensor pads, 10-pack — TechShopBD](https://techshopbd.com/product/biomedical-sensor-pad-china-10-pack) | 1 pack | ৳608 | **৳608** | Replacement snap electrodes for repeated testing; confirm snap fit with the supplied EMG cables. |
| EMG power | [9 V extra-heavy-duty battery — ElectronicsBD](https://electronics.com.bd/9v-volts-extra-heavy-duty-battery) | 2 | ৳70 | **৳140** | Two batteries create the required `+9 V / GND / -9 V` supply for both EMG sensors. |
| EMG power | [9 V battery connector — RoboticsBD](https://store.roboticsbd.com/robotics-parts/651-9v-battery-connector-robotics-bangladesh.html) | 2 | ৳10 | **৳20** | Connects the two 9 V cells into a split supply. |
| EMG controller power | [HW-411A LM2596 buck converter — RoboticsBD](https://store.roboticsbd.com/power-module-adapter/1855-hw-411a-lm2596-dc-to-dc-buck-converter-step-down-module-power-supply-robotics-bangladesh.html) | 1 | ৳99 | **৳99** | Converts the positive 9 V rail to 5.0 V for the wearable ESP32. |
| EMG wireless MCU | [ESP32 V1.3 CH340C NodeMCU-32 — RoboticsBD](https://store.roboticsbd.com/development-boards/2268-esp32-v13-dev-board-ch340c-nodemcu-32-robotics-bangladesh.html) | 1 | ৳580 | **৳580** | Reads EMG through the ADS1115 and sends left/right features to the laptop over Wi-Fi. |
| EMG ADC | [ADS1115 original 16-bit ADC module — RoboticsBD](https://store.roboticsbd.com/analog-digital-module/383-ads1115-4-channel-16-bit-precision-analog-to-digital-converter-adc-module-original-robotics-bangladesh.html) | 1 | ৳570 | **৳570** | Cleaner, more repeatable two-channel acquisition than the ESP32's internal ADC. Regular price is used because the indexed offer had expired. |
| EMG level protection | [Kiloohm resistor packs — RoboticsBD](https://store.roboticsbd.com/components/245-kiloohm-kω-14w-resistors-pack-of-5-robotics-bangladesh.html) | 2 packs | ৳5 | **৳10** | Buy one 20 kΩ pack and one 10 kΩ pack. Each pair forms a 2:1 divider, mapping a possible 0–9 V EMG output to 0–3 V for the 3.3 V ADS1115. |
| Wearable enclosure | [ABS project box, about 115×70×30 mm — ElectronicsBD](https://electronics.com.bd/black-plastic-project-box-115x70x30mm) | 1 | ৳200 | **৳200** | Covers the EMG power and processing electronics so conductors cannot be touched accidentally. |
| Wheel controller | [ESP32 V1.3 CH340C NodeMCU-32 — RoboticsBD](https://store.roboticsbd.com/development-boards/2268-esp32-v13-dev-board-ch340c-nodemcu-32-robotics-bangladesh.html) | 1 | ৳580 | **৳580** | Receives fused commands from the laptop and independently enforces timeout and obstacle stopping. |
| Robot base | [2WD mobile robot platform — RoboticsBD](https://store.roboticsbd.com/robot-platform-chassis-Bangladesh/259-2wd-wheel-drive-mobile-robot-platform-chassis-robotics-bangladesh.html) | 1 | ৳650 | **৳650** | Includes two 3–6 V geared motors, wheels, chassis and caster. |
| Motor driver | [TB6612FNG dual motor driver — RoboticsBD](https://store.roboticsbd.com/robotics-parts/684-motor-driver-dual-tb6612fng-1a-robotics-bangladesh.html) | 1 | ৳219 | **৳219** | Its 1.2 A average channel rating comfortably exceeds the selected chassis motors' approximately 250 mA listed maximum load current. |
| Obstacle sensor | [HC-SR04 Plus 3.3–5 V ultrasonic sensor — RoboticsBD](https://store.roboticsbd.com/sensors/2446-hc-sr04-plus-ultrasonic-sonar-sensor-2021-33v-5v-robotics-bangladesh.html) | 1 | ৳93 | **৳93** | Front stopping sensor; this Plus version is directly compatible with ESP32 3.3 V logic. |
| Emergency stop | [AB6-V 16 mm emergency-stop button — RoboticsBD](https://store.roboticsbd.com/electronic-switches/2649-16mm-emergency-stop-push-button-ab6-v-robotics-bangladesh.html) | 1 | ৳99 | **৳99** | Interrupts the motor power branch in hardware rather than relying only on firmware. Confirm the NC terminals and DC contact rating before wiring. |
| Robot battery | [XW Power Eagle 2S 7.4 V 1500 mAh LiPo — RoboticsBD](https://store.roboticsbd.com/battery/1259-lipo-battery-1500mah-74v-2s-robotics-bangladesh.html) | 1 | ৳1,650 | **৳1,650** | Adequate current and runtime for the two-motor tabletop base; uses T-plug output and JST-XH balance lead. |
| Battery connector | [T-connector male/female pair — RoboticsBD](https://store.roboticsbd.com/quadcopter/825-t-connectors-male-and-female-pair-robotics-bangladesh.html) | 1 pair | ৳40 | **৳40** | Matches the LiPo's T-plug and provides a solderable connection to the fuse and buck converters. |
| Robot charger | [iMax B3 Compact 2S/3S balance charger — ElectronicsBD](https://electronics.com.bd/imax-b3-rc-battery-balance-charger) | 1 | ৳450 | **৳450** | Compatible with the battery's 2S JST-XH balance connector. |
| Robot regulators | [HW-411A LM2596 buck converter — RoboticsBD](https://store.roboticsbd.com/power-module-adapter/1855-hw-411a-lm2596-dc-to-dc-buck-converter-step-down-module-power-supply-robotics-bangladesh.html) | 2 | ৳99 | **৳198** | One set to 5.0 V for ESP32/sensor logic and one set to about 5.5 V for the 3–6 V motors. Separate rails reduce motor-noise resets. |
| Fuse holder | [BLX-A 5×20 mm covered fuse holder — RoboticsBD](https://store.roboticsbd.com/components/2055-blx-a-cover-with-fuse-holder-robotics-bangladesh.html) | 1 | ৳30 | **৳30** | Protects the motor branch and wiring. |
| Fuses | [2 A fast-acting 5×20 mm fuse — ElectronicsBD](https://electronics.com.bd/fast-acting-glass-tube-fuse-2a-5x20) | 2 | ৳15 | **৳30** | One installed and one spare; verify by measuring actual motor stall current. |
| Eye tracking | [A4Tech PK-940HA 1080p autofocus webcam — Star Tech](https://www.startech.com.bd/a4tech-pk-940ha-fhd-webcam) | 1 | ৳5,000 | **৳5,000** | 1080p, autofocus and USB 2.0 provide a clearer, repeatable eye image without the cost of a C920. |
| Prototyping | [400-point solderless breadboard — RoboticsBD](https://store.roboticsbd.com/robotics-parts/120-breadboard-half-size-bare-400-tie-points-robotics-bangladesh.html) | 1 | ৳88 | **৳88** | Used for non-body-connected bench testing before final soldering. |
| Wiring | [40-piece 20 cm jumper-wire set — RoboticsBD](https://store.roboticsbd.com/robotics-parts/31-jumper-wire-40-pcs-set-20cm-robotics-bangladesh.html) | 1 | ৳100 | **৳100** | Select male-to-female for module headers; supplement with lab wire for motor power. |
| Final assembly | [Veroboard dot type — RoboticsBD](https://store.roboticsbd.com/robotics-parts/840-veroboard-dot-type-robotics-bangladesh.html) | 1 | ৳35 | **৳35** | Final soldered low-current interconnections. Do not route motor current through thin breadboard jumpers. |
| Insulation | [Heat-shrink tube — RoboticsBD](https://store.roboticsbd.com/assorted-kit/984--heat-shrink-tube-robotics-bangladesh.html) | 20 pieces | ৳2 | **৳40** | Insulates every soldered power and electrode-side joint. |
|  | **Recommended total** |  |  | **৳31,399** | Local delivery excluded; laptop and software reused. |

## Compatibility verification

| Connection | Electrical/data compatibility | Required wiring or setting | Status |
|---|---|---|---|
| MindWave → laptop | Headset provides Bluetooth/BLE; product page lists Windows and macOS support. | Use a laptop with Bluetooth 4.0 or later. Pair and prove that attention, signal-quality and raw values can be read before buying the rest. | **Compatible, but test OS/driver first** |
| EMG sensors → split battery | Each EMG V3 requires a positive and negative supply, typically ±5 V and at least approximately ±3.5 V. | Wire two 9 V cells in series: top=`+9 V`, midpoint=`GND`, bottom=`-9 V`; both sensors share these three rails. | **Compatible** |
| EMG output → ADS1115 | EMG output may approach the positive 9 V rail; ADS1115 powered at 3.3 V must not receive this directly. | For each channel: EMG `SIG` → 20 kΩ → ADC input; ADC input → 10 kΩ → GND. This produces at most about 3.0 V. | **Compatible only with divider** |
| ADS1115 → wearable ESP32 | Both operate at 3.3 V and communicate over I²C. | `VDD=3.3 V`, common GND, `SDA/SCL` to ESP32 I²C pins; use 128–250 samples/s for envelope signals. | **Compatible** |
| Wearable ESP32 → laptop | Both support 2.4 GHz Wi-Fi. | Put laptop and both ESP32 boards on one router/hotspot; transmit timestamped EMG values by UDP or WebSocket. | **Compatible** |
| Laptop → wheel ESP32 | Both support 2.4 GHz Wi-Fi. | Send command, sequence number and timestamp at least 10 times/s; wheel timeout stops motors after 300 ms without a valid packet. | **Compatible** |
| Wheel ESP32 → TB6612 | ESP32 uses 3.3 V logic; TB6612 accepts a 2.7–5.5 V logic supply. | Power TB6612 `VCC` from 3.3 V, connect common GND, PWM/direction GPIOs, and hold `STBY` high only when safety conditions pass. | **Compatible** |
| TB6612 → chassis motors | Driver supports 1.2 A average per channel; selected base has one small motor per channel. | Set motor buck to about 5.5 V; left motor to channel A and right motor to channel B. | **Compatible** |
| 2S LiPo → electronics | LiPo is 7.4 V nominal and up to 8.4 V full; motors accept only 3–6 V and ESP32 needs regulated input. | Never connect raw battery voltage to motors or ESP32. Use separate buck converters set and measured at 5.5 V and 5.0 V. | **Compatible only through bucks** |
| HC-SR04 Plus → ESP32 | Plus model supports 3.3 V supply/logic. | Power from 3.3 V and connect Trigger/Echo to GPIO; no divider is needed for this specific Plus model. | **Compatible** |
| Webcam → laptop | USB 2.0 UVC webcam; OpenCV/MediaPipe can read it. | Mount directly in front of the face with steady lighting; select 1080p/30 fps. | **Compatible** |

## Parts intentionally not included

- No OpenBCI hardware or imported component.
- No Raspberry Pi: the existing laptop handles EEG, eye tracking, fusion and TTS.
- No separate display or speaker: use the laptop screen and speaker.
- No four-sensor ToF array: one front ultrasonic sensor is enough while reverse
  motion is disabled.
- No MyoWare 2.0: it is easier to power, but two local units cost about
  ৳23,900. The selected EMG V3 pair costs ৳5,100 and is workable when the
  documented split supply and ADC dividers are used.
- No full-size wheelchair motors or controller. This BOM is only for the
  unloaded model shown in the project demonstration.

If the laptop's built-in camera passes eye-tracking calibration, omit the
A4Tech webcam and the total becomes **৳26,399**. Do not omit it merely to save
money if the built-in camera cannot keep both eyes sharp and well exposed.

## Purchase order and acceptance tests

1. Buy the MindWave first. On the intended laptop, confirm Bluetooth pairing
   and record attention, signal quality and raw EEG for at least 15 minutes.
   NeuroSky provides ThinkGear tools that expose these values. If this fails,
   stop—do not purchase the rest based on an assumed software connection.
2. Buy one EMG sensor and prove the split supply, 2:1 divider and ADS1115 signal
   path with the electrodes **off the body**. Then test briefly on battery power.
   Purchase the second matching unit after the first path works.
3. Build the wheel base and test manual commands, E-stop, 300 ms command timeout
   and front obstacle stopping before enabling EEG or EMG commands.
4. Calibrate EEG and EMG separately, then integrate them. Start with the wheels
   raised and limit every accepted movement to a 300–500 ms pulse.
5. Confirm seller stock and current prices before payment; product pages can
   change after the research date.

## Safety requirements

- MindWave and the entire EMG wearable must be battery-powered while worn.
  Never attach USB, a charger, an oscilloscope or a mains-powered programmer to
  the EMG ESP32 while electrodes touch a person.
- Program the wearable ESP32 first, disconnect USB, then attach batteries and
  electrodes.
- Wire the E-stop in the motor-power branch so it stops motion even if the
  ESP32 firmware freezes.
- Charge the LiPo only with the balance charger, away from the user and robot.
- This is an educational prototype, not a medical device and not safe for
  transporting a person.
