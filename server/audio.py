"""Offline audio processing for Resolve clips (the Fairlight mixer/automation has no API).

Standard library only, PCM WAV in / PCM WAV out (16/24/32-bit int; 32-bit float WAV is not handled by the
`wave` module). The original file is never modified: a new file is written next to it (or to `out_path`)
and can then be imported or swapped in with MediaPoolItem.ReplaceClip.

Operations (applied in this order): trim -> gain_db -> volume envelope (dB points, linear in dB) ->
fade in / fade out (linear | equal_power | log) -> normalize (peak dBFS or RMS dBFS) -> safety limiter (hard clip).
Every result is measured back (peak/RMS dBFS overall and around each envelope point).
"""
import array
import math
import os
import struct
import sys
import wave


def db_to_lin(db):
    return 10 ** (db / 20.0)


def lin_to_db(x):
    return 20 * math.log10(x) if x > 0 else -math.inf


def read_wav(path):
    with wave.open(path, "rb") as w:
        ch, sw, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 2:
        a = array.array("h")
        a.frombytes(raw)
        if sys.byteorder == "big":
            a.byteswap()
        data = [x / 32768.0 for x in a]
    elif sw == 4:
        a = array.array("i")
        a.frombytes(raw)
        if sys.byteorder == "big":
            a.byteswap()
        data = [x / 2147483648.0 for x in a]
    elif sw == 3:
        data = [int.from_bytes(raw[i:i + 3], "little", signed=True) / 8388608.0 for i in range(0, len(raw), 3)]
    else:
        raise ValueError("unsupported sample width %d bytes (8-bit WAV)" % sw)
    return {"channels": ch, "width": sw, "rate": rate, "frames": n, "data": data}


def write_wav(path, wav):
    sw, data = wav["width"], wav["data"]
    if sw == 2:
        a = array.array("h", (max(-32768, min(32767, int(round(x * 32767)))) for x in data))
        if sys.byteorder == "big":
            a.byteswap()
        raw = a.tobytes()
    elif sw == 4:
        a = array.array("i", (max(-2147483648, min(2147483647, int(round(x * 2147483647)))) for x in data))
        if sys.byteorder == "big":
            a.byteswap()
        raw = a.tobytes()
    else:
        raw = b"".join(max(-8388608, min(8388607, int(round(x * 8388607)))).to_bytes(3, "little", signed=True) for x in data)
    with wave.open(path, "wb") as w:
        w.setnchannels(wav["channels"])
        w.setsampwidth(sw)
        w.setframerate(wav["rate"])
        w.writeframes(raw)


def measure(wav, start_s=0.0, end_s=None):
    ch, rate, d = wav["channels"], wav["rate"], wav["data"]
    a = int(start_s * rate) * ch
    b = len(d) if end_s is None else min(len(d), int(end_s * rate) * ch)
    seg = d[a:b]
    if not seg:
        return {"peak_dbfs": None, "rms_dbfs": None}
    peak = max(abs(x) for x in seg)
    rms = math.sqrt(sum(x * x for x in seg) / len(seg))
    return {"peak_dbfs": round(lin_to_db(peak), 2), "rms_dbfs": round(lin_to_db(rms), 2)}


def envelope_fn(points):
    """[[t_sec, gain_db], ...] -> gain(t) linear, interpolated linearly in dB, held at the ends."""
    pts = sorted((float(t), float(g)) for t, g in points)

    def f(t):
        if t <= pts[0][0]:
            return db_to_lin(pts[0][1])
        if t >= pts[-1][0]:
            return db_to_lin(pts[-1][1])
        for (t0, g0), (t1, g1) in zip(pts, pts[1:]):
            if t0 <= t <= t1:
                return db_to_lin(g0 + (g1 - g0) * (t - t0) / ((t1 - t0) or 1e-9))
        return 1.0
    return f


def fade_curve(x, shape):
    x = min(max(x, 0.0), 1.0)
    if shape == "equal_power":
        return math.sin(x * math.pi / 2)
    if shape == "log":
        return x * x
    return x


def process(src, out_path=None, *, trim=None, gain_db=0.0, envelope=None, fade_in=0.0, fade_out=0.0,
            fade_shape="equal_power", normalize=None, limit_dbfs=-0.1):
    """normalize: {"mode": "peak"|"rms", "target_dbfs": -1.0}. trim: [start_s, end_s]. Returns a report."""
    wav = read_wav(src)
    before = measure(wav)
    ch, rate = wav["channels"], wav["rate"]
    d = wav["data"]
    if trim:
        a, b = int(trim[0] * rate) * ch, (int(trim[1] * rate) * ch if trim[1] is not None else len(d))
        d = d[a:b]
    nframes = len(d) // ch
    dur = nframes / rate
    env = envelope_fn(envelope) if envelope else None
    g0 = db_to_lin(gain_db)
    fi, fo = int(fade_in * rate), int(fade_out * rate)
    out = [0.0] * len(d)
    for i in range(nframes):
        gain = g0
        if env:
            gain *= env(i / rate)
        if fi and i < fi:
            gain *= fade_curve(i / fi, fade_shape)
        if fo and i >= nframes - fo:
            gain *= fade_curve((nframes - 1 - i) / fo, fade_shape)
        base = i * ch
        for c in range(ch):
            out[base + c] = d[base + c] * gain
    norm_gain_db = 0.0
    if normalize:
        tmp = {"channels": ch, "rate": rate, "data": out}
        m = measure(tmp)
        cur = m["peak_dbfs"] if normalize.get("mode", "peak") == "peak" else m["rms_dbfs"]
        if cur is not None and cur != -math.inf:
            norm_gain_db = float(normalize.get("target_dbfs", -1.0)) - cur
            k = db_to_lin(norm_gain_db)
            out = [x * k for x in out]
    lim = db_to_lin(limit_dbfs)
    clipped = sum(1 for x in out if abs(x) > lim)
    if clipped:
        out = [max(-lim, min(lim, x)) for x in out]
    res = dict(wav, data=out, frames=nframes)
    if out_path is None:
        root, ext = os.path.splitext(src)
        n = 1
        while os.path.exists("%s_claude%d%s" % (root, n, ext)):
            n += 1
        out_path = "%s_claude%d%s" % (root, n, ext)
    write_wav(out_path, res)
    check = read_wav(out_path)
    report = {"source": src, "output": out_path, "duration_s": round(dur, 3), "channels": ch, "rate": rate,
              "bit_depth": wav["width"] * 8, "before": before, "after": measure(check),
              "normalize_gain_db": round(norm_gain_db, 2), "samples_limited": clipped}
    if envelope:
        # measured gain change around each envelope point (RMS in a 50 ms window, after vs before)
        pts = []
        src_trim = dict(wav, data=d)
        for t, g in envelope:
            a, b = max(0.0, t - 0.025), min(dur, t + 0.025)
            mb, ma = measure(src_trim, a, b), measure(check, a, b)
            measured = None if mb["rms_dbfs"] in (None, -math.inf) or ma["rms_dbfs"] in (None, -math.inf) else round(ma["rms_dbfs"] - mb["rms_dbfs"], 2)
            pts.append({"t": t, "requested_db": round(g + gain_db + norm_gain_db, 2), "measured_db": measured})
        report["envelope_check"] = pts
    if normalize:
        key = "peak_dbfs" if normalize.get("mode", "peak") == "peak" else "rms_dbfs"
        report["normalize_check"] = {"target": normalize.get("target_dbfs", -1.0), "actual": report["after"][key],
                                     "ok": abs(report["after"][key] - float(normalize.get("target_dbfs", -1.0))) < 0.2}
    return report
