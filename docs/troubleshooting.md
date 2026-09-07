# Troubleshooting

[Documentation home](README.md) · [Getting started](getting-started.md) ·
[Commissioning](commissioning.md) · [Update recovery](ota.md)

Use the visible state and recorded error to choose the next check. A saved
Wi-Fi name, a running HTTP listener and a successful home-network connection
are three different observations.

For USB maintenance, wiring changes or recovery, disconnect valve power and
the water supply first. Fresh defaults enable both scheduled and
moisture-triggered watering. Review or disable those settings before reconnecting
the actual watering hardware. If water is already flowing unexpectedly, use
the physical shutoff; a browser command depends on a working connection and
cannot establish whether a valve has mechanically closed.

## Wi-Fi setup: which name and password?

| Place | What belongs there |
| --- | --- |
| Your phone or laptop's Wi-Fi chooser | Join the controller's `Planter-Setup-xxxx` hotspot. It is open and has no password. |
| **Home Wi-Fi network name (SSID)** in the setup page | The exact name of the existing home network that the controller should join. |
| **Home Wi-Fi password** in the setup page | That home network's Wi-Fi password. It is not a Google account password, router administration password or laptop login. |

While joined to `Planter-Setup-xxxx`, open
[`http://192.168.4.1/setup`](http://192.168.4.1/setup). A warning that this
hotspot has no internet is expected: it provides local configuration access.
Keep the phone or laptop connected to it if the operating system asks whether
to switch networks. If the captive window does not open, use the explicit
HTTP address in a browser.

Copy the home SSID exactly, including case and intentional spaces. The API
accepts 1–32 UTF-8 bytes for the SSID and up to 63 UTF-8 bytes for the password;
non-ASCII characters can occupy more than one byte. Saving credentials
verifies the file write and restarts the controller. A successful save means
the details were stored; connection is checked after the restart.

The password field is deliberately not filled with the saved password.
**Re-enter the home password whenever you save the Wi-Fi form. Leaving it
blank saves an empty password; it does not preserve the old password.** Use
an empty password only for a home network that is itself open.

### Google Wifi and Nest Wifi with one shared SSID

Google Wifi and Nest Wifi use one network name across their radio bands.
The classic ESP32 in this project uses 2.4 GHz; enter the shared home SSID
and its normal password. Do not invent a separate `-2.4` network name unless
your router actually broadcasts one. A phone or laptop using 5 GHz can still
be on the same home network as the planter. Google's
[Wi-Fi band guide](https://support.google.com/googlehome/answer/6293481?hl=en)
explains the shared name and how clients choose a compatible band.

This setup page sends the typed credentials directly to the ESP32 through
its own hotspot. After saving, reconnect the browser device to the home
network and use the planter's assigned home-LAN address. Guest-network or
client-isolation rules can restrict device-to-device access even when both
devices have internet; check the router's settings if association succeeds
but the local dashboard cannot be reached.

## Understand the connection state

Open **Garden configuration → WiFi** on the dashboard, or read
`http://DEVICE-IP/api/wifi` in a browser.

| Observation | Meaning and next check |
| --- | --- |
| Saved network shown | Credentials were loaded. This alone does not mean the network was found or joined. |
| Rescue hotspot active; connected: no | The controller is reachable locally for setup but has not established its home connection. Read **Last error**. |
| State `connecting` | An attempt is in progress. Allow its deadline to finish before judging the result. |
| Connected: yes, with an IP address | The native driver has received a station IP address. Switch the browser to the home network and use that address. |
| Rescue hotspot disappears after a successful connection | Expected: the controller closes its setup AP after joining the home network. |
| HTTP listener started on serial | The local server is running. This does not prove Wi-Fi association or access from another device. |
| Gateway health `healthy` | A TCP connection or refusal/reset provided evidence of a response on the probed path. It does not test internet access or every dashboard request. |
| Gateway health `unverified` or `inconclusive` | The controller has not established a usable probe result. These values are not proof of a network outage. |

The initial station attempt has a 20-second deadline. With the rescue AP
already active, each attempt gets 12 seconds, with 60 seconds between failed
rescue retries. Saved credentials that fail the first connection make the
rescue hotspot available after that attempt. After losing a previously
established connection, prolonged disconnection opens rescue access according
to `WIFI_RESCUE_AFTER_SEC`, which defaults to five minutes.

The native ESP32 driver can retain an old failure status while a new attempt
is running. This application therefore waits for the attempt's deadline
instead of cancelling it immediately on that old status. A successful
connection clears the displayed Wi-Fi error.

### Error messages and native status codes

These numeric codes describe the supplied ESP32/MicroPython platform. Other
MicroPython ports may use different values.

| Message or code | Interpretation |
| --- | --- |
| `1000` / idle | No successful connection is currently reported. |
| `1001` / connecting | The driver is attempting to connect. |
| `1010` / got IP | Association and IP assignment succeeded. |
| `201` / **WiFi network not found** | The connection scan did not find the requested AP. Compare the exact SSID and check whether its 2.4 GHz signal is visible at the board. This is not a password verdict. |
| `202` / **WiFi authentication failed** | Authentication failed; the native API also maps some general connection failures to this value. Check the intended home network and its password, but do not treat the code as proof of a mistyped password. |
| `203` / **WiFi connection failed** | Association failed. Record the error and the surrounding observations. |
| `204` | The native driver reports a handshake timeout. The application may display its general deadline message with this status. |
| `210`, `211`, `212` | Native reasons for incompatible security, authentication-mode threshold or signal threshold respectively. Record the code before changing router settings. |
| **WiFi connection timed out (status …)** | The application deadline expired without a connected IP state. The included native status gives additional context. |
| **WiFi credential read-back failed** | Saving/verifying the credential file failed. Resolve storage/recovery problems; this message does not describe router authentication. |
| `Wifi Unknown Error 0xffffffff` | A native driver error surfaced through MicroPython. Record when it happened; this text alone does not identify a password, signal or memory cause. |

Espressif documents the connection phases and native reasons in its
[ESP-IDF 5.5.1 Wi-Fi driver guide](https://docs.espressif.com/projects/esp-idf/en/v5.5.1/esp32/api-guides/wifi.html).

### If setup keeps returning to the hotspot

1. Confirm that the form contains the actual home SSID and home Wi-Fi
   password. The open setup hotspot does not have a password to copy.
2. Let the full connection attempt complete, then inspect **Last error**.
3. For status 201, confirm the home network is broadcasting 2.4 GHz and is
   visible near the controller. Test the board nearer a mesh point as a
   comparison, without changing the stored credentials at the same time.
4. If a scan is needed, use a supervised USB diagnostic with watering
   disconnected. Record whether the saved SSID matches and the matching
   APs' channels, signal levels and security modes; do not publish the
   credential file or a complete list of surrounding network names.
5. If the driver gets an IP but the page fails, follow the reachability checks
   below before entering credentials again.

An earlier bench attempt returned 201 and found no matching SSID. Later
scans found four exact matches, and both a standalone connection and the
production application joined the home LAN. No further firmware change was
needed for that later success. The reason the network was initially absent
is not established. Signal levels were recorded alongside successful access;
they do not establish the cause of the earlier failure. See the complete
[validation report](validation.md).

## Connected, but the dashboard is unavailable or slow

Use the current address from the router's device list or the serial
`WiFi connected:` message. `192.168.4.1` is the setup address, not the
controller's assigned home address. DHCP can give the controller a different
address after network changes or restarts. Use `http://`, not `https://`;
the embedded dashboard does not provide TLS.

Try `http://DEVICE-IP/api/wifi` or `/api/status` before requesting a long
history range. Check that the browser is on the same reachable LAN and that
guest isolation or a VPN is not diverting local traffic. Hostname discovery
such as `planter.local` depends on the network; the numeric address is the
clearest diagnostic.

One failed or reset TCP connection does not mean the listener has stopped.
Compare subsequent requests, uptime and the listener's counters in
`/api/status`. Repeatedly resetting the board destroys useful timing evidence.
If safe to do so, record the symptom before restarting.

Live history is held in RAM and clears on restart. Saved history is recorded
less frequently and can take longer to retrieve. Start with the 3-hour view
when checking a slow connection. The bench results include a short home-LAN
check and separate loopback stress tests; they do not establish a completed
multi-day network soak.

## Settings changes did not do what I expected

| File or action | What it controls |
| --- | --- |
| Dashboard settings / `settings.json` | Saved zones, valves, schedules, calibration and watering preferences. These become authoritative after first boot. |
| `settings.json.prev` | Previous saved settings generation used when recovery can read it. Keep it during investigation. |
| `config.py` | First-boot defaults and local platform/safety options, including board profile, hard cutoff, watchdog and GitHub/update mirror options. Editing first-boot zone defaults does not overwrite existing saved zones. |
| `wifi.json` | Saved home-network credentials. A saved nonempty SSID takes precedence over credentials in `config.py`. |
| **Download backup** | Exports the garden configuration. Wi-Fi credentials are excluded. |
| **Restore backup & restart** | Validates and replaces the garden configuration, then restarts. It does not restore a Wi-Fi password from that export. |

Hardware and valve configuration changes close the old outputs and restart
the controller. Other configuration saves also cancel pending watering.
Validation errors leave the saved configuration unchanged. If both settings
generations are unusable, the controller fails closed rather than silently
replacing the garden with defaults; restore a compatible configuration over
USB.

Do not delete settings or watering-state files as a Wi-Fi troubleshooting
step. That can discard calibration, schedules or cooldown history without
addressing a radio problem.

## Sensors are missing, or watering does not start

Missing I2C hardware produced handled sensor/bus errors on the bare bench
board. Those errors do not mean home Wi-Fi authentication failed. Confirm
the ADS1115 addresses, board order, shared SDA/SCL wires, 3.3 V pull-ups and
zone channel mapping in the [hardware guide](hardware.md).

Before treating a refused watering request as a fault, check startup grace,
automation enable switches, clock synchronization, cooldown/lockout state,
calibration/update activity, sensor validity and the reported safety fault.
Manual starts also respect startup grace and the local hard cutoff. Scheduled
starts need a synchronized clock. A network loss does not automatically
stop all local watering logic.

Do not bypass a safety refusal by driving GPIOs directly. Follow the
[commissioning checklist](commissioning.md) to verify real flow and closure.

## Firmware updates

**“Updates require a plain HTTP mirror”** comes from .2/.3 firmware. Those
versions cannot fetch GitHub directly. Install .5 once using USB or the
existing LAN mirror and follow its [configuration step](ota.md#one-time-upgrade-from-2-or-3).
Changing from USB power to a separate supply does not upgrade firmware.

On .5 or later, **Firmware updates** should show **Direct from GitHub**. A
local-network source means `UPDATE_BASE_URL` is still set; clear it in the
board's `config.py`, save and restart to select GitHub. Confirm that the
repository option names `okayaleh/ESP32-watering-rebuild`.

| Message or observation | What to check |
| --- | --- |
| Waiting for home Wi-Fi | The setup hotspot gives local access, not internet. Complete home-network provisioning and confirm the station has connected. |
| Waiting for the clock | HTTPS needs a correct date. Allow NTP synchronization; check internet access and any router rules blocking NTP. Do not disable TLS verification. |
| Certificate or TLS failure | Preserve the error and installed version. Check device time, internet filtering and available memory. A changed GitHub certificate chain may require a reviewed trust-bundle update. |
| Timeout or download failure | Check Wi-Fi signal and internet availability. Automatic checks retry; manual checks may be retried once connectivity returns. Keep normal power on. |
| No retained-release selector | Run a successful check first. It appears only when another compatible channel release exists; the initial .5 channel has one entry. |
| Automatic updates paused | A previous channel release was deliberately installed. Check and install the latest release to resume. |
| Excluded after a failed boot | The board recovered from an unsuccessful release. That version is not retried; use a newly published corrected release. |
| Plain HTTP mirror unavailable | This optional mode needs the serving computer running and reachable through its LAN firewall. Clear the mirror URL to use direct GitHub mode. |

A manual check never installs automatically. Review the selected version and
choose **Install update**. Download/install work waits for idle watering,
and new runs pause while maintenance is active. Automatic-update options
are separate from the daily and moisture watering enable switches.

## USB diagnostics and safe restart

Use a USB data cable and select the board's current serial port in Thonny or
your serial tool. COM8 was the bench port, not a fixed project setting. Only
one program should own the serial port at a time.

With watering disconnected, Ctrl+C interrupts the application, attempts to
close every configured output and returns to the REPL only if those closures
succeed. The existing watchdog remains armed. At the default setting there
are at most two minutes from its last feed before it resets the board.
Prepare files before opening this maintenance window; do not assume an
interrupted session stopped the watchdog.

These REPL commands inspect identity, station state and memory without
printing a password or initiating a connection:

```python
import sys, machine, network, gc, esp32
print(sys.implementation)
print("Reset cause:", machine.reset_cause())
station = network.WLAN(network.STA_IF)
print("STA active:", station.active())
print("Connected:", station.isconnected(), "Native status:", station.status())
if station.isconnected():
    print("IP:", station.ifconfig()[0], "RSSI:", station.status("rssi"))
print("GC free:", gc.mem_free())
print("IDF heaps:", esp32.idf_heap_info(esp32.HEAP_DATA))
```

On this platform, GC free memory includes possible heap expansion; inspect
the native/IDF figures too. A reset-cause number records a category, not a
complete diagnosis. The recorded watchdog test returned cause 3 on the
supplied board, but a software reset after saving settings is expected.

When maintenance is complete, use `machine.reset()` once to resume the normal
boot sequence. If the dashboard is reachable, **Maintenance → System status
→ Restart controller** requests output closure and a restart through the
application. A failed output closure causes a reset rather than allowing
normal maintenance; keep the watering supply disconnected until investigated.

### Repeated resets or update recovery

Capture the first boot error before changing files. Messages such as
`Boot recovery failed; refusing application startup` indicate that normal
startup was intentionally blocked. Preserve `.ota/`, `.ota-journal.json`,
`settings.json` and their previous generations while diagnosing recovery.
Do not repeatedly delete the ROM state or edit flash banks by hand.

The USB boot helpers close configured outputs before rollback or ROM cache
work. They can restore the prior installation after an interrupted update or
failed boot trial. If recovery itself cannot complete, use the compatible
USB files and procedure in the [firmware guide](../firmware/README.md) and
[update recovery guide](ota.md). Back up the device before reflashing a
partition layout.

To deliberately choose an older release, use the dashboard retained-release
selector or an explicit archived tag as documented in [Updates, retention and recovery](ota.md). Repository history
and the board's one automatic recovery generation are separate. An older
application must remain compatible with saved settings and the installed
native/boot files.

## What to include in a problem report

- Board/module type, native firmware version and installed application version.
- The exact visible error and the action immediately before it happened.
- Whether the setup AP was active, whether the station had an IP, and whether
  `/api/status` responded from another device on the LAN.
- Relevant timestamps, uptime/reset cause and a short sanitized serial excerpt.
- For connection problems: native status code and matching-network
  channel/RSSI/security observations, if already collected.
- For watering or sensors: configured pins/polarity, attached hardware and
  whether the supply was disconnected during the test.

Do not post `wifi.json`, an unsanitized `config.py`, passwords or account
tokens in a public issue. Review status/event excerpts for home-network names
or addresses before sharing. Refer to the [validation report](validation.md)
when distinguishing a measured result from an untested assumption.
