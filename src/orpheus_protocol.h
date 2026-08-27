#pragma once

// Wire protocol of orpheus-classic-host.exe.
//
// Transport: the client opens a TCP listener on 127.0.0.1 and spawns the host
// with "--address 127.0.0.1:<port>"; the host connects back.  Every frame is
// <u32 length><payload>.  Command payloads are
// <u8 FRAME_COMMAND><u32 msg_id><u16 command_id><data>, responses are
// <u8 FRAME_RESPONSE><u32 msg_id><u32 status><data> and events are
// <u8 FRAME_EVENT><u16 event_id><data>.

#include <stdint.h>

namespace Orpheus {
namespace protocol {

constexpr uint8_t FRAME_COMMAND = 1;
constexpr uint8_t FRAME_RESPONSE = 2;
constexpr uint8_t FRAME_EVENT = 3;

constexpr uint16_t CMD_INITIALIZE = 1;   // u32 len + utf-8 path of the orpheus data dir
constexpr uint16_t CMD_APPEND = 2;       // u32 params_len, u32 text_len, params, utf-16le text
constexpr uint16_t CMD_SPEAK_APPEND = 3; // no data; starts rendering appended text
constexpr uint16_t CMD_MUTE = 4;         // u32 value (3 = stop speech)
constexpr uint16_t CMD_CONFIG = 5;
constexpr uint16_t CMD_GET_PARAMS = 6;   // -> u32 count + {i32 min,max,current + str name}
constexpr uint16_t CMD_GET_LANGS = 7;    // -> u32 count + {u32 country + str code + str name}
constexpr uint16_t CMD_GET_VOICES = 8;   // u32 country -> u32 count + {str name}
constexpr uint16_t CMD_CLOSE = 9;

constexpr uint16_t EV_AUDIO = 1;         // u32 audio_len, pcm, u32 controls_len, {u32 pos,type,value}*

constexpr uint32_t STATUS_OK = 0;

// A control value with this bit set marks the end of the utterance.
constexpr uint32_t CONTROL_FINAL_FLAG = 0x80000000u;

// Inline "@<param=value>" command ids understood by the engine (Build 18).
constexpr int PARAM_RATE = 0;          // 40..700, default 110
constexpr int PARAM_PITCH = 1;         // 50..500, default 110
constexpr int PARAM_INTONATION = 2;    // "Prosody", 0..100, default 50
constexpr int PARAM_WORD_PAUSE = 3;    // 0..1000 ms, default 0
constexpr int PARAM_PHRASE_PAUSE = 4;  // 0..2000 ms, default 250
constexpr int PARAM_VOICING = 5;       // 0..100, default 65
constexpr int PARAM_VOLUME = 7;        // 0..100
constexpr int PARAM_LANGUAGE = 9;      // country code (1, 31, 33, ...)
constexpr int PARAM_VOICE_SOURCE = 12; // 0 or 1
constexpr int PARAM_INDEX = 14;        // index marker (via the parameters block)

// The host streams 16-bit PCM at 22050 Hz with both stereo channels carrying
// identical samples; the client downmixes to mono.
constexpr uint32_t HOST_SAMPLE_RATE = 22050;
constexpr uint32_t HOST_CHANNELS = 2;
constexpr uint32_t HOST_BITS = 16;

}
}
