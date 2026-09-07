# Validation report

## 2.0.0-rebuild.3 — dark mode and RGB status

This application release passed **197 host tests**, the **25-check dashboard
DOM audit**, and real headless Chrome checks against the desktop simulator.
The build uses the same custom MicroPython 1.28.0 platform image as .2.
The ESP32 was not available on COM8 during this update; no .3 physical LED,
USB deployment or radio acceptance is claimed. Earlier board observations
below apply to .2 and are retained as their original measurements.

| Check | Result and scope |
| --- | --- |
| Compilation and host suite | Python/device compilation, dashboard JavaScript syntax and all 197 tests passed. Local log: `test-results/host-tests-dark-rgb.txt`. |
| RGB state selection | Seven tests cover requested colors, all overlapping status flags, steady output, bounded writes through tick wrap, retry after an LED write failure, disabled output and legacy plain/auto configurations. |
| Supervisor integration | The actual runtime with fake pins/NeoPixel passes yellow → white → green → blue → white → green transitions, including watering through a network/listener fault and actual portal-state priority. Existing cutoff and telemetry checks still pass. |
| Dashboard behavior | All 25 DOM audit checks pass, including saved/system/blocked-storage preferences, accessible toggle state, immediate chart/legend recoloring and no HTTP requests caused by theme changes. Existing control and stop-action queue checks pass with at most one active fetch. |
| Real Chrome | Six captured views at 1280×900 and 390×844 cover dark/light overview and expanded dark settings. No page JavaScript errors or horizontal overflow; sampled body/card/control/button text contrast is at least 4.5:1. Keyboard toggle, reload persistence, system changes and blocked storage pass. |
| Visual inspection | Desktop dark/light, mobile dark, and dark configuration screenshots were inspected for readable controls, theme coverage and layout. Evidence: `test-results/dashboard-*.png` and `dashboard-browser-dark-rgb.json`. |
| Release artifacts | 21 hashed OTA application files, with a 60,164-byte dashboard and 19,038-byte gzip on the local Python build. Config and both boot helpers remain excluded from OTA. |

The `.3` tests exercise the LED driver through test doubles, not a physical
pixel. The specified four-color behavior requires an addressable RGB LED and
the [local RGB configuration](hardware.md#rgb-status-led). Existing `config.py`
is preserved by OTA. Desktop theme preferences live in each browser and do not
alter controller settings or watering decisions.

## 2.0.0-rebuild.2 — prior bench baseline

The rebuild passed **190 host tests**, the dashboard DOM audit, and supervised bench acceptance on the user's ESP32 connected through COM8. The board is a classic ESP32-D0WD-V3 revision 3.1 with 4 MB flash, running the supplied custom MicroPython 1.28.0 ROMFS image and ESP-IDF 5.5.1. The completed board stress run verified **83 responses totaling 706,508 bytes**, followed by installation verification and normal-boot observation. After an initially unsuccessful provisioning attempt and a tested retry correction, the board subsequently connected to the home LAN, synchronized time using NTP, and served HTTP successfully to the laptop. The cause of the earlier network invisibility is not established. No sensors, valve drivers, valves, or water supply were attached. Commissioning with the actual watering hardware and longer network testing remain outstanding.

## Build and host checks

Host validation used the Windows workspace, Thonny's CPython 3.14, Node 22.18.0, and `mpy-cross` 1.28.0. Device modules use portable MicroPython bytecode v6.3 compiled with `-O3`.

| Check | Recorded result |
| --- | --- |
| Source/tool/test compilation and dashboard `node --check` | Passed through `tools/check.py` |
| Host unit/integration suite | **190 tests passed**, including fifteen release discovery/sync/mirror checks and a compatible-version downgrade regression |
| Device module compilation | Passed; hashed application manifest identifies exact OTA sizes and SHA-256 values |
| Actual supervisor with simulated clock and peripherals | Grace, deadlines, closure through an injected web failure, journal ordering, and idle-only maintenance passed |
| Controller persistence and scheduling | Cooldowns, interrupted intent writes, schedule deduplication, rename recovery, malformed state, sequential/active-low controls, and soak/recheck cases passed |
| Settings-save memory and ownership | Internal candidates avoid a redundant full copy; public inputs remain isolated from later mutation. Failure tests preserve durable and live settings. Passed on the host; final memory-loaded board save is covered by the stress acceptance below. |
| TCP transport and API | Real local HTTP transfers, two clients, Unicode streaming, partial writes, slow/malformed peers, body limits, multipart boundaries, and low-heap stop prioritization passed |
| Bounded JSON work | History-record size checks and legacy fallback, a 32-entry request-local name cache, Unicode/escape bounds, one send/eight fragments per client step, and exact configuration-response byte preservation passed |
| Network state machines | DNS/NTP validation, reconnect backoff, gateway completion without unavailable ESP32 socket methods, and rescue behavior passed with driver/socket doubles |
| Application OTA | Local HTTP download/install/rollback, protected paths, invalid hashes/bytecode, interrupted commit and rollback, and boot trials passed |
| ROMFS boot helper | Exact upstream image format, two-bank selection, no-write unchanged boots, corruption, interrupted writes/marker updates, rollback, and capability checks passed |
| Sensor drivers | ADS channel mapping, absent devices, open-drain bus recovery model, AHT CRC, BMP reference values, and calibration failure handling passed with simulated I2C |

`tools/check.py` reproduces the compilation, JavaScript syntax, and host tests. The final complete run is `test-results/host-tests-release-retention.txt`: all 190 tests passed. Fifteen release checks cover discovery, verification, safe extraction, immutable cache handling, and restricted HTTP mirroring. A downgrade regression installs older compatible application files while preserving settings, Wi-Fi credentials, and watering intent. The application artifact manifest was unchanged by the publication-tooling work. The earlier 171-test log includes a pause of host execution and is not a performance measurement. After clarifying the setup and dashboard Wi-Fi fields, the separate `tests/test_dashboard.cjs` audit against `tools/simulate.py` passed again, with evidence in `test-results/dashboard-final.txt`. It covered dashboard startup, charts, failed-request recovery, settings edits, rename propagation, export, safe text rendering, a stop action preempting a stalled history GET, and the cancelled read releasing the queue. At most one fetch was active. This audit is separate from the 190-test count. No browser surface was available for visual inspection; the DOM audit establishes behavior rather than visual layout.

The final dashboard is **56,421 bytes**, or **17,748 bytes gzip**. Live history retains an 18-second request timeout; saved-history requests use 90 seconds. A mutation preempts an active GET and waits for its cleanup through the existing single request queue. This preemption does not cancel an in-flight POST; normal request deadlines still apply.

The custom native firmware was built twice using the published Windows scripts; the second build produced identical bytes. Esptool validated its checksum and image hash, the official ESP-IDF parser validated the partition table, and the ELF contains `VfsRom`, `rom_ioctl`, and the configured GC split allowance. The combined image's SHA-256 is `77c731f506c3bf13404373c094dad681981d226567021f69e87a5d05182a2722`. Exact runtime artifacts, revisions, and other hashes are in [firmware/manifest.json](../firmware/manifest.json); reproduction instructions and patches are in [tools/firmware](../tools/firmware/README.md).

## Physical bench observations

The following logs were captured from the actual board. Automatic watering was disabled in the bench settings. Automated HTTP stress clients used the ESP32's own `127.0.0.1` interface while its setup AP was active; those transfers did not traverse a phone, laptop Wi-Fi link, or home router. The later user provisioning attempt is recorded separately below.

| Check | Recorded result and scope | Local evidence |
| --- | --- | --- |
| ROMFS imports and native HTTP | Application modules loaded from `/rom`; ten JSON routes, the streamed dashboard, and 40 repeated configuration exports completed. At the recorded counter snapshot: **51 completed, zero failed, zero listener restarts**. A subsequent settings-save request also passed. | `device-smoke-rom.txt` |
| Setup AP startup | Driver reported an active `Planter-Setup-xxxx` hotspot, IP `192.168.4.1`, and bound captive DNS. External association, DHCP leases, and captive-portal interaction were not tested in this run. | `device-smoke-rom.txt` |
| GPIO26 timed activation | Three requested one-second runs measured **1000, 1000, and 999 ms** between instrumented GPIO writes. Startup grace rejected a run, manual stop drove GPIO26 low, and a reconstructed controller retained an unsynchronized cooldown. | `device-gpio-rom.txt` |
| Production watchdog timeout | With timeout set to **120 seconds**, the test fed the watchdog once, raised GPIO26, then stopped feeding. The board reset; MicroPython reported watchdog reset cause `3`, and GPIO26 read low after boot. This observes the pin after reboot, not the electrical waveform throughout reset. | `device-watchdog-rom-120.txt` |
| Interrupted ROMFS construction | A software reset was injected after the fifth write to the inactive bank. On the next boot, the new module imported, the previous bank still matched its saved hash, the new bank was selected, and GPIO26 was low. This was not an electrical power cut. | `device-rom-fault.txt` |
| Actual supervisor run | A supervised **65-second** run completed startup grace, kept GPIO26 low, and retained its listener and rescue state without a safety fault. Captured idle samples showed IDF free memory about 152.8 KB and largest block 110,592 bytes. The run also logged the Wi-Fi error described below. | `device-runtime-rom.txt` |
| Full live-history transfer | Final **180-point × 16-zone** history was 118,081 bytes and completed in **9.093 s** without per-chunk serial logging. Its byte count and SHA-256 matched the independently decoded logged transfer. Earlier quiet measurements were 51.292 s with the original encoder and 11.413 s with the first native encoder. | `device-stress-release.txt`, `device-stress-validation.txt`; earlier `device-history-before.txt`, `device-history-after.txt` |
| Native gateway probe | Both a listening local TCP endpoint and a refused local connection were classified `healthy` after the driver compatibility fix. This exercises real ESP32 sockets through loopback, not the user's router or LAN. | `device-history-after.txt` |
| GET work-slice duration | Final maximum `HTTPServer.poll()` duration during the GET phase before the settings POST was **60 ms**, compared with an earlier 542 ms. Each client step is limited to one send and eight encoder fragments. The whole run's aggregate maximum was **2613 ms**, including the idle settings save. These are observed workload maxima, not guaranteed deadlines. | `device-stress-release.txt`; earlier `device-stress-final.txt` |
| Settings save after sustained GET load | The final settings POST succeeded after 40 maximum-configuration GET responses. Subsequent GET responses contained the saved change. This passed after the copy-reduction and ownership fix for the earlier `MemoryError` inside `atomic_json`. | `device-stress-release.txt`, `device-stress-validation.txt`; earlier `device-stress-save-trace.txt` |
| Extended board stress test | **Passed:** 40 pairs of concurrent configuration GETs, one settings POST, and two history transfers: **83 verified responses / 706,508 bytes**. The fixture had 8 valves, 16 Unicode zones, 20 schedules, 180 history points, and 64 RAM events. Both local gateway outcomes passed, no peer-close errors were counted, and cleanup completed. | `device-stress-release.txt`, `device-stress-validation.txt` |
| Stress memory minima | Reported GC free minimum **15,088 bytes**, IDF free minimum **62,992 bytes**, and largest native block minimum **32,768 bytes**. These minima apply to the completed fixture and duration. | `device-stress-release.txt` |
| Final file verification and normal boot | All **23 build files** checked matched the device. Automatic watering was disabled and GPIO26 read low at installation verification. The production entrypoint was observed for **75 seconds**, with the setup AP and HTTP listener starting and no Wi-Fi error or unexpected reset. Unattached-I2C errors remained expected. | `device-final-install.txt`, `device-final-boot.txt` |
| Earlier dashboard request-queue update | Updated HTML and gzip assets were installed; all 23 checked build files matched again. A further **12-second** startup observation showed the AP and HTTP listener starting. The Python firmware was unchanged. | `dashboard-device-verify.txt`, `dashboard-final-boot.txt` |
| Initial user Wi-Fi provisioning | The user saved Wi-Fi details through the setup hotspot, triggering a software reset, but production station connections repeatedly failed. A private USB diagnostic confirmed a nonempty saved SSID and a password present without disclosing either value. That scan found no matching SSID; native status changed from `1001` (`CONNECTING`) to **`201` (`NO_AP_FOUND`) at 2601 ms**. Pre-connect native heap remained available. This attempt did not establish LAN association. | `wifi-diagnostic.txt` |
| Production restoration after the first Wi-Fi diagnostic | The normal entrypoint was restored; the HTTP listener and rescue hotspot started while the requested network was still absent from that diagnostic scan. | `wifi-diagnostic-production-boot.txt` |
| Final Wi-Fi retry acceptance | **Passed on the board:** initial attempt 20.020 s, retry 12.013 s with old native status 201 retained, correct failure reason, and GPIO26 low. All 23 build files matched after installing the correction. | `wifi-retry-acceptance.txt`, `wifi-final-install.txt` |
| Final production startup after retry correction | Observed for **95 seconds** after the commanded software reset. HTTP listener and rescue AP started; the absent saved network produced a handled connection failure. No traceback or unexpected reset appeared. Missing-I2C errors remained expected on this bare board. | `wifi-final-boot.txt` |
| Subsequent native home-network association | Three later scans each found **four exact matches** for the saved SSID on channels **1, 6, and 11**, with authentication mode `3` and RSSI from **−79 to −91 dBm**. No case or leading/trailing-space mismatch was found. The standalone native connection reached status `1010` (`GOT_IP`) in **1401 ms** and received a home-LAN IP address. SSID and password values are omitted. | `wifi-scan-diagnostic.txt` |
| Restored production home-LAN operation | After restoring the normal entrypoint and restarting, production firmware connected to the home LAN and logged successful NTP synchronization. A laptop on that LAN received HTTP 200 from the board's status API; status showed the rescue AP inactive, no Wi-Fi error, and the valve closed. | `wifi-scan-production-boot.txt`, `home-wifi-check.json` |
| Short single-client home-LAN check | **Passed:** the laptop verified the dashboard against the build and exercised eight initial JSON routes, followed by approximately **120 seconds** of status/history polling. **34 HTTP 200 responses, zero errors**; median latency **1878 ms**, maximum **8982 ms**. Sampled uptime advanced from 159 to 304 seconds. All samples remained connected and time-synchronized, with the AP inactive, no safety fault, and all reported valves closed. | `home-wifi-check.json` |
| Final setup-label deployment | Both Wi-Fi forms now identify the home-network password and explain that the setup hotspot is open; SSID autocapitalization and autocorrection are disabled. All 23 build files matched after installation. The board rejoined the home LAN after reboot, synchronized NTP, and disabled the hotspot. LAN reads verified the served dashboard bytes, setup labels, current connection, closed outputs, and disabled automation. A runtime counter recorded one peer connection reset (`ECONNRESET`); the listener remained running with zero restarts. | `wifi-label-install.txt`, `wifi-label-production-boot.txt`, `wifi-label-lan-verification.json` |
| Home-LAN signal and heap observations | During that check, RSSI ranged from **−81 to −75 dBm**; IDF free memory minimum was **82,612 bytes**, and largest native block minimum was **53,248 bytes**. Final gateway health was `healthy`. These observations cover one client and this short interval. | `home-wifi-check.json` |

The 83-response stress result predates the final Wi-Fi-only retry change; the HTTP transport and application workload were unchanged. The later investigation confirmed that the native driver retained status 201 when a fresh connection began. With the correction installed, the first failed attempt lasted 20.020 seconds and the rescue-AP retry lasted 12.013 seconds despite retaining that error throughout. GPIO26 remained low. The probe skipped only the 60-second backoff using the supplied test clock; each connection attempt used its full real-time deadline. The updated code passed all 174 host tests, and all 23 installed build files matched again.

The subsequent successful scans, native association, and restored production LAN/NTP operation required no further application or native firmware changes. They show that the saved network was visible and usable during that later observation. They do not prove why it was absent from the earlier scan, or that restarting the radio permanently fixed the cause. The HTTP stress measurements remain loopback results; the later laptop requests separately establish actual home-LAN reachability.

GPIO timings use the board's monotonic clock around calls to real `machine.Pin`; no oscilloscope, logic analyzer, or flow measurement was used. The earlier smoke test's 1552 ms close-report time included work after driving the output low. The later GPIO probe above measures the transition interval directly in software.

The earlier 65-second supervisor log contains `wifi_error: Wifi Unknown Error 0xffffffff` near its end. The blank-SSID retry path was corrected, and that error did not recur during the final 75-second normal-boot observation. Missing I2C hardware continued to produce handled bus-recovery errors; no sensor reading or physical bus fault-recovery capability was established.

The final quiet history transfer took 9.093 seconds; forwarding the same data as per-chunk serial text took **22.801 seconds**. Serial overhead makes the latter unsuitable as an HTTP throughput measurement. The host independently decoded six representative JSON bodies and verified the remaining responses by size and SHA-256 against those samples, including the quiet history transfer. The measured 180-point fixture fits the 18-second live-history timeout. The full seven-day history case and saved-history timeout have not been physically validated on the user's network.

Settings persistence runs while watering is idle. Its flash-write/readback time is distinct from GET work-slice timing, and the final aggregate poll maximum of 2613 ms includes that operation. Passing an already-owned settings candidate into persistence avoids another full clone while preserving caller-input isolation. The final loaded save and subsequent reads passed; this does not make idle flash operations suitable for an active valve's deadline-critical path.

Stock ESP32_GENERIC 1.28 encountered a native allocation failure during startup and exhausted usable networking heap when the complete application was loaded. The delivered custom runtime enables ROMFS and limits GC heap expansion; the recorded custom-image smoke test completed without that allocation failure. This does not establish a permanent memory floor: Wi-Fi and other native allocations can still consume or fragment memory. On this port, `gc.mem_free()` includes potential heap expansion and must be interpreted alongside explicit IDF free/largest-block values.

## Remaining commissioning

Real valve polarity, driver bias during reset/power loss, flow closure, sensor accuracy, physical I2C deadlines, brownout recovery, and electrical power-cut durability remain untested. Initial home-LAN association, IP assignment, NTP synchronization, and laptop-to-board HTTP have now been observed. DHCP renewal, router/ISP outage recovery, DNS/NTP failure recovery, and end-to-end OTA on that network remain untested. Neither the short home-LAN observation nor loopback stress proves the original intermittent MicroPython/lwIP listener stall is eliminated. A 48–72-hour network soak remains outstanding.

Follow [commissioning.md](commissioning.md) with the actual board, supply, sensors, valves, and router. A read-only HTTP soak can be recorded with:

```text
python tools/stress_web.py http://DEVICE-IP --seconds 172800 --interval 5 --output soak.csv
```

Raw bench logs are under local `test-results/`, which is ignored by Git. Source, automated tests, and the firmware reproduction files are included in the release. The dashboard and OTA mirror remain unauthenticated; SHA-256 verifies transfer integrity against a manifest, not publisher identity. Flow pulse counting and rain-based watering suppression remain explicit groundwork inherited from the original project.
