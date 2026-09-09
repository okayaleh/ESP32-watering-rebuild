# Validation report

## 2.0.0-rebuild.6 — compact zone dashboard

Validation on **2026-09-09** covers the compact zone layout,
speedometer moisture gauges, grouped valve and sensor controls, tighter
spacing, sunset-to-sky background and sand-colored panels. The controller
backend, native MicroPython image and boot helpers are unchanged.

| Check | Result and scope |
| --- | --- |
| Host suite | All 233 host tests passed in 18.992 seconds. |
| Dashboard DOM audit | All 50 behavior checks passed, including existing update, theme and request-queue behavior. Regression checks confirm untouched watering-duration inputs follow refreshed defaults while a manual duration override is preserved. |
| Real Chrome | Light and dark views at 1280-, 390- and 320-pixel widths showed no horizontal overflow or page JavaScript errors. Fixtures covered long names, shared and multiple mapped valves, sensor-only zones, raw ADC zero, missing readings and 100% moisture. Navigation expansion and history-chart redraw also passed. These are browser fixtures, not physical sensor measurements. |
| Default-view height | With the same two-zone fixture, document height fell from 4,626 to 968 pixels at a 1280×900 viewport (79.1%), and from 7,292 to 1,367 pixels at 390×900 (81.3%). Shared sections were in their default collapsed state. Actual height depends on zone count, names, mappings and expanded sections. |
| Text contrast samples | Selected zone text, SVG labels, primary buttons and danger buttons measured at least 5.02:1 in light mode and 6.63:1 in dark mode. These samples are not a complete accessibility conformance audit. |
| Dashboard artifacts | The local dashboard is 76,180 bytes, or 22,525 bytes gzip. |
| Existing board baseline | A read-only LAN status check found the installed .5 controller online, reporting 79,180 seconds uptime and no safety fault. This is a pre-update observation, not a .6 installation or multi-day soak result. |

### Published .6 release and board acceptance

GitHub validation and the release-publishing workflow passed for commit
`d7e3d3d0468819c8c7b9adbaec464cd5da541c30`.
[Version .6](https://github.com/okayaleh/ESP32-watering-rebuild/releases/tag/v2.0.0-rebuild.6)
was published on September 9 at 22:58:57 UTC. The standalone channel retains
.6 and .5; the older .3 and .2 release archives also remain available.

The real controller installed .6 directly from GitHub over HTTPS after an
update request on the LAN, restarted and rejoined home Wi-Fi. The laptop did
not serve update files. Before/after API configuration exports were equal,
confirming preserved garden settings. A read-only acceptance check reported
three zones, all valve outputs closed and no safety fault. A subsequent
status read reported 134 seconds uptime, .6, connected Wi-Fi and no safety or
update error. This is a normal-runtime observation; the update journal was
not inspected to establish its stable marker. Two polling failures during
update/reboot—a host unreachable response and a timeout—recovered afterward.

The served 76,180-byte dashboard exactly matched the release, with SHA-256
`7b1c4e04acc0f80427b455b8231174af57f0caf2a54c75a38b46e67813a54b02`.
Real Chrome loaded the board's dashboard in light and dark modes at
1280×900 and 390×844. All four views rendered three zone panels with no
horizontal overflow or page JavaScript errors. Document height was 968
pixels on desktop and 1,770 pixels on mobile. The inspected desktop capture
showed all three sand-colored zone panels fully visible. Status stayed online
with outputs closed, and Maintenance navigation exposed **Check for updates**.

No watering commands or sensor calibration captures were triggered on the
physical board. The native platform was unchanged. Earlier .5 GitHub OTA
acceptance and .2 hardware measurements below retain their original scope.

## 2.0.0-rebuild.5 — independent GitHub updates

This release adds direct HTTPS downloads, a bounded immutable-commit channel,
platform compatibility checks, automatic check/retry/install scheduling and
retained-release policy. The dashboard now reports its source and automatic
mode, explains network/clock errors and allows compatible version selection.
The supplied native MicroPython image and USB boot helpers are unchanged.

| Check | Result and scope |
| --- | --- |
| Dashboard DOM audit | All 32 checks passed against the local simulator, including retained selection across polling, source/version/automatic-mode text, idle/busy controls, literal error rendering, clock/offline guidance and manual checks without installation. Existing dark mode and control queue checks remain included; at most one fetch was active. Evidence: `test-results/dashboard-github.txt`. |
| Browser update card | Real headless Chrome at 1280×900 and 390×844, in light and dark modes, rendered the update card and expanded retained-release controls without JavaScript errors or horizontal overflow. Desktop dark and mobile light captures were visually inspected. The release/status fixtures are simulated; no GitHub or ESP32 installation is implied. Evidence: `dashboard-browser-github.json` and `dashboard-github-*.png`. |
| Documentation links | All 102 relative README/document links and Markdown heading anchors resolved. |

The prepublication .4 candidate passed **232 host tests**, including real loopback TLS
acceptance/rejection for an untrusted CA, wrong hostname, expired certificate,
redirect, truncated body and unexpected compression. The actual supervisor
also checks fresh installation scheduling and preserves its watering pause
when a concurrent maintenance request is rejected.

The .4 tag was retained but no release or channel entry was published: CI
rejected a gzip artifact mismatch between Windows Python 3.14's zlib-ng and
Linux Python 3.12's zlib. Version .5 pins Zopfli 0.4.3 for reproducible
compressed dashboard bytes across the supported build platforms. The .4
candidate's physical observations below remain historical evidence; they
are not a claim that the final .5 release was installed or tested.

On 2026-09-07, COM8 became available and the bench ESP32 connected to home
Wi-Fi. A supervised native TLS download fetched the 3,867-byte GitHub manifest
in 1,221 ms with certificate verification enabled. The largest measured
transport slice was 507 ms; minimum native free heap was 44,744 bytes and
minimum largest free block was 34,816 bytes. This isolated probe had stopped
the application, so those margins are not a full-runtime stress result.

A candidate .4 application was then installed over USB: all 22 installed
files matched its manifest, the existing native image hash matched, and
Wi-Fi credentials and garden settings were preserved. Normal production
startup joined Wi-Fi, synchronized NTP, completed its boot trial and attempted
the automatic HTTPS channel check. The not-yet-published channel returned
HTTP 404 after TLS verification; the controller stayed healthy and released
its update hold. Afterward it reported 60,880 bytes free Python heap,
86,632 bytes free native heap and a 53,248-byte largest native block.
Maximum observed loop duration was 1,782 ms while updates held valves closed.
This is a brief bench observation, not a watering deadline measurement.

### Published .5 release acceptance

The final .5 release passed **233 host tests on GitHub Actions**, including
the pinned compression golden fixture. Linux produced exactly the same
64,145-byte dashboard and 19,443-byte gzip as Windows. The publisher verified
all 22 OTA artifacts, the manifest and native platform against the tag, then
verified the immutable published ZIP before advertising the channel.
The published source commit is `3716873526f54260e31d2d115e938e8983201e3f`.
The channel manifest SHA-256 is
`97f8ec2f7085f98521cafd94b1fc8a544ebf192257af515b15b6b7eabf6a582a`.

The bench ESP32 running the .4 candidate was restarted after publication.
After Wi-Fi, NTP and startup grace, it automatically checked GitHub, downloaded
the four changed artifacts (`runtime.mpy`, `updater.mpy`, `index.html.gz`,
`version.json`), installed them transactionally and rebooted into .5. The
laptop supplied USB power and observed the device; it did not serve any update
files. The final firmware files were downloaded by the ESP32 over HTTPS.
Serial capture shows the expected software reboot followed by Wi-Fi and NTP,
with no traceback or boot recovery failure. Missing-I2C-device messages are
expected on this sensor-free bench board.

A subsequent COM8 audit verified SHA-256 and size of **all 22 application
files**, plus both boot helpers, local configuration, settings and Wi-Fi files.
The on-board journal reported **stable / 2.0.0-rebuild.5**. Updated modules
loaded from `/rom`; GitHub source, automatic installation, 04:00 check time and
GPIO2 RGB settings survived. All three configured valve outputs read closed.
Garden settings and Wi-Fi credentials matched their saved pre-update files.
Automatic garden watering remains disabled in the existing bench settings.
Evidence: `github-ota-serial.txt` and `github-final-usb-audit.json`.

The real dashboard then passed manual **Check for updates** acceptance:
HTTP 200, a newer `last_check`, installed/selected .5, `checked`, no update
available and no error. That click did not install or reboot. Three zones
rendered, and dark/light desktop/mobile captures had no JavaScript errors or
horizontal overflow. One initial browser load timeout did not reproduce; a
fresh diagnostic load returned all seven startup requests with HTTP 200.
An intentionally preempted GET during the manual check is not a failed update.
Evidence: `github-board-ui.json` and four `github-board-ui-*.png` captures.

Post-install LAN observations covered successful startup/automatic checks,
and later UI acceptance observed more than forty minutes of uninterrupted
reported uptime. Wi-Fi and time remained healthy with no watering safety
fault. These observations do not measure worst-case TLS heap usage with full
history and maximum hardware configuration, or replace multi-day Wi-Fi and
real garden commissioning. The board has no connected watering hardware.

## 2.0.0-rebuild.3 — dark mode and RGB status

This application release passed **197 host tests**, the **25-check dashboard
DOM audit**, and real headless Chrome checks against the desktop simulator.
The build uses the same custom MicroPython 1.28.0 platform image as .2.
At .3 release publication the ESP32 was unavailable. In a subsequent USB
deployment, all 21 application files matched the published release, the
native-image hash matched, the boot trial reached stable and saved garden
settings/credentials were preserved. The local configuration selected GPIO2
RGB. Physical LED colors were not visually measured. Normal startup returned
to the rescue hotspot after unsuccessful home-Wi-Fi retries; a matching
network scan had a strongest observed signal of -83 dBm, but the connection
failure cause was not established. Those observations do not establish .3
LAN acceptance or .4 HTTPS behavior. Earlier .2 observations below remain
their original measurements.

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

Real valve polarity, driver bias during reset/power loss, flow closure, sensor accuracy, physical I2C deadlines, brownout recovery, and electrical power-cut durability remain untested. Initial home-LAN association, IP assignment, NTP synchronization, and laptop-to-board HTTP have now been observed. The earlier end-to-end OTA gap was closed by the September 7 automatic GitHub download, installation and reboot into .5 recorded above. DHCP renewal, router/ISP outage recovery and DNS/NTP failure recovery remain untested on the physical network. Neither the short home-LAN observation nor loopback stress proves the original intermittent MicroPython/lwIP listener stall is eliminated. A 48–72-hour network soak remains outstanding.

Follow [commissioning.md](commissioning.md) with the actual board, supply, sensors, valves, and router. A read-only HTTP soak can be recorded with:

```text
python tools/stress_web.py http://DEVICE-IP --seconds 172800 --interval 5 --output soak.csv
```

Raw bench logs are under local `test-results/`, which is ignored by Git. Source, automated tests, and the firmware reproduction files are included in the release. The dashboard and OTA mirror remain unauthenticated; SHA-256 verifies transfer integrity against a manifest, not publisher identity. Flow pulse counting and rain-based watering suppression remain explicit groundwork inherited from the original project.
