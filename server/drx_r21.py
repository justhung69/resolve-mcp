"""Make DRX grade files written by the samuelgursky advanced generator render on Resolve 21.

The generator (calibrated on Resolve 19) writes a power window as corrector type 4 holding only
the parameters that differ from neutral. Resolve 21.0.4 loads that into the Window palette (the panel
reads back correctly), but the rendered mask is EMPTY: the node's grade disappears from the whole
frame. Grades exported by Resolve 21 itself store the circle/linear window transform as corrector
type 65540 (0x10004) with the full parameter set, including REF_WIDTH 288.0 and an identity 3x3 transform
matrix. They also list 65540 in the node's packed corrector-type list.
This module rewrites the window block into that measured form. Everything else is copied byte for byte.
Measured on 21.0.4 Free, see COVERAGE.md "DRX grades".
"""
import re
import struct
from compression import zstd

CT_WINDOW_R19 = 4
CT_WINDOW_R21 = 0x10004
QUAL_MODE, QUAL_MATRIX = 0x88300001, 0x88300031
ROTATE, ASPECT, SOFT_REF, SIZE, PAN, TILT, OPACITY = (0x8500001, 0x8500004, 0x8500005, 0x8500006,
                                                       0x850000b, 0x850000c, 0x8500012)
WTYPE, REF_WIDTH, UNK_0E, UNK_0F, XFORM = 0x88500008, 0x8850000d, 0x8850000e, 0x8850000f, 0x88500010
# Order and neutral values exactly as Resolve 21.0.4 exports a fresh circle window.
R21_ORDER = [(ROTATE, 0.0), (ASPECT, 0.0), (SOFT_REF, 0.0), (SIZE, 1.0), (PAN, 0.0), (TILT, 0.0)]


# ------------------------------------------------------------------ protobuf wire helpers

def _varint(b, i):
    r = s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        s += 7
        if c < 0x80:
            return r, i


def _enc_varint(n):
    out = bytearray()
    while True:
        c = n & 0x7F
        n >>= 7
        out.append(c | (0x80 if n else 0))
        if not n:
            return bytes(out)


def parse(b):
    """-> list of [field, wiretype, value]; value is bytes for wire types 1/2/5, int for 0."""
    i, out = 0, []
    while i < len(b):
        k, i = _varint(b, i)
        f, w = k >> 3, k & 7
        if w == 0:
            v, i = _varint(b, i)
        elif w == 1:
            v, i = b[i:i + 8], i + 8
        elif w == 5:
            v, i = b[i:i + 4], i + 4
        elif w == 2:
            n, i = _varint(b, i)
            v, i = b[i:i + n], i + n
        else:
            raise ValueError("unsupported wire type %d" % w)
        out.append([f, w, v])
    if i != len(b):
        raise ValueError("truncated message")
    return out


def build(fields):
    out = bytearray()
    for f, w, v in fields:
        out += _enc_varint(f << 3 | w)
        if w == 0:
            out += _enc_varint(v)
        elif w == 2:
            out += _enc_varint(len(v)) + v
        else:
            out += v
    return bytes(out)


def _get(fields, f):
    return [x for x in fields if x[0] == f]


# ------------------------------------------------------------------ DRX parameter entries

def _param_id(entry):
    return _get(parse(entry), 1)[0][2]


def _float_param(pid, value):
    return build([[1, 0, pid], [2, 2, build([[1, 5, struct.pack("<f", value)]])]])


def _varint_param(pid, value):
    return build([[1, 0, pid], [2, 2, build([[2, 0, value]])]])


def _identity_param(pid):
    m = b"".join(build([[k, 5, struct.pack("<f", 1.0 if k in (1, 5, 9) else 0.0)]]) for k in range(1, 10))
    return build([[1, 0, pid], [2, 2, build([[10, 2, m]])]])


def _block_params(block):
    """corrector block -> (header fields, list of raw parameter entries)."""
    f6 = _get(block, 6)[0][2]
    f2 = _get(parse(f6), 2)[0][2]
    inner = parse(f2)
    return inner, [v for f, w, v in inner if f == 3]


def _with_params(block, params):
    inner, _ = _block_params(block)
    inner = [x for x in inner if x[0] != 3] + [[3, 2, p] for p in params]
    return [x if x[0] != 6 else [6, 2, build([[2, 2, build(inner)]])] for x in block]


def _r21_window(params, overrides=None):
    by_id = {_param_id(p): p for p in params}
    for pid, val in (overrides or {}).items():
        by_id[pid] = _float_param(pid, val)
    out = []
    for pid, neutral in R21_ORDER:
        out.append(by_id.pop(pid, None) or _float_param(pid, neutral))
    by_id.pop(WTYPE, None)
    out += [_varint_param(WTYPE, 2), _float_param(REF_WIDTH, 288.0), _float_param(UNK_0E, 0.0),
            _float_param(UNK_0F, 0.0), by_id.pop(XFORM, None) or _identity_param(XFORM)]
    return out + list(by_id.values())  # e.g. OPACITY, kept after the measured set


def _fix_node(node, overrides=None):
    changed = False
    for i, (f, w, v) in enumerate(node):
        if f != 9 or w != 2:
            continue
        corr = parse(v)
        for c in corr:
            if c[0] != 1:
                continue
            block = parse(c[2])
            ctype = _get(block, 1)[0][2]
            if ctype == CT_WINDOW_R19:
                _, params = _block_params(block)
                block = [[1, 0, CT_WINDOW_R21] if x[0] == 1 else x for x in _with_params(block, _r21_window(params, overrides))]
                c[2] = build(block)
                changed = True
            elif ctype == 2:  # qualifier: Resolve 21 always carries the identity matrix next to the mode
                _, params = _block_params(block)
                if QUAL_MODE in {_param_id(p) for p in params} and QUAL_MATRIX not in {_param_id(p) for p in params}:
                    c[2] = build(_with_params(block, params + [_identity_param(QUAL_MATRIX)]))
                    changed = True
        if changed:
            for c in corr:
                if c[0] == 2 and c[1] == 2:
                    types, j = [], 0
                    while j < len(c[2]):
                        t, j = _varint(c[2], j)
                        types.append(t)
                    if CT_WINDOW_R21 not in types:
                        c[2] = b"".join(_enc_varint(t) for t in types + [CT_WINDOW_R21])
            node[i] = [f, w, build(corr)]
    return changed


def _fix_body(raw, overrides=None):
    """Walk version message -> F1 clip graph -> F7 nodes."""
    top = parse(raw)
    n = 0
    for x in top:
        if x[0] in (1, 3) and x[1] == 2:
            g = parse(x[2])
            for y in g:
                if y[0] == 7 and y[1] == 2:
                    node = parse(y[2])
                    if _fix_node(node, overrides):
                        y[2] = build(node)
                        n += 1
            x[2] = build(g)
    return build(top), n


def to_r21(src_path, dst_path, overrides=None):
    """Rewrite every <Body> in a .drx. Returns the number of nodes changed.
    overrides: {param_id: raw float} written into the window block (calibration / exact values)."""
    s = open(src_path, encoding="utf-8").read()
    total = 0

    def sub(m):
        nonlocal total
        b = bytes.fromhex(m.group(1))
        if b[:1] != b"\x81" or b[1:5] != bytes.fromhex("28b52ffd"):
            return m.group(0)  # not the zstd form this was measured on: leave untouched
        raw, n = _fix_body(zstd.decompress(b[1:]), overrides)
        total += n
        return "<Body>" + (b"\x81" + zstd.compress(raw)).hex() + "</Body>"

    s = re.sub(r"<Body>([0-9a-f]+)</Body>", sub, s)
    with open(dst_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(s)
    return total


# ------------------------------------------------------------------ calibrated window scales (21.0.4 panel readback)

def size_to_raw(ui):
    """Window Size. Panel readback on 21.0.4: raw 0.2 -> 9.92, 0.5 -> 24.95, 1.0 -> 50, 1.8 -> 60, 3.0 -> 75.
    Above 50 it is 1 + (UI-50) * 0.08 (as samuelgursky measured). Below 50 it is linear through the origin,
    not that slope, which would go negative for UI < 37.5 and give an empty window."""
    ui = float(ui)
    return 1 + (ui - 50) * 0.08 if ui >= 50 else (ui + 0.1) / 50.1


def size_to_ui(raw):
    return 50 + (raw - 1) / 0.08 if raw >= 1 else raw * 50.1 - 0.1


WINDOW_TO_UI = {ROTATE: ("rotate", lambda v: -v * 180), ASPECT: ("aspect", lambda v: 50 - v * 50),
                SOFT_REF: ("soft1", lambda v: v / 16), SIZE: ("size", size_to_ui),
                PAN: ("pan", lambda v: 50 + v / 4096 * 50), TILT: ("tilt", lambda v: 50 + v / 4096 * 50),
                OPACITY: ("opacity", lambda v: v * 100)}


def read_windows(drx_path):
    """Decode the window transform blocks of a .drx into UI numbers, one dict per window block."""
    s = open(drx_path, encoding="utf-8").read()
    found = []

    def walk(b, depth=0):
        if depth > 14:
            return
        try:
            fs = parse(b)
        except Exception:
            return
        if _get(fs, 1) and _get(fs, 1)[0][1] == 0 and _get(fs, 1)[0][2] in (CT_WINDOW_R19, CT_WINDOW_R21) and _get(fs, 6):
            _, params = _block_params(fs)
            w = {"corrector_type": _get(fs, 1)[0][2]}
            for p in params:
                pf = parse(p)
                pid = _get(pf, 1)[0][2]
                if pid in WINDOW_TO_UI:
                    v = struct.unpack("<f", parse(_get(pf, 2)[0][2])[0][2])[0]
                    name, conv = WINDOW_TO_UI[pid]
                    w[name] = round(conv(v), 2)
            found.append(w)
            return
        for f, wt, v in fs:
            if wt == 2 and v:
                walk(v, depth + 1)

    for body in re.findall(r"<Body>([0-9a-f]+)</Body>", s)[:1]:  # first Body = the clip grade
        b = bytes.fromhex(body)
        if b[:1] == b"\x81":
            walk(zstd.decompress(b[1:]))
    return found
