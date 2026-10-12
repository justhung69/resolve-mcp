"""Tiny PNG reader (standard library) for pixel checks in tests: mean and max luma of an image.
Handles non-interlaced 8/16-bit greyscale, RGB and RGBA, all five scanline filters."""
import struct
import zlib


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else (b if pb <= pc else c)


def read_png(path):
    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, idat, ihdr = 8, b"", None
    while pos < len(data):
        n, tag = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + n]
        if tag == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", chunk)
        elif tag == b"IDAT":
            idat += chunk
        pos += 12 + n
    w, h, depth, ctype, _, _, interlace = ihdr
    assert interlace == 0, "interlaced PNG not supported"
    chans = {0: 1, 2: 3, 4: 2, 6: 4}[ctype]
    bpp = chans * depth // 8
    stride = w * bpp
    raw = zlib.decompress(idat)
    rows, prev = [], bytearray(stride)
    i = 0
    for _ in range(h):
        f = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 255
            elif f == 4:
                line[x] = (line[x] + _paeth(a, b, c)) & 255
        rows.append(bytes(line))
        prev = line
    return w, h, depth, chans, rows


def luma_stats(path, step=4):
    """(mean, max) luma in 0..1, sampling every `step`-th pixel for speed."""
    w, h, depth, chans, rows = read_png(path)
    scale = 255.0 if depth == 8 else 65535.0
    size = depth // 8
    total, n, mx = 0.0, 0, 0.0
    for y in range(0, h, step):
        row = rows[y]
        for x in range(0, w, step):
            o = x * chans * size
            vals = [int.from_bytes(row[o + k * size:o + (k + 1) * size], "big") / scale for k in range(min(chans, 3))]
            l = vals[0] if len(vals) == 1 else 0.2126 * vals[0] + 0.7152 * vals[1] + 0.0722 * vals[2]
            total += l
            n += 1
            mx = max(mx, l)
    return total / n, mx


def edge_energy(path, row_step=8):
    """Mean absolute luma difference between horizontally adjacent pixels (sampled rows): drops when blurred,
    even if highlights are clipped by a grade."""
    w, h, depth, chans, rows = read_png(path)
    scale = 255.0 if depth == 8 else 65535.0
    size = depth // 8
    total, n = 0.0, 0
    for y in range(0, h, row_step):
        row = rows[y]
        prev = None
        for x in range(w):
            o = x * chans * size
            v = [int.from_bytes(row[o + k * size:o + (k + 1) * size], "big") / scale for k in range(min(chans, 3))]
            l = v[0] if len(v) == 1 else 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]
            if prev is not None:
                total += abs(l - prev)
                n += 1
            prev = l
    return total / n


def region_mean(path, fx0, fy0, fx1, fy1, step=4):
    """Mean luma of a rectangle given as fractions of the frame (0..1)."""
    w, h, depth, chans, rows = read_png(path)
    scale = 255.0 if depth == 8 else 65535.0
    size = depth // 8
    total, n = 0.0, 0
    for y in range(int(fy0 * h), int(fy1 * h), step):
        row = rows[y]
        for x in range(int(fx0 * w), int(fx1 * w), step):
            o = x * chans * size
            v = [int.from_bytes(row[o + k * size:o + (k + 1) * size], "big") / scale for k in range(min(chans, 3))]
            total += v[0] if len(v) == 1 else 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]
            n += 1
    return total / n


def dark_bbox(path, below, step=8):
    """Bounding box (fractions x0, y0, x1, y1) and area fraction of pixels with luma < `below`. One decode."""
    w, h, depth, chans, rows = read_png(path)
    scale = 255.0 if depth == 8 else 65535.0
    size = depth // 8
    xs, ys, n, hit = [], [], 0, 0
    for y in range(0, h, step):
        row = rows[y]
        for x in range(0, w, step):
            o = x * chans * size
            v = [int.from_bytes(row[o + k * size:o + (k + 1) * size], "big") / scale for k in range(min(chans, 3))]
            luma = v[0] if len(v) == 1 else 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]
            n += 1
            if luma < below:
                hit += 1
                xs.append(x)
                ys.append(y)
    if not hit:
        return None
    return {"bbox": (round(min(xs) / w, 3), round(min(ys) / h, 3), round(max(xs) / w, 3), round(max(ys) / h, 3)),
            "area": round(hit / n, 4)}
