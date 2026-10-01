# Notices

The code written for this project is licensed under the MIT License (see [LICENSE](LICENSE)). The material
below is not covered by that licence and stays under its own terms.

## Dolphin Orpheus Classic engine and language data

The README says:

> The Orpheus engine and its language data remain the property of Dolphin.

It describes Orpheus as the software text-to-speech synthesizer created by Dolphin (Dolphin Computer Access, UK).
The engine and its data are in `bin/orpheus/`, together with the licence files that came with them
(`bin/orpheus/lic*.txt`). Those files read "(c) 1998-2001 Dolphin Oceanic Limited. All rights reserved." or the
equivalent in the other languages; the Swedish one names Dolphin Computer Access Limited. They are left exactly
as they are. None of this is covered by the MIT License.

## Orpheus Classic NVDA add-on files and the engine host

These files in `bin/` were not written for this project and are not covered by the MIT License:

- `__init__.py`, `orpheusClassic.py`, `_onjWebUpdater.py`, `defaultDictionary.json` and `__pycache__/`: the NVDA
  driver, global plugin, web updater, default dictionary and compiled bytecode of an Orpheus Classic NVDA add-on.
  Its update checker points at onj.me.
- `orpheus-classic-host.exe`: the prebuilt 32-bit engine host (the add-on's driver starts the same host). It is not
  built from anything in this repository.

They stay under their owner's terms.

## Source files taken from other projects

Some files in this repository are copies of, or adapted from, files in two SAPI 5 projects published on GitHub by
gozaltech: [espeak-ng-sapi](https://github.com/gozaltech/espeak-ng-sapi), which is published under the GPL-3.0,
and [BstSpeech-sapi](https://github.com/gozaltech/BstSpeech-sapi), which has no licence file. They are not covered
by the MIT License of this project and stay under their author's terms.

Copied, with at most small changes:

- `src/com.hpp` and `src/com.cpp`
- `src/registry.hpp` and `src/registry.cpp`
- `src/utils.hpp`
- `src/ISpDataKeyImpl.hpp` and `src/ISpDataKeyImpl.cpp`
- `src/IEnumSpObjectTokensImpl.hpp` and `src/IEnumSpObjectTokensImpl.cpp`
- `src/voice_token.hpp` and `src/voice_token.cpp`
- `src/ISpTTSEngineImpl.hpp`

Adapted from the file of the same role in those projects:

- `src/ISpTTSEngineImpl.cpp`
- `src/sapi_main.cpp`
- `src/voice_attributes.hpp`
- `CMakeLists.txt` and `build_all.bat`
