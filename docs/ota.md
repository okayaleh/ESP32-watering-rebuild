# Firmware updates and recovery

From **2.0.0-rebuild.5**, the controller can check and install application
releases directly from GitHub over verified HTTPS. It needs home Wi-Fi with
internet access, a synchronized clock and reliable power. **No laptop,
USB connection, GitHub account or token is needed during normal updates.**
The setup hotspot alone does not provide internet access.

The native MicroPython image and USB boot helpers remain separate. Install
this rebuild over USB first; the original project's updater cannot migrate
it. Back up device files and export settings before initial installation or
recovery. An existing .2/.3 rebuild needs the one-time upgrade below.

## Normal standalone updates

Open **Maintenance → Firmware updates** in the dashboard. The card shows the
installed and selected versions, source, last check/install, automatic mode
and any failure. Choose **Check for updates** to check the newest release.
A manual check does not install it: review the selected version, then choose
**Install update** and keep the controller powered through its restart.

Fresh .5 configurations enable an automatic check and installation at
**04:00 controller local time**, using the fixed UTC offset in watering
settings. The controller catches up after boot or reconnection and retries
failed checks after five minutes, then every thirty minutes if failures
continue. It waits at least sixty seconds after startup, for synchronized
time and for idle watering before
starting, then pauses new watering during the update. Automatic installation
only follows an automatic check; a manually selected release or uploaded
bundle waits for the explicit Install action.

A Wi-Fi or download failure leaves the running installation available. The
controller reports the error and can retry later. Do not repeatedly restart
the board to force a check. If a firmware version fails its boot trial, that
version is excluded from later automatic and manual installation; publish a
new corrected version. See [troubleshooting](troubleshooting.md#firmware-updates).

### One-time upgrade from .2 or .3

Those versions only understand a LAN HTTP mirror. Install .5 once using its
verified release files over USB, or use the optional mirror procedure below
with the .5 release. No native platform reflash or erase is required for a
controller already running the supplied .2/.3 platform.

OTA preserves local `config.py`, so save these values to the board over USB
and restart if they differ from the desired settings:

```python
UPDATE_GITHUB_REPO = "okayaleh/ESP32-watering-rebuild"
UPDATE_BASE_URL = ""  # Empty selects direct GitHub updates.
UPDATE_CHECK_HOUR = 4
UPDATE_AUTO_INSTALL = True
UPDATE_TIMEOUT_SEC = 120
```

Set `UPDATE_AUTO_INSTALL = False` to keep manual installation. An existing
`False` value is preserved by an application-only update. A nonempty
`UPDATE_BASE_URL` continues to select the optional LAN mirror. Editing a
laptop copy does not change a running board; save the on-board file and reboot.
Keep valve supply power disconnected during USB maintenance. Garden settings,
schedules and Wi-Fi credentials are separate and remain preserved.

## Select a previous application version

After a successful GitHub check, **Choose a retained release** offers other
compatible channel versions, up to two prior releases alongside the current
release. Select one previously tested on your controller, use **Check selected
release**, verify the displayed version and choose **Install update**.
Checking a selection does not install it or change watering configuration.

A deliberate older-version installation pauses automatic updates so the
controller stays on that release. To resume, check and install the latest
channel release. The dashboard reports the hold. Update policy is retained
separately from application files in `.ota-policy.json`.

The standalone channel starts at .5. Older .2/.3 archives remain available
for supervised USB or mirror restoration, but their manifests do not declare
the platform compatibility required by the standalone channel and they do not
appear in its selector. Installing either removes standalone updating until
.5 or later is installed again. Fewer dashboard choices appear until more
compatible releases have actually been published.

A previous application must remain compatible with saved settings, the
native platform and boot helpers. Reverting application files does not revert
settings or remove files absent from an older manifest. The board's automatic
recovery keeps one previous set of changed files; repository retention
provides the additional selectable versions. A passed boot trial verifies
startup of the safety loop, not long-term field reliability.

## Publish and select a GitHub release

The public repository is `okayaleh/ESP32-watering-rebuild`. Its `v*` tag
workflow builds the tagged application, runs host checks and publishes
`planter-<version>.zip` plus `.sha256`. Tag application `2.0.0-rebuild.5` as
`v2.0.0-rebuild.5`. Hyphenated versions are prereleases; this project's update
channel intentionally includes the published rebuild prereleases.

The workflow also publishes the small `channel.json` document at the root of
the `updates` branch. The board reads it from `raw.githubusercontent.com` and
selects the newest compatible entry or an explicitly requested retained
version. Each entry points to a full immutable commit SHA containing the
manifest and flat application files. The board does not download a ZIP,
follow release-asset redirects, or query the GitHub API.

The channel holds at most three compatible entries: current and the previous
two. Older GitHub releases, tags and assets remain retained; there is no
automatic archive pruning. Published release tags and assets are immutable.
Upload assets to a draft before publishing, never replace an existing release
or reuse its tag, and keep channel publication ordered after validation.
The packaged native image is for USB maintenance; publication does not
rebuild or remotely flash that image.

For development or archive recovery, list the newest three published ZIPs:

```powershell
python tools/sync_updates.py --repo okayaleh/ESP32-watering-rebuild --list
```

This list includes older mirror-compatible archives as well as standalone
releases. It orders eligible packages by publication date and skips drafts
and incomplete assets. Listing metadata is not payload verification; the
download command below verifies the archive and application files.

## Optional LAN mirror and archived releases

The mirror remains useful for a local build, restricted internet access or
restoring a release older than .5. It requires a running reachable computer
through installation; the controller need not be attached to that computer
by USB. Public downloads need no GitHub token.

```powershell
$releaseRoot = python tools/sync_updates.py --repo okayaleh/ESP32-watering-rebuild --tag v2.0.0-rebuild.5
if ($LASTEXITCODE -ne 0) { throw "Release download or verification failed" }
python tools/mirror.py --directory "$releaseRoot" --port 8000
```

The helper checks the archive SHA-256 and each manifest-listed file's size
and hash, then writes an immutable, hash-addressed local cache. Only allowed
application artifacts are served, excluding configuration, credentials, boot
helpers and the native platform image. Finish any active update and stop the
mirror before starting one pinned to a different release directory.

For an unpublished local build, use:

```powershell
python tools/build.py --version 2.0.0-rebuild.5
python tools/mirror.py --port 8000
```

Set the board's local options to the computer's trusted LAN address and
restart after saving:

```python
UPDATE_BASE_URL = "http://192.168.1.50:8000"
UPDATE_MANIFEST_PATH = "build/manifest.json"
UPDATE_AUTO_INSTALL = False
```

Allow the port through the computer's private-network firewall. Check the
release in the dashboard and install while idle. Choose an explicit older
`--tag` for an archived recovery version; any retained compatible tag can be
downloaded, including one older than the three listed choices. Clear
`UPDATE_BASE_URL` and restart to restore direct GitHub mode on .5 or later.

## What installation does

Each manifest entry provides its own repository path, exact size and SHA-256.
Unchanged files are skipped. The updater downloads into `.ota/`, verifies
streaming hashes, then reads each staged file back to verify its hash again.
Uploads through the dashboard use the same staging and installation path;
uploading alone does not activate the file. Select **Install staged files**
after all desired files finish uploading.

The supervisor pauses watering for installation. The updater requests valve
closure and checks that all watering is idle before it prepares backups and
before each file replacement. It copies the previous files before writing a
durable commit journal. The journal also records newly added files and
deletions, so recovery can remove additions and restore deletions. Older `.py`
siblings of manifest `.mpy` files are removed transactionally, including when
the `.mpy` file itself is unchanged.

The updated device reboots. With an intact filesystem and the persistence assumptions below, a commit interrupted by a power loss is restored
on the next boot before application code runs. A completed installation gets
up to three boots to survive 60 seconds of successful safety-loop operation;
otherwise the boot guard restores the previous files. Interrupted rollback
can run again because it copies from its backups without consuming them.
Once the trial passes, a stable journal remains as a tombstone; a later update
cleans old staging and backups incrementally.

On the custom classic ESP32 platform, the immutable ROM helper runs after
recovery. It compares the authoritative root module files with the cached ROM
image, streams changed code into the inactive ROM bank, verifies it, and only
then selects that bank. Thus normal manifests and individual file uploads
retain their existing behavior. Interrupted ROM construction leaves the prior
bank and root files intact; the next boot rebuilds from the recovered files.
An unchanged boot hashes/verifies its cache without erasing either bank.

`boot.py` closes configured valve pins before recovery, including active-low
valves. It reads `settings.json.prev` if the main settings file cannot be
parsed. The normal `main.py` entrypoint independently closes the pins too.
Both boot helpers are intentionally immutable through OTA and uploads: update
them manually over USB when a future rebuild explicitly requires that change.
Replacing the sole recovery entrypoint cannot itself be made crash-safe with
this flat filesystem design.

## Limits and assumptions

- Direct GitHub transfers use HTTPS to the configured repository on
  `raw.githubusercontent.com`. TLS requires certificate-chain, hostname and
  date validation against the bundled trusted roots; checks wait for clock
  synchronization. A verification failure is an error, never a fallback to
  unverified TLS. The optional mirror uses plain HTTP. Both transports require
  HTTP 200 and one valid `Content-Length`; redirects, chunked responses and
  unbounded lengths are rejected.
- Sockets remain nonblocking, with at most 512 bytes sent or received per
  polling step and an absolute deadline per transfer. Network code never feeds
  the watchdog. Hardware/driver stalls remain subject to the watchdog and
  physical valve wiring; a mocked host test does not prove electrical safety.
- A manifest is limited to 16 KiB, 48 entries, 512 KiB per file and 2 MiB of
  total new payload. The filesystem needs room for staged changes **and** copies
  of the files they replace. An out-of-space error before committing leaves
  the live files unchanged. Do not fill the filesystem with unrelated files.
- Settings, WiFi credentials, watering state, the local config, boot helpers and ROM marker
  cannot be updated or deleted through this mechanism. Other JSON files are
  also rejected except `version.json`.
- The application ROM cache has 256 KiB per bank; it contains application
  `.py`/`.mpy` modules, excluding boot and configuration files. Oversized code
  refuses startup and a pending update rolls back through the boot guard.
  Updating the MicroPython platform image or partition layout requires USB;
  the dashboard updates application files only.
- Only portable `.mpy` major version 6 bytecode with at most 31-bit small
  integers is accepted. Native/Viper architecture-specific bundles are
  rejected. This build's artifacts are compiled without native emitters;
  use a compatible MicroPython firmware and compiler. The minor version is
  relevant to native code; see the official
  [MicroPython bytecode compatibility documentation](https://docs.micropython.org/en/latest/reference/mpyfiles.html).
- GitHub TLS authenticates the download host, and immutable commit paths plus
  SHA-256/size checks bind files to the selected manifest. The manifest also
  declares the required platform and native-image hash. This trusts the
  configured GitHub repository and certificate authorities; signed manifests
  are not implemented. A malicious plain-HTTP mirror or LAN attacker could
  alter both a mirror manifest and its files. Keep the dashboard and any
  optional mirror on a trusted network without port forwarding.
- Recovery assumes a functioning flash filesystem with atomic rename and
  working `os.sync()` semantics. It does not repair physical flash damage,
  power-rail failures, a welded valve or a failed MOSFET. Hardware bias that keeps each driver inactive,
  normally closed valves and flyback diodes remain necessary.

Host tests exercise real local HTTP transfers using the generated manifest,
partial sends, transfer deadlines, invalid bytecode, protected files, staged
hash failures, interrupted commits, interrupted rollback and boot trials.
Commissioning must still include power-cut and hung-driver tests on the actual
ESP32, with water disconnected during initial safety checks.
