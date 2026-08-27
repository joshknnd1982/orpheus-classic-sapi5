"""Test intermediate index markers and mute responsiveness."""
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from orpheus_probe import HostClient, ORPHEUS_DIR, CMD_APPEND, CMD_SPEAK_APPEND, CMD_MUTE


def main():
    client = HostClient()
    try:
        client.initialize(ORPHEUS_DIR)
        print("Initialized OK")

        prefix = "@<9=44>@<0=110>@<1=110> "
        words = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
        text = prefix
        params = []
        idx = 1
        for w in words:
            params.append((len(text), 0, 14, idx))  # marker before each word
            idx += 1
            text += w + " "
        params.append((max(0, len(text) - 1), 0, 14, idx | 0x80000000))

        with client._audio_lock:
            client._audio_chunks = []
            client._controls = []
        client._done_event.clear()

        text_bytes = text.encode("utf-16le")
        param_bytes = b"".join(struct.pack("<4I", *p) for p in params)
        data = struct.pack("<II", len(param_bytes), len(text_bytes)) + param_bytes + text_bytes
        client.command(CMD_APPEND, data)

        # instrument: track chunk boundaries
        chunk_log = []
        orig = client._on_audio
        def wrapped(payload):
            offset = 0
            (audio_len,) = struct.unpack_from("<I", payload, offset)
            offset += 4 + audio_len
            (controls_len,) = struct.unpack_from("<I", payload, offset)
            offset += 4
            controls = list(struct.iter_unpack("<III", payload[offset:offset + controls_len]))
            chunk_log.append((audio_len, controls))
            orig(payload)
        client._on_audio = wrapped

        client.command(CMD_SPEAK_APPEND)
        deadline = time.time() + 30
        while time.time() < deadline and not client._done_event.wait(0.2):
            pass
        time.sleep(0.3)

        total = 0
        print(f"\n{len(chunk_log)} audio chunks:")
        for audio_len, controls in chunk_log:
            desc = ""
            for pos, typ, value in controls:
                final = " FINAL" if value & 0x80000000 else ""
                desc += f" [pos={pos} type={typ} value={value & 0x7FFFFFFF}{final}]"
            print(f"  chunk {audio_len:7d} bytes at stream {total:8d}{desc}")
            total += audio_len

        # Mute responsiveness test
        print("\n--- mute test: speak long text, mute after 0.5s ---")
        with client._audio_lock:
            client._audio_chunks = []
            client._controls = []
        client._done_event.clear()
        long_text = prefix + ("The quick brown fox jumps over the lazy dog. " * 20)
        tb = long_text.encode("utf-16le")
        pb = struct.pack("<4I", len(long_text) - 1, 0, 14, 0x80000000)
        client.command(CMD_APPEND, struct.pack("<II", len(pb), len(tb)) + pb + tb)
        client.command(CMD_SPEAK_APPEND)
        time.sleep(0.5)
        t0 = time.time()
        client.command(CMD_MUTE, struct.pack("<I", 3), timeout=5.0)
        print(f"mute ack in {time.time() - t0:.3f}s")
        time.sleep(1.0)
        with client._audio_lock:
            n1 = sum(len(c) for c in client._audio_chunks)
        time.sleep(1.0)
        with client._audio_lock:
            n2 = sum(len(c) for c in client._audio_chunks)
        print(f"audio bytes 1s after mute: {n1}, 2s after: {n2} (stopped: {n1 == n2})")

        # can we speak again after mute?
        print("\n--- speak after mute ---")
        with client._audio_lock:
            client._audio_chunks = []
        client._done_event.clear()
        t = prefix + "Speaking again after mute works."
        tb = t.encode("utf-16le")
        pb = struct.pack("<4I", len(t) - 1, 0, 14, 0x80000000)
        client.command(CMD_APPEND, struct.pack("<II", len(pb), len(tb)) + pb + tb)
        client.command(CMD_SPEAK_APPEND)
        ok = client._done_event.wait(15)
        with client._audio_lock:
            n = sum(len(c) for c in client._audio_chunks)
        print(f"done={ok}, audio bytes={n}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
