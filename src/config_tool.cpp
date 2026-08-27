// Configuration utility for the Orpheus Classic SAPI5 voices.
//
// Edits the "Orpheus Custom Voice" settings stored in
// %APPDATA%\OrpheusClassicSAPI\settings.ini.  Every change is saved
// immediately and picked up by the SAPI engine on the next utterance, so
// adjustments take effect right away, including under a running screen
// reader.  All controls are labelled and reachable with the Tab key.

#include <windows.h>
#include <commctrl.h>
#include <sapi.h>
#include <string>

#include "config_resource.h"
#include "settings.h"
#include "debug_log.h"

#pragma comment(lib, "comctl32.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(linker, "/manifestdependency:\"type='win32' \
name='Microsoft.Windows.Common-Controls' version='6.0.0.0' \
processorArchitecture='*' publicKeyToken='6595b64144ccf1df' language='*'\"")

namespace {

struct LanguageEntry {
    int country;
    const wchar_t* name;
};

constexpr LanguageEntry LANGUAGES[] = {
    {1, L"US English"},
    {44, L"UK English"},
    {31, L"Dutch"},
    {33, L"French"},
    {34, L"Castilian Spanish"},
    {39, L"Italian"},
    {46, L"Swedish"},
    {49, L"German"},
    {52, L"Latin American Spanish"},
};
constexpr int LANGUAGE_COUNT = sizeof(LANGUAGES) / sizeof(LANGUAGES[0]);

constexpr wchar_t CUSTOM_VOICE_NAME[] = L"Orpheus Custom Voice";
constexpr wchar_t DEFAULT_TEST_TEXT[] = L"This is the Orpheus custom voice.";

// EN_CHANGE fires while the dialog is still being created (spin buddies set
// their text during creation), so the guard starts out true and is only
// cleared at the end of WM_INITDIALOG.
bool g_loading = true;

ISpVoice* g_voice = nullptr;

struct SpinBinding {
    int edit_id;
    int spin_id;
    int min_value;
    int max_value;
};

constexpr SpinBinding SPINS[] = {
    {IDC_RATE, IDC_RATE_SPIN, 40, 700},
    {IDC_PITCH, IDC_PITCH_SPIN, 50, 500},
    {IDC_VOLUME, IDC_VOLUME_SPIN, 0, 100},
    {IDC_INTONATION, IDC_INTONATION_SPIN, 0, 100},
    {IDC_VOICING, IDC_VOICING_SPIN, 0, 100},
    {IDC_WORDPAUSE, IDC_WORDPAUSE_SPIN, 0, 1000},
    {IDC_PHRASEPAUSE, IDC_PHRASEPAUSE_SPIN, 0, 2000},
};

int get_edit_int(HWND dialog, int control_id)
{
    BOOL translated = FALSE;
    const int value = static_cast<int>(GetDlgItemInt(dialog, control_id, &translated, TRUE));
    return translated ? value : 0;
}

void load_settings_into_dialog(HWND dialog)
{
    const bool was_loading = g_loading;
    g_loading = true;

    const Orpheus::settings::CustomVoice value = Orpheus::settings::load_custom_voice();

    int language_index = 1; // UK English fallback
    for (int i = 0; i < LANGUAGE_COUNT; ++i) {
        if (LANGUAGES[i].country == value.language) {
            language_index = i;
            break;
        }
    }
    SendDlgItemMessageW(dialog, IDC_LANGUAGE, CB_SETCURSEL, language_index, 0);
    SetDlgItemInt(dialog, IDC_RATE, value.rate, TRUE);
    SetDlgItemInt(dialog, IDC_PITCH, value.pitch, TRUE);
    SetDlgItemInt(dialog, IDC_VOLUME, value.volume, TRUE);
    SetDlgItemInt(dialog, IDC_INTONATION, value.intonation, TRUE);
    SetDlgItemInt(dialog, IDC_VOICING, value.voicing, TRUE);
    SendDlgItemMessageW(dialog, IDC_SOURCE, CB_SETCURSEL, value.voice_source, 0);
    SetDlgItemInt(dialog, IDC_WORDPAUSE, value.word_pause, TRUE);
    SetDlgItemInt(dialog, IDC_PHRASEPAUSE, value.phrase_pause, TRUE);
    CheckDlgButton(dialog, IDC_LOGGING,
                   Orpheus::settings::logging_enabled() ? BST_CHECKED : BST_UNCHECKED);

    g_loading = was_loading;
}

void save_settings_from_dialog(HWND dialog)
{
    Orpheus::settings::CustomVoice value;

    const int language_index =
        static_cast<int>(SendDlgItemMessageW(dialog, IDC_LANGUAGE, CB_GETCURSEL, 0, 0));
    if (language_index >= 0 && language_index < LANGUAGE_COUNT) {
        value.language = LANGUAGES[language_index].country;
    }
    value.rate = get_edit_int(dialog, IDC_RATE);
    value.pitch = get_edit_int(dialog, IDC_PITCH);
    value.volume = get_edit_int(dialog, IDC_VOLUME);
    value.intonation = get_edit_int(dialog, IDC_INTONATION);
    value.voicing = get_edit_int(dialog, IDC_VOICING);
    value.voice_source =
        static_cast<int>(SendDlgItemMessageW(dialog, IDC_SOURCE, CB_GETCURSEL, 0, 0));
    if (value.voice_source < 0) {
        value.voice_source = 0;
    }
    value.word_pause = get_edit_int(dialog, IDC_WORDPAUSE);
    value.phrase_pause = get_edit_int(dialog, IDC_PHRASEPAUSE);

    Orpheus::settings::save_custom_voice(value);
    Orpheus::settings::set_logging_enabled(
        IsDlgButtonChecked(dialog, IDC_LOGGING) == BST_CHECKED);
}

void clamp_edit(HWND dialog, int control_id)
{
    for (const SpinBinding& spin : SPINS) {
        if (spin.edit_id != control_id) {
            continue;
        }
        int value = get_edit_int(dialog, control_id);
        int clamped = value;
        if (clamped < spin.min_value) clamped = spin.min_value;
        if (clamped > spin.max_value) clamped = spin.max_value;
        if (clamped != value) {
            SetDlgItemInt(dialog, control_id, clamped, TRUE);
        }
        break;
    }
}

bool find_custom_voice_token(ISpObjectToken** token_out)
{
    *token_out = nullptr;
    ISpObjectTokenCategory* category = nullptr;
    if (FAILED(CoCreateInstance(CLSID_SpObjectTokenCategory, nullptr, CLSCTX_INPROC_SERVER,
                                IID_ISpObjectTokenCategory,
                                reinterpret_cast<void**>(&category)))) {
        return false;
    }
    bool found = false;
    if (SUCCEEDED(category->SetId(SPCAT_VOICES, FALSE))) {
        IEnumSpObjectTokens* tokens = nullptr;
        std::wstring required = std::wstring(L"Name=") + CUSTOM_VOICE_NAME;
        if (SUCCEEDED(category->EnumTokens(required.c_str(), nullptr, &tokens)) && tokens) {
            ISpObjectToken* token = nullptr;
            ULONG fetched = 0;
            if (tokens->Next(1, &token, &fetched) == S_OK && fetched == 1) {
                *token_out = token;
                found = true;
            }
            tokens->Release();
        }
    }
    category->Release();
    return found;
}

void speak_test(HWND dialog)
{
    // Settings were already saved by the change notifications; the SAPI
    // engine reads them fresh for each utterance.
    if (!g_voice) {
        if (FAILED(CoCreateInstance(CLSID_SpVoice, nullptr, CLSCTX_INPROC_SERVER,
                                    IID_ISpVoice, reinterpret_cast<void**>(&g_voice)))) {
            MessageBoxW(dialog, L"Could not create a SAPI voice object.",
                        L"Orpheus Classic Configuration", MB_OK | MB_ICONERROR);
            return;
        }
    }

    ISpObjectToken* token = nullptr;
    if (!find_custom_voice_token(&token)) {
        MessageBoxW(dialog,
                    L"The Orpheus Custom Voice is not registered with SAPI yet. "
                    L"Reinstall or repair Orpheus Classic SAPI5 and try again.",
                    L"Orpheus Classic Configuration", MB_OK | MB_ICONWARNING);
        return;
    }
    g_voice->SetVoice(token);
    token->Release();

    wchar_t text[512] = {};
    GetDlgItemTextW(dialog, IDC_TESTTEXT, text, 512);
    if (!text[0]) {
        wcscpy_s(text, DEFAULT_TEST_TEXT);
    }
    const HRESULT hr = g_voice->Speak(text, SPF_ASYNC | SPF_PURGEBEFORESPEAK | SPF_IS_NOT_XML,
                                      nullptr);
    if (FAILED(hr)) {
        MessageBoxW(dialog, L"The test speech request failed.",
                    L"Orpheus Classic Configuration", MB_OK | MB_ICONERROR);
    }
}

INT_PTR CALLBACK dialog_proc(HWND dialog, UINT message, WPARAM wparam, LPARAM /*lparam*/)
{
    switch (message) {
    case WM_INITDIALOG: {
        for (int i = 0; i < LANGUAGE_COUNT; ++i) {
            SendDlgItemMessageW(dialog, IDC_LANGUAGE, CB_ADDSTRING, 0,
                                reinterpret_cast<LPARAM>(LANGUAGES[i].name));
        }
        SendDlgItemMessageW(dialog, IDC_SOURCE, CB_ADDSTRING, 0,
                            reinterpret_cast<LPARAM>(L"Default (0)"));
        SendDlgItemMessageW(dialog, IDC_SOURCE, CB_ADDSTRING, 0,
                            reinterpret_cast<LPARAM>(L"Alternative (1)"));
        for (const SpinBinding& spin : SPINS) {
            SendDlgItemMessageW(dialog, spin.spin_id, UDM_SETRANGE32,
                                spin.min_value, spin.max_value);
        }
        SendDlgItemMessageW(dialog, IDC_TESTTEXT, EM_SETLIMITTEXT, 500, 0);
        SetDlgItemTextW(dialog, IDC_TESTTEXT, DEFAULT_TEST_TEXT);
        load_settings_into_dialog(dialog);
        g_loading = false;
        return TRUE;
    }
    case WM_COMMAND: {
        const int control_id = LOWORD(wparam);
        const int notification = HIWORD(wparam);
        switch (control_id) {
        case IDOK:
        case IDCANCEL:
            EndDialog(dialog, 0);
            return TRUE;
        case IDC_TEST:
            if (notification == BN_CLICKED) {
                speak_test(dialog);
                return TRUE;
            }
            break;
        case IDC_DEFAULTS:
            if (notification == BN_CLICKED) {
                Orpheus::settings::save_custom_voice(Orpheus::settings::CustomVoice());
                Orpheus::settings::set_logging_enabled(true);
                load_settings_into_dialog(dialog);
                return TRUE;
            }
            break;
        case IDC_LOGGING:
            if (notification == BN_CLICKED && !g_loading) {
                save_settings_from_dialog(dialog);
                return TRUE;
            }
            break;
        case IDC_LANGUAGE:
        case IDC_SOURCE:
            if (notification == CBN_SELCHANGE && !g_loading) {
                save_settings_from_dialog(dialog);
                return TRUE;
            }
            break;
        case IDC_RATE:
        case IDC_PITCH:
        case IDC_VOLUME:
        case IDC_INTONATION:
        case IDC_VOICING:
        case IDC_WORDPAUSE:
        case IDC_PHRASEPAUSE:
            if (notification == EN_CHANGE && !g_loading) {
                save_settings_from_dialog(dialog);
                return TRUE;
            }
            if (notification == EN_KILLFOCUS && !g_loading) {
                clamp_edit(dialog, control_id);
                save_settings_from_dialog(dialog);
                return TRUE;
            }
            break;
        default:
            break;
        }
        break;
    }
    case WM_CLOSE:
        EndDialog(dialog, 0);
        return TRUE;
    default:
        break;
    }
    return FALSE;
}

}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE /*prev*/, LPWSTR /*cmdline*/, int /*show*/)
{
    INITCOMMONCONTROLSEX icc = { sizeof(icc), ICC_UPDOWN_CLASS | ICC_STANDARD_CLASSES };
    InitCommonControlsEx(&icc);
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);

    ORPHEUS_LOG("Config: utility started");
    DialogBoxParamW(instance, MAKEINTRESOURCEW(IDD_CONFIG), nullptr, dialog_proc, 0);
    ORPHEUS_LOG("Config: utility closed");

    if (g_voice) {
        g_voice->Release();
        g_voice = nullptr;
    }
    CoUninitialize();
    return 0;
}
