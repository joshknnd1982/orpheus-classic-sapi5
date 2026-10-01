# Orpheus Classic SAPI5

A native Windows SAPI5 interface for the Dolphin Orpheus Classic text-to-speech
engine, making all nine Orpheus languages available to any SAPI5-compatible
application, including screen readers (NVDA, JAWS, Windows Narrator) and
reading applications (Balabolka, Bookworm).

## About Orpheus Classic

Orpheus is the software text-to-speech synthesizer created by Dolphin
(Dolphin Computer Access, UK). It shipped with Dolphin's HAL screen reader
and Supernova magnifier in the Windows 95 era, giving blind users fast,
distinctive multilingual speech at a time when most synthesizers were
hardware devices. Orpheus is long discontinued - Dolphin's modern products
moved on to other voices - but its crisp, highly responsive sound is still
loved by many long-time screen reader users.

This project revives the classic engine (often called "Orpheus Classic",
Build 18) on modern 64-bit Windows by wrapping it in Microsoft Speech API 5
interfaces, without needing SAPI4, HAL, or any Dolphin product installed.

The Orpheus engine and its language data remain the property of Dolphin.
This repository contains only the open-source wrapper; the engine files are
installed by the release installer.

## Voices

Each language appears as its own SAPI5 voice, plus a user-configurable voice:

- Orpheus US English
- Orpheus UK English
- Orpheus Dutch
- Orpheus French
- Orpheus Castilian Spanish
- Orpheus Italian
- Orpheus Swedish
- Orpheus German
- Orpheus Latin American Spanish
- **Orpheus Custom Voice** - fully configurable via the bundled configuration
  utility

## Features

- **32-bit and 64-bit SAPI5 interfaces.** Both talk to the 32-bit Orpheus
  engine host (`orpheus-classic-host.exe`) over a local socket, so the engine
  works identically from any application architecture.
- **No SAPI4, no registry configuration.** The engine host drives the Orpheus
  DLLs directly and seeds the few values the engine expects on its own.
- **Configuration utility** (`OrpheusClassicConfig.exe`) exposing every engine
  parameter: language, rate, pitch, volume, intonation (prosody), voicing,
  voice source, word pause and phrase pause. Changes are saved automatically
  to `%APPDATA%\OrpheusClassicSAPI\settings.ini` and apply to the Orpheus
  Custom Voice immediately - even mid-session under a running screen reader.
  Every control is labelled and keyboard accessible.
- **Built for screen reader responsiveness.** Audio streams to SAPI while the
  engine renders, cancellation is detected within milliseconds, and a pool of
  pre-warmed engine hosts means interrupting speech - even rapid
  character-by-character arrowing - never waits for the engine to wind down.
- **Detailed logs** for the installer and both SAPI interfaces under
  `%APPDATA%\OrpheusClassicSAPI\logs` (and `install.log` in the application
  folder). Logging can be switched off in the configuration utility.

## Download

Download the installer from the [Releases](../../releases) page. It installs
the complete engine (all languages and data files), both SAPI5 interfaces,
the configuration utility and a desktop shortcut. The installer wizard uses
only standard pages, which are screen-reader accessible.

## Building from Source

```batch
build_all.bat
```

**Requirements:**
- Windows 10 or later
- Visual Studio 2022 (or Build Tools) with the C++ workload
- CMake 3.15+
- Inno Setup 6

The Orpheus engine binaries and language data are not part of this
repository. To build the installer, place the engine host
(`orpheus-classic-host.exe`) and the engine data tree (`orpheus\`) in `bin\`
- for example from an existing installation of this project.

## How it works

`orpheus-classic-host.exe` (32-bit) loads the Orpheus engine DLLs (`a3s.dll`,
`sam\Dolosam.dll`) and exposes a small framed TCP protocol on a loopback
socket. The SAPI5 DLL spawns the host on demand, sends text with inline
`@<parameter=value>` commands, and streams the rendered 22.05 kHz PCM back to
SAPI as it is produced.

Because the engine's mute command can take seconds to settle mid-render while
a fresh host process is ready in about 50 milliseconds, cancellation never
waits: a pre-warmed standby host takes over instantly, and interrupted short
renders (single characters, words) are parked, allowed to finish quietly, and
reused. The "Orpheus Custom Voice" re-reads `settings.ini` on every
utterance, which is what makes configuration changes take effect immediately.

## License

The SAPI5 wrapper is licensed under the MIT License - see [LICENSE](LICENSE). The
Orpheus Classic engine and its language data remain the property of their
respective owners. They, the NVDA add-on files in `bin/` and a few source files
taken from other projects are not covered by that licence; see
[NOTICE.md](NOTICE.md).
