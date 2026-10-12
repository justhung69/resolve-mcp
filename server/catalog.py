"""Parse the installed Resolve scripting README.txt into an API catalog.

The catalog is built at runtime from the README that ships with the user's Resolve, so the
tool coverage always matches the installed version (no hard-coded method list).
"""
import os
import re

README_CANDIDATES = [
    os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                 r"Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\README.txt"),
]

CLASSES = ["Resolve", "ProjectManager", "Project", "MediaStorage", "MediaPool", "Folder", "MediaPoolItem",
           "Timeline", "TimelineItem", "Gallery", "GalleryStillAlbum", "GalleryStill", "Graph", "ColorGroup"]

METHOD_RE = re.compile(r"^\s{2}(\w+)\((.*)$")
CONSTANT_RE = re.compile(r"\bresolve\.([A-Z][A-Z0-9_]+)")


def find_readme():
    for p in README_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def parse(path=None):
    """Returns {"path", "classes": {cls: {method: {"signatures": [...], "returns": [...], "doc": str,
    "deprecated": bool, "unsupported": bool}}}, "constants": [...], "sections": {title: text}}."""
    path = path or find_readme()
    if not path:
        raise FileNotFoundError("Resolve scripting README.txt not found")
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()

    classes = {c: {} for c in CLASSES}
    mode = None          # None | "api" | "deprecated" | "unsupported"
    cls = None
    cur = None           # method entry being accumulated
    pending_sig = None   # signature spanning several lines before "-->"

    def flush_sig(sig_text):
        nonlocal cur
        m = re.match(r"\s*(\w+)\((.*)", sig_text, re.S)
        name = m.group(1)
        before, _, after = sig_text.partition("-->")
        ret, _, doc = after.partition("#")
        sig = " ".join(before.split())
        entry = classes[cls].setdefault(name, {"signatures": [], "returns": [], "doc": "",
                                               "deprecated": mode == "deprecated",
                                               "unsupported": mode == "unsupported"})
        if mode == "unsupported" and not entry["unsupported"]:
            # e.g. StartRendering(index1, ...) - an overload that is no longer supported
            entry.setdefault("unsupported_signatures", []).append(sig)
        elif sig not in entry["signatures"]:
            entry["signatures"].append(sig)
            entry["returns"].append(ret.strip())
        if doc.strip():
            entry["doc"] = (entry["doc"] + " " + doc.strip()).strip()
        cur = entry

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if stripped == "DaVinci Resolve API":
            mode = "api"
            continue
        if stripped.startswith("Deprecated Resolve API Functions"):
            mode = "deprecated"
            cls = None
            continue
        if stripped.startswith("Unsupported Resolve API Functions"):
            mode = "unsupported"
            cls = None
            continue
        if stripped == "List and Dict Data Structures":
            mode = None if mode == "api" else mode
            continue
        if mode is None:
            continue
        if stripped in CLASSES and not line.startswith(" "):
            cls, cur, pending_sig = stripped, None, None
            continue
        if cls is None:
            continue
        if pending_sig is not None:
            pending_sig += " " + stripped
            if "-->" in pending_sig:
                flush_sig(pending_sig)
                pending_sig = None
            continue
        m = METHOD_RE.match(line)
        if m and not stripped.startswith("#"):
            if "-->" in line:
                flush_sig(line)
            else:
                pending_sig = line
            continue
        if (cur is not None and stripped and not stripped.startswith("#") and "-->" not in line
                and cur["signatures"] and cur["signatures"][-1].count("(") > cur["signatures"][-1].count(")")):
            # signature wrapped onto the next line, e.g. "AddMarker(frameId, ..., duration," / "customData)"
            cur["signatures"][-1] += " " + stripped
            continue
        if stripped.startswith("#") and cur is not None:
            cur["doc"] = (cur["doc"] + " " + stripped.lstrip("# ").strip()).strip()
        elif "-->" in line and cur is not None and not m:
            # continuation of a multi-line signature, e.g. "          startOffsetInSamples, ...)  --> Bool  # doc"
            _, _, after = line.partition("-->")
            _, _, doc = after.partition("#")
            if doc.strip():
                cur["doc"] = (cur["doc"] + " " + doc.strip()).strip()

    text = "\n".join(lines)
    constants = sorted(set(CONSTANT_RE.findall(text)))
    return {"path": path, "classes": {k: v for k, v in classes.items()}, "constants": constants}


_CACHE = {}


def get_catalog():
    path = find_readme()
    mtime = os.path.getmtime(path) if path else None
    if _CACHE.get("mtime") != mtime:
        _CACHE["cat"] = parse(path)
        _CACHE["mtime"] = mtime
    return _CACHE["cat"]


def method_info(cls, method):
    return get_catalog()["classes"].get(cls, {}).get(method)


if __name__ == "__main__":
    import json
    import sys
    cat = parse(sys.argv[1] if len(sys.argv) > 1 else None)
    total = 0
    for c, ms in cat["classes"].items():
        live = [m for m, e in ms.items() if not e["deprecated"] and not e["unsupported"]]
        total += len(live)
        print("%-18s %3d methods" % (c, len(live)))
    print("total", total, "constants", len(cat["constants"]))
    if "-v" in sys.argv:
        print(json.dumps(cat["classes"]["TimelineItem"], indent=1)[:3000])
