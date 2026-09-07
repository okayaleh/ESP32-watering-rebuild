# Physical commissioning and stability release gate

Host tests do not demonstrate leak prevention or radio stability. Commission the actual board, wiring, supply, valves, sensors and router together. Keep valve water/supply disconnected for initial electrical and update-fault tests. Do not leave the new controller unattended until the recorded observations below pass.

| Check | Required observation |
|---|---|
| Board/firmware identity | Record exact ESP32 variant, MicroPython build, flash/PSRAM mode, reset cause and pin assignments |
| Reset and power-off state | Every driver stays closed before Python starts, during reset and without ESP32 power; verify active-low bias separately |
| Normal valve operation | Each valve starts/stops real flow, correct polarity, no unintended simultaneous opening |
| Startup grace | Moisture/status populate; manual and automatic starts are held for60s |
| Hard cutoff | Every valve closes by requested duration and configured maximum using an independent stopwatch |
| Hung loop/watchdog | With water disconnected, deliberately stall the loop and verify actual reset and output closure within the watchdog interval; restore production watchdog setting afterward |
| Schedule/reset dedup | Reboot during a scheduled minute; no second occurrence; valid RTC time required after boot |
| Persistent cooldown | Interrupt power during and after a run, including without internet; no immediate duplicate moisture watering |
| Hysteresis | Dry starts watering, soak requires a new sample, wet target ends it, maximum cycle count ends a stuck-dry session |
| Sensor disconnection | Remove each ADC/probe; affected readings fail, other boards and schedules continue; reconnect recovers |
| I2C recovery | Exercise held-low SDA/SCL safely with an appropriate fixture; no push-pull high contention, bounded recovery/failure |
| First run/rescue | Phone joins setup, gets DHCP, captive redirect works; saved credentials verified; prolonged router loss opens rescue; return closes it |
| ISP outage | Local dashboard remains usable; a healthy gateway path is not recycled because internet/NTP is down |
| LAN recovery | Router reboot, DHCP renewal and WiFi loss recover; log IP changes and reset causes |
| Two-client soak | Run two dashboard tabs for48–72h with full configuration and periodic reloads; record response times, failures and recovery |
| Heap margin | Track Python free/allocated and IDF free/largest block at boot, load, import, history and OTA; investigate sustained decline or low largest blocks |
| Listener stall | Record serial loop activity, ping, TCP80, accept/error counters, heaps and router state during any failure; do not declare it solved solely because pings work |
| Slow/malformed clients | Large bodies rejected, trickling upload ends by120s, valves still close; observe maximum loop duration |
| OTA success/failure | Direct GitHub TLS update and optional mirror update, invalid/expired certificates, SHA mismatch, incompatible platform, out-of-space, network interruption, power cut during commit and repeated failed boots all preserve settings and close outputs |
| Automatic update policy | Catch-up after boot/reconnection, daily idle-time check/install, failed-check retry, failed-boot exclusion and retained-version hold behave as configured without reopening outputs |
| Configuration durability | Save/rename/import/reboot; calibration, schedule targets, credentials and cooldowns retained |
| Long history/logs |7-day retention and bounded event files; full history requests leave controls responsive |

The hardware watchdog is120s by default and cannot guarantee instantaneous closure during a driver hang. A mechanical valve stuck open, failed MOSFET, incorrect bias, failed regulator or damaged flash requires physical mitigation. The software's fail-closed paths are only one safety layer.

For USB maintenance, Ctrl+C closes every configured output before returning to the REPL. The watchdog remains armed, so the default setting gives at most two minutes from its last feed to upload files before a reset. A failed output closure resets immediately. Disconnect the watering supply for this work; software closure does not guarantee the physical valve is closed.

Useful first boot commands at a supervised REPL, with watering supply disconnected:

```python
import sys, time, machine, gc, esp32
print(sys.implementation)
print(time.gmtime(0)[0], machine.reset_cause())
print(gc.mem_free(), esp32.idf_heap_info(esp32.HEAP_DATA))
```

Changing the watchdog setting to0 is only for supervised development; restore it before commissioning watering. Never assume a WROOM pin configuration applies to an S3. Retain the previous firmware/files and a USB recovery path during the trial.
