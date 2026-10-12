"""Generate throwaway test media with the standard library only (no ffmpeg needed):
PNG image sequences (imported by Resolve as video clips) and a WAV tone."""
import math
import os
import struct
import wave
import zlib


def write_png(path, w, h, rgb_rows):
    raw = b"".join(b"\x00" + row for row in rgb_rows)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def image_sequence(folder, prefix, frames, color, w=320, h=180):
    """Solid colour frames with a white bar whose position encodes the frame number."""
    os.makedirs(folder, exist_ok=True)
    bg = bytes(color) * w
    for i in range(frames):
        x = int(i * (w - 8) / max(1, frames - 1))
        bar = bytes(color) * x + b"\xff\xff\xff" * 8 + bytes(color) * (w - x - 8)
        rows = [bar if 70 <= y < 110 else bg for y in range(h)]
        write_png(os.path.join(folder, "%s_%04d.png" % (prefix, i)), w, h, rows)
    return os.path.join(folder, "%s_%%04d.png" % prefix)


def tone(path, seconds=2.0, freq=440.0, rate=48000):
    with wave.open(path, "wb") as wv:
        wv.setnchannels(2)
        wv.setsampwidth(2)
        wv.setframerate(rate)
        frames = bytearray()
        for n in range(int(seconds * rate)):
            s = int(12000 * math.sin(2 * math.pi * freq * n / rate))
            frames += struct.pack("<hh", s, s)
        wv.writeframes(bytes(frames))
    return path


def lut_cube(path, size=17, warm=0.08):
    """A mild warm-look 3D LUT (.cube) for SetLUT tests."""
    with open(path, "w") as f:
        f.write('TITLE "ClaudeMCP test warm"\nLUT_3D_SIZE %d\n' % size)
        for b in range(size):
            for g in range(size):
                for r in range(size):
                    rr, gg, bb = r / (size - 1), g / (size - 1), b / (size - 1)
                    f.write("%.6f %.6f %.6f\n" % (min(1, rr * (1 + warm)), gg, max(0, bb * (1 - warm))))
    return path


def make_all(root):
    os.makedirs(root, exist_ok=True)
    seq_a = image_sequence(os.path.join(root, "clipA"), "clipA", 48, (200, 40, 40))
    seq_b = image_sequence(os.path.join(root, "clipB"), "clipB", 48, (40, 60, 200))
    wav = tone(os.path.join(root, "tone.wav"))
    return {"seq_a": seq_a, "seq_a_range": (0, 47), "seq_b": seq_b, "seq_b_range": (0, 47), "wav": wav}


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(make_all(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ["TEMP"], "resolve_mcp_media")), indent=1))
