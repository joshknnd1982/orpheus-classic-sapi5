"""Standalone test client for orpheus-classic-host.exe.

Drives the host over its framed TCP protocol (no NVDA, no SAPI4, no
pre-existing registry configuration) and renders one WAV per language.

Usage: python orpheus_probe.py [output_dir]
"""
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST_EXE = os.path.join(ROOT, "bin", "orpheus-classic-host.exe")
ORPHEUS_DIR = os.path.join(ROOT, "bin", "orpheus")

FRAME_COMMAND = 1
FRAME_RESPONSE = 2
FRAME_EVENT = 3
CMD_INITIALIZE = 1
CMD_APPEND = 2
CMD_SPEAK_APPEND = 3
CMD_MUTE = 4
CMD_CONFIG = 5
CMD_GET_PARAMS = 6
CMD_GET_LANGS = 7
CMD_GET_VOICES = 8
CMD_CLOSE = 9
EV_AUDIO = 1
STATUS_OK = 0

SAMPLE_RATE = 22050
CHANNELS = 2
SAMPLE_BITS = 16

SAMPLE_TEXTS = {
    1: "Hello! This is the U S English voice of Orpheus Classic.",
    44: "Hello! This is the British English voice of Orpheus Classic.",
    31: "Hallo! Dit is de Nederlandse stem van Orpheus Classic.",
    33: "Bonjour! Ceci est la voix francaise d'Orpheus Classic.",
    34: "Hola! Esta es la voz del espanol de Castilla de Orpheus Classic.",
    39: "Ciao! Questa e la voce italiana di Orpheus Classic.",
    46: "Hej! Det har ar den svenska rosten i Orpheus Classic.",
    49: "Hallo! Dies ist die deutsche Stimme von Orpheus Classic.",
    52: "Hola! Esta es la voz del espanol latinoamericano de Orpheus Classic.",
}


class HostClient:
    def __init__(self):
        self._next_id = 1
        self._responses = {}
        self._resp_lock = threading.Lock()
        self._audio_lock = threading.Lock()
        self._audio_chunks = []
        self._controls = []
        self._done_event = threading.Event()
        self._last_audio = 0.0
        self._stop = False

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.settimeout(10.0)
        addr = server.getsockname()
        self.process = subprocess.Popen(
            [HOST_EXE, "--address", f"{addr[0]}:{addr[1]}"],
            cwd=os.path.dirname(HOST_EXE),
        )
        self.conn, _ = server.accept()
        self.conn.settimeout(0.5)
        server.close()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_exact(self, n):
        chunks = []
        while n:
            try:
                chunk = self.conn.recv(n)
            except socket.timeout:
                if self._stop:
                    return None
                continue
            except OSError:
                return None
            if not chunk:
                return None
            chunks.append(chunk)
            n -= len(chunk)
        return b"".join(chunks)

    def _read_loop(self):
        while not self._stop:
            header = self._read_exact(4)
            if header is None:
                break
            (length,) = struct.unpack("<I", header)
            frame = self._read_exact(length)
            if frame is None:
                break
            ftype = frame[0]
            if ftype == FRAME_RESPONSE:
                msg_id, status = struct.unpack_from("<II", frame, 1)
                with self._resp_lock:
                    slot = self._responses.get(msg_id)
                    if slot is not None:
                        slot[1] = (status, frame[9:])
                        slot[0].set()
            elif ftype == FRAME_EVENT:
                (event,) = struct.unpack_from("<H", frame, 1)
                if event == EV_AUDIO:
                    self._on_audio(frame[3:])

    def _on_audio(self, payload):
        offset = 0
        (audio_len,) = struct.unpack_from("<I", payload, offset)
        offset += 4
        audio = payload[offset:offset + audio_len]
        offset += audio_len
        (controls_len,) = struct.unpack_from("<I", payload, offset)
        offset += 4
        controls = list(struct.iter_unpack("<III", payload[offset:offset + controls_len]))
        with self._audio_lock:
            self._audio_chunks.append(audio)
            self._controls.extend(controls)
            self._last_audio = time.time()
            for _pos, _typ, value in controls:
                if value & 0x80000000:
                    self._done_event.set()

    def command(self, cmd_id, data=b"", timeout=30.0):
        with self._resp_lock:
            msg_id = self._next_id
            self._next_id += 1
            evt = threading.Event()
            self._responses[msg_id] = [evt, None]
        frame = struct.pack("<BIH", FRAME_COMMAND, msg_id, cmd_id) + data
        self.conn.sendall(struct.pack("<I", len(frame)) + frame)
        if not evt.wait(timeout):
            raise RuntimeError(f"Timed out waiting for command {cmd_id}")
        with self._resp_lock:
            status, payload = self._responses.pop(msg_id)[1]
        if status != STATUS_OK:
            raise RuntimeError(
                f"Command {cmd_id} failed: {payload.decode('utf-8', 'replace')}"
            )
        return payload

    def initialize(self, orpheus_dir):
        path = orpheus_dir.encode("utf-8")
        self.command(CMD_INITIALIZE, struct.pack("<I", len(path)) + path, timeout=60.0)

    @staticmethod
    def _read_string(payload, offset):
        (length,) = struct.unpack_from("<I", payload, offset)
        offset += 4
        return payload[offset:offset + length].decode("utf-8", "replace"), offset + length

    def get_params(self):
        payload = self.command(CMD_GET_PARAMS)
        (count,) = struct.unpack_from("<I", payload, 0)
        offset = 4
        result = []
        for _ in range(count):
            minv, maxv, current = struct.unpack_from("<iii", payload, offset)
            offset += 12
            name, offset = self._read_string(payload, offset)
            result.append({"min": minv, "max": maxv, "current": current, "name": name})
        return result

    def get_langs(self):
        payload = self.command(CMD_GET_LANGS)
        (count,) = struct.unpack_from("<I", payload, 0)
        offset = 4
        result = []
        for _ in range(count):
            (country,) = struct.unpack_from("<I", payload, offset)
            offset += 4
            lang, offset = self._read_string(payload, offset)
            name = None
            if offset + 4 <= len(payload):
                try:
                    name, offset = self._read_string(payload, offset)
                except Exception:
                    name = None
            result.append({"country": country, "lang": lang, "name": name or lang})
        return result

    def get_voices(self, country):
        payload = self.command(CMD_GET_VOICES, struct.pack("<I", country))
        (count,) = struct.unpack_from("<I", payload, 0)
        offset = 4
        result = []
        for _ in range(count):
            name, offset = self._read_string(payload, offset)
            result.append(name)
        return result

    def render(self, text, timeout=60.0):
        """Speak text, return (pcm bytes, controls)."""
        with self._audio_lock:
            self._audio_chunks = []
            self._controls = []
        self._done_event.clear()
        text_bytes = text.encode("utf-16le")
        # Final index marker at end of text: param 14, value flag 0x80000000.
        params = struct.pack("<4I", max(0, len(text) - 1), 0, 14, 0x80000000)
        data = struct.pack("<II", len(params), len(text_bytes)) + params + text_bytes
        self.command(CMD_APPEND, data)
        self.command(CMD_SPEAK_APPEND)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._done_event.wait(0.2):
                break
            with self._audio_lock:
                last = self._last_audio
            if last and time.time() - last > 3.0:
                break  # fallback: silence for 3s after audio started
        # small drain for trailing audio frames
        time.sleep(0.3)
        with self._audio_lock:
            return b"".join(self._audio_chunks), list(self._controls)

    def close(self):
        try:
            self.command(CMD_CLOSE, timeout=3.0)
        except Exception:
            pass
        self._stop = True
        try:
            self.conn.close()
        except Exception:
            pass
        try:
            self.process.wait(timeout=3.0)
        except Exception:
            self.process.terminate()


def analyze_stereo(pcm):
    """Check whether interleaved stereo pairs are identical (mono content)."""
    same = 0
    total = 0
    for i in range(0, min(len(pcm) - 3, 40000), 4):
        left = struct.unpack_from("<h", pcm, i)[0]
        right = struct.unpack_from("<h", pcm, i + 2)[0]
        total += 1
        if left == right:
            same += 1
    return same, total


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "test_wavs")
    os.makedirs(out_dir, exist_ok=True)

    client = HostClient()
    try:
        client.initialize(ORPHEUS_DIR)
        print("Initialized OK")

        langs = client.get_langs()
        print(f"\n=== Languages ({len(langs)}) ===")
        for lang in langs:
            print(f"  country={lang['country']:5d} lang={lang['lang']:8s} name={lang['name']}")

        print("\n=== Parameters (startup) ===")
        for i, p in enumerate(client.get_params()):
            print(f"  [{i:2d}] {p['name']:<20s} min={p['min']:6d} max={p['max']:6d} current={p['current']:6d}")

        print("\n=== Voices per language ===")
        for lang in langs:
            try:
                voices = client.get_voices(lang["country"])
            except Exception as exc:
                voices = [f"<error: {exc}>"]
            print(f"  country={lang['country']:5d} ({lang['name']}): {voices}")

        prefix_params = [(0, 110), (1, 110), (2, 50), (5, 65), (3, 0), (4, 240), (7, 100), (12, 0)]
        for lang in langs:
            country = lang["country"]
            text = SAMPLE_TEXTS.get(country, "Hello from Orpheus Classic.")
            raw = "@<9=%d>" % country
            raw += "".join("@<%d=%d>" % pv for pv in prefix_params)
            full = raw + " " + text + " "
            print(f"\nRendering country={country} ({lang['name']})...")
            start = time.time()
            pcm, controls = client.render(full)
            elapsed = time.time() - start
            if not pcm:
                print(f"  !! NO AUDIO for {lang['name']}")
                continue
            same, total = analyze_stereo(pcm)
            secs = len(pcm) / (SAMPLE_RATE * CHANNELS * SAMPLE_BITS // 8)
            print(f"  {len(pcm)} bytes ({secs:.2f}s), rendered in {elapsed:.2f}s, "
                  f"L==R pairs: {same}/{total}, controls: {len(controls)}")
            wav_path = os.path.join(out_dir, f"{country:05d}_{lang['name'].replace(' ', '_')}.wav")
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(SAMPLE_BITS // 8)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(pcm)
            print(f"  wrote {wav_path}")

            print("  params after voice switch:")
            for i, p in enumerate(client.get_params()):
                print(f"    [{i:2d}] {p['name']:<20s} min={p['min']:6d} max={p['max']:6d} current={p['current']:6d}")
    finally:
        client.close()
    print("\nAll done.")


if __name__ == "__main__":
    main()
