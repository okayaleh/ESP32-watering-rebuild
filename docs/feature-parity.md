# Feature checklist against the reference project

The baseline is the supplied [rebuild prompt](https://github.com/supercrossed/ESP32-watering/blob/main/docs/REBUILD-PROMPT.md), [overview](https://github.com/supercrossed/ESP32-watering/blob/main/docs/OVERVIEW.md) and source on `main`, inspected September2026. “Implemented” means code and host validation are present; measured bench behavior and remaining electrical/radio commissioning are recorded in [validation.md](validation.md).

| Existing function | Replacement | Status / relevant detail |
|---|---|---|
| Multiple solenoids, active-high/low | `valve.py`, `controller.py` | Implemented; global sequential queue |
| Manual open/close, timed valve/zone/all runs | `api.py`, `application.py` | Implemented; extra stop-all cancels pending session |
| Multiple daily schedules, zone expansion/dedup | `controller.py` | Implemented;20 simultaneous8-valve schedules retained |
| Moisture thresholds and per-zone duration | `controller.py` | Implemented; unrelated valve lockouts remain independent |
| Wet targets, soak/recheck, max cycles | `controller.py` | Implemented; fresh sample required; missing sensor ends session |
| Hard cutoff, watchdog, startup grace | `runtime.py`, `main.py`, `valve.py` | Implemented; all output closes attempted independently |
| Persistent cooldowns | `controller.py`, `persistence.py` | Implemented; adds write-before-open intent and durable schedule occurrence |
| Config defaults vs runtime authoritative settings | `config.example.py`, `settings_store.py` | Implemented; credentials separate |
| Older settings migrations | `settings_store.py` | Single schedule/valve/address/string mapping and UI-only zone calibration |
| Rename propagation | `application.py` | Zones update schedules/maps; valves update schedules/maps and retain cooldown identity |
| ADS1115 on one bus, up to4 boards | `ads1x15.py`, `moisture.py` | Implemented; global channels0–15 |
| Bus clear, absence probing, failure backoff | `moisture.py` | Implemented; open-drain recovery and incremental conversions |
| Calibration wizard and averaged capture | `moisture.py`, `index.html` | Implemented; pauses watering and validates calibration span |
| AHT20/BMP280 auto-detection | `env_sensors.py` | Implemented; temperature, humidity, pressure and error display |
| LM393 rain display | `env_sensors.py`, dashboard | Implemented; display-only |
| Flow-meter pin/volume settings | Settings and dashboard | Preserved as groundwork; pulse counting absent in original and rebuild |
| WROOM and S3 N16R8 pin map | `settings_store.py`, dashboard | Implemented; conservative reserved-pin exclusions |
| RGB network/watering states | `status_led.py` | Requested steady colors: blue watering, white hotspot, green connected, yellow disconnected; explicit type/profile selection replaces unreliable electrical autodetection |
| First-run captive portal | `wifi_setup.py`, `web.py` | Implemented; enter SSID/password; watering remains supervised |
| Runtime rescue AP and router-return retries | `wifi.py`, `wifi_setup.py` | Implemented; AP/STA radio parking and DHCP bounce ordering |
| Retry/backoff, modem power-save off | `wifi.py` | Implemented; hostname set to `planter` |
| Gateway TCP health and reconnect escalation | `wifi.py` | Implemented; refusal/reset healthy, unknown inconclusive |
| NTP resync and fixed timezone | `wifi.py`, controller | Implemented; nonblocking UDP/DNS, schedules wait for sync |
| Listener restart/error diagnostics | `web.py` | Implemented; idle is not proof of reachability; native stall unproven |
| Streamed API and gzip dashboard | `web.py`, build tool | Implemented; max2 clients,512-byte socket writes, UTF-8-safe EOF framing |
| Sequential/staggered dashboard requests | `index.html` | Implemented; actions and polling share one request queue |
|3h RAM and7-day saved history/charts | `state.py`, dashboard | Implemented; compact RAM, incremental file handling, legacy CSV readable |
| Event log, reset cause, heaps/CPU/RSSI/uptime | State/runtime/dashboard | Implemented; event rotation and restart restoration |
| Zone/valve/schedule/hardware settings UI | Dashboard and application | Implemented; validation before persistence |
| Config import/export, WiFi updates, reboot | Application/API/dashboard | Implemented; no credential export |
| Browser weather | Dashboard | Implemented; optional external fetch, independent of watering |
| Multipart code upload | Web/updater/dashboard | Implemented; staged, protected names, explicit apply |
| Hash-based manifest OTA and changed-file downloads | `updater.py` | Implemented; verified GitHub HTTPS, immutable commit paths, platform checks, readback hashes and optional LAN mirror |
| Automatic checks/install option | Runtime/config | Implemented; daily idle-time installation enabled in fresh configurations, catch-up/retry, manual opt-out and rollback hold |
| Retained compatible release selection | Updater/dashboard/release channel | Added; current and up to two prior channel releases, with older ZIP archives retained separately |
| Boot-count rollback | `boot.py`, updater | Implemented; journal also handles interrupted commit, additions and deletions |
| Nightly maintenance reboot | Runtime/config | Implemented; disabled by default; only idle after1h uptime |
| Precompiled build, scrubbed config, manifest | `tools/build.py` | Implemented; immutable boot excluded from OTA |
| Classic ESP32 native memory headroom | Custom platform, `romboot.py` | Added: bytecode in flash, two recoverable ROM banks, native GC reserve; filesystem OTA preserved |
| Tests without hardware | `tests/`, simulator | Fault injection, actual local HTTP transfers and dashboard DOM audit |

Deliberate safety changes: manual opens respect startup grace; settings changes stop/cancel queued watering; reboot deduplication can skip a run interrupted before activation; invalid saved timestamps retain conservative cooldowns; user GPIO choices exclude strapping outputs; code uploads require apply; both boot helpers require USB replacement. Telemetry flash writes and I2C polling wait while a valve is energized, and firmware checks require idle watering. Setup uses entered SSID rather than an in-operation WiFi scan. Automatic NTP failure alone does not recycle a healthy LAN. None of these changes adds flow counting, rain suppression, signed OTA or dashboard authentication.
