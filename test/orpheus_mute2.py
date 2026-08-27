"""Measure real mute ack latency and post-mute behavior."""
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from orpheus_probe import HostClient, ORPHEUS_DIR, CMD_APPEND, CMD_SPEAK_APPEND, CMD_MUTE


def speak(client, text):
    tb = text.encode("utf-16le")
    pb = struct.pack("<4I", len(text) - 1, 0, 14, 0x80000000)
    client.command(CMD_APPEND, struct.pack("<II", len(pb), len(tb)) + pb + tb)
    client.command(CMD_SPEAK_APPEND)


def main():
    client = HostClient()
    try:
        client.initialize(ORPHEUS_DIR)
        prefix = "@<9=44>@<0=110>@<1=110> "
        long_text = prefix + ("The quick brown fox jumps over the lazy dog. " * 20)

        with client._audio_lock:
            client._audio_chunks = []
        client._done_event.clear()
        speak(client, long_text)
        time.sleep(0.5)
        t0 = time.time()
        try:
            client.command(CMD_MUTE, struct.pack("<I", 3), timeout=30.0)
            print(f"mute ack in {time.time() - t0:.3f}s")
        except Exception as e:
            print(f"mute failed: {e}")
        with client._audio_lock:
            n0 = sum(len(c) for c in client._audio_chunks)
        done_after = client._done_event.is_set()
        print(f"audio at mute-ack: {n0} bytes, done_event={done_after}")
        for i in range(4):
            time.sleep(0.5)
            with client._audio_lock:
                n = sum(len(c) for c in client._audio_chunks)
            print(f"  +{(i + 1) * 0.5:.1f}s: {n} bytes, done={client._done_event.is_set()}")

        print("\n--- speak again after mute ---")
        with client._audio_lock:
            client._audio_chunks = []
        client._done_event.clear()
        t0 = time.time()
        speak(client, prefix + "Speaking again after mute works fine.")
        ok = client._done_event.wait(15)
        with client._audio_lock:
            n = sum(len(c) for c in client._audio_chunks)
        print(f"done={ok} in {time.time() - t0:.2f}s, audio bytes={n}")

        print("\n--- short utterance latency (time to first audio) ---")
        for text in ["Hello.", "This is a responsiveness test.", "OK"]:
            with client._audio_lock:
                client._audio_chunks = []
            client._done_event.clear()
            first_audio = [None]
            orig = client._on_audio
            t0 = time.time()
            def wrapped(payload, _t0=t0, _fa=first_audio):
                if _fa[0] is None:
                    _fa[0] = time.time() - _t0
                orig(payload)
            client._on_audio = wrapped
            speak(client, prefix + text)
            client._done_event.wait(15)
            client._on_audio = orig
            with client._audio_lock:
                n = sum(len(c) for c in client._audio_chunks)
            print(f"  '{text}': first audio after {first_audio[0]:.3f}s, total {n} bytes")
    finally:
        client.close()


if __name__ == "__main__":
    main()
