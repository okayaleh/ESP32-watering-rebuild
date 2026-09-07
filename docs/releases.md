# Release notes

[Documents](README.md) · [Updates and rollback](ota.md) ·
[All GitHub releases](https://github.com/okayaleh/ESP32-watering-rebuild/releases)

## 2.0.0-rebuild.5 — independent GitHub updates

- The ESP32 can fetch application updates directly from the public GitHub
  repository over HTTPS with certificate, hostname and date verification.
  No laptop, USB connection or GitHub account is needed for normal updates.
- The release channel points to bounded manifests and flat files at immutable
  commit SHAs. Sizes, SHA-256 hashes and native-platform compatibility are
  checked before installation; redirects and unverified TLS are rejected.
- Fresh configurations enable daily automatic checks/installations at 04:00
  controller local time, with catch-up and retry. Checks wait for home Wi-Fi,
  synchronized time and idle watering. New watering pauses during maintenance.
- Manual checks and uploads always wait for explicit installation. The
  dashboard explains the source, version, automatic mode, failures and any
  deliberate rollback hold.
- Up to three compatible channel releases support manual version selection.
  Choosing an older release pauses automatic upgrades; a failed-boot release
  is excluded from future attempts. The transactional recovery and ROM cache
  retain their existing behavior.
- Dark mode, RGB status colors, Wi-Fi credentials, garden settings and
  schedules are preserved. The native platform and USB boot helpers are
  unchanged; those parts remain USB maintenance only.
- The build pins Zopfli 0.4.3 so Windows and Linux generate identical gzip
  dashboard bytes. Release verification rejects artifacts differing from
  the tagged manifest before publication.

### Upgrade from .2 or .3

Use USB or the existing LAN mirror once to install .5. Existing local
`config.py` is protected from OTA. Configure `UPDATE_GITHUB_REPO`, clear any
`UPDATE_BASE_URL`, set `UPDATE_AUTO_INSTALL = True` if desired and restart;
see the exact [one-time upgrade steps](ota.md#one-time-upgrade-from-2-or-3).
Keeping automatic installation off remains supported.

The standalone channel begins with .5. The older .2/.3 manifests do not
include the required platform metadata, and .4 was not published. The .2 and .3 release archives are
retained for USB/mirror recovery. Reverting to either removes direct GitHub
updating until .5 or later is installed again. More dashboard recovery choices
appear as actual compatible releases are published.

The [validation report](validation.md) records 233 passing host tests and
physical COM8 acceptance: automatic GitHub download/install/reboot into .5,
all 22 firmware files verified, a stable boot trial, preserved settings and
a successful dashboard update check. Garden hardware and multi-day network
commissioning remain separate.

## 2.0.0-rebuild.4 — unpublished candidate

The .4 tag remains for history, but CI blocked publication because Windows
Python 3.14 and Linux Python 3.12 produced different compressed dashboard
bytes using zlib-ng and zlib. No .4 release or update-channel entry was
published, and its tag was not rewritten. The .5 build fixes reproducibility
with pinned Zopfli. Supervised USB/TLS measurements from the installed .4
candidate are retained in the [validation report](validation.md).

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
[application update procedure](ota.md).

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
