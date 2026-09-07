# ESP32 garden watering controller

A ground-up MicroPython rebuild of [supercrossed/ESP32-watering](https://github.com/supercrossed/ESP32-watering), based on its [rebuild specification](https://github.com/supercrossed/ESP32-watering/blob/main/docs/REBUILD-PROMPT.md), overview, API documentation and source. The ESP32 controls normally closed solenoid valves using soil moisture readings, daily schedules and manual commands. A phone-friendly dashboard runs on the controller; watering has no cloud-service dependency.

**Start here:** [Getting started](docs/getting-started.md) · [All documentation](docs/README.md) · [Download releases](https://github.com/okayaleh/ESP32-watering-rebuild/releases) · [Updates and rollback](docs/ota.md)

## Project status

The current application is **2.0.0-rebuild.2**, published as a **prerelease** and installed on a classic ESP32-D0WD-V3 with 4 MB flash. The custom MicroPython 1.28.0 platform keeps application bytecode in flash and preserves native networking allocation headroom.

Validation includes **190 host tests**, 83 verified HTTP responses under maximum configuration, a settings save under load, GPIO timing, watchdog reset, ROM cache recovery and production startup. The bench board joined a home Google mesh network, synchronized its clock and completed a short LAN check with **34 successful HTTP responses and no failed requests**.

The installed bench board has both automatic watering modes disabled. **Fresh application defaults enable daily and moisture watering. Keep valve supply power disconnected during setup, then disable or review both modes in Settings before commissioning.** Router outage recovery, DHCP renewal, actual sensors and valves, and a 48–72-hour network soak remain to be validated. Read the [validation report](docs/validation.md) for measured results and limits, and complete the [commissioning checklist](docs/commissioning.md) before unattended watering.

## Features

| Area | Included behavior |
| --- | --- |
| Watering | Up to 8 valves, 16 zones and 20 daily schedules; one valve runs at a time, including manual, zone and all-valve batches |
| Moisture control | Per-zone calibration, dry threshold, wet target, duration and valve mappings; bounded soak/recheck cycles and per-valve cooldowns |
| Output supervision | Closure at boot, monotonic deadlines, hard maximum run duration, startup grace, hardware watchdog and persistent watering intent |
| Schedule persistence | Occurrences recorded before activation to prevent duplicate starts after resets or backward clock changes |
| Sensors | Up to 4 ADS1115 boards on one I2C bus; optional AHT20, BMP280 and digital rain indication; bounded reads and bus recovery |
| Dashboard | Live status, manual controls, charts, schedules, settings, calibration, GPIO map, environment/weather, Wi-Fi, event log and diagnostics |
| Networking | Bounded nonblocking HTTP, DNS, NTP and update transfers; Wi-Fi retry/backoff, setup/rescue hotspot and gateway health checks |
| Configuration | Validated settings, rename propagation, import/export, separate credentials and migration support for the original project |
| Updates | Hashed manifests, staged uploads, transactional installation, failed-boot recovery and selectable archived releases |
| Development | Precompiled application build, local simulator, fault tests, GitHub validation and tag-driven releases |

Flow-meter pin and volume fields are preserved as configuration groundwork, but watering is **timed**: flow pulse counting is not implemented. Rain is **display-only** and does not suppress watering. These functions were also groundwork in the original. The [feature checklist](docs/feature-parity.md) maps the original functions to the rebuild and documents deliberate behavior changes.

## Documentation

The repository's **[Documents section](docs/README.md)** is the entry point for user guides and technical references.

| Document | What it covers |
| --- | --- |
| [Getting started](docs/getting-started.md) | Download, first USB installation, home Wi-Fi setup and initial configuration |
| [Hardware and wiring](docs/hardware.md) | Valve drivers, flyback protection, pin assignments, ADC addressing and board profiles |
| [Troubleshooting](docs/troubleshooting.md) | Hotspot passwords, Google mesh, connection diagnostics, startup and recovery |
| [Commissioning](docs/commissioning.md) | Physical checks and network soak required before unattended watering |
| [Updates and rollback](docs/ota.md) | Publish releases, retain earlier updates, run the laptop mirror and recover |
| [HTTP API](docs/api.md) | Routes, request formats, limits, errors and control behavior |
| [Architecture](docs/architecture.md) | Control loop, persistence, networking, memory and failure handling |
| [Feature checklist](docs/feature-parity.md) | Comparison with the original project and remaining groundwork |
| [Validation report](docs/validation.md) | Host and board measurements, tested conditions and outstanding checks |
| [Platform firmware](firmware/README.md) | Native images, flash addresses, partition layout and ROM cache |
| [Rebuild the platform](tools/firmware/README.md) | Pinned upstream sources, toolchain and Windows reproduction instructions |

## Hardware requirements

The supplied platform image targets a **classic 4 MB ESP32**, such as an ESP32-WROOM-32 development board. It is **not an ESP32-S3 image**. The application includes a separate ESP32-S3-WROOM-1 N16R8 pin profile; that board requires its own compatible firmware and pin configuration, and has not received the same physical validation.

For the intended garden installation, use normally closed 12 V solenoid valves, a suitable 3.3 V-controlled driver per valve, flyback diodes, an adequately rated valve supply and regulated ESP32 power. Capacitive probes connect through ADS1115 inputs. Driver wiring must keep valves closed while the ESP32 resets or loses power; the [hardware guide](docs/hardware.md) explains active-high and active-low requirements.

| Classic ESP32 default | Connection |
| --- | --- |
| First valve | GPIO26, active-high |
| I2C SDA / SCL | GPIO21 / GPIO22 |
| First ADS1115 | Address `0x48` |
| First moisture probe | ADS1115 A0, global channel 0 |
| Plain status LED | GPIO2 |

No valve supply or sensors are required to explore the dashboard on a bench board. Sensor errors are expected when those devices are absent.

## Quick start

1. Download and extract `planter-<version>.zip` from [Releases](https://github.com/okayaleh/ESP32-watering-rebuild/releases). Use the packaged ZIP asset for prebuilt application files and the custom platform image.
2. For a new installation, back up the existing device and follow the [USB installation guide](docs/getting-started.md#install-on-a-classic-4-mb-esp32). The original project's OTA updater cannot migrate to this rebuild.
3. With valve power disconnected, start the ESP32. Join its open **`Planter-Setup-xxxx`** Wi-Fi network and visit **http://192.168.4.1/setup** if the setup page does not open automatically.
4. Enter your **home Wi-Fi name and home Wi-Fi password**. The password field does not create a password for the Planter hotspot. On Google mesh, use your normal shared network name; you do not need to choose or separate the 2.4 GHz and 5 GHz names. See [Wi-Fi troubleshooting](docs/troubleshooting.md).
5. Rejoin home Wi-Fi and open the controller's assigned IP address, shown on serial or in the router's device list. Review watering modes, hardware, calibration, timezone and schedules before enabling physical watering.

The hostname is `planter`; automatic `planter.local` resolution depends on firmware and the LAN. Use the numeric IP when hostname resolution is unavailable. The device has a 60-second startup grace period by default, which also blocks manual opens. Local schedule time uses an editable fixed UTC offset; daylight saving changes are manual, and schedules wait for successful time synchronization after boot.

## Normal operation and configuration

Use the dashboard to map each soil zone to its ADC channel and valves, calibrate dry/wet readings, then set its dry threshold, wet target and run duration. Configure daily schedules separately. Manual zone or all-valve actions run mapped valves sequentially; **Stop all** cancels pending runs and moisture recheck cycles. Saving settings can stop and cancel queued watering, and hardware/pin changes require a reboot.

| Location | Purpose | Updated by application OTA? |
| --- | --- | --- |
| `settings.json` on the board | Authoritative saved zones, valves, schedules and runtime settings | No |
| `wifi.json` on the board | Saved home Wi-Fi credentials; takes precedence over config defaults | No |
| `watering_state.json` on the board | Watering intent, schedule occurrences and cooldowns | No |
| `config.py` on the board | First-boot defaults and local options such as board type, watchdog, hard cutoff and update mirror | No |
| `boot.py` and `romboot.py` | Recovery and ROM cache boot helpers | No; USB maintenance only |
| Manifest-listed application files | Compiled modules, entrypoint, dashboard and version | Yes |

After first boot, editing a default zone in `config.py` does not replace saved runtime settings. Export settings from the dashboard before substantial changes; that export excludes Wi-Fi credentials. Keep private device backups outside the public repository. The dashboard and API are unauthenticated and intended for a trusted local network; do not expose them through router port forwarding.

## Updates and the previous two versions

[GitHub Releases](https://github.com/okayaleh/ESP32-watering-rebuild/releases) retain the **current update and at least two previous updates** as releases accumulate. Older releases and local download caches are kept too; there is no automatic pruning. Published release tags and assets are protected by GitHub release immutability. The first published application is `v2.0.0-rebuild.2`; earlier recovery choices appear as actual subsequent versions are released.

List the newest three available choices from an extracted package or repository checkout:

```powershell
python tools/sync_updates.py --repo okayaleh/ESP32-watering-rebuild --list
```

Download a specific release, verify its archive and application hashes, and serve it from the laptop:

```powershell
$releaseRoot = python tools/sync_updates.py --repo okayaleh/ESP32-watering-rebuild --tag v2.0.0-rebuild.2
if ($LASTEXITCODE -ne 0) { throw "Release verification failed" }
python tools/mirror.py --directory "$releaseRoot" --port 8000
```

Set `UPDATE_BASE_URL` in the board's local `config.py` to the laptop's trusted LAN HTTP address, for example `http://192.168.1.50:8000`. For an installed controller, save that file over USB with valve power disconnected and reboot to load the new address. Then use **Check for update** in the dashboard, review the displayed version and apply while watering is idle. The base URL is blank by default, so the mirror must be configured before this works. Keep the laptop and mirror running through installation; direct GitHub HTTPS URLs are not supported by the board updater.

To revert, select a retained older tag that worked on your controller and use the same process. Check that its application is compatible with the saved settings and native firmware. Repository archives provide multiple selectable versions; the board's transaction recovery keeps one previous set of changed files, not two complete permanent installations. A successful boot trial measures safety-loop startup, not long-term field reliability. Follow the [complete update and recovery guide](docs/ota.md) for compatibility limits and interrupted-update behavior.

## Build and validate

Use Python 3.10+ and Node.js 22+. From a new checkout:

```text
git clone https://github.com/okayaleh/ESP32-watering-rebuild.git
cd ESP32-watering-rebuild
python -m pip install -r requirements-dev.txt
python tools/build.py --version 2.0.0-rebuild.2
python tools/check.py
```

The build pins `mpy-cross` to MicroPython 1.28.0 and emits portable `.mpy` v6.3 files. If `src/config.py` does not exist, the blank-credential `src/config.example.py` supplies defaults. To customize local board options, copy the example to `src/config.py` before building. The build copies local configuration to the ignored `build/config.py`; it scrubs the public example and rejects credentials that remain elsewhere in that example.

`tools/check.py` compiles Python source, checks dashboard JavaScript syntax and runs the host test suite. Physical testing remains a separate commissioning step. `python tools/package.py` verifies application and platform hashes and writes a release ZIP plus SHA-256 file under `release/`, including a scrubbed default config.

On this project's Windows development laptop, Thonny Python can be selected explicitly:

```powershell
$python = "$env:LOCALAPPDATA/Programs/Thonny/python.exe"
& $python tools/build.py
& $python tools/check.py
```

The supplied `build_mpy.ps1` and `serve_updates.ps1` also detect that Python installation. Building application files does not rebuild the native MicroPython runtime; follow the [platform build recipe](tools/firmware/README.md) when that is required.

## Preview without an ESP32

```text
python tools/simulate.py
```

Open **http://127.0.0.1:8080**. The simulator uses the real controller, API and HTTP transport with fake GPIO and moisture readings, and displays a simulation banner. Its private data stays in `.tools/simulation/`. It cannot validate the ESP32 radio, GPIO, watchdog or I2C hardware.

With the simulator still running, the optional dashboard integration audit is `node tests/test_dashboard.cjs`. Stop the simulator with Ctrl+C.

## Repository layout

| Path | Contents |
| --- | --- |
| [`src/`](src/) | MicroPython application, local-config example and dashboard source |
| [`build/`](build/) | Generated application files, boot helpers and hashed OTA manifest |
| [`docs/`](docs/README.md) | User guides, design references and validation records |
| [`firmware/`](firmware/README.md) | Classic ESP32 platform images, provenance, partition table and third-party licenses |
| [`tools/`](tools/) | Build, package, release download, mirror, simulator and runtime reproduction tools |
| [`tests/`](tests/) | Host fault tests and dashboard integration audit |
| [`.github/workflows/`](.github/workflows/) | Push/PR validation and version-tag release publication |

Local tools, credentials, device backups, logs and test output are ignored by Git. The original project is kept in the development workspace under the ignored `.reference/` directory for comparison; it is not included in release packages.

## Contributing and publishing

Report reproducible problems in [Issues](https://github.com/okayaleh/ESP32-watering-rebuild/issues), including the application version, exact board, relevant event messages and steps to reproduce. Remove passwords and private network details from shared logs.

For changes, run the build and host checks, update the relevant documents and record any physical validation. GitHub also validates pushes and pull requests. A maintainer publishes an update by pushing a new `v<version>` tag; the release workflow builds, checks and packages that tag, then publishes immutable assets. Versions containing a hyphen are prereleases. Never reuse a published tag; see the [release procedure](docs/ota.md#publish-and-select-a-github-release).

## License and attribution

MIT licensed; see [LICENSE](LICENSE). The original project and specification are by [supercrossed](https://github.com/supercrossed/ESP32-watering). The custom runtime includes upstream MicroPython, ESP-IDF and other components with their own retained [license notices](firmware/licenses/README.md).
