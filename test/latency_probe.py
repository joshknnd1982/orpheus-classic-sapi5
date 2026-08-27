"""Measure the latency components of the Orpheus host pipeline."""
import os
import struct
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from orpheus_probe import HostClient, ORPHEUS_DIR, CMD_APPEND, CMD_SPEAK_APPEND, CMD_MUTE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def leading_silence_ms(path, channels):
    with wave.open(path, "rb") as wf:
        rate = wf.getframerate()
        data = wf.readframes(wf.getnframes())
    samples = struct.unpack("<%dh" % (len(data) // 2), data)
    threshold = 500
    for i, s in enumerate(samples):
        if abs(s) > threshold:
            frame = i // channels
            return 1000.0 * frame / rate
    return -1


def speak(client, text):
    tb = text.encode("utf-16le")
    pb = struct.pack("<4I", len(text) - 1, 0, 14, 0x80000000)
    client.command(CMD_APPEND, struct.pack("<II", len(pb), len(tb)) + pb + tb)
    client.command(CMD_SPEAK_APPEND)


def main():
    print("=== leading silence in rendered WAVs ===")
    for name, ch in (("test_wavs/00044_UK_English.wav", 2),
                     ("test_wavs/sapi_Orpheus_UK_English.wav", 1)):
        path = os.path.join(ROOT, name)
        if os.path.exists(path):
            print(f"  {name}: {leading_silence_ms(path, ch):.0f} ms")

    print("\n=== host startup time ===")
    t0 = time.time()
    client = HostClient()
    t_spawn = time.time() - t0
    client.initialize(ORPHEUS_DIR)
    t_init = time.time() - t0
    print(f"  spawn+connect: {t_spawn*1000:.0f} ms, +initialize: {t_init*1000:.0f} ms")

    try:
        prefix = "@<9=44>@<0=110>@<1=110>@<2=50>@<5=65>@<3=0>@<4=250>@<7=100>@<12=0> "
        sentence = "The quick brown fox jumps over the lazy dog near the river bank. "
        text600 = prefix + (sentence * 9)[:600]

        print("\n=== mute settle: 600-char segment interrupted at 300 ms ===")
        for trial in range(3):
            with client._audio_lock:
                client._audio_chunks = []
            client._done_event.clear()
            speak(client, text600)
            time.sleep(0.3)
            t0 = time.time()
            try:
                client.command(CMD_MUTE, struct.pack("<I", 3), timeout=10.0)
                ack = time.time() - t0
            except Exception as e:
                ack = -1
            done = client._done_event.wait(10.0)
            settle = time.time() - t0
            print(f"  trial {trial}: mute ack {ack*1000:.0f} ms, done_event after {settle*1000:.0f} ms (done={done})")
            time.sleep(0.2)

        print("\n=== interrupt EARLY (60 ms in) ===")
        for trial in range(3):
            with client._audio_lock:
                client._audio_chunks = []
            client._done_event.clear()
            speak(client, text600)
            time.sleep(0.06)
            t0 = time.time()
            try:
                client.command(CMD_MUTE, struct.pack("<I", 3), timeout=10.0)
                ack = time.time() - t0
            except Exception:
                ack = -1
            done = client._done_event.wait(10.0)
            settle = time.time() - t0
            print(f"  trial {trial}: mute ack {ack*1000:.0f} ms, done after {settle*1000:.0f} ms (done={done})")
            time.sleep(0.2)

        print("\n=== back-to-back: how soon can the next utterance start after mute? ===")
        for trial in range(3):
            with client._audio_lock:
                client._audio_chunks = []
            client._done_event.clear()
            speak(client, text600)
            time.sleep(0.3)
            t0 = time.time()
            try:
                client.command(CMD_MUTE, struct.pack("<I", 3), timeout=10.0)
            except Exception:
                pass
            client._done_event.wait(10.0)
            # new utterance immediately
            with client._audio_lock:
                client._audio_chunks = []
            client._done_event.clear()
            first = [None]
            orig = client._on_audio
            t1 = time.time()
            def wrapped(payload, _fa=first, _t=t1):
                if _fa[0] is None:
                    _fa[0] = time.time() - _t
                orig(payload)
            client._on_audio = wrapped
            speak(client, prefix + "Next line.")
            client._done_event.wait(10.0)
            client._on_audio = orig
            total = time.time() - t0
            print(f"  trial {trial}: mute->new-utterance-first-audio {1000*(t1 - t0 + (first[0] or 0)):.0f} ms (total incl. drain wait)")
            time.sleep(0.2)

        print("\n=== first-audio latency, short utterances (warm host) ===")
        for text in ("Next line.", "Another short line to read.", "OK."):
            with client._audio_lock:
                client._audio_chunks = []
            client._done_event.clear()
            first = [None]
            orig = client._on_audio
            t0 = time.time()
            def wrapped(payload, _fa=first, _t=t0):
                if _fa[0] is None:
                    _fa[0] = time.time() - _t
                orig(payload)
            client._on_audio = wrapped
            speak(client, prefix + text)
            client._done_event.wait(10.0)
            client._on_audio = orig
            print(f"  '{text}': first audio {first[0]*1000:.0f} ms")
    finally:
        client.close()

    print("\n=== fresh host cold start, repeated ===")
    for trial in range(3):
        t0 = time.time()
        c = HostClient()
        c.initialize(ORPHEUS_DIR)
        t_ready = time.time() - t0
        first = [None]
        orig = c._on_audio
        t1 = time.time()
        def wrapped(payload, _fa=first, _t=t1):
            if _fa[0] is None:
                _fa[0] = time.time() - _t
            orig(payload)
        c._on_audio = wrapped
        speak(c, "@<9=44> Cold start line.")
        c._done_event.wait(10.0)
        c.close()
        print(f"  trial {trial}: ready in {t_ready*1000:.0f} ms, then first audio {first[0]*1000:.0f} ms")


if __name__ == "__main__":
    main()
