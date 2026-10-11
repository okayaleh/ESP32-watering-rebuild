# HTTP API

The public API retains the original routes below. JSON is UTF-8 and responses close-delimited; clients must read through EOF. Unknown API routes return404, wrong methods405, malformed input400, busy/safety conflicts409 and storage failures503. Configuration and credentials have separate storage. A successful mutation acknowledges persistence/queuing, not physical water flow.

| Route | Methods and behavior |
|---|---|
| `/` | GET dashboard, gzip when accepted |
| `/setup` | GET captive WiFi form |
| `/api/status` | GET lean readings, valves, grace, clock, network, heaps, CPU, queue/session, safety fault and OTA state |
| `/api/history` | GET live3h; `?hours=N` saved history, clamped1–336h; retention7days |
| `/api/events` | GET bounded recent persisted event ring |
| `/api/valve?state=open&valve=NAME` | POST timed at hard maximum; `state=close` cancels batch/session |
| `/api/water/trigger?duration=N&valve=NAME` | POST bounded manual run |
| `/api/zone/trigger?zone=NAME&duration=N` | POST mapped valves in sequence; duration optional |
| `/api/water/all?duration=N` | POST every valve in sequence |
| `/api/water/stop` | POST stop all and cancel pending work |
| `/api/settings` | GET public runtime settings; POST whitelisted watering fields |
| `/api/schedules` | GET list; POST full list or `{schedules:[...]}` |
| `/api/zones` | GET list; POST `{zones:[...],renames:{old:new}}`; live change |
| `/api/valves` | GET list; POST `{valves:[...],renames:{old:new},flow_meter_pins:[]}`; reboot |
| `/api/hardware` | GET hardware; POST `{hardware:{...}}`; reboot |
| `/api/pinmap` | GET board-specific GPIO roles/safety and ADC assignments |
| `/api/i2c/scan` | GET queues scan; poll until `busy:false` for completed result |
| `/api/calibrate` | POST measured `{zone,point:"dry"}` (or `"wet"`) or manual `{zone,dry_raw,wet_raw}`; GET `{busy,result,calibration}` |
| `/api/config/export` | GET downloadable runtime settings, no credentials |
| `/api/config/import` | POST validated full configuration; save then reboot |
| `/api/wifi` | GET SSID/state without password; POST `{ssid,password}` or setup form; save/readback/reboot |
| `/api/update/check` | POST `{}` checks newest release; optional `{version:"2.0.0-rebuild.5"}` checks a retained GitHub channel version; observe `/api/status.update` |
| `/api/update/apply` | POST install checked or staged files while idle |
| `/api/upload` | POST multipart code upload into staging; apply separately |
| `/api/reboot` | POST close outputs, cancel batch and reboot after response window |

Schedule: `{id,hour,minute,duration_sec,enabled,valve_names:[],zone_names:[]}`. Zone: `{name,channel,valves:[],threshold,wet_target,water_duration_sec,dry_raw,wet_raw}`. Omitted calibration values preserve the previous calibration. Zone `old_name` is also accepted and propagates to schedules. Valves carry `{name,pin,active_high,flow_meter_pin,watering_mode,target_volume_l}`; volume fields remain groundwork and do not change timed operation.

Names allow48 UTF-8 bytes. Maximum8 valves,16 zones,4 ADC boards and20 schedules; requests remain limited to16KiB, so a very large import/list must fit that byte limit. GPIO assignments must satisfy the selected board profile and cannot collide. Wet target must exceed dry threshold. Durations cannot exceed the local hard cutoff.

Compared with the old server, update checks are queued rather than blocking a request, code uploads require explicit apply, history timestamps are consistently Unix seconds, pin-map/status diagnostics are richer, and startup grace also blocks manual opens. API access is unauthenticated like the original; keep the controller on a trusted LAN.

## Moisture calibration

Both calibration methods use `POST /api/calibrate` and update the same saved
zone endpoints used for moisture percentages and automatic watering.

To measure a reference, send:

```json
{"zone":"zone1","point":"dry"}
```

Use `"wet"` for the wet reference. Hold the connected probe steadily in the
known reference throughout the roughly ten-second capture. Poll
`GET /api/calibrate` until it is no longer busy and inspect the result. A
successful capture automatically saves the measured endpoint; it does not
require a separate manual save.

From .8, to enter known raw ADC limits instead, send both endpoints without
`point`:

```json
{"zone":"zone1","dry_raw":21567,"wet_raw":7645}
```

These example values are not universal sensor defaults. Both fields must be
JSON integers from 0 to 32,767, separated by at least 100 counts. Strings,
booleans, fractions, a missing endpoint, an out-of-range value, or mixing
manual fields with `point` are rejected. Either sensor polarity is supported.
The pair is validated and saved together for the named zone; a rejected
request leaves its saved limits unchanged.

In the dashboard, typed endpoints stay local until **Save limits** is clicked.
Regular polling preserves unsaved edits. A successful **Capture dry** or
**Capture wet** updates its own endpoint field while preserving an unsaved
draft in the other field. Capture does not apply that other draft. These
endpoints define a relative dry-to-wet scale, not volumetric soil water content.

## Update status and version selection

`/api/status.update` includes `source` (`github` or `mirror`), `repository`,
`installed_version`, `available_version`, `available`, `state`, `busy`,
`error`, `files`, `last_check` and `last_install`. GitHub checks also report
`available_versions` (at most three channel versions) and `release_commit`.
`auto_install` and `check_hour` describe the configured automatic mode;
`automatic_paused` and `held_version` report a deliberate rollback hold.
`blocked_version` identifies a release rejected after a failed boot trial.

A version selection must exist in the freshly downloaded channel and match
the installed native platform requirements. Arbitrary URLs and Git refs are
not accepted by this API. A manual check never automatically installs, even
when daily automatic installation is enabled. Use `/api/update/apply` after
observing a successful check with `available:true`; uploads require the same
explicit apply step. Checks and installation require idle watering, and the
supervisor pauses new runs during maintenance.
