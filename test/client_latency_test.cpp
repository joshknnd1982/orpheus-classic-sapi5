// Direct OrpheusClient exercise: abort mid-render, swap to standby, speak
// again.  Measures cancel-to-next-audio latency without SAPI in the loop.
//
// Usage: client_latency_test.exe  (ORPHEUS_SAPI_HOME must point at a dir with
// orpheus-classic-host.exe and orpheus\)

#include "orpheus_client.h"

#include <crtdbg.h>
#include <cstdio>
#include <string>

using namespace Orpheus;

namespace {

struct TrialState {
    ULONGLONG start_tick = 0;
    ULONGLONG abort_after_ms = 0;
    ULONGLONG first_audio_tick = 0;
    ULONGLONG bytes = 0;
};

bool counting_sink(const void* /*pcm*/, uint32_t size, void* user)
{
    auto* state = static_cast<TrialState*>(user);
    if (state->first_audio_tick == 0) {
        state->first_audio_tick = GetTickCount64();
    }
    state->bytes += size;
    return true;
}

bool timed_abort(void* user)
{
    auto* state = static_cast<TrialState*>(user);
    return state->abort_after_ms != 0 &&
           GetTickCount64() - state->start_tick >= state->abort_after_ms;
}

void check_heap(const char* label)
{
    if (!HeapValidate(GetProcessHeap(), 0, nullptr)) {
        printf("!!! HEAP CORRUPT at %s\n", label);
        ExitProcess(99);
    }
    printf("    heap ok: %s\n", label);
}

}

int main()
{
    setvbuf(stdout, nullptr, _IONBF, 0);
#ifdef _DEBUG
    _CrtSetReportMode(_CRT_WARN, _CRTDBG_MODE_FILE);
    _CrtSetReportFile(_CRT_WARN, _CRTDBG_FILE_STDOUT);
    _CrtSetReportMode(_CRT_ERROR, _CRTDBG_MODE_FILE);
    _CrtSetReportFile(_CRT_ERROR, _CRTDBG_FILE_STDOUT);
    _CrtSetReportMode(_CRT_ASSERT, _CRTDBG_MODE_FILE);
    _CrtSetReportFile(_CRT_ASSERT, _CRTDBG_FILE_STDOUT);
    _CrtSetDbgFlag(_CRTDBG_ALLOC_MEM_DF | _CRTDBG_CHECK_ALWAYS_DF);
#endif

    std::wstring long_text = L"@<9=44>@<0=110>@<1=110> ";
    for (int i = 0; i < 8; ++i) {
        long_text += L"The quick brown fox jumps over the lazy dog near the river bank. ";
    }
    const std::wstring short_text = L"@<9=44>@<0=110>@<1=110> Next line.";

    OrpheusClient& client = OrpheusClient::instance();
    if (!client.ensure_ready()) {
        printf("FAIL: ensure_ready\n");
        return 1;
    }
    printf("warm.\n");
    Sleep(300); // let the standby spawn

    int failures = 0;
    for (int trial = 0; trial < 6; ++trial) {
        TrialState long_state;
        long_state.start_tick = GetTickCount64();
        long_state.abort_after_ms = 400;
        bool aborted = false;
        check_heap("before long speak");
        if (!client.speak_segment(long_text, counting_sink, timed_abort,
                                  &long_state, &aborted)) {
            printf("trial %d: FAIL long speak transport\n", trial);
            ++failures;
            continue;
        }
        const ULONGLONG abort_done = GetTickCount64();
        check_heap("after abort");

        TrialState short_state;
        short_state.start_tick = abort_done;
        if (!client.speak_segment(short_text, counting_sink, nullptr,
                                  &short_state, nullptr)) {
            printf("trial %d: FAIL short speak transport\n", trial);
            ++failures;
            continue;
        }
        check_heap("after short speak");
        printf("trial %d: long aborted=%d (%llu bytes, abort handled in %llu ms), "
               "next utterance first audio %llu ms after cancel (%llu bytes)\n",
               trial, aborted ? 1 : 0, long_state.bytes,
               abort_done - long_state.start_tick - long_state.abort_after_ms,
               short_state.first_audio_tick - abort_done, short_state.bytes);
        if (!aborted || short_state.bytes == 0) {
            ++failures;
        }
        Sleep(150);
    }

    // Character navigation storms: each character is cancelled by the next
    // keypress, with quick_recover so hosts are parked and reused.  At 35 ms
    // key-repeat the cancel usually lands before the first audio (that is
    // expected - what matters is that nothing fails or stalls); at 120 ms
    // nearly every character should be heard starting.
    for (const ULONGLONG cadence : { 35ull, 120ull }) {
        printf("\ncharacter storm (%llu ms key repeat):\n", cadence);
        ULONGLONG total_first = 0, worst_first = 0, total_cycle = 0, worst_cycle = 0;
        int heard = 0;
        const ULONGLONG storm_start = GetTickCount64();
        for (int i = 0; i < 30; ++i) {
            wchar_t body[8] = { static_cast<wchar_t>(L'a' + (i % 26)), L'\0' };
            std::wstring text = L"@<9=44>@<0=110>@<1=110> ";
            text += body;
            TrialState state;
            state.start_tick = GetTickCount64();
            state.abort_after_ms = cadence;
            bool aborted = false;
            if (!client.speak_segment(text, counting_sink, timed_abort, &state, &aborted,
                                      true)) {
                printf("  char %d: transport FAILURE\n", i);
                ++failures;
                continue;
            }
            const ULONGLONG cycle = GetTickCount64() - state.start_tick;
            total_cycle += cycle;
            worst_cycle = cycle > worst_cycle ? cycle : worst_cycle;
            if (state.first_audio_tick != 0) {
                const ULONGLONG first = state.first_audio_tick - state.start_tick;
                total_first += first;
                worst_first = first > worst_first ? first : worst_first;
                ++heard;
            }
        }
        printf("  30 chars in %llu ms: %d audible; first audio avg %llu ms worst %llu ms; "
               "cycle avg %llu ms worst %llu ms\n",
               GetTickCount64() - storm_start, heard,
               heard ? total_first / heard : 0, worst_first,
               total_cycle / 30, worst_cycle);
        if (cadence >= 120 && heard < 28) {
            printf("  FAILURE: expected nearly all characters audible at this cadence\n");
            ++failures;
        }
        Sleep(400);
    }

    // After a storm, the very next utterance is what the user actually wants
    // to hear - it must start promptly.
    {
        TrialState state;
        state.start_tick = GetTickCount64();
        if (!client.speak_segment(short_text, counting_sink, nullptr, &state, nullptr)) {
            printf("post-storm speak: transport FAILURE\n");
            ++failures;
        } else {
            printf("post-storm utterance: first audio %llu ms\n",
                   state.first_audio_tick - state.start_tick);
        }
    }

    printf(failures ? "RESULT: %d failure(s)\n" : "RESULT: all passed\n", failures);
    return failures ? 1 : 0;
}
