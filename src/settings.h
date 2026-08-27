#pragma once

// Persistent settings for the "Orpheus Custom Voice" and for diagnostics.
// Stored as a plain INI file under %APPDATA%\OrpheusClassicSAPI so both the
// 32-bit and the 64-bit SAPI interfaces and the configuration utility share
// one copy without touching the registry.

#include <windows.h>
#include <string>

namespace Orpheus {
namespace settings {

struct CustomVoice {
    int language = 44;      // engine country code
    int rate = 110;         // 40..700
    int pitch = 110;        // 50..500
    int volume = 100;       // 0..100
    int intonation = 50;    // 0..100 ("Prosody")
    int voicing = 65;       // 0..100
    int voice_source = 0;   // 0 or 1
    int word_pause = 0;     // 0..1000 ms
    int phrase_pause = 250; // 0..2000 ms
};

CustomVoice clamp(const CustomVoice& value);

// %APPDATA%\OrpheusClassicSAPI (created on demand); empty string on failure.
std::wstring settings_dir();
std::wstring settings_path();

CustomVoice load_custom_voice();
bool save_custom_voice(const CustomVoice& value);

bool logging_enabled();
bool set_logging_enabled(bool enabled);

// True when the settings file changed since the caller's last check.
// `last_write` is caller-owned state, zero-initialised before the first call.
bool changed_since(FILETIME& last_write);

}
}
