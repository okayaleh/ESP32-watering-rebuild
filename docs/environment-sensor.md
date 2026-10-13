# AHT20 + BMP280 temperature, humidity and pressure module

[Documents](README.md) · [Hardware](hardware.md)

The UMLIFE AHT20 + BMP280 combination uses two digital I2C sensors on one
module. The AHT20 supplies air temperature and relative humidity; the BMP280
supplies atmospheric pressure and a fallback temperature reading. The
controller already has drivers for both chips. Version 2.0.0-rebuild.9 puts
their readings in an always-visible **At the planter** panel above the zones.

## Connect to the existing classic ESP32

Turn off the ESP32 and valve supply before changing wiring. Follow the
printed pin labels on your module; the physical pin order can vary between
board revisions.

| Module pin | ESP32 connection | Shared with existing ADS1115 |
| --- | --- | --- |
| VDD, VCC or 3V3 | **3.3V** | ADS1115 VDD |
| GND | GND | ADS1115 GND |
| SDA | **GPIO21** | ADS1115 SDA |
| SCL | **GPIO22** | ADS1115 SCL |

These are the saved I2C pins on the project's current classic ESP32. If you
have deliberately changed I2C pins, use the pins shown in your dashboard's
hardware configuration instead. The ESP32-S3 profile can use different pins.

Connect the sensor in parallel with the ADS1115: both SDA wires meet at
GPIO21, and both SCL wires meet at GPIO22. A breadboard or suitable terminal
splitter can provide additional connection points. The new module does not
use an ADS1115 A0–A3 analog input and does not need a new watering zone.

**No ADC adapter, I2C multiplexer or voltage-level converter is needed for
one of these modules on this 3.3V bus.** Use jumper wires and, if the board
arrived with loose header pins, solder the header properly first. Keep wires
short and away from valve/motor wiring. Keep all I2C pull-ups at 3.3V; do not
add pull-up resistors blindly when the breakout boards already provide them.

Use **3.3V, not the ESP32's 5V/VIN pin**, for this connection. Marketplace
module descriptions do not establish that every board revision includes
5V-safe logic conversion. Bosch specifies a maximum 3.6V operating supply
for the bare BMP280. See the [Bosch BMP280 datasheet](https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bmp280-ds001.pdf).
The AHT20 also supports 3.3V operation and communicates digitally over I2C;
see the [Aosong AHT20 manual](https://www.aosong.com/userfiles/files/media/Data%20Sheet%20AHT20.pdf).

## Confirm detection and readings

1. Restore controller power and open its dashboard.
2. In **Garden configuration → Hardware & I2C**, use **Scan I?C bus** and wait
   for the completed result. The existing ADS1115 normally appears at `0x48`;
   the AHT20 should add `0x38`, and the BMP280 should add `0x76` or `0x77`.
   Automatic scans also discover the chips without a settings change.
3. Allow approximately 30–60 seconds while watering is idle. The environment
   driver normally samples every 30 seconds; the dashboard refreshes about
   every five seconds. Sensor polling pauses during active watering, so
   existing readings can remain displayed until watering finishes.
4. **At the planter** shows air temperature in °F and °C, air humidity in
   % RH, and station pressure in hPa. Missing readings appear as dashes,
   with partial/error status rather than invented zero values.

The two chips have different I2C addresses and can coexist with the ADS1115.
Connect only one AHT20 module to this bus: its fixed `0x38` address would
conflict with a second one. Multiple AHT20 modules would need a different
bus or an I2C multiplexer and corresponding firmware support.

If only `0x48` is found, power off and check the module's 3.3V supply, common
ground, SDA/SCL labels and solder joints. If `0x38` is found without a BMP
address, temperature/humidity may work while pressure remains unavailable.
If only a BMP address is found, pressure and fallback temperature may work
while humidity remains unavailable. A BMP chip-ID error can indicate a
different chip, such as a BME280, rather than the specified BMP280.

## Placement and future shade control

This is an **air sensor**, not a soil-temperature probe. Keep the board out
of soil and water. Place it near the plant in a ventilated radiation shield,
protected from direct sun and irrigation spray and separated from heat from
the ESP32, regulators and valve drivers. Airflow must reach the sensing
openings; a sealed waterproof box cannot measure surrounding humidity well.
The sensor manufacturer's mounting guidance explains how board heat affects
the [AHT20's temperature and humidity measurements](https://www.aosong.com/userfiles/files/media/Data%20Sheet%20AHT20.pdf).

Pressure is the measured pressure at the planter, without an elevation
correction to sea level, so it can differ from a weather service's figure.
The BMP280 temperature reading primarily supports pressure compensation;
the dashboard prefers the AHT20 temperature when available. These are live
readings; the existing history chart still records soil moisture.

For future shading based on root-zone temperature, add a temperature probe
suited to wet soil and measure at the relevant depth. Air temperature alone
cannot establish soil temperature. Shade automation will also need a chosen
actuator and driver, travel limits, separate deploy/retract thresholds and
defined behavior when a sensor fails. This update displays the environment
readings; it does not operate a shade actuator or alter watering thresholds.
