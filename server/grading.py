"""Numeric colour grading compiled into a 3D LUT (.cube).

The Resolve API cannot touch colour wheels, curves or qualifiers, but it can put a LUT on a node.
Every global (non-spatial) colour operation is a function RGB -> RGB, so it can be baked into a LUT:
primaries (offset, lift/gamma/gain, contrast/pivot, temperature/tint), curves (master/R/G/B,
monotone cubic through control points), hue-vs-sat, hue-vs-hue, lum-vs-sat, saturation and soft
HSL qualifiers with their own adjustments. Spatial work (power windows, tracking) cannot be a LUT.

Values are display-referred 0..1 (the timeline colour space); out-of-range results are clipped.
Order of operations (like a single Resolve node): offset -> lift/gamma/gain -> contrast ->
temperature/tint -> curves -> hue-vs-hue -> hue-vs-sat -> lum-vs-sat -> qualifiers -> saturation.
"""
import colorsys
import math
import os

LUMA = (0.2126, 0.7152, 0.0722)  # Rec.709


def _rgb3(v, default):
    if v is None:
        return [default] * 3
    if isinstance(v, (int, float)):
        return [float(v)] * 3
    if isinstance(v, dict):
        m = float(v.get("master", default))
        return [m + float(v.get(c, default)) - default for c in "rgb"]
    return [float(x) for x in v]


def monotone_curve(points):
    """Monotone cubic (Fritsch-Carlson) through [[x, y], ...] with x in 0..1. Returns f(x)."""
    pts = sorted((float(x), float(y)) for x, y in points)
    if pts[0][0] > 0:
        pts.insert(0, (0.0, pts[0][1] if len(pts) == 1 else 0.0))
    if pts[-1][0] < 1:
        pts.append((1.0, 1.0))
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    n = len(xs)
    d = [(ys[i + 1] - ys[i]) / ((xs[i + 1] - xs[i]) or 1e-9) for i in range(n - 1)]
    m = [d[0]] + [0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2 for i in range(1, n - 1)] + [d[-1]]
    for i in range(n - 1):
        if d[i] == 0:
            m[i] = m[i + 1] = 0.0
        else:
            a, b = m[i] / d[i], m[i + 1] / d[i]
            s = a * a + b * b
            if s > 9:
                t = 3 / math.sqrt(s)
                m[i], m[i + 1] = t * a * d[i], t * b * d[i]

    def f(x):
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        i = max(j for j in range(n - 1) if xs[j] <= x)
        h = xs[i + 1] - xs[i]
        t = (x - xs[i]) / h
        h00, h10 = 2 * t ** 3 - 3 * t ** 2 + 1, t ** 3 - 2 * t ** 2 + t
        h01, h11 = -2 * t ** 3 + 3 * t ** 2, t ** 3 - t ** 2
        return h00 * ys[i] + h10 * h * m[i] + h01 * ys[i + 1] + h11 * h * m[i + 1]
    return f


def hue_curve(points):
    """Periodic linear interpolation over hue degrees: [[hue_deg, value], ...] -> f(hue_deg)."""
    pts = sorted((float(h) % 360, float(v)) for h, v in points)
    if not pts:
        return lambda h: None

    def f(h):
        h %= 360
        if len(pts) == 1:
            return pts[0][1]
        for (h0, v0), (h1, v1) in zip(pts, pts[1:] + [(pts[0][0] + 360, pts[0][1])]):
            hh = h if h >= h0 else h + 360
            if h0 <= hh <= h1:
                t = (hh - h0) / ((h1 - h0) or 1e-9)
                return v0 + (v1 - v0) * t
        return pts[0][1]
    return f


def smooth_band(x, lo, hi, soft):
    """1 inside [lo, hi], fading to 0 over `soft` outside."""
    if lo <= x <= hi:
        return 1.0
    dist = lo - x if x < lo else x - hi
    return max(0.0, 1.0 - dist / soft) if soft > 0 else 0.0


def hue_band(h, center, width, soft):
    dist = abs((h - center + 180) % 360 - 180)
    half = width / 2.0
    if dist <= half:
        return 1.0
    return max(0.0, 1.0 - (dist - half) / soft) if soft > 0 else 0.0


def set_sat(rgb, mult):
    y = sum(c * w for c, w in zip(rgb, LUMA))
    return [y + (c - y) * mult for c in rgb]


def build_transform(g):
    """Compile a grade dict into f(r, g, b) -> (r, g, b). See color_grade_lut for the keys."""
    offset = _rgb3(g.get("offset"), 0.0)
    lift = _rgb3(g.get("lift"), 0.0)
    gamma = _rgb3(g.get("gamma"), 1.0)
    gain = _rgb3(g.get("gain"), 1.0)
    contrast = float(g.get("contrast", 1.0))
    pivot = float(g.get("pivot", 0.435))
    temp, tint = float(g.get("temperature", 0.0)), float(g.get("tint", 0.0))
    curves = {k: monotone_curve(v) for k, v in (g.get("curves") or {}).items() if v}
    hvh = hue_curve(g.get("hue_vs_hue") or [])
    hvs = hue_curve(g.get("hue_vs_sat") or [])
    lvs = monotone_curve(g["lum_vs_sat"]) if g.get("lum_vs_sat") else None
    quals = g.get("qualifiers") or []
    sat = float(g.get("saturation", 1.0))
    temp_mul = [1 + 0.1 * temp, 1 - 0.1 * tint, 1 - 0.1 * temp]

    def f(r, gg, b):
        c = [r + offset[0], gg + offset[1], b + offset[2]]
        c = [x * (gn - lf) + lf for x, gn, lf in zip(c, gain, lift)]
        c = [max(x, 0.0) ** (1.0 / gm) if gm > 0 else x for x, gm in zip(c, gamma)]
        if contrast != 1.0:
            c = [(x - pivot) * contrast + pivot for x in c]
        if temp or tint:
            c = [x * m for x, m in zip(c, temp_mul)]
        if curves:
            if "master" in curves:
                c = [curves["master"](min(max(x, 0), 1)) for x in c]
            for i, k in enumerate("rgb"):
                if k in curves:
                    c[i] = curves[k](min(max(c[i], 0), 1))
        c = [min(max(x, 0.0), 1.0) for x in c]
        if g.get("hue_vs_hue") or g.get("hue_vs_sat") or quals:
            h, s, v = colorsys.rgb_to_hsv(*c)
            hdeg = h * 360
            if g.get("hue_vs_hue") and s > 0:
                shift = hvh(hdeg) or 0.0
                c = list(colorsys.hsv_to_rgb(((hdeg + shift) % 360) / 360, s, v))
            if g.get("hue_vs_sat") and s > 0:
                c = set_sat(c, 1.0 + ((hvs(hdeg) or 1.0) - 1.0) * min(1.0, s * 4))
            for q in quals:
                hq, sq, vq = colorsys.rgb_to_hsv(*[min(max(x, 0), 1) for x in c])
                y = sum(x * w for x, w in zip(c, LUMA))
                wgt = (hue_band(hq * 360, q.get("hue", 0), q.get("hue_width", 40), q.get("hue_soft", 20))
                       * smooth_band(sq, *(q.get("sat_range") or [0.08, 1.0]), q.get("soft", 0.1))
                       * smooth_band(y, *(q.get("lum_range") or [0.0, 1.0]), q.get("soft", 0.1)))
                if wgt <= 0:
                    continue
                adj = q.get("adjust") or {}
                t = list(c)
                if adj.get("hue_shift"):
                    hh, ss, vv = colorsys.rgb_to_hsv(*[min(max(x, 0), 1) for x in t])
                    t = list(colorsys.hsv_to_rgb(((hh * 360 + adj["hue_shift"]) % 360) / 360, ss, vv))
                if "saturation" in adj:
                    t = set_sat(t, float(adj["saturation"]))
                if "gain" in adj:
                    t = [x * gm for x, gm in zip(t, _rgb3(adj["gain"], 1.0))]
                if "lift" in adj:
                    t = [x + lf * (1 - x) for x, lf in zip(t, _rgb3(adj["lift"], 0.0))]
                c = [a + (bb - a) * wgt for a, bb in zip(c, t)]
        if lvs is not None:
            y = sum(x * w for x, w in zip(c, LUMA))
            c = set_sat(c, lvs(min(max(y, 0), 1)))
        if sat != 1.0:
            c = set_sat(c, sat)
        return tuple(min(max(x, 0.0), 1.0) for x in c)
    return f


def write_cube(path, transform, size=33, title="Claude grade"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ['TITLE "%s"' % title.replace('"', "'"), "LUT_3D_SIZE %d" % size, "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"]
    n = size - 1
    for b in range(size):
        for g in range(size):
            for r in range(size):
                lines.append("%.6f %.6f %.6f" % transform(r / n, g / n, b / n))
    with open(path, "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return path


SAMPLES = {"black": (0, 0, 0), "shadow_grey": (0.18, 0.18, 0.18), "mid_grey": (0.45, 0.45, 0.45),
           "white": (1, 1, 1), "skin": (0.80, 0.58, 0.47), "sky_blue": (0.35, 0.55, 0.85),
           "foliage": (0.25, 0.45, 0.20), "red": (0.8, 0.15, 0.12)}


def sample_report(transform):
    """What the grade does to a few reference colours (input -> output), for Claude to sanity-check."""
    return {k: {"in": v, "out": [round(x, 4) for x in transform(*v)]} for k, v in SAMPLES.items()}
