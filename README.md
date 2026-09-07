# Garden watering controller — rebuild

A new MicroPython implementation of [supercrossed/ESP32-watering](https://github.com/supercrossed/ESP32-watering), built from its [rebuild specification](https://github.com/supercrossed/ESP32-watering/blob/main/docs/REBUILD-PROMPT.md), overview, API documentation and source. It controls normally closed solenoid valves from moisture readings and daily schedules, with an embedded dashboard and no cloud dependency for watering.

**Status:** installed and bench validated on a classic ESP32-D0WD-V3 with 4 MB flash. Validation includes 190 host tests, 83 verified HTTP responses under maximum configuration, a settings save under load, GPIO timing, watchdog recovery, and production startup. The supplied custom platform image keeps application code in flash and reserves native networking memory. The connected board has automatic watering disabled. It has joined the user's home Wi-Fi, synchronized its clock, and completed a short LAN check with 34 successful HTTP responses and no failed requests. Router outage recovery, sensors, valves, and a 48–72-hour network soak remain to be commissioned; see the [validation report](docs/validation.md) and [commissioning checklist](docs/commissioning.md) before unattended watering.

## What is included

- Up to 8 valves, 16 zones, 4 ADS1115 boards and 20 daily schedules. One valve runs at a time, including manual and scheduled batches.
- Per-zone dry thresholds, wet targets, durations, calibration and mappings to multiple valves; bounded soak/recheck sessions and per-valve cooldowns.
- Valve closure at boot, monotonic run deadlines, startup grace, hardware watchdog, and durable watering intent/cooldowns. Scheduled occurrences are recorded before their valves can open, preventing duplicate runs after resets or backward clock changes.
- Nonblocking HTTP, DNS, NTP and OTA transfers. Two browser clients maximum, 512-byte socket writes, partial-write handling, bounded requests and absolute deadlines.
- WiFi retry/backoff, captive setup and rescue hotspot, gateway health probes, and observable listener faults. Networking faults are isolated from watering decisions.
- A self-contained phone-friendly dashboard: valve/zone controls, history charts, schedules, settings, GPIO map, sensors, calibration, WiFi, environment/weather, diagnostics, event log, import/export, uploads and updates.
- Transactional OTA staging, SHA-256 verification, boot trials and rollback. An immutable boot guard preserves recovery across interrupted installations.
- Versioned release archives retain the current update and at least two previous updates as releases accumulate. The update helper lists the newest three available releases; any retained compatible tag can be selected for manual rollback. Release history is not automatically pruned.

The [feature checklist](docs/feature-parity.md) records implementation locations, deliberate changes and inherited groundwork. Flow-volume settings and the rain display are present; flow pulse counting and rain-based watering suppression were not implemented in the original and remain groundwork here.

Versioned downloads are published under [GitHub Releases](https://github.com/okayaleh/ESP32-watering-rebuild/releases). See [update and rollback instructions](docs/ota.md) to list the newest release and two previous choices, then select a tested compatible version.

## Build

Use Python 3.10+ and Node.js 22+. On this Windows workspace, the installed Thonny Python and local compiler/Node are already available:

```powershell
$python = "$env:LOCALAPPDATA/Programs/Thonny/python.exe"
& $python tools/build.py
& $python tools/check.py
```

On another computer:

```text
python -m pip install -r requirements-dev.txt
python tools/build.py
python tools/check.py
```

The compiler is pinned to MicroPython 1.28.0, emitting portable `.mpy` v6.3 with optimization enabled. `build/` contains the application filesystem bundle: `main.py`, immutable `boot.py` and `romboot.py`, local `config.py`, precompiled modules, dashboard files, and a hashed manifest. The separate [platform firmware](firmware/README.md) contains the flashable Espressif `.bin`, source patches and reproduction instructions. `config.py` is excluded from Git and OTA; the example has blank WiFi credentials. The build refuses failed credential scrubbing.

## Configure and install

1. Export settings and back up files from the original controller. Initial migration to this rebuild is through USB, not the original OTA mechanism.
2. Follow [hardware and board configuration](docs/hardware.md). If customizing defaults, copy `src/config.example.py` to `src/config.py` and edit it before building. WROOM-32 defaults must be changed for an S3.
3. For the classic 4 MB ESP32, use the supplied [Planter platform firmware](firmware/README.md). Stock ESP32_GENERIC 1.28 exhausted native networking memory with this complete application. The custom image is **not for ESP32-S3**; S3 boards need their correct PSRAM firmware and pin configuration. Disconnect valve supply power during installation.
4. On a fresh filesystem, copy the contents of `build/` into `/` using Thonny, including both USB boot helpers. Copy `main.py` last. `manifest.json` is not required on the device. On the custom platform, boot verifies the files and builds a ROM cache; the filesystem copies remain authoritative for updates. Remove older `.py` siblings when installing `.mpy` files.
5. Reboot. With no credentials, join `Planter-Setup-xxxx` and open `http://192.168.4.1/setup`; enter the SSID and password. Once connected, use the IP printed on serial/the router's lease list. The hostname is `planter`; `.local` resolution depends on firmware and the LAN.
6. Configure zones and pins, or import the exported settings. Existing `wifi.json` takes precedence; settings and watering-state migrations also support the old project. Fixed timezone offset is editable; DST changes are manual.
7. Complete commissioning with water disconnected first, then verify actual flow and closure under supervision.

Settings become authoritative in `settings.json` after first boot. Editing a first-boot zone in `config.py` does not overwrite runtime settings. OTA connection options, board type, watchdog and hard cutoff remain local config parameters.

## Preview without hardware

```powershell
& $python tools/simulate.py
```

Open `http://127.0.0.1:8080`. The simulator uses the actual controller, API and HTTP transport with fake pins and moisture readings; a banner identifies it. Its data stays in `.tools/simulation/`. It cannot validate radio, GPIO, watchdog or I2C behavior.

With the simulator running, the dashboard integration audit is:

```powershell
& ./.tools/node.exe tests/test_dashboard.cjs
```

Use `node tests/test_dashboard.cjs` on another computer. Stop the simulator with Ctrl+C. [OTA setup](docs/ota.md), [API](docs/api.md), [architecture/reliability](docs/architecture.md), and [validation report](docs/validation.md) cover the remaining details.

MIT license. The original project is retained locally under `.reference/` for comparison and is excluded from the deliverable repository.
