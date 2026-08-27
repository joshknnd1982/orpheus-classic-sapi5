#include "settings.h"

#include <shlobj.h>
#include <cstdio>

namespace Orpheus {
namespace settings {

namespace {

constexpr wchar_t SECTION_CUSTOM[] = L"custom";
constexpr wchar_t SECTION_DIAG[] = L"diagnostics";

int clamp_int(int value, int min_value, int max_value)
{
    if (value < min_value) return min_value;
    if (value > max_value) return max_value;
    return value;
}

bool is_valid_language(int country)
{
    switch (country) {
    case 1: case 31: case 33: case 34: case 39:
    case 44: case 46: case 49: case 52:
        return true;
    default:
        return false;
    }
}

bool write_int(const wchar_t* section, const wchar_t* name, int value, const std::wstring& path)
{
    wchar_t buffer[32];
    swprintf_s(buffer, L"%d", value);
    return WritePrivateProfileStringW(section, name, buffer, path.c_str()) != FALSE;
}

}

CustomVoice clamp(const CustomVoice& value)
{
    CustomVoice result = value;
    if (!is_valid_language(result.language)) {
        result.language = 44;
    }
    result.rate = clamp_int(result.rate, 40, 700);
    result.pitch = clamp_int(result.pitch, 50, 500);
    result.volume = clamp_int(result.volume, 0, 100);
    result.intonation = clamp_int(result.intonation, 0, 100);
    result.voicing = clamp_int(result.voicing, 0, 100);
    result.voice_source = clamp_int(result.voice_source, 0, 1);
    result.word_pause = clamp_int(result.word_pause, 0, 1000);
    result.phrase_pause = clamp_int(result.phrase_pause, 0, 2000);
    return result;
}

std::wstring settings_dir()
{
    wchar_t appdata[MAX_PATH] = {};
    if (FAILED(SHGetFolderPathW(nullptr, CSIDL_APPDATA, nullptr, SHGFP_TYPE_CURRENT, appdata))) {
        return std::wstring();
    }
    std::wstring dir = std::wstring(appdata) + L"\\OrpheusClassicSAPI";
    CreateDirectoryW(dir.c_str(), nullptr);
    return dir;
}

std::wstring settings_path()
{
    const std::wstring dir = settings_dir();
    if (dir.empty()) {
        return std::wstring();
    }
    return dir + L"\\settings.ini";
}

CustomVoice load_custom_voice()
{
    CustomVoice defaults;
    const std::wstring path = settings_path();
    if (path.empty()) {
        return defaults;
    }
    CustomVoice value;
    value.language = static_cast<int>(GetPrivateProfileIntW(SECTION_CUSTOM, L"language", defaults.language, path.c_str()));
    value.rate = static_cast<int>(GetPrivateProfileIntW(SECTION_CUSTOM, L"rate", defaults.rate, path.c_str()));
    value.pitch = static_cast<int>(GetPrivateProfileIntW(SECTION_CUSTOM, L"pitch", defaults.pitch, path.c_str()));
    value.volume = static_cast<int>(GetPrivateProfileIntW(SECTION_CUSTOM, L"volume", defaults.volume, path.c_str()));
    value.intonation = static_cast<int>(GetPrivateProfileIntW(SECTION_CUSTOM, L"intonation", defaults.intonation, path.c_str()));
    value.voicing = static_cast<int>(GetPrivateProfileIntW(SECTION_CUSTOM, L"voicing", defaults.voicing, path.c_str()));
    value.voice_source = static_cast<int>(GetPrivateProfileIntW(SECTION_CUSTOM, L"voiceSource", defaults.voice_source, path.c_str()));
    value.word_pause = static_cast<int>(GetPrivateProfileIntW(SECTION_CUSTOM, L"wordPause", defaults.word_pause, path.c_str()));
    value.phrase_pause = static_cast<int>(GetPrivateProfileIntW(SECTION_CUSTOM, L"phrasePause", defaults.phrase_pause, path.c_str()));
    return clamp(value);
}

bool save_custom_voice(const CustomVoice& raw)
{
    const std::wstring path = settings_path();
    if (path.empty()) {
        return false;
    }
    const CustomVoice value = clamp(raw);
    bool ok = true;
    ok &= write_int(SECTION_CUSTOM, L"language", value.language, path);
    ok &= write_int(SECTION_CUSTOM, L"rate", value.rate, path);
    ok &= write_int(SECTION_CUSTOM, L"pitch", value.pitch, path);
    ok &= write_int(SECTION_CUSTOM, L"volume", value.volume, path);
    ok &= write_int(SECTION_CUSTOM, L"intonation", value.intonation, path);
    ok &= write_int(SECTION_CUSTOM, L"voicing", value.voicing, path);
    ok &= write_int(SECTION_CUSTOM, L"voiceSource", value.voice_source, path);
    ok &= write_int(SECTION_CUSTOM, L"wordPause", value.word_pause, path);
    ok &= write_int(SECTION_CUSTOM, L"phrasePause", value.phrase_pause, path);
    return ok;
}

bool logging_enabled()
{
    const std::wstring path = settings_path();
    if (path.empty()) {
        return true;
    }
    return GetPrivateProfileIntW(SECTION_DIAG, L"logging", 1, path.c_str()) != 0;
}

bool set_logging_enabled(bool enabled)
{
    const std::wstring path = settings_path();
    if (path.empty()) {
        return false;
    }
    return write_int(SECTION_DIAG, L"logging", enabled ? 1 : 0, path);
}

bool changed_since(FILETIME& last_write)
{
    const std::wstring path = settings_path();
    WIN32_FILE_ATTRIBUTE_DATA data = {};
    if (path.empty() ||
        !GetFileAttributesExW(path.c_str(), GetFileExInfoStandard, &data)) {
        // Missing file counts as "changed" once, so defaults get applied.
        const bool first = (last_write.dwLowDateTime == 0 && last_write.dwHighDateTime == 0);
        last_write.dwLowDateTime = 1;
        last_write.dwHighDateTime = 0;
        return first;
    }
    if (CompareFileTime(&data.ftLastWriteTime, &last_write) != 0) {
        last_write = data.ftLastWriteTime;
        return true;
    }
    return false;
}

}
}
