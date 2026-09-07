# Rebuilding the custom ESP32 runtime on Windows

Run these commands from the project root in PowerShell. Setup needs an existing Python 3.10 or newer, internet access, and several GB of free disk space for ESP-IDF and its submodules. The build uses its own Python 3.12.10, Git, CMake, Ninja, and Espressif compiler under `.tools/firmware-build`. It does not install Windows services, require administrator access or WSL, or change global environment variables.

```powershell
python tools/firmware/setup_windows.py
& ./.tools/firmware-build/python/python.exe tools/firmware/build_firmware.py
& ./.tools/firmware-build/python/python.exe tools/firmware/publish_firmware.py
```

The setup and build commands were exercised from this published script location. The build log and intermediate ELF remain in `.tools/firmware-build`. The publishing command copies only the firmware, small provenance files, and license notices into the release folders. It replaces the published firmware artifacts and updates their hashes; review and hardware-test a rebuilt image before using it for deployment.

Set `PLANTER_FIRMWARE_BUILD_ROOT` to an absolute directory to place the isolated cache elsewhere. Keep the source checkout path reasonably short: Windows path limits can still affect SDK tools even though Git long-path support and GCC response files are enabled.

## Pinned sources and tools

| Component | Revision |
| --- | --- |
| MicroPython 1.28.0 | `e0e9fbb17ed6fd06bb76e266ae554784c9c80804` |
| ESP-IDF 5.5.1 | `fcae32885b0296b32044cb99ecbdc50d98dddb83` |
| Xtensa toolchain | `esp-14.2.0_20241119` |
| Python | `3.12.10` embedded, Windows x64 |
| CMake | `3.30.2` |
| Ninja | `1.12.1` |
| Git for Windows | `2.55.0.windows.5` MinGit |
| mpy-cross | `1.28.0.post2` |

`bootstrap.py` verifies all five portable tool archives against pinned SHA-256 values. `tool-sources.json` contains their official download URLs. Python packages are pinned in `requirements-lock.txt`, with Espressif's constraints snapshot alongside it. `components.lock` and the two `*-submodules.txt` files record exact dependencies. Source checkout revisions are checked before patches are applied. This is a repeatable build recipe; embedded timestamps and source path metadata can change binary hashes between builds.

## Changes from upstream

`prepare_firmware.py` applies the native changes with exact context checks and creates the external `PLANTER_ESP32` board definition. `micropython.patch` contains the resulting tracked source diff. The generated board header and CMake file are also retained under `board/`. `sdkconfig.reference` is the complete configuration of the delivered build; its workspace-specific partition filename is regenerated for a new checkout.

The native changes enable `MICROPY_VFS_ROM` and `MICROPY_VFS_ROM_IOCTL`, add two independent memory-mapped ROMFS partition objects, and preserve `32 * 1024 + 64` bytes of contiguous native allocation headroom when the GC expands. The board disables Bluetooth and wired Ethernet and selects the partition table shipped in `firmware/partitions.csv`.

`prepare_windows.py` and `portable_qstr.py` replace MicroPython's POSIX `cat`/`sed`/`touch` preprocessing commands with Python and CMake, and put long GCC argument lists into response files. This avoids cmd.exe's command-length limit. These changes affect build orchestration, not runtime behavior. `sitecustomize.py` gives the isolated embedded Python the script and working-directory imports expected by upstream build tools. All environment settings are local to the build subprocess.

ESP-IDF may print a warning about missing `ESP_ROM_ELF_DIR` when preparing optional debugger initialization files. Those separate debugger symbol assets are not needed to compile or flash this runtime. The build must still finish successfully and esptool must validate the application image.

## Artifact verification

```powershell
& ./.tools/firmware-build/python/python.exe -m esptool image_info firmware/planter-esp32-1.28.0-romfs.app-bin
& ./.tools/firmware-build/python/python.exe .tools/firmware-build/esp-idf/components/partition_table/gen_esp32part.py .tools/firmware-build/build/partition_table/partition-table.bin
Get-FileHash firmware/planter-esp32-1.28.0-romfs.bin -Algorithm SHA256
```

The combined image is flashed at `0x1000`. The app-only image is flashed at `0x10000` and does not install the required partition layout. These scripts do not access a serial port or flash a board.
