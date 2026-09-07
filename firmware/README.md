# Planter ESP32 firmware

This custom MicroPython 1.28.0 runtime is for a classic ESP32 with 4 MB flash. It enables the watering application's ROM filesystem and leaves native memory available for Wi-Fi and sockets. The watering application and configuration are installed separately from `build/` using the project deployment instructions.

| Artifact | Flash address | Contents |
| --- | --- | --- |
| `planter-esp32-1.28.0-romfs.bin` | `0x1000` | Bootloader, partition table, and runtime |
| `planter-esp32-1.28.0-romfs.app-bin` | `0x10000` | Runtime only; requires this partition table already installed |

`SHA256SUMS` and `manifest.json` identify the delivered bytes. The combined image changes the filesystem partition layout. An existing stock MicroPython filesystem must be backed up and its application files reinstalled after the new partition table is flashed. Application OTA continues to update files; it does not replace this native firmware.

The factory application stays at `0x10000`, size `0x1f0000`. The two 256 KB ROMFS banks are at `0x200000` and `0x240000`. The 1.5 MB writable filesystem starts at `0x280000`. NVS and PHY partition locations remain unchanged. See `partitions.csv` for the exact table.

The immutable `romboot.py` helper rebuilds the inactive ROMFS bank from application files after OTA processing, verifies it, then mounts the selected bank at `/rom`. Modules loaded from ROMFS keep their bytecode in flash, reducing Python heap use. `vfs.rom_ioctl(1)` returns two banks; `vfs.rom_ioctl(2, index)` returns their native partition objects, which support the read-only buffer protocol required by `VfsRom`.

The GC split allocator subtracts 32 KB plus 64 bytes of allocator allowance from the largest available native block before allowing Python heap expansion. This prevents that expansion from consuming the entire final native block. Wi-Fi and other native allocations still share that memory; it is not a separate permanently reserved hardware region. Bluetooth and wired Ethernet are disabled. Wi-Fi, sockets, TLS, GPIO, I2C, watchdog, and normal ESP32 APIs remain available.

The binary was compiled with ESP-IDF 5.5.1 and the official Espressif Xtensa GCC 14.2.0 toolchain. The application image passes esptool checksum and SHA-256 validation and fits in the factory partition. The official ESP-IDF partition parser validates the table. The ELF contains both `VfsRom` and `rom_ioctl`, and disassembly confirms the allocator allowance is `0x8040` bytes. Hardware acceptance results are recorded in the main project's test report.

Exact upstream revisions, tool archive hashes, component versions, patches, and the native Windows rebuild recipe are in [`tools/firmware`](../tools/firmware/README.md). Upstream runtime and library licenses are retained in [`licenses`](licenses/README.md).
