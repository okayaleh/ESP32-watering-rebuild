# Release notes

[Documents](README.md) · [Updates and rollback](ota.md) ·
[All GitHub releases](https://github.com/okayaleh/ESP32-watering-rebuild/releases)

## 2.0.0-rebuild.3 — dark mode and RGB status

- Dashboard light/dark toggle in the header, with the initial theme following
  the system preference and explicit choices remembered per browser.
- Dark colors for the dashboard, controls, configuration editors, status
  messages and history chart. Theme switching does not contact the controller
  or alter its watering settings.
- A single addressable RGB LED on GPIO2 glows **blue while watering**, **white
  in setup/rescue hotspot mode**, **green when connected to home Wi-Fi**, and
  **yellow when disconnected**. Priority follows that order, with steady,
  low-brightness output and no repeated writes of unchanged colors.
- The runtime passes the actual hotspot state to the indicator, including
  station retries while rescue access remains available. Startup grace,
  listener errors and firmware-update activity do not override these colors.
- The default configuration now selects `STATUS_LED_TYPE="rgb"` on GPIO2.
  Plain single-color LEDs and disabling the indicator remain supported.

### Upgrade from 2.0.0-rebuild.2

The application update uses the existing platform image, boot helpers and
settings format. No native firmware reflash is needed. Follow the normal
[release download and laptop mirror procedure](ota.md).

**Existing `config.py` is preserved by OTA.** If the controller was configured
for a plain LED, edit its on-board `config.py` over USB with valve power
disconnected, save these values, and reboot:

```python
STATUS_LED_PIN = 2
STATUS_LED_TYPE = "rgb"
```

This configuration is for a one-data-pin NeoPixel/WS2812-compatible RGB LED.
An ordinary single-color GPIO2 LED cannot display multiple colors; a four-lead
analog RGB LED requires different hardware and control. See the
[RGB wiring and status guide](hardware.md#rgb-status-led).

The prior `v2.0.0-rebuild.2` archive remains available for application rollback.
Rollback preserves local configuration, including the selected LED type.
Version .2 has its older RGB color behavior if that type is retained.
The current release's validation and remaining physical checks are recorded
in the [validation report](validation.md).

## 2.0.0-rebuild.2 — initial public rebuild

The first public release contains the rebuilt watering controller, dashboard,
network retry fixes, custom classic-ESP32 MicroPython ROMFS platform,
transactional application updates and recovery tools. Its USB bench and
short home-LAN measurements are preserved in the validation report.

Releases retain the current version and at least two earlier versions as
updates accumulate, with older history retained too. Published assets and
their tags are immutable. Choose a previously functioning compatible version
for your own controller; repository availability is not a hardware test record.
