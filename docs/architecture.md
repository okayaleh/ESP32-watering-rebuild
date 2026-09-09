# Architecture and reliability decisions

The reference was downloaded from [main](https://github.com/supercrossed/ESP32-watering/tree/main) and reviewed together with its overview, rebuild prompt, API guide and listener-stall handoff. The replacement is a flat MicroPython application. It does not reuse the old server or main loop.

## Runtime boundaries

`boot.py` is immutable recovery code and closes configured valve pins before any rollback or ROM cache work. A failed close prevents application startup. Tiny `main.py` independently closes outputs, records reset cause, invokes the compiled supervisor, and attempts every output closure before reset on application exit. `runtime.py` checks watering deadlines before/after each subsystem. It first validates safety and feeds the watchdog before subsystem initialization, then feeds only after successful safety checks. Physical testing showed that the initial feed is necessary to reliably arm this ESP32 watchdog. Driver calls can still exceed their specified timeout.

`controller.py` owns all valve activation. It runs a single global queue and independent per-valve lockouts. A batch can hold192 pending valve runs, enough for20 simultaneous schedules with8 valves each. This is bounded, not an unlimited job queue. Schedules expand zone mappings when due; excess requests receive a queue-full error. Schedules require one successful NTP synchronization since boot; an internet loss after synchronization does not stop the running RTC or schedules. Moisture and manual watering do not require wall time.

A moisture session starts below its dry threshold, waters its eligible mapped valves sequentially, then waits at least one measurement interval. A fresh reading after the soak is required before another cycle. A failed/stale sensor ends the session. Wet-target attainment or `max_water_cycles` ends rewatering; cooldowns are measured from the last close of each valve. Manual actions, scheduled runs and configuration edits cancel further soak cycles. Manual stop cancels the whole batch/session, so a queued duplicate cannot reopen a stopped valve.

`watering_state.json` records intent **before energizing** a valve and records close timestamps afterward. The duration starts after the durable write, immediately before GPIO activation; real flash commits can take hundreds of milliseconds or longer. On boot without reliable time, saved events retain a conservative full cooldown. NTP can restore valid recent timestamps; future/invalid/old timestamps retain conservative countdowns. Schedule occurrences discovered while another valve is active queue in RAM, and the complete ledger is committed after closure/before the next valve opens. No schedule journal write delays an active valve's cutoff. A power loss can skip a queued run, while already started scheduled occurrences remain protected against duplication.

`settings_store.py` supplies bounded schema validation and migrations; `application.py` handles atomic rename propagation. A valve rename copies journal identity before the settings write so interrupted migrations keep both names protected. Hardware changes close old pin objects before saving and rebooting; sensor objects are not reconfigured against mismatched hardware. Invalid/damaged settings fail closed rather than silently replacing user configuration. Recovery from two unusable settings generations requires USB restoration.

## Network behavior

`web.py` uses nonblocking sockets, at most2 clients and512-byte send/receive calls. Partial writes retain their offsets. Growing JSON responses are generated token-by-token; dashboard files stream from flash. HTTP/1.0 close-delimited responses avoid both a large serialized buffer and incorrect UTF-8 `Content-Length`. Request bodies are limited to16KiB, setup credentials to2KiB and multipart uploads to512KiB/120s. Multipart files stage before installation. Slow or failed clients cannot feed the watchdog.

`wifi.py` owns station retries, bounded UDP DNS/NTP clients and gateway TCP health checks. Refused/reset gateway connections count as evidence of a working LAN; unknown errors are inconclusive. Radio reconnects/probes defer during active watering. Each connection attempt receives its full 20-second deadline, or 12 seconds with the rescue AP active: the ESP32 driver can retain an earlier failure status while a new attempt connects. Deadline failures expose the latest status reason; a successful connection clears it. Rescue AP retries park the station between attempts to limit shared-radio channel disruption. AP setup uses activate/configure/bounce ordering and captive probes receive redirects. Setup accepts an entered SSID; it does not scan the radio while watering.

The dashboard serializes **all** fetches through one promise queue, including actions and weather requests. A single scheduler staggers periodic work, and errors release the queue. A mutation cancels an active read before joining the queue, so a slow history download does not hold up a stop action. Mutations are never cancelled by later requests. Live-history reads have an 18-second timeout; saved-history reads allow 90 seconds. A browser-side timeout cannot guarantee the board did not receive a watering action, so the UI does not blindly retry mutations.

The compact dashboard groups each zone's moisture dial, raw ADC reading,
sensor commands and mapped valve controls in one panel. Repeated views of a
shared valve represent the same controller output; they do not create new
valves or independent watering queues. Shared settings and maintenance stay
available in expandable sections. Gauges and themes render in the browser,
using the existing readings and control APIs.

Listener health distinguishes fatal `accept()` errors from EAGAIN and retries an invalid listener after30s. Last accept/response times, counters and errors are exposed. An idle listener is not labeled “reachable”: end-to-end reachability remains explicitly unverified. The original project reproduced intermittent stalls below the application in MicroPython/lwIP, so listener recreation is not claimed to cure that fault. Establish behavior on the actual firmware/router with the commissioning soak test.

## Standalone GitHub updates

The update channel is a bounded JSON document on the repository's `updates`
branch. Each of at most three entries binds a release version to immutable
commit paths. The board downloads a manifest and flat files directly from
`raw.githubusercontent.com`, avoiding GitHub API calls, ZIP extraction and
release-asset redirects. Manifest platform identity and native-image hash
must match the installed platform before staging.

The HTTPS state machine uses nonblocking sockets, a required certificate
chain, SNI/hostname checks and a synchronized clock. It streams bounded
chunks under an absolute transfer deadline. Trust roots are bundled in the
application and can be refreshed by a compatible verified update. Failed
verification never disables certificate checks or falls back to HTTP.
Plain HTTP remains an explicit local-mirror configuration only.

Automatic checks wait for connected Wi-Fi, valid time and idle watering,
catch up after boot/reconnection and retry failures. Only a successful
automatic check can lead to automatic installation. Manual release checks
and uploads require explicit apply. A durable update policy preserves a
chosen older release by holding automatic upgrades, and excludes versions
which failed a boot trial. Installing the latest selected channel release
clears a deliberate hold. Existing transactional file recovery and ROM cache
bank switching remain authoritative after installation.

## Storage, sensors and memory

Small JSON writes use temp files, flush/sync, readback verification and a previous generation. A corrupt current file cannot overwrite its only good backup during repair. Settings changes copy incoming request values into a private candidate; persistence takes ownership of that candidate without another full-tree clone. Public save calls still defensively copy their inputs. This reduces peak allocation during a maximum-configuration save while preserving input isolation and atomic verification. OTA has its own commit journal and immutable recovery code; see [OTA](ota.md).

`moisture.py` scans before touching absent ADCs, splits conversions across polls, isolates errors by board/zone, and backs off total failures. Bus recovery only drives low or releases a line. `env_sensors.py` performs incremental AHT20 and BMP280 reads and validates AHT CRC. Calibration averages10s and reports sample count/range/spread; watering pauses during capture.

Live history uses180 compact points (one/minute,3h), with shared zone-name tuples and half-percent byte values. Saved history is one point/15min,7-day retention, streamed from flash. Excluded/torn records yield a transport checkpoint; pruning processes one record per idle supervisor turn. Normal history records use native JSON encoding one validated record at a time, capped at 4096 bytes and split into 512-byte transport fragments. Larger or unusual legacy records keep generic streaming. Each response caches at most 32 validated zone names. Short strings use native escaping; each client advances at most eight encoder fragments and one socket send per poll. This limits work between control-loop checks and avoids thousands of nested generator steps during a full history download. Original `history.csv` remains readable and untouched. Event buffers are capped64 records and the log rotates around32KiB.

Modules are compiled off-device. Both Python heap and ESP-IDF C-heap free/largest-block figures are exposed. When the sampled largest C-heap block falls below2048 bytes, ordinary requests receive a small503 response; stop requests bypass that shedding rule, although allocation or listener failures can still prevent remote delivery. Streaming bounds JSON/socket fragments, but **does not prove that every allocation in a status/settings operation is below512 bytes**: configuration parsing, copies and a16-zone history record allocate more. Two16KiB request bodies plus parsed settings can materially pressure WROOM memory. Physical full-configuration heap measurements are a release gate; host tests cannot establish the C-heap margin.

The classic ESP32 platform image enables ROMFS and two 256 KiB application cache banks. After filesystem OTA recovery, immutable `romboot.py` hashes the root application modules. An unchanged, verified image mounts without erasing flash. A changed image streams into the other bank, verifies every byte, then commits its marker and mounts at `/rom` ahead of the root import path. Root files remain the update authority; rollback regenerates/selects the corresponding previous code. Configuration and credentials never enter the ROM image. Application bytecode and constant data can then remain in mapped flash instead of consuming the Python heap.

The platform also caps new GC heap segments to preserve approximately 32 KiB in the largest native free block. This prevents stock split-heap growth from consuming the last usable networking allocation. It is an allocation reserve, not proof that every future network operation succeeds: native drivers can themselves consume or fragment it. `gc.mem_free()` on this port includes potential heap expansion, so it must be read together with allocated memory and explicit IDF metrics.

Telemetry queues event records in bounded RAM and flushes only while the controller is idle. History flash writes, boot-trial commits and firmware staging are also restricted to idle periods. Sensor/I2C work waits while a valve is energized; fresh samples after closure still govern soak/recheck cycles. Firmware checks pause watering while they stage their results. These rules keep observed slow flash writes and potentially slow I2C transactions away from active output deadlines.

Public/persisted timestamps use Unix seconds. Ports with a native2000 epoch are detected and normalized; local schedule hour/minute arithmetic uses the normalized time. This follows the documented port difference in [MicroPython time](https://docs.micropython.org/en/v1.28.0/library/time.html). Monotonic timers remain independent of RTC and NTP changes.
