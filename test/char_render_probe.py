"""Measure render duration/audio length for single characters at various
rates and pause settings."""
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from orpheus_probe import HostClient, ORPHEUS_DIR, CMD_APPEND, CMD_SPEAK_APPEND


def render(client, text):
    with client._audio_lock:
        client._audio_chunks = []
    client._done_event.clear()
    tb = text.encode("utf-16le")
    pb = struct.pack("<4I", len(text) - 1, 0, 14, 0x80000000)
    t0 = time.time()
    client.command(CMD_APPEND, struct.pack("<II", len(pb), len(tb)) + pb + tb)
    client.command(CMD_SPEAK_APPEND)
    client._done_event.wait(15)
    wall = time.time() - t0
    with client._audio_lock:
        n = sum(len(c) for c in client._audio_chunks)
    return n, wall


def main():
    client = HostClient()
    try:
        client.initialize(ORPHEUS_DIR)
        for rate in (110, 200, 300, 450):
            for pauses, label in (("", "default pauses"), ("@<3=0>@<4=0>", "pauses zeroed")):
                text = f"@<9=44>@<0={rate}>@<1=110>{pauses} a"
                n, wall = render(client, text)
                secs = n / 88200.0
                print(f"rate {rate:3d} {label:15s}: 'a' = {secs:.2f}s audio, rendered in {wall*1000:.0f} ms")
    finally:
        client.close()


if __name__ == "__main__":
    main()
