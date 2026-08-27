"""Two host processes rendering at the same time."""
import os
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from orpheus_probe import HostClient, ORPHEUS_DIR, CMD_APPEND, CMD_SPEAK_APPEND


def render(client, text, label, results):
    tb = text.encode("utf-16le")
    pb = struct.pack("<4I", len(text) - 1, 0, 14, 0x80000000)
    client.command(CMD_APPEND, struct.pack("<II", len(pb), len(tb)) + pb + tb)
    client.command(CMD_SPEAK_APPEND)
    ok = client._done_event.wait(30)
    with client._audio_lock:
        n = sum(len(c) for c in client._audio_chunks)
    results[label] = (ok, n)


def main():
    a = HostClient()
    b = HostClient()
    try:
        a.initialize(ORPHEUS_DIR)
        b.initialize(ORPHEUS_DIR)
        print("both initialized")
        results = {}
        t1 = threading.Thread(target=render, args=(a, "@<9=44> Host one speaking the first test sentence with several words.", "A", results))
        t2 = threading.Thread(target=render, args=(b, "@<9=49> Host zwei spricht den zweiten Testsatz mit mehreren Woertern.", "B", results))
        t1.start(); t2.start()
        t1.join(); t2.join()
        print("results:", results)
    finally:
        a.close()
        b.close()


if __name__ == "__main__":
    main()
