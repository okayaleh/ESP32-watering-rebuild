# Firmware updates and recovery

Commission this rebuild over USB first, including `boot.py`, `romboot.py` and
the correct platform image. The old
project's updater and recovery marker are not a migration mechanism for this
replacement. Export settings and keep a copy of the old device files before
commissioning.

## Publish and select a GitHub release

The public update repository is `okayaleh/ESP32-watering-rebuild`. Its
`v*` tag workflow builds the tagged application with the pinned compiler,
runs host checks, and publishes `planter-<version>.zip` plus its `.sha256`
file. For example, tag version `2.0.0-rebuild.2` as `v2.0.0-rebuild.2`.
Tags whose version contains a hyphen are published as prereleases. The ZIP
also includes the existing platform image for supervised USB installation;
this workflow does not rebuild or install that platform image.

Release retention keeps the current update and at least two previous updates
once those versions have been published. Older releases, tags, assets and
downloaded caches are retained too; there is no automatic deletion.
The workflow uploads assets to a draft before publishing the release. A
failed upload leaves the draft for review; it does not replace existing
release assets. Publish a new version tag for each update rather than reusing
a published tag.

List the newest published update and two previous available choices:

```powershell
python tools/sync_updates.py --repo okayaleh/ESP32-watering-rebuild --list
```

The list includes clearly marked prereleases and skips drafts, unrelated
releases and incomplete package assets. It orders eligible releases by
publication date. Listing checks release metadata; downloading performs the
archive and payload hash verification. Availability does not mean a version
was tested successfully on your controller. Fewer choices appear until three
eligible updates have been published.

GitHub serves releases over HTTPS. This controller's updater requires a
trusted LAN HTTP mirror, so the laptop downloads and verifies a selected
release first. From the repository directory:

```powershell
$releaseRoot = python tools/sync_updates.py --repo okayaleh/ESP32-watering-rebuild --tag v2.0.0-rebuild.2
if ($LASTEXITCODE -ne 0) { throw "Release download or verification failed" }
python tools/mirror.py --directory "$releaseRoot" --port 8000
```

The sync command requires an explicit tag, including for prereleases. It
checks the archive SHA-256 and every OTA file's manifest size and hash, then
creates `.tools/updates/<owner>/<repository>/<tag>/<archive-sha256>/build/`.
Only the manifest and allowed OTA artifacts enter this cache. Local config,
credentials, boot helpers and the native platform image are never mirrored.
No GitHub token is needed to download public release assets.

Each mirror stays pinned to the directory passed at startup. Finish any
controller update and stop that mirror before starting one for a different
release. Downloading another release preserves existing cache directories;
a changed or damaged existing cache is rejected rather than overwritten.
Use the same laptop HTTP URL below for both local builds and downloaded
releases. Direct GitHub URLs cannot be used as `UPDATE_BASE_URL`.

## Select a previous application version

1. Run the `--list` command above and choose a tag you previously tested
   successfully on this controller. Check that release's platform and settings
   compatibility before reverting.
2. Finish any active update and stop the laptop mirror. Run the download
   command above with the selected older `--tag`, then start the mirror using
   its returned directory. Any retained tag can be selected, including tags
   older than the three displayed choices.
3. In the dashboard, use **Check for update**, confirm the displayed version
   matches the chosen tag, then install while watering is idle. Verify the
   controller after it restarts.

This selects older application files; it does not restore older settings or
replace the native platform/USB boot helpers. Files introduced by a newer
version but absent from an older manifest also remain on the device. Choose
an application release compatible with those files and the saved settings;
native firmware, boot-helper or incompatible settings-schema changes require
an explicit migration or USB recovery procedure. Repository retention is separate
from the board's automatic recovery: the board keeps one previous installation
for its interrupted-update and failed-boot recovery mechanism. The laptop and
repository retain the additional selectable versions.

## Serve this build on your LAN

From the repository directory, build and start the restricted mirror:

```powershell
python tools/build.py --version 2.0.0-rebuild.2
python tools/mirror.py --port 8000
```

The mirror serves only `build/manifest.json` and the artifacts listed in it.
It does not serve `config.py`, WiFi credentials, arbitrary repository files or
the immutable boot guard. Set these first-boot/local configuration values on
the ESP32, replacing the example IP with the computer's LAN address:

```python
UPDATE_BASE_URL = "http://192.168.1.50:8000"
UPDATE_MANIFEST_PATH = "build/manifest.json"
UPDATE_AUTO_INSTALL = False
```

Allow the mirror port through the computer's firewall for the private LAN.
Keep the computer and mirror running until installation completes. A hostname
also works through the controller's asynchronous DNS resolver. The URL must
point to this rebuild's artifacts, not the original repository's manifest.

Use **Check for update** in the dashboard, review the version and file list,
then install while watering is idle. Daily checks use `UPDATE_CHECK_HOUR`;
automatic installation remains off by default. The installed version comes
from `version.json`. Check timestamps are held in RAM; the last installation
timestamp is retained in the recovery journal. Timestamps require a correct
device clock to be meaningful.

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

- Plain HTTP is required. Responses must be HTTP 200 with one valid
  `Content-Length`. HTTPS, redirects, chunked responses and unbounded lengths
  are rejected. The supplied mirror returns the required headers.
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
- SHA-256 proves the transfer matches its manifest; it does not authenticate
  the publisher. A malicious HTTP mirror or LAN attacker could change both.
  Keep the dashboard and mirror on a trusted network and do not port-forward
  them. Signed release manifests are not implemented.
- Recovery assumes a functioning flash filesystem with atomic rename and
  working `os.sync()` semantics. It does not repair physical flash damage,
  power-rail failures, a welded valve or a failed MOSFET. Hardware bias that keeps each driver inactive,
  normally closed valves and flyback diodes remain necessary.

Host tests exercise real local HTTP transfers using the generated manifest,
partial sends, transfer deadlines, invalid bytecode, protected files, staged
hash failures, interrupted commits, interrupted rollback and boot trials.
Commissioning must still include power-cut and hung-driver tests on the actual
ESP32, with water disconnected during initial safety checks.
