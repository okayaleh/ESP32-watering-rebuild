# Getting started

[Documents](README.md) · [Project README](../README.md)

This guide takes a classic ESP32 with 4 MB flash from a backed-up bench board to a configured dashboard. It also explains which steps an already-installed controller can skip. Use [hardware and wiring](hardware.md) alongside it; the supplied native image is not for ESP32-S3 or other ESP32 variants.

## Choose your starting point

| Situation | Next step |
| --- | --- |
| The rebuild already boots and its dashboard opens | Continue at [Configure the controller](#configure-the-controller); use [application updates](ota.md) for later releases |
| The rebuild boots but cannot join home Wi-Fi | Continue at [Connect to home Wi-Fi](#connect-to-home-wi-fi), then [troubleshooting](troubleshooting.md) |
| The board runs the original project or stock MicroPython | Back up the board, then perform the first USB installation below |
| You only want to explore the interface | Run the simulator described in the [project README](../README.md#preview-without-an-esp32) |

The current development bench board has already received the custom runtime and application. Creating or downloading a repository does not require erasing it again.

## Download and prepare

1. Open [GitHub Releases](https://github.com/okayaleh/ESP32-watering-rebuild/releases) and choose the intended version. Read its release status; `v2.0.0-rebuild.2` is a bench-tested prerelease.
2. Download the **`planter-<version>.zip` asset** and its **`.zip.sha256`** file. Extract the ZIP into a working folder. The ZIP includes `build/`, `firmware/`, source, tools and documents; GitHub's separate source-code downloads are not the named release package.
3. In PowerShell, run `Get-FileHash .\planter-<version>.zip -Algorithm SHA256` with the actual filename and compare the value with the downloaded checksum file. An application-only verified download is also available through `tools/sync_updates.py`; see [OTA](ota.md).
4. Use a USB **data** cable and identify the board's current serial port in Windows Device Manager or Thonny. The development board was on COM8; another board or USB socket may have a different port.
5. Disconnect valve supply power and keep it disconnected throughout installation and initial configuration. Close other serial monitors before using a flashing tool; only one program can own the port.

For the command-line USB procedure below, use Python and esptool 5.4.0 (`python -m pip install esptool==5.4.0`). Thonny is convenient for browsing and copying the MicroPython filesystem after flashing. Commands below run from the extracted project folder in PowerShell. Replace `COM8` with the actual board port.

## Back up an existing installation

Before changing firmware, export settings from the original dashboard if it is available. Use Thonny's device file browser to save the existing application, configuration, credentials and watering state to a **private** backup folder. A settings export does not contain the Wi-Fi password.

Also keep a full flash image if migrating a working board. The following reads the entire flash; it does not erase it:

```powershell
$serialPort = "COM8"
New-Item -ItemType Directory -Force .tools/device-backups | Out-Null
$backupFile = ".tools/device-backups/flash-before-$(Get-Date -Format yyyyMMdd-HHmmss).bin"
python -m esptool --chip esp32 --port $serialPort read-flash 0 ALL $backupFile
if ($LASTEXITCODE -ne 0) { throw "Backup failed; stop before flashing" }
Get-FileHash $backupFile -Algorithm SHA256
```

For a 4 MB board, the full image should be 4,194,304 bytes. Record its hash and retain the file privately; a complete flash backup can contain credentials. `.tools/` is ignored by this repository. Check that your exported files and flash backup exist before continuing.

## Install on a classic 4 MB ESP32

### 1. Install the platform image

**This is an initial installation or deliberate USB recovery procedure. Erasing flash removes existing firmware, settings and credentials. Do not use it for normal application updates.** The supplied image changes the writable filesystem layout, so an old stock filesystem must not simply be reused in place.

Confirm the board is a classic ESP32 with 4 MB flash, and compare the platform image's SHA-256 with `firmware/SHA256SUMS`:

```powershell
Get-FileHash firmware/planter-esp32-1.28.0-romfs.bin -Algorithm SHA256
Get-Content firmware/SHA256SUMS
```

After the backup is verified and valve power is disconnected:

```powershell
$serialPort = "COM8"
python -m esptool --chip esp32 --port $serialPort erase-flash
if ($LASTEXITCODE -ne 0) { throw "Erase failed" }
python -m esptool --chip esp32 --port $serialPort write-flash 0x1000 firmware/planter-esp32-1.28.0-romfs.bin
if ($LASTEXITCODE -ne 0) { throw "Flash failed" }
python -m esptool --chip esp32 --port $serialPort verify-flash 0x1000 firmware/planter-esp32-1.28.0-romfs.bin
if ($LASTEXITCODE -ne 0) { throw "Flash verification failed" }
```

Use the combined `.bin` at **`0x1000`**. The `.app-bin` is a runtime-only image at `0x10000` and requires this project's partition table to be installed already. See the [platform reference](../firmware/README.md) for the layout and artifact provenance. None of the commands above installs the watering application files.

### 2. Copy the application

1. Open Thonny, select its MicroPython ESP32 interpreter and the correct port, and open the device file browser. Close esptool and any other serial owner first.
2. If the board requires different pins or local options, edit `build/config.py` in your extracted package before copying. When building from source, edit an ignored `src/config.py` and rebuild instead. Leave `WIFI_SSID` and `WIFI_PASSWORD` blank to use hotspot setup. Review the [hardware profile](hardware.md); never copy WROOM pin defaults to an S3.
3. Copy the files **inside `build/` to the device root `/`**, not to a `/build` folder. Include `boot.py`, `romboot.py`, `config.py`, all `.mpy` modules, `index.html`, `index.html.gz` and `version.json`. `manifest.json` is used by the laptop mirror and is not needed on the device.
4. Copy **`main.py` last**, then reboot. A fresh filesystem has no stale modules. During recovery onto a populated filesystem, remove obsolete `.py` siblings of installed `.mpy` modules only after backing them up; otherwise MicroPython may import old source.

The first custom-platform boot verifies the root application files and creates a ROM cache. Allow startup to finish and watch serial output for the setup network or assigned LAN address. The root files remain authoritative for subsequent application updates. The boot helpers are deliberately excluded from OTA.

If maintaining an already-running application, Ctrl+C closes configured outputs but leaves the hardware watchdog armed. The default watchdog can reset the board within two minutes of its last feed; use a supervised maintenance procedure and keep valve power disconnected. See [commissioning](commissioning.md) and [recovery](ota.md) before changing boot files or disabling the watchdog.

## Connect to home Wi-Fi

1. Join the open Wi-Fi network named **`Planter-Setup-xxxx`** from a phone or laptop. There is no Planter hotspot password. If your device warns that this network has no internet, remain connected long enough to complete setup.
2. Open **http://192.168.4.1/setup** explicitly if the captive page does not appear. Use `http`, not `https`.
3. Enter your **home Wi-Fi SSID** and **home Wi-Fi password**. This password belongs to the router network, not to the setup hotspot. Preserve spelling, spaces and capitalization.
4. Save the credentials. The controller verifies storage and reboots to connect. A saved-password acknowledgement does not prove association or DHCP succeeded.
5. Rejoin your home network and open the controller's assigned address with `http://`. Find it in serial output or the router's connected-device list. The setup hotspot should close once the station connects successfully; use the LAN address afterward.

**Google mesh:** use your usual home network name even when 2.4 GHz and 5 GHz share it. The classic ESP32 uses the available 2.4 GHz radio; your phone does not need to select a separate 2.4 GHz network to submit credentials. If the hotspot stays available, inspect connection status and events rather than treating the shared name as the cause. The [troubleshooting guide](troubleshooting.md) covers retries, rescue mode, signal strength and router observations.

## Configure the controller

Keep valve supply disconnected while doing the following:

1. In **Settings**, disable **daily watering** and **moisture watering** while commissioning. Both are enabled in fresh defaults; the existing development bench board has them explicitly disabled.
2. Review valve GPIOs, driver polarity and names. Review I2C pins, ADC addresses and zone channels. Pin changes reboot the application; allow the startup grace period to finish afterward.
3. Set local timezone offset and confirm clock synchronization. Daylight saving changes need a manual offset adjustment. Daily schedules wait for one successful synchronization after boot; manual and moisture control use monotonic timing.
4. Connect sensors as described in the [hardware guide](hardware.md). With valves unpowered, verify readings and use each zone's dry/wet calibration capture. The calibration process pauses watering.
5. Set zone thresholds, wet targets, durations, mappings and cooldowns. The wet target must exceed the dry threshold. Review the hard cutoff and schedule targets before enabling automatic operation.
6. Export the runtime settings and retain a private copy. Credentials remain stored separately; import/export does not transfer the home Wi-Fi password.
7. Complete the [commissioning checklist](commissioning.md), including supervised physical closure and flow checks, before restoring valve power for normal use or leaving watering unattended.

The status page shows startup grace, clock, sensor errors, queue, watering state and networking diagnostics. Missing probes or environment sensors on a bare bench board are expected. The application does not measure actual water flow, and rain indication does not automatically stop a run.

## Install future updates or revert

Use the [update and recovery guide](ota.md). Public release downloads need no GitHub token. The laptop verifies the selected release and serves its application files over a trusted LAN HTTP mirror; the board installs only after an explicit dashboard action unless local automatic-install settings were deliberately changed.

The repository keeps the current release and at least two earlier updates as versions accumulate, with older history retained too. Keep a record of which tag worked on your own hardware. An archived release is a selectable recovery option, not proof of compatibility with every later settings format or platform image.
