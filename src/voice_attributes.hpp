#pragma once

#include <string>
#include "utils.hpp"
#include "settings.h"

namespace Orpheus {
namespace sapi {

struct voice_info
{
    const wchar_t* name;   // SAPI token name
    int country;           // engine country code; -1 = custom voice
    const wchar_t* lcid;   // SAPI Language attribute (hex LCID)
};

// The nine languages shipped with Orpheus Classic Build 18, plus the
// user-configurable custom voice.  Each language exposes the engine's single
// "Orpheus" voice.
inline constexpr int CUSTOM_VOICE_COUNTRY = -1;

inline constexpr voice_info orpheus_voices[] = {
    {L"Orpheus US English", 1, L"409"},
    {L"Orpheus UK English", 44, L"809"},
    {L"Orpheus Dutch", 31, L"413"},
    {L"Orpheus French", 33, L"40C"},
    {L"Orpheus Castilian Spanish", 34, L"C0A"},
    {L"Orpheus Italian", 39, L"410"},
    {L"Orpheus Swedish", 46, L"41D"},
    {L"Orpheus German", 49, L"407"},
    {L"Orpheus Latin American Spanish", 52, L"80A"},
    {L"Orpheus Custom Voice", CUSTOM_VOICE_COUNTRY, nullptr},
};

inline constexpr int orpheus_voice_count =
    sizeof(orpheus_voices) / sizeof(orpheus_voices[0]);

[[nodiscard]] inline const wchar_t* lcid_for_country(int country)
{
    for (const auto& voice : orpheus_voices) {
        if (voice.country == country && voice.lcid) {
            return voice.lcid;
        }
    }
    return L"809";
}

class voice_attributes
{
public:
    explicit voice_attributes(int voice_index = 0) noexcept
        : index_(voice_index)
    {
        if (index_ < 0 || index_ >= orpheus_voice_count) {
            index_ = 0;
        }
    }

    [[nodiscard]] std::wstring get_name() const
    {
        return orpheus_voices[index_].name;
    }

    [[nodiscard]] int get_index() const noexcept
    {
        return index_;
    }

    [[nodiscard]] bool is_custom() const noexcept
    {
        return orpheus_voices[index_].country == CUSTOM_VOICE_COUNTRY;
    }

    // Engine country code; the custom voice resolves through settings.ini.
    [[nodiscard]] int get_country() const
    {
        if (is_custom()) {
            return settings::load_custom_voice().language;
        }
        return orpheus_voices[index_].country;
    }

    [[nodiscard]] std::wstring get_age() const
    {
        return L"Adult";
    }

    [[nodiscard]] std::wstring get_gender() const
    {
        return L"Male";
    }

    [[nodiscard]] std::wstring get_language() const
    {
        if (is_custom()) {
            return lcid_for_country(settings::load_custom_voice().language);
        }
        return orpheus_voices[index_].lcid;
    }

    [[nodiscard]] std::wstring get_vendor() const
    {
        return L"Dolphin";
    }

private:
    int index_;
};
}
}
