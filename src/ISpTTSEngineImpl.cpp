#include "orpheus_client.h" // must come first: winsock2.h before windows.h

#include <new>
#include <string>
#include <vector>
#include <cmath>
#include <cwctype>
#include <algorithm>
#include "utils.hpp"
#include "ISpTTSEngineImpl.hpp"
#include "orpheus_protocol.h"
#include "settings.h"
#include "debug_log.h"

namespace Orpheus {
namespace sapi {

namespace {

constexpr WORD AUDIO_CHANNELS = 1;
constexpr DWORD AUDIO_SAMPLE_RATE = protocol::HOST_SAMPLE_RATE;
constexpr WORD AUDIO_BITS_PER_SAMPLE = 16;
constexpr DWORD AUDIO_BYTES_PER_SEC = AUDIO_SAMPLE_RATE * AUDIO_BITS_PER_SAMPLE / 8;

constexpr int SAPI_RATE_MIN = -10;
constexpr int SAPI_RATE_MAX = 10;
constexpr int SAPI_PITCH_ADJ_RANGE = 24; // SAPI pitch units are 1/24 octave

constexpr int ENGINE_RATE_MIN = 40;
constexpr int ENGINE_RATE_MAX = 700;
constexpr int ENGINE_PITCH_MIN = 50;
constexpr int ENGINE_PITCH_MAX = 500;

// A segment is one host render; bounded so that a cancel never has to wait
// for a long render to wind down.
constexpr size_t MAX_SEGMENT_CHARS = 600;

// Segments at most this long (single characters and short words - the things
// a screen reader user arrows through rapidly - including their inline
// "@<param=value>" commands, e.g. the pitch change around a capital letter)
// render within a few hundred milliseconds, so an interrupted host can finish
// quietly and be reused instead of being killed.
constexpr size_t QUICK_RECOVER_MAX_CHARS = 48;

struct BaseParams {
    int country = 44;
    int rate = 110;
    int pitch = 110;
    int volume = 100; // configured volume for the custom voice
    int intonation = 50;
    int voicing = 65;
    int voice_source = 0;
    int word_pause = 0;
    int phrase_pause = 250;
};

[[nodiscard]] BaseParams base_params_for_voice(const voice_attributes& attr)
{
    BaseParams base;
    if (attr.is_custom()) {
        const settings::CustomVoice custom = settings::load_custom_voice();
        base.country = custom.language;
        base.rate = custom.rate;
        base.pitch = custom.pitch;
        base.volume = custom.volume;
        base.intonation = custom.intonation;
        base.voicing = custom.voicing;
        base.voice_source = custom.voice_source;
        base.word_pause = custom.word_pause;
        base.phrase_pause = custom.phrase_pause;
    } else {
        base.country = attr.get_country();
    }
    return base;
}

[[nodiscard]] int clamp_int(int value, int min_value, int max_value)
{
    return (std::max)(min_value, (std::min)(max_value, value));
}

// SAPI rate -10..10 sweeps the engine range exponentially around the base.
[[nodiscard]] int engine_rate(int base_rate, long sapi_rate)
{
    const int rate = clamp_int(base_rate, ENGINE_RATE_MIN, ENGINE_RATE_MAX);
    const long adj = clamp_int(static_cast<int>(sapi_rate), SAPI_RATE_MIN, SAPI_RATE_MAX);
    double value = rate;
    if (adj > 0) {
        value = rate * std::pow(static_cast<double>(ENGINE_RATE_MAX) / rate, adj / 10.0);
    } else if (adj < 0) {
        value = rate * std::pow(static_cast<double>(ENGINE_RATE_MIN) / rate, -adj / 10.0);
    }
    return clamp_int(static_cast<int>(std::lround(value)), ENGINE_RATE_MIN, ENGINE_RATE_MAX);
}

// SAPI pitch adjustment is in 1/24 octave steps.
[[nodiscard]] int engine_pitch(int base_pitch, long middle_adj)
{
    const int pitch = clamp_int(base_pitch, ENGINE_PITCH_MIN, ENGINE_PITCH_MAX);
    const long adj = clamp_int(static_cast<int>(middle_adj), -SAPI_PITCH_ADJ_RANGE, SAPI_PITCH_ADJ_RANGE);
    const double value = pitch * std::pow(2.0, adj / static_cast<double>(SAPI_PITCH_ADJ_RANGE));
    return clamp_int(static_cast<int>(std::lround(value)), ENGINE_PITCH_MIN, ENGINE_PITCH_MAX);
}

[[nodiscard]] int engine_volume(int base_volume, unsigned short sapi_volume, ULONG frag_volume)
{
    const double value = clamp_int(base_volume, 0, 100) / 100.0 *
                         clamp_int(sapi_volume, 0, 100) / 100.0 *
                         clamp_int(static_cast<int>(frag_volume), 0, 100) / 100.0 * 100.0;
    return clamp_int(static_cast<int>(std::lround(value)), 0, 100);
}

void append_command(std::wstring& text, int param, int value)
{
    wchar_t buffer[32];
    swprintf_s(buffer, L"@<%d=%d>", param, value);
    text += buffer;
}

[[nodiscard]] std::wstring build_prefix(const BaseParams& base, int rate, int pitch, int volume)
{
    std::wstring prefix;
    append_command(prefix, protocol::PARAM_LANGUAGE, base.country);
    append_command(prefix, protocol::PARAM_RATE, rate);
    append_command(prefix, protocol::PARAM_PITCH, pitch);
    append_command(prefix, protocol::PARAM_INTONATION, base.intonation);
    append_command(prefix, protocol::PARAM_VOICING, base.voicing);
    append_command(prefix, protocol::PARAM_WORD_PAUSE, base.word_pause);
    append_command(prefix, protocol::PARAM_PHRASE_PAUSE, base.phrase_pause);
    append_command(prefix, protocol::PARAM_VOLUME, volume);
    append_command(prefix, protocol::PARAM_VOICE_SOURCE, base.voice_source);
    prefix += L' ';
    return prefix;
}

// Neutralise engine command sequences in user text and normalise characters
// the engine mispronounces.
void append_sanitized(std::wstring& out, const wchar_t* text, size_t length)
{
    for (size_t i = 0; i < length; ++i) {
        wchar_t ch = text[i];
        switch (ch) {
        case 0x2019: ch = L'\''; break; // right single quotation mark
        case 0x201C:                    // left double quotation mark
        case 0x201D: ch = L'"'; break;  // right double quotation mark
        case L'\r':
        case L'\n':
        case L'\t': ch = L' '; break;
        default:
            if (ch != 0 && ch < 0x20) {
                ch = L' ';
            }
            break;
        }
        if (ch == L'<' && !out.empty() && out.back() == L'@') {
            out += L' ';
        }
        out += ch;
    }
}

struct UpfrontEvent {
    SPEVENTENUM event_id;
    ULONG text_offset;
    ULONG text_length;
};

struct WorkItem {
    enum class Type { Text, Bookmark, Silence };
    Type type = Type::Text;
    std::wstring text;                  // Text: segment body; Bookmark: bookmark string
    std::vector<UpfrontEvent> events;   // Text: boundary events fired at segment start
    ULONG silence_ms = 0;
    // Fragment prosody overrides captured when the fragment differed from the
    // utterance defaults (emitted inline in `text` already).
};

struct SpeakContext {
    ISpTTSEngineSite* site = nullptr;
    ULONGLONG bytes_written = 0;
    bool aborted = false;
    bool skip_requested = false;
};

// Polled between audio chunks so cancellation is noticed within ~30 ms even
// while the engine is still rendering.
bool abort_check(void* user)
{
    auto* ctx = static_cast<SpeakContext*>(user);
    const DWORD actions = ctx->site->GetActions();
    if (actions & SPVES_ABORT) {
        ctx->aborted = true;
        return true;
    }
    if (actions & SPVES_SKIP) {
        ctx->site->CompleteSkip(0);
        ctx->skip_requested = true;
        ctx->aborted = true;
        return true;
    }
    return false;
}

bool write_audio(SpeakContext& ctx, const void* data, uint32_t size)
{
    if (ctx.bytes_written == 0) {
        ORPHEUS_LOG("Speak: first audio write (%u bytes)", size);
    }
    const BYTE* ptr = static_cast<const BYTE*>(data);
    ULONG remaining = size;
    while (remaining > 0) {
        const DWORD actions = ctx.site->GetActions();
        if (actions & SPVES_ABORT) {
            ctx.aborted = true;
            return false;
        }
        if (actions & SPVES_SKIP) {
            ctx.site->CompleteSkip(0);
            ctx.skip_requested = true;
            ctx.aborted = true;
            return false;
        }
        ULONG written = 0;
        const HRESULT hr = ctx.site->Write(ptr, remaining, &written);
        if (FAILED(hr)) {
            ORPHEUS_LOG("Speak: site Write failed, hr=0x%08lX", static_cast<unsigned long>(hr));
            ctx.aborted = true;
            return false;
        }
        // Some SAPI sites do not fill pcbWritten reliably; on success treat
        // the whole buffer as consumed unless a smaller value was returned.
        if (written == 0 || written > remaining) {
            written = remaining;
        }
        ctx.bytes_written += written;
        remaining -= written;
        ptr += written;
    }
    return true;
}

bool audio_sink(const void* pcm, uint32_t bytes, void* user)
{
    return write_audio(*static_cast<SpeakContext*>(user), pcm, bytes);
}

void collect_boundary_events(WorkItem& item, const SPVTEXTFRAG* frag,
                             bool sentence_events, bool word_events)
{
    if (sentence_events) {
        item.events.push_back({ SPEI_SENTENCE_BOUNDARY, frag->ulTextSrcOffset, frag->ulTextLen });
    }
    if (word_events) {
        const wchar_t* text = frag->pTextStart;
        const ULONG length = frag->ulTextLen;
        bool in_word = false;
        ULONG word_start = 0;
        for (ULONG i = 0; i <= length; ++i) {
            const bool is_word_char = (i < length) &&
                (iswalnum(text[i]) || text[i] == L'\'' || text[i] == L'-');
            if (is_word_char && !in_word) {
                word_start = i;
                in_word = true;
            } else if (!is_word_char && in_word) {
                item.events.push_back({ SPEI_WORD_BOUNDARY,
                                        frag->ulTextSrcOffset + word_start, i - word_start });
                in_word = false;
            }
        }
    }
}

// Split an accumulated segment body into host-sized renders, preferring
// sentence punctuation, then whitespace.
void split_segment(const std::wstring& body, std::vector<std::wstring>& out)
{
    size_t position = 0;
    while (body.size() - position > MAX_SEGMENT_CHARS) {
        const size_t window_end = position + MAX_SEGMENT_CHARS;
        size_t cut = std::wstring::npos;
        for (size_t i = window_end; i > position + 50; --i) {
            const wchar_t ch = body[i - 1];
            if ((ch == L'.' || ch == L'!' || ch == L'?' || ch == L';' || ch == L':') &&
                (i == body.size() || body[i] == L' ')) {
                cut = i;
                break;
            }
        }
        if (cut == std::wstring::npos) {
            for (size_t i = window_end; i > position + 50; --i) {
                if (body[i - 1] == L' ') {
                    cut = i;
                    break;
                }
            }
        }
        if (cut == std::wstring::npos) {
            cut = window_end;
        }
        out.push_back(body.substr(position, cut - position));
        position = cut;
    }
    if (position < body.size()) {
        out.push_back(body.substr(position));
    }
}

}

ISpTTSEngineImpl::ISpTTSEngineImpl()
    : voice_index_(0)
{
}

ISpTTSEngineImpl::~ISpTTSEngineImpl() = default;

STDMETHODIMP ISpTTSEngineImpl::SetObjectToken(ISpObjectToken* pToken)
{
    if (!pToken) {
        return E_INVALIDARG;
    }

    try {
        ISpDataKeyPtr attr;
        if (FAILED(pToken->OpenKey(L"Attributes", &attr))) {
            return E_INVALIDARG;
        }

        utils::out_ptr<wchar_t> name(CoTaskMemFree);
        if (FAILED(attr->GetStringValue(L"Name", name.address()))) {
            return E_INVALIDARG;
        }

        voice_index_ = 0;
        for (int i = 0; i < orpheus_voice_count; ++i) {
            if (_wcsicmp(orpheus_voices[i].name, name.get()) == 0) {
                voice_index_ = i;
                break;
            }
        }

        token_ = pToken;
        ORPHEUS_LOG("SetObjectToken: voice \"%S\" -> index %d", name.get(), voice_index_);

        // Pre-warm the engine host (and its standby) in the background so the
        // very first utterance does not pay the spawn cost.
        QueueUserWorkItem(
            [](PVOID) -> DWORD {
                OrpheusClient::instance().ensure_ready();
                return 0;
            },
            nullptr, WT_EXECUTEDEFAULT);
        return S_OK;
    }
    catch (const std::bad_alloc&) {
        return E_OUTOFMEMORY;
    }
    catch (...) {
        return E_UNEXPECTED;
    }
}

STDMETHODIMP ISpTTSEngineImpl::GetObjectToken(ISpObjectToken** ppToken)
{
    if (!ppToken) {
        return E_POINTER;
    }
    *ppToken = nullptr;

    if (token_) {
        token_.AddRef();
        *ppToken = token_.GetInterfacePtr();
        return S_OK;
    }
    return E_UNEXPECTED;
}

STDMETHODIMP ISpTTSEngineImpl::GetOutputFormat(
    const GUID* /*pTargetFmtId*/,
    const WAVEFORMATEX* /*pTargetWaveFormatEx*/,
    GUID* pOutputFormatId,
    WAVEFORMATEX** ppCoMemOutputWaveFormatEx)
{
    if (!pOutputFormatId || !ppCoMemOutputWaveFormatEx) {
        return E_POINTER;
    }

    *pOutputFormatId = SPDFID_WaveFormatEx;
    *ppCoMemOutputWaveFormatEx = nullptr;

    auto* pwfex = static_cast<WAVEFORMATEX*>(CoTaskMemAlloc(sizeof(WAVEFORMATEX)));
    if (!pwfex) {
        return E_OUTOFMEMORY;
    }

    pwfex->wFormatTag = WAVE_FORMAT_PCM;
    pwfex->nChannels = AUDIO_CHANNELS;
    pwfex->nSamplesPerSec = AUDIO_SAMPLE_RATE;
    pwfex->wBitsPerSample = AUDIO_BITS_PER_SAMPLE;
    pwfex->nBlockAlign = pwfex->nChannels * pwfex->wBitsPerSample / 8;
    pwfex->nAvgBytesPerSec = pwfex->nSamplesPerSec * pwfex->nBlockAlign;
    pwfex->cbSize = 0;

    *ppCoMemOutputWaveFormatEx = pwfex;
    return S_OK;
}

STDMETHODIMP ISpTTSEngineImpl::Speak(
    DWORD dwSpeakFlags,
    REFGUID /*rguidFormatId*/,
    const WAVEFORMATEX* /*pWaveFormatEx*/,
    const SPVTEXTFRAG* pTextFragList,
    ISpTTSEngineSite* pOutputSite)
{
    if (!pTextFragList || !pOutputSite) {
        return E_INVALIDARG;
    }

    try {
        const voice_attributes attr(voice_index_);
        const BaseParams base = base_params_for_voice(attr);

        long sapi_rate = 0;
        pOutputSite->GetRate(&sapi_rate);
        unsigned short sapi_volume = 100;
        pOutputSite->GetVolume(&sapi_volume);

        ULONGLONG event_interest = 0;
        pOutputSite->GetEventInterest(&event_interest);
        const bool sentence_events = (event_interest & (1ULL << SPEI_SENTENCE_BOUNDARY)) != 0;
        const bool word_events = (event_interest & (1ULL << SPEI_WORD_BOUNDARY)) != 0;

        ORPHEUS_LOG("Speak: flags=0x%08lX voice=%d country=%d rate=%ld volume=%u",
                    dwSpeakFlags, voice_index_, base.country, sapi_rate, sapi_volume);

        // ---- Pass 1: build the work list from the fragment list. ----
        std::vector<WorkItem> items;
        WorkItem current;
        bool current_has_text = false;

        auto flush_segment = [&]() {
            if (!current_has_text) {
                current = WorkItem();
                return;
            }
            std::vector<std::wstring> pieces;
            split_segment(current.text, pieces);
            for (size_t i = 0; i < pieces.size(); ++i) {
                WorkItem item;
                item.type = WorkItem::Type::Text;
                item.text = std::move(pieces[i]);
                if (i == 0) {
                    item.events = std::move(current.events);
                }
                items.push_back(std::move(item));
            }
            current = WorkItem();
            current_has_text = false;
        };

        for (const SPVTEXTFRAG* frag = pTextFragList; frag; frag = frag->pNext) {
            switch (frag->State.eAction) {
            case SPVA_Bookmark: {
                flush_segment();
                WorkItem item;
                item.type = WorkItem::Type::Bookmark;
                if (frag->ulTextLen > 0 && frag->pTextStart) {
                    item.text.assign(frag->pTextStart, frag->ulTextLen);
                }
                items.push_back(std::move(item));
                break;
            }
            case SPVA_Silence: {
                flush_segment();
                WorkItem item;
                item.type = WorkItem::Type::Silence;
                item.silence_ms = frag->State.SilenceMSecs;
                items.push_back(std::move(item));
                break;
            }
            case SPVA_Speak:
            case SPVA_Pronounce:
            case SPVA_SpellOut: {
                if (frag->ulTextLen == 0 || !frag->pTextStart) {
                    break;
                }
                collect_boundary_events(current, frag, sentence_events, word_events);

                // Inline prosody overrides when this fragment differs from
                // the utterance defaults.
                const int frag_rate = engine_rate(base.rate, sapi_rate + frag->State.RateAdj);
                const int frag_pitch = engine_pitch(base.pitch, frag->State.PitchAdj.MiddleAdj);
                const int frag_volume = engine_volume(base.volume, sapi_volume, frag->State.Volume);
                const int default_rate = engine_rate(base.rate, sapi_rate);
                const int default_pitch = engine_pitch(base.pitch, 0);
                const int default_volume = engine_volume(base.volume, sapi_volume, 100);
                if (frag_rate != default_rate) {
                    append_command(current.text, protocol::PARAM_RATE, frag_rate);
                }
                if (frag_pitch != default_pitch) {
                    append_command(current.text, protocol::PARAM_PITCH, frag_pitch);
                }
                if (frag_volume != default_volume) {
                    append_command(current.text, protocol::PARAM_VOLUME, frag_volume);
                }

                if (frag->State.eAction == SPVA_SpellOut) {
                    for (ULONG i = 0; i < frag->ulTextLen; ++i) {
                        append_sanitized(current.text, frag->pTextStart + i, 1);
                        current.text += L' ';
                    }
                } else {
                    append_sanitized(current.text, frag->pTextStart, frag->ulTextLen);
                    current.text += L' ';
                }

                if (frag_rate != default_rate) {
                    append_command(current.text, protocol::PARAM_RATE, default_rate);
                }
                if (frag_pitch != default_pitch) {
                    append_command(current.text, protocol::PARAM_PITCH, default_pitch);
                }
                if (frag_volume != default_volume) {
                    append_command(current.text, protocol::PARAM_VOLUME, default_volume);
                }
                current_has_text = true;
                break;
            }
            default:
                break;
            }
        }
        flush_segment();

        // ---- Pass 2: execute. ----
        SpeakContext ctx;
        ctx.site = pOutputSite;

        OrpheusClient& client = OrpheusClient::instance();

        for (WorkItem& item : items) {
            const DWORD actions = pOutputSite->GetActions();
            if (actions & SPVES_ABORT) {
                ORPHEUS_LOG("Speak: abort before item");
                break;
            }
            if (actions & SPVES_SKIP) {
                pOutputSite->CompleteSkip(0);
                break;
            }
            if (actions & SPVES_RATE) {
                pOutputSite->GetRate(&sapi_rate);
            }
            if (actions & SPVES_VOLUME) {
                pOutputSite->GetVolume(&sapi_volume);
            }

            if (item.type == WorkItem::Type::Bookmark) {
                long bookmark_id = 0;
                if (!item.text.empty()) {
                    try {
                        bookmark_id = std::stol(item.text);
                    } catch (...) {
                    }
                }
                SPEVENT event = {};
                event.eEventId = SPEI_TTS_BOOKMARK;
                event.elParamType = SPET_LPARAM_IS_STRING;
                event.ullAudioStreamOffset = ctx.bytes_written;
                event.lParam = reinterpret_cast<LPARAM>(item.text.c_str());
                event.wParam = bookmark_id;
                pOutputSite->AddEvents(&event, 1);
                ORPHEUS_LOG("Speak: bookmark \"%S\" at %llu", item.text.c_str(),
                            ctx.bytes_written);
                continue;
            }

            if (item.type == WorkItem::Type::Silence) {
                const ULONGLONG bytes =
                    static_cast<ULONGLONG>(item.silence_ms) * AUDIO_BYTES_PER_SEC / 1000 & ~1ull;
                std::vector<BYTE> zeros(8192, 0);
                ULONGLONG remaining = bytes;
                while (remaining > 0 && !ctx.aborted) {
                    const uint32_t chunk = static_cast<uint32_t>(
                        (std::min)(remaining, static_cast<ULONGLONG>(zeros.size())));
                    if (!write_audio(ctx, zeros.data(), chunk)) {
                        break;
                    }
                    remaining -= chunk;
                }
                if (ctx.aborted) {
                    break;
                }
                continue;
            }

            // Text segment: fire its boundary events, then render.
            for (const UpfrontEvent& upfront : item.events) {
                SPEVENT event = {};
                event.eEventId = upfront.event_id;
                event.elParamType = SPET_LPARAM_IS_UNDEFINED;
                event.ullAudioStreamOffset = ctx.bytes_written;
                event.lParam = upfront.text_offset;
                event.wParam = upfront.text_length;
                pOutputSite->AddEvents(&event, 1);
            }

            const int rate = engine_rate(base.rate, sapi_rate);
            const int pitch = engine_pitch(base.pitch, 0);
            const int volume = engine_volume(base.volume, sapi_volume, 100);
            std::wstring text = build_prefix(base, rate, pitch, volume);
            text += item.text;
            text += L' ';

            bool aborted = false;
            const bool quick_recover = item.text.size() <= QUICK_RECOVER_MAX_CHARS;
            if (!client.speak_segment(text, audio_sink, abort_check, &ctx, &aborted,
                                      quick_recover)) {
                ORPHEUS_LOG("Speak: host transport failure");
                return E_FAIL;
            }
            if (ctx.aborted || aborted) {
                break;
            }
        }

        ORPHEUS_LOG("Speak: done, wrote %llu bytes%s", ctx.bytes_written,
                    ctx.aborted ? " (aborted)" : "");
        return S_OK;
    }
    catch (const std::bad_alloc&) {
        return E_OUTOFMEMORY;
    }
    catch (...) {
        return E_UNEXPECTED;
    }
}

}
}
