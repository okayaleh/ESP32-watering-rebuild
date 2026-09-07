# Hardware and board configuration

The target is an ESP32-WROOM-32 devkit with normally closed 12V solenoid valves, one suitable 3.3V-controlled MOSFET driver per valve, an ADS1115 ADC and capacitive soil probes. AHT20/BMP280 environment sensors and an LM393 digital rain input are optional. The software cannot establish whether a particular MOSFET module switches fully at 3.3V; verify its gate-drive requirements and actual valve closure.

Each solenoid needs a flyback diode across the coil: cathode toward +12V, anode toward the switched negative terminal. Provide common ground, sufficient regulated ESP32 power, and hardware gate bias that holds each valve **closed during reset and loss of power**. Active-high MOSFET gates normally use a 10k pull-down; active-low relay/driver inputs need the appropriate pull-up or other fail-closed circuit. A pull-down on active-low control can open a valve during reset. Use an independent maximum-on timer/master shutoff where flooding has serious consequences.

## WROOM-32 defaults

| Connection | Default |
|---|---|
| ADS1115 SDA | GPIO21 |
| ADS1115 SCL | GPIO22 |
| Valve1 active-high drive | GPIO26 |
| Addressable RGB LED data | GPIO2 |
| First ADS1115 | 0x48 |
| First zone | Global ADC channel0 |

Moisture probe analog output goes to ADS1115 A0–A3, not an ESP32 GPIO. Keep the ADC/sensor signal within its supply range. Use 3.3V I2C pull-ups and one suitable pull-up set across the shared bus. The bus is fixed to100kHz with an explicit transaction timeout and open-drain recovery pulses.

All four possible ADS1115 boards use the **same two wires**. Select addresses with ADDR: GND→0x48, VDD→0x49, SDA→0x4A, SCL→0x4B. `ads1115_addresses` determines board order; global channel `c` selects address index `c//4`, input `c%4`. Keep that order stable when adding boards.

For WROOM-32, assign outputs only to 4,5,13,14,16,17,18,19,21,22,23,25,26,27,32,33. The validator excludes flash, UART, unbonded and boot-strapping pins for user assignments. GPIO34,35,36,39 are input-only and have no internal pull-up. GPIO2 is reserved for the configured onboard LED. This is more conservative than the old project's advice about using strapping outputs after boot.

## ESP32-S3-WROOM-1 N16R8

Use a MicroPython variant compatible with the board's flash and octal PSRAM. Before building, edit `src/config.py`:

```python
BOARD = "esp32s3"
I2C_SDA_PIN = 8
I2C_SCL_PIN = 9
VALVES = [{"name": "valve1", "pin": 10, "active_high": True,
           "flow_meter_pin": None}]
STATUS_LED_PIN = 48
STATUS_LED_TYPE = "rgb"
```

The supported output set is1,2,4–18,21,38–42,47,48, excluding the configured LED. Pins26–32 are flash and33–37 are consumed by octal PSRAM on N16R8. USB19/20, UART43/44, and strapping0/3/45/46 are excluded. This profile is for the named S3 module, not every board sold as “ESP32-S3”.

## RGB status LED

From application `2.0.0-rebuild.3`, the supplied configuration uses a single
addressable RGB LED (NeoPixel/WS2812-compatible) with its data input on GPIO2:

```python
STATUS_LED_PIN = 2
STATUS_LED_TYPE = "rgb"
```

| Controller state | Steady color |
| --- | --- |
| A valve is watering | Blue |
| Setup or rescue hotspot is active | White |
| Connected to home Wi-Fi | Green |
| Disconnected from Wi-Fi, with no active hotspot | Yellow |

Watering takes priority over the hotspot, and the hotspot takes priority over
the home connection. Startup grace, listener errors and updates do not replace
these four colors; inspect the dashboard for those diagnostics. Brightness is
kept low, and unchanged colors are not repeatedly written to the LED.

This is a one-data-pin addressable LED, not a four-lead analog RGB LED. Use a
common ground and follow the LED module's supply and data-level requirements.
An ordinary single-color onboard LED cannot display these colors. For that
hardware set `STATUS_LED_TYPE="plain"`, or set `STATUS_LED_PIN=None` to disable
the indicator. GPIO2 remains reserved for the configured indicator.

`STATUS_LED_TYPE="auto"` uses the legacy board profile (plain WROOM, RGB S3).
Electrical LED auto-detection is not possible through a successful NeoPixel
constructor; use the explicit `rgb` setting for this GPIO2 RGB installation.

Application OTA preserves the existing `config.py`. When upgrading a controller
that previously used `STATUS_LED_TYPE="plain"`, update the two values above in
its on-board `config.py` over USB with valve power disconnected, save and reboot.
Downloading a new release or editing only a laptop copy does not change that
local hardware setting. The supplied native platform image already supports
NeoPixel and does not need reflashing for this change.

The rain input is active-low and display-only. Flow-meter pins, `watering_mode` and `target_volume_l` are retained as configuration groundwork; every run is still timed and capped. Fit suitable external pull-ups and voltage conversion for input devices.
