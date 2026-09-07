# Documentation

Start with the [project README](../README.md) for the system overview, then
follow [Getting started](getting-started.md) to install and configure a controller.
The supplied native firmware targets the classic ESP32 with 4 MB flash; check
the hardware guide before using another ESP32 variant.

Documents on `main` evolve as the project is maintained. Each tagged release
and its package preserve the documentation at that tag; an already published
immutable archive is not replaced when a guide changes. Consult the current
guides and the selected release's compatibility notes before installation.

## Find the right guide

| What you want to do | Read this |
| --- | --- |
| Install the firmware, connect Wi-Fi and configure the first garden | [Getting started](getting-started.md) |
| Choose pins, wire valves and connect moisture/environment sensors | [Hardware and board configuration](hardware.md) |
| Resolve setup, connection, settings, sensor or restart problems | [Troubleshooting](troubleshooting.md) |
| Verify the actual valves, wiring and network before unattended use | [Commissioning checklist](commissioning.md) |
| Install an application update or select a previous compatible release | [Updates, retention and recovery](ota.md) |
| Check version changes and upgrade compatibility | [Release notes](releases.md) |
| Understand which original functions are implemented | [Feature parity](feature-parity.md) |
| Understand timing, persistence, networking and recovery design | [Architecture and reliability](architecture.md) |
| Integrate another client or inspect JSON endpoints | [HTTP API](api.md) |
| See measured results and checks still outstanding | [Validation report](validation.md) |

## Firmware and development references

| Reference | Contents |
| --- | --- |
| [Native firmware](../firmware/README.md) | Supported board, flash layout, supplied images and ROM cache behavior |
| [Native firmware manifest](../firmware/manifest.json) | Exact revisions, artifact sizes and hashes |
| [Native build instructions](../tools/firmware/README.md) | Toolchain setup and reproducible platform build |
| [Application configuration example](../src/config.example.py) | First-boot defaults, local watchdog/cutoff settings and update mirror configuration |
| [Automated tests](../tests) | Controller, persistence, networking, update and dashboard regressions |
| [Third-party firmware licenses](../firmware/licenses/README.md) | Notices for the runtime and bundled components |

The platform image, application files and garden settings are separate parts
of the installation. Application OTA does not replace the native image or USB
boot helpers, and it preserves the saved garden configuration and Wi-Fi
credentials. See the update guide before selecting an older release.

## Before connecting water

Fresh default settings enable scheduled and moisture-triggered watering.
Keep valve power and the water supply disconnected while reviewing pins,
polarity, schedules and automation settings. The development bench board had
automation disabled for testing; that is not the fresh-install default.

Software tests and a functioning dashboard do not demonstrate physical valve
closure. Use the [commissioning checklist](commissioning.md), and consult the
[validation report](validation.md) for the distinction between host tests,
board observations and remaining electrical/network checks.

For a problem report, start with the
[diagnostic checklist](troubleshooting.md#what-to-include-in-a-problem-report).
Do not attach credential files to a public issue.
