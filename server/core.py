"""Shared logic for the Resolve MCP server: catalog-checked calls, read-back verification,
safety (confirm / dry-run / backups), operation log, state snapshots and diffs."""
import difflib
import json
from contextlib import contextmanager
import os
import re
import time
import uuid

import catalog
from bridge_client import STATE_DIR, BridgeClient, BridgeError, handle_id, handle_type

OPLOG = os.path.join(STATE_DIR, "oplog.jsonl")
SNAP_DIR = os.path.join(STATE_DIR, "snapshots")
BACKUP_DIR = os.path.join(STATE_DIR, "backups")
for _d in (STATE_DIR, SNAP_DIR, BACKUP_DIR):
    os.makedirs(_d, exist_ok=True)

B = BridgeClient()

# Calls that delete, overwrite or discard user work. They require confirm=True and trigger a .drp backup first.
DESTRUCTIVE = {
    "DeleteProject", "DeleteFolder", "CloseProject", "SetCurrentDatabase", "Quit", "ArchiveProject",
    "DeleteTimelines", "DeleteClips", "DeleteFolders", "DeleteClipMattes", "ReplaceClip",
    "ReplaceClipPreserveSubClip", "UnlinkClips", "RelinkClips", "UnlinkProxyMedia", "DeleteRenderJob",
    "DeleteAllRenderJobs", "DeleteRenderPreset", "DeleteTrack", "DeleteMarkersByColor", "DeleteMarkerAtFrame",
    "DeleteMarkerByCustomData", "DeleteStills", "DeleteColorGroup", "DeleteVersionByName", "DeleteTakeByIndex",
    "DeleteFusionCompByName", "ResetAllGrades", "DeleteLayoutPreset", "DeleteBurnInPreset",
    "DeleteUserPreferencesPreset", "LoadUserPreferencesPreset", "UpdateLayoutPreset", "StartRendering",
    "ClearTranscription", "ClearAudioClassification", "FinalizeTake", "ResetIntellisearchAnalysis",
    "DeleteMarkerAtFrame",
}
# Project-level operations where a backup of the *current* project is pointless or impossible.
NO_BACKUP = {"Quit", "SetCurrentDatabase", "CloseProject", "StartRendering", "DeleteLayoutPreset",
             "DeleteBurnInPreset", "DeleteUserPreferencesPreset", "LoadUserPreferencesPreset", "UpdateLayoutPreset"}


# --------------------------------------------------------------------------- oplog

def oplog(tool, args, ok, result=None, error=None, ms=None):
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": tool, "args": args, "ok": ok}
    if result is not None:
        s = json.dumps(result, default=str)
        rec["result"] = result if len(s) < 4000 else s[:4000] + "...(truncated)"
    if error:
        rec["error"] = error
    if ms is not None:
        rec["ms"] = ms
    with open(OPLOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- compare

def _num(v):
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*(?:DF)?\s*", v)
        if m:
            return float(m.group(1))
    return None


def _boolish(v):
    if isinstance(v, bool):
        return v
    if v in (0, 1) and not isinstance(v, float):
        return bool(v)
    if isinstance(v, str) and v.strip().lower() in ("0", "1", "true", "false"):
        return v.strip().lower() in ("1", "true")
    return None


def same(requested, actual, tol=1e-3):
    """Loose equality for read-back checks (Resolve returns '1' for True, numbers for numeric strings, ...)."""
    if requested == actual:
        return True
    if isinstance(requested, dict) and isinstance(actual, dict):
        return all(k in actual and same(v, actual[k], tol) for k, v in requested.items())
    if isinstance(requested, dict) and "$h" in requested:
        return handle_id(requested) == handle_id(actual)
    rb, ab = _boolish(requested), _boolish(actual)
    if isinstance(requested, bool) and ab is not None:
        return requested == ab
    rn, an = _num(requested), _num(actual)
    if rn is not None and an is not None:
        return abs(rn - an) <= tol * max(1.0, abs(rn))
    if rb is not None and ab is not None and not isinstance(requested, str):
        return rb == ab
    if isinstance(requested, str) and isinstance(actual, str):
        return requested.strip().casefold() == actual.strip().casefold()
    return False


# --------------------------------------------------------------------------- verification table

def verify_spec(cls, method, args):
    """(getter, getter_args, expected) for setters whose effect can be read back, else None."""
    a = list(args)
    n = len(a)
    if method == "SetName" and n == 1:
        return ("GetName", [], a[0])
    if method == "SetClipColor" and n == 1:
        return ("GetClipColor", [], a[0])
    if method == "ClearClipColor":
        return ("GetClipColor", [], "")
    if method == "SetProperty" and n == 2 and cls == "TimelineItem":
        return ("GetProperty", [a[0]], a[1])
    if method in ("SetSetting",) and n == 2:
        return ("GetSetting", [a[0]], a[1])
    if method == "SetClipProperty" and n == 2:
        return ("GetClipProperty", [a[0]], a[1])
    if method == "SetMetadata" and n == 2:
        return ("GetMetadata", [a[0]], a[1])
    if method == "SetMetadata" and n == 1 and isinstance(a[0], dict):
        return ("GetMetadata", [], a[0])
    if method == "SetThirdPartyMetadata" and n == 2:
        return ("GetThirdPartyMetadata", [a[0]], a[1])
    if method == "SetCurrentTimecode" and n == 1:
        return ("GetCurrentTimecode", [], a[0])
    if method == "SetStartTimecode" and n == 1:
        return ("GetStartTimecode", [], a[0])
    if method == "OpenPage" and n == 1:
        return ("GetCurrentPage", [], a[0])
    if method == "SetClipEnabled" and n == 1:
        return ("GetClipEnabled", [], a[0])
    if method == "SetTrackName" and n == 3:
        return ("GetTrackName", a[:2], a[2])
    if method == "SetTrackEnable" and n == 3:
        return ("GetIsTrackEnabled", a[:2], a[2])
    if method == "SetTrackLock" and n == 3:
        return ("GetIsTrackLocked", a[:2], a[2])
    if method == "SetLUT" and n == 2:
        return ("GetLUT", [a[0]], {"$lut": a[1]})
    if method == "SetNodeCacheMode" and n == 2:
        return ("GetNodeCacheMode", [a[0]], a[1])
    if method == "SetCurrentRenderFormatAndCodec" and n == 2:
        return ("GetCurrentRenderFormatAndCodec", [], {"format": a[0], "codec": a[1]})
    if method == "SetCurrentRenderMode" and n == 1:
        return ("GetCurrentRenderMode", [], a[0])
    if method == "SetKeyframeMode" and n == 1:
        return ("GetKeyframeMode", [], a[0])
    if method == "SetAlbumName" and n == 2:
        return ("GetAlbumName", [a[0]], a[1])
    if method == "SetLabel" and n == 2:
        return ("GetLabel", [a[0]], a[1])
    if method == "SetCurrentTimeline" and n == 1:
        return ("GetCurrentTimeline", [], a[0])
    if method == "SetCurrentFolder" and n == 1 and cls == "MediaPool":
        return ("GetCurrentFolder", [], a[0])
    if method == "SetCurrentStillAlbum" and n == 1:
        return ("GetCurrentStillAlbum", [], a[0])
    if method == "SelectTakeByIndex" and n == 1:
        return ("GetSelectedTakeIndex", [], a[0])
    if method == "LoadVersionByName" and n == 2:
        return ("GetCurrentVersion", [], {"versionName": a[0]})
    if method == "SetMarkInOut" and n >= 2:
        kind = a[2] if n > 2 else "all"
        exp = {"in": a[0], "out": a[1]}
        return ("GetMarkInOut", [], {"video": exp} if kind in ("all", "video") else {"audio": exp})
    if method == "UpdateMarkerCustomData" and n == 2:
        return ("GetMarkerCustomData", [a[0]], a[1])
    return None


def lut_matches(requested_path, actual):
    if not isinstance(actual, str) or not actual:
        return False
    norm = lambda p: p.replace("\\", "/").strip().casefold()
    return norm(requested_path).endswith(norm(actual)) or norm(actual).endswith(os.path.basename(norm(requested_path)))


# --------------------------------------------------------------------------- calls

def check_method(target, method):
    """Validate method against the installed README catalog (and the live object for non-catalog types)."""
    cls = handle_type(target)
    cat = catalog.get_catalog()["classes"]
    notes = []
    if cls in cat:
        info = cat[cls].get(method)
        if info is None:
            live = B.request("has_methods", target=handle_id(target), methods=[method]).get(method)
            if live:
                notes.append("%s.%s exists in this Resolve build but is not documented in README.txt - "
                             "behaviour unverified" % (cls, method))
            else:
                close = difflib.get_close_matches(method, list(cat[cls]), n=5)
                raise ValueError("%s has no method %r in the installed README. Did you mean: %s" % (cls, method, close))
        elif info.get("unsupported"):
            raise ValueError("%s.%s is listed as unsupported in the installed README" % (cls, method))
        elif info.get("deprecated"):
            notes.append("%s.%s is deprecated in the installed README" % (cls, method))
    else:
        notes.append("%s is outside the Resolve README (Fusion object API) - call passed through unvalidated" % cls)
    return cls, notes


def backup_project(reason, also_project=None):
    """Save the current project and export it as .drp to the backup folder. Returns paths or raises."""
    roots = B.request("roots")
    pm = handle_id(roots["project_manager"])
    paths = []
    names = []
    cur = B.call(handle_id(roots["project"]), "GetName") if roots.get("project") else None
    if cur == "Untitled Project":
        # SaveProject can never succeed on the never-saved default project (and blocks forever headless).
        raise BridgeError("the current project is the unsaved 'Untitled Project' - it cannot be backed up. Save it "
                          "under a name in Resolve first, or create a named project.")
    if cur:
        B.call(pm, "SaveProject")
        names.append(cur)
    if also_project and also_project not in names:
        names.append(also_project)
    for name in names:
        safe_name = re.sub(r"[^\w.-]+", "_", name)
        path = os.path.join(BACKUP_DIR, "%s_%s.drp" % (safe_name, time.strftime("%Y%m%d-%H%M%S")))
        ok = B.call(pm, "ExportProject", name, path, False)
        if not ok or not os.path.exists(path):
            raise BridgeError("backup of project %r failed (ExportProject returned %r)" % (name, ok))
        paths.append(path)
    oplog("backup", {"reason": reason}, True, result=paths)
    return paths


def call(target, method, args=None, *, confirm=False, dry_run=False, verify=True, backup=True,
         timeout=None, tool="api_call", force=False):
    """Catalog-checked call with confirm/dry-run/backup and read-back verification.

    Returns {"result", "ok", "verified", "requested", "before", "actual", "notes", "backup"}."""
    args = list(args or [])
    t0 = time.time()
    hid = handle_id(target)
    out = {"target": hid, "method": method, "args": args, "notes": []}
    try:
        cls, notes = check_method(target, method)
        out["notes"] += notes
        if not dry_run and not force and studio_gated(cls, method) and not is_studio():
            # On Free a Studio-gated call returns False AND raises a modal upsell dialog that makes unrelated later
            # calls fail until a human closes it (recorded by samuelgursky/davinci-resolve-mcp on free 21.0.3).
            raise PermissionError("%s.%s is listed under 'Studio and AI Scripting APIs' and this is Resolve Free: it "
                                  "would return False and open a blocking 'limitation' dialog. Pass force=true to try "
                                  "anyway (some AI-Extras functions do work on Free)." % (cls, method))
        spec = verify_spec(cls, method, args) if verify else None
        destructive = method in DESTRUCTIVE
        if dry_run:
            out.update(dry_run=True, would_call="%s.%s(%s)" % (cls, method, ", ".join(json.dumps(a, default=str) for a in args)),
                       destructive=destructive, verification=spec[0] if spec else None)
            if spec:
                out["current"] = B.try_call(hid, spec[0], *spec[1])
            return out
        if destructive and not confirm:
            raise PermissionError("%s.%s is destructive. Re-run with confirm=true (a .drp backup of the current "
                                  "project is written first), or use dry_run=true to preview." % (cls, method))
        if destructive and backup and method not in NO_BACKUP:
            out["backup"] = backup_project("%s.%s" % (cls, method), also_project=args[0] if method == "DeleteProject" else None)
        if spec:
            out["before"] = B.try_call(hid, spec[0], *spec[1])
        res = B.request("call", target=hid, method=method, args=args, timeout=timeout)
        out["result"] = res
        out["ok"] = res is not False
        if res is False:
            out["notes"].append("Resolve returned False - the call was rejected (invalid value, wrong page/context, "
                                "or a Studio-only feature on Resolve Free).")
        if spec:
            getter, gargs, expected = spec
            if isinstance(expected, dict) and "$const" in expected:
                expected = B.request("consts", names=[expected["$const"]])[expected["$const"]]
            actual = B.try_call(hid, getter, *gargs)
            out["requested"] = expected.get("$lut") if isinstance(expected, dict) and "$lut" in expected else expected
            out["actual"] = actual
            if isinstance(expected, dict) and "$lut" in expected:
                out["verified"] = lut_matches(expected["$lut"], actual)
            else:
                out["verified"] = same(expected, actual)
            if res is not False and not out["verified"]:
                out["notes"].append("Resolve reported success but the read-back value differs - the change did not "
                                    "(fully) take effect.")
            out["ok"] = bool(out["ok"] and out["verified"])
        else:
            out["verified"] = None
        oplog(tool, {"target": hid, "method": method, "args": args}, out["ok"], result=out.get("result"),
              ms=int((time.time() - t0) * 1000))
        return out
    except Exception as e:
        oplog(tool, {"target": hid, "method": method, "args": args}, False, error="%s: %s" % (type(e).__name__, e),
              ms=int((time.time() - t0) * 1000))
        raise


def get(target, method, *args, timeout=None):
    """Plain read call (no verification, no log)."""
    return B.request("call", target=handle_id(target), method=method, args=list(args), timeout=timeout)


@contextmanager
def on_page(page=None):
    """Switch to `page` for the duration of a block (or just guard the current page) and restore the user's page.
    Several calls are page-gated or move the page themselves (measured on 21.0.4 and/or recorded in
    samuelgursky/davinci-resolve-mcp api_truth): ExportLUT / AddVersion / ApplyGradeFromDRX want Color,
    DeleteClips wants Edit, render getters/setters jump to Deliver."""
    old = get("resolve", "GetCurrentPage")
    if page and old != page:
        get("resolve", "OpenPage", page)
    try:
        yield old
    finally:
        if old and get("resolve", "GetCurrentPage") != old:
            get("resolve", "OpenPage", old)


def is_studio():
    return "studio" in (get("resolve", "GetProductName") or "").lower()


def studio_gated(cls, method):
    info = catalog.method_info(cls, method) or {}
    return "Studio and AI Scripting APIs" in info.get("doc", "")


def current_project_name():
    r = roots()
    return get(r["project"], "GetName") if r.get("project") else None


def unique_timeline_name(name):
    """ImportTimelineFromFile returns None when timelineName already exists - pick a free name instead."""
    p = roots().get("project")
    existing = set()
    if p:
        for i in range(1, (get(p, "GetTimelineCount") or 0) + 1):
            tl = get(p, "GetTimelineByIndex", i)
            if tl:
                existing.add(get(tl, "GetName"))
    if name not in existing:
        return name
    n = 2
    while "%s %d" % (name, n) in existing:
        n += 1
    return "%s %d" % (name, n)


def roots():
    return B.request("roots")


def require(handle_or_none, what):
    if not handle_or_none:
        raise ValueError("no %s - open a project/timeline first" % what)
    return handle_or_none


def resolve_timeline(timeline=None):
    """Timeline handle from a handle, a timeline name, or None (= current timeline)."""
    r = roots()
    if timeline is None:
        return require(r.get("timeline"), "current timeline")
    if isinstance(timeline, dict) or (isinstance(timeline, str) and timeline.startswith("Timeline:")):
        return timeline if isinstance(timeline, dict) else {"$h": timeline}
    p = require(r.get("project"), "project")
    for i in range(1, (get(p, "GetTimelineCount") or 0) + 1):
        tl = get(p, "GetTimelineByIndex", i)
        if tl and get(tl, "GetName") == timeline:
            return tl
    raise ValueError("no timeline named %r" % timeline)


def as_handle(h):
    if h is None:
        return None
    if isinstance(h, dict):
        return h
    return {"$h": h}


# --------------------------------------------------------------------------- snapshots / diff

def compact(v):
    """Drop None / empty containers to keep state JSON small."""
    if isinstance(v, dict):
        out = {k: compact(x) for k, x in v.items()}
        return {k: x for k, x in out.items() if x not in (None, "", [], {})}
    if isinstance(v, list):
        return [compact(x) for x in v]
    return v


def save_snapshot(state, params):
    sid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    with open(os.path.join(SNAP_DIR, sid + ".json"), "w", encoding="utf-8") as f:
        json.dump({"id": sid, "params": params, "state": state}, f, ensure_ascii=False)
    return sid


def load_snapshot(sid):
    path = os.path.join(SNAP_DIR, sid + ".json")
    if not os.path.exists(path):
        raise FileNotFoundError("no snapshot %r (see list_snapshots)" % sid)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _key_of(x, i):
    if isinstance(x, dict):
        for k in ("h", "index", "JobId", "name"):
            if k in x:
                return "%s=%s" % (k, x[k])
    return "#%d" % i


VOLATILE = {"captured_at", "elapsed_s", "handles"}


def diff(a, b, path="", out=None, limit=500):
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k in VOLATILE:
                continue
            p = path + "/" + str(k)
            if k not in a:
                out.append({"path": p, "change": "added", "after": b[k]})
            elif k not in b:
                out.append({"path": p, "change": "removed", "before": a[k]})
            else:
                diff(a[k], b[k], p, out, limit)
    elif isinstance(a, list) and isinstance(b, list):
        ka = {_key_of(x, i): x for i, x in enumerate(a)}
        kb = {_key_of(x, i): x for i, x in enumerate(b)}
        for k in list(ka) + [k for k in kb if k not in ka]:
            p = path + "[" + k + "]"
            if k not in kb:
                out.append({"path": p, "change": "removed", "before": ka[k]})
            elif k not in ka:
                out.append({"path": p, "change": "added", "after": kb[k]})
            else:
                diff(ka[k], kb[k], p, out, limit)
    elif a != b:
        out.append({"path": path, "change": "changed", "before": a, "after": b})
    return out
