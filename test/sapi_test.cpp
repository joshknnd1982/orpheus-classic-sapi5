// End-to-end SAPI test harness for the Orpheus Classic SAPI5 engine.
//
// Loads the engine DLL directly, obtains voice tokens from its token
// enumerator and speaks through a real SpVoice into WAV files.  This runs the
// full SAPI pipeline (XML parsing, format negotiation, engine instantiation
// via the CLSID registered under HKCU) without requiring administrator
// rights.
//
// Usage: sapi_test.exe <path-to-OrpheusClassicSAPI.dll> <output-dir> [voice-filter]

#include <sapi.h>
#include <sapiddk.h>
#include <sperror.h>
#include <windows.h>
#include <cstdio>
#include <string>

typedef HRESULT(STDAPICALLTYPE* DllGetClassObjectFn)(REFCLSID, REFIID, void**);

// CLSID of Orpheus::sapi::IEnumSpObjectTokensImpl.
static const CLSID ENUM_CLSID =
    { 0x25db4556, 0x07b0, 0x41b1, { 0x99, 0x1a, 0x5b, 0x6f, 0xfa, 0x07, 0xd2, 0x16 } };

static bool check(HRESULT hr, const char* what)
{
    if (FAILED(hr)) {
        printf("FAIL: %s (hr=0x%08lX)\n", what, static_cast<unsigned long>(hr));
        return false;
    }
    return true;
}

int wmain(int argc, wchar_t** argv)
{
    if (argc < 3) {
        printf("usage: sapi_test <dll> <outdir> [voice-filter]\n");
        return 1;
    }
    setvbuf(stdout, nullptr, _IONBF, 0);
    const wchar_t* dll_path = argv[1];
    const std::wstring out_dir = argv[2];
    const wchar_t* filter = argc > 3 ? argv[3] : nullptr;
    const bool interrupt_mode = filter && wcscmp(filter, L"--interrupt") == 0;

    if (!check(CoInitialize(nullptr), "CoInitialize")) {
        return 1;
    }

    HMODULE dll = LoadLibraryW(dll_path);
    if (!dll) {
        printf("FAIL: LoadLibrary %ls (error %lu)\n", dll_path, GetLastError());
        return 1;
    }
    auto get_class_object =
        reinterpret_cast<DllGetClassObjectFn>(GetProcAddress(dll, "DllGetClassObject"));
    if (!get_class_object) {
        printf("FAIL: DllGetClassObject export missing\n");
        return 1;
    }

    IClassFactory* factory = nullptr;
    if (!check(get_class_object(ENUM_CLSID, IID_IClassFactory,
                                reinterpret_cast<void**>(&factory)),
               "get enumerator class object")) {
        return 1;
    }
    IEnumSpObjectTokens* tokens = nullptr;
    if (!check(factory->CreateInstance(nullptr, __uuidof(IEnumSpObjectTokens),
                                       reinterpret_cast<void**>(&tokens)),
               "create enumerator")) {
        return 1;
    }
    factory->Release();

    ULONG count = 0;
    tokens->GetCount(&count);
    printf("voice tokens: %lu\n", count);

    if (interrupt_mode) {
        // Interrupt-latency scenario: speak a long utterance to the default
        // audio device, purge it mid-render, and time how quickly the next
        // utterance starts.  Correlate with the engine log (ms timestamps).
        ISpObjectToken* token = nullptr;
        if (!check(tokens->Item(1, &token), "Item(1)")) { // UK English
            return 1;
        }
        ISpVoice* voice = nullptr;
        if (!check(CoCreateInstance(CLSID_SpVoice, nullptr, CLSCTX_INPROC_SERVER,
                                    IID_ISpVoice, reinterpret_cast<void**>(&voice)),
                   "create SpVoice")) {
            return 1;
        }
        if (!check(voice->SetVoice(token), "SetVoice")) {
            return 1;
        }
        token->Release();

        std::wstring long_text;
        for (int i = 0; i < 8; ++i) {
            long_text += L"The quick brown fox jumps over the lazy dog near the river bank. ";
        }

        for (int trial = 0; trial < 4; ++trial) {
            SYSTEMTIME st;
            GetLocalTime(&st);
            printf("trial %d: speak long at %02u:%02u:%02u.%03u\n", trial,
                   st.wHour, st.wMinute, st.wSecond, st.wMilliseconds);
            voice->Speak(long_text.c_str(),
                         SPF_ASYNC | SPF_PURGEBEFORESPEAK | SPF_IS_NOT_XML, nullptr);
            Sleep(400);
            GetLocalTime(&st);
            const ULONGLONG t0 = GetTickCount64();
            printf("trial %d: purge+speak short at %02u:%02u:%02u.%03u\n", trial,
                   st.wHour, st.wMinute, st.wSecond, st.wMilliseconds);
            voice->Speak(L"Next line.",
                         SPF_ASYNC | SPF_PURGEBEFORESPEAK | SPF_IS_NOT_XML, nullptr);
            voice->WaitUntilDone(15000);
            printf("trial %d: short line done %llu ms after purge\n", trial,
                   GetTickCount64() - t0);
            Sleep(300);
        }
        voice->Release();
        tokens->Release();
        CoUninitialize();
        printf("RESULT: interrupt scenario complete\n");
        return 0;
    }

    int failures = 0;
    for (ULONG i = 0; i < count; ++i) {
        ISpObjectToken* token = nullptr;
        if (!check(tokens->Item(i, &token), "enumerator Item")) {
            ++failures;
            continue;
        }

        std::wstring name = L"voice";
        {
            ISpDataKey* attributes = nullptr;
            if (SUCCEEDED(token->OpenKey(L"Attributes", &attributes))) {
                LPWSTR value = nullptr;
                if (SUCCEEDED(attributes->GetStringValue(L"Name", &value))) {
                    name = value;
                    CoTaskMemFree(value);
                }
                attributes->Release();
            }
        }
        if (filter && name.find(filter) == std::wstring::npos) {
            token->Release();
            continue;
        }
        printf("[%lu] %ls: ", i, name.c_str());

        ISpVoice* voice = nullptr;
        if (!check(CoCreateInstance(CLSID_SpVoice, nullptr, CLSCTX_INPROC_SERVER,
                                    IID_ISpVoice, reinterpret_cast<void**>(&voice)),
                   "create SpVoice")) {
            token->Release();
            ++failures;
            continue;
        }

        HRESULT hr = voice->SetVoice(token);
        if (!check(hr, "SetVoice")) {
            voice->Release();
            token->Release();
            ++failures;
            continue;
        }

        ISpStream* stream = nullptr;
        if (!check(CoCreateInstance(CLSID_SpStream, nullptr, CLSCTX_INPROC_SERVER,
                                    IID_ISpStream, reinterpret_cast<void**>(&stream)),
                   "create SpStream")) {
            voice->Release();
            token->Release();
            ++failures;
            continue;
        }

        WAVEFORMATEX wfx = {};
        wfx.wFormatTag = WAVE_FORMAT_PCM;
        wfx.nChannels = 1;
        wfx.nSamplesPerSec = 22050;
        wfx.wBitsPerSample = 16;
        wfx.nBlockAlign = 2;
        wfx.nAvgBytesPerSec = 44100;

        std::wstring safe_name = name;
        for (auto& ch : safe_name) {
            if (ch == L' ') ch = L'_';
        }
        const std::wstring wav_path = out_dir + L"\\sapi_" + safe_name + L".wav";
        GUID format_id = SPDFID_WaveFormatEx;
        if (!check(stream->BindToFile(wav_path.c_str(), SPFM_CREATE_ALWAYS, &format_id,
                                      &wfx, 0),
                   "BindToFile")) {
            stream->Release();
            voice->Release();
            token->Release();
            ++failures;
            continue;
        }

        voice->SetOutput(stream, TRUE);
        const std::wstring text = L"Hello. You are listening to " + name +
                                  L", speaking through Microsoft Speech A P I five.";
        const ULONGLONG t0 = GetTickCount64();
        hr = voice->Speak(text.c_str(), SPF_DEFAULT, nullptr);
        const ULONGLONG elapsed = GetTickCount64() - t0;
        stream->Close();
        stream->Release();
        voice->Release();
        token->Release();

        if (!check(hr, "Speak")) {
            ++failures;
            continue;
        }

        WIN32_FILE_ATTRIBUTE_DATA info = {};
        LONGLONG size = 0;
        if (GetFileAttributesExW(wav_path.c_str(), GetFileExInfoStandard, &info)) {
            size = (static_cast<LONGLONG>(info.nFileSizeHigh) << 32) | info.nFileSizeLow;
        }
        printf("OK, %lld bytes in %llu ms -> %ls\n", size, elapsed, wav_path.c_str());
        if (size < 40000) {
            printf("  WARNING: output suspiciously small\n");
            ++failures;
        }
    }

    tokens->Release();
    CoUninitialize();
    printf(failures ? "RESULT: %d failure(s)\n" : "RESULT: all passed\n", failures);
    return failures ? 1 : 0;
}
