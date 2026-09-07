# Runtime and third-party notices

These files retain the licenses and copyright notices distributed with the MicroPython and ESP-IDF sources used for this firmware, including the Espressif Wi-Fi, PHY, and coexistence binary libraries. The SDK license collection also includes optional SDK components whose archive objects may be discarded by the linker. Bluetooth and wired Ethernet are disabled in this build.

The runtime modifications follow MicroPython's MIT license. Their complete source is supplied in `tools/firmware/micropython.patch`, the board generator, and the portable build helper. MicroPython and ESP-IDF source repository addresses and exact revisions are in `firmware/manifest.json`; exact submodule revisions and the component lockfile are in `tools/firmware/`.

`source-notices.txt` files preserve copyright and licensing comments from bundled Berkeley DB, ooFatFs, and littlefs source files. The littlefs BSD-3-Clause license text is from its upstream `v2.11.2` release; the copyright comments in MicroPython's bundled sources are retained separately. GCC's runtime library exception and GPLv3 text accompany the compiled runtime support library. Portable compiler/build-tool executables are downloaded during setup and are not included in the release artifacts.
