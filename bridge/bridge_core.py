"""bridge_core - the hot-reloadable part of ClaudeBridge (handle registry, ops, state snapshots).

Loaded by ClaudeBridge.py with exec() into a namespace that already contains `resolve`.
The launcher re-loads this file on the `reload` op, so updates need no Resolve restart.
"""
import json
import os
import re
import sys
import time
import traceback

CORE_VERSION = "1.3.0"
STATE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ResolveMCP")
CONFIG_FILE = os.path.join(STATE_DIR, "config.json")
LOG_FILE = os.path.join(STATE_DIR, "bridge.log")
R = resolve  # noqa: F821 - provided by the launcher namespace


def log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# Classes whose objects expose GetUniqueId(); their handles stay stable across calls and sessions.
UID_TYPES = {"Project", "MediaPool", "Folder", "MediaPoolItem", "Timeline", "TimelineItem"}
REMOTE_RE = re.compile(r"^(.+?) \((0x[0-9A-Fa-f]+)\)")
# str() labels are not always the class name ("Media pool item", "Timeline item", "Color Group");
# map them onto the README class names. Fusion objects keep their own label (Composition, Input, ...).
KNOWN = ["Resolve", "ProjectManager", "Project", "MediaStorage", "MediaPool", "Folder", "MediaPoolItem", "Timeline",
         "TimelineItem", "Gallery", "GalleryStillAlbum", "GalleryStill", "Graph", "ColorGroup", "FusionUI",
         "Composition", "Input", "Output"]
CANON = {k.lower(): k for k in KNOWN}


def canon_type(label):
    key = re.sub(r"\s+", "", label).lower()
    return CANON.get(key, re.sub(r"\W+", "_", label.strip()))


# --------------------------------------------------------------------------- handles

def remote_info(obj):
    """(type, address) if obj is a Resolve/Fusion proxy object, else None."""
    if obj is None or isinstance(obj, (bool, int, float, str, bytes, list, tuple, dict)):
        return None
    if type(obj).__name__ != "PyRemoteObject":
        return None
    # repr() is generic; str() is "Timeline (0x...) [App: 'Resolve' ...]" with the native pointer.
    m = REMOTE_RE.match(str(obj))
    return (canon_type(m.group(1)), m.group(2)) if m else ("Unknown", hex(id(obj)))


class Registry:
    def __init__(self):
        self.objs = {}

    def put(self, obj):
        info = remote_info(obj)
        if not info:
            raise TypeError("not a Resolve object: %r" % (obj,))
        typ, addr = info
        hid = None
        if typ in UID_TYPES:
            try:
                uid = obj.GetUniqueId()
                if uid:
                    hid = "%s:%s" % (typ, uid)
            except Exception:
                pass
        if hid is None:
            hid = "%s@%s" % (typ, addr)
        self.objs[hid] = obj
        return {"$h": hid, "type": typ}

    def get(self, hid):
        if hid == "resolve":
            return R
        obj = self.objs.get(hid)
        if obj is None:
            raise KeyError("unknown or expired handle %r - re-fetch it (e.g. get_roots / get_full_state)" % hid)
        return obj

    def release(self, ids=None):
        if ids is None:
            n = len(self.objs)
            self.objs.clear()
            return n
        return sum(1 for i in ids if self.objs.pop(i, None) is not None)


REG = Registry()


def ser(v):
    """Python/Resolve value -> JSON-safe value (objects become handles)."""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return str(v)
        return int(v) if v.is_integer() and abs(v) < 2 ** 53 else v
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if isinstance(v, dict):
        return {str(ser(k)): ser(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [ser(x) for x in v]
    if remote_info(v):
        return REG.put(v)
    return repr(v)


def deser(v, refs=None):
    """JSON value -> Python value. {"$h": id} -> object, {"$const": NAME} -> resolve.NAME, {"$ref": i} -> batch result i."""
    if isinstance(v, dict):
        if "$h" in v:
            return REG.get(v["$h"])
        if "$const" in v:
            c = getattr(R, v["$const"], None)
            if c is None:
                raise KeyError("resolve has no constant %r" % v["$const"])
            return c
        if "$ref" in v:
            if refs is None or not (0 <= v["$ref"] < len(refs)):
                raise KeyError("bad $ref %r" % v["$ref"])
            return deser(refs[v["$ref"]])
        return {k: deser(x, refs) for k, x in v.items()}
    if isinstance(v, list):
        return [deser(x, refs) for x in v]
    return v


# --------------------------------------------------------------------------- ops

def safe(fn, *a, default=None):
    try:
        return fn(*a)
    except Exception:
        return default


def op_ping(req):
    pm = R.GetProjectManager()
    p = pm.GetCurrentProject() if pm else None
    tl = p.GetCurrentTimeline() if p else None
    return {
        "bridge_version": LAUNCHER_VERSION, "core_version": CORE_VERSION,
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "product": safe(R.GetProductName),
        "version": safe(R.GetVersionString),
        "page": safe(R.GetCurrentPage),
        "project": p.GetName() if p else None,
        "timeline": tl.GetName() if tl else None,
        "handles": len(REG.objs),
        "exec_enabled": bool(load_config().get("allow_exec")),
    }


def op_roots(req):
    pm = R.GetProjectManager()
    p = pm.GetCurrentProject() if pm else None
    mp = p.GetMediaPool() if p else None
    tl = p.GetCurrentTimeline() if p else None
    out = {"resolve": {"$h": "resolve", "type": "Resolve"}}
    for k, fn in (("project_manager", lambda: pm), ("project", lambda: p), ("media_pool", lambda: mp),
                  ("root_folder", lambda: mp.GetRootFolder()), ("current_folder", lambda: mp.GetCurrentFolder()),
                  ("timeline", lambda: tl), ("media_storage", R.GetMediaStorage),
                  ("gallery", lambda: p.GetGallery()), ("fusion", R.Fusion),
                  ("current_video_item", lambda: tl.GetCurrentVideoItem())):
        out[k] = ser(safe(fn))
    return out


def invoke(target, method, args, kwargs=None, refs=None):
    obj = REG.get(target) if isinstance(target, str) else deser(target, refs)
    fn = getattr(obj, method, None)
    if fn is None:
        t = (remote_info(obj) or ("?",))[0] if obj is not R else "Resolve"
        raise AttributeError("%s has no method %r in this Resolve version" % (t, method))
    return fn(*deser(args or [], refs), **deser(kwargs or {}, refs))


def op_call(req):
    return ser(invoke(req["target"], req["method"], req.get("args"), req.get("kwargs")))


def op_batch(req):
    results, out = [], []
    for i, c in enumerate(req["calls"]):
        try:
            r = ser(invoke(c["target"], c["method"], c.get("args"), c.get("kwargs"), refs=results))
            results.append(r)
            out.append({"i": i, "ok": True, "result": r})
        except Exception as e:
            results.append(None)
            out.append({"i": i, "ok": False, "error": "%s: %s" % (type(e).__name__, e)})
            if req.get("stop_on_error", True):
                break
    return out


def op_type(req):
    obj = REG.get(req["target"])
    info = remote_info(obj)
    return {"type": "Resolve" if obj is R else (info[0] if info else None)}


def op_has_methods(req):
    """Which of the given method names exist on the target (Resolve returns None for unknown attributes)."""
    obj = REG.get(req["target"])
    return {m: getattr(obj, m, None) is not None for m in req["methods"]}


def op_methods(req):
    """Live method list of an object, as reported by the installed Resolve (dir() of the proxy)."""
    obj = REG.get(req["target"])
    info = remote_info(obj)
    return {"type": "Resolve" if obj is R else (info[0] if info else None),
            "methods": sorted(set(x for x in dir(obj) if not x.startswith("_") and x != "Print"))}


def op_consts(req):
    """Values of resolve.* constants, e.g. {"COMPOSITE_SCREEN": 5}."""
    return {n: ser(getattr(R, n, None)) for n in req["names"]}


def op_release(req):
    return {"released": REG.release(req.get("ids"))}


def op_exec(req):
    if not load_config().get("allow_exec"):
        raise PermissionError("exec is disabled. Set \"allow_exec\": true in %s and restart the bridge." % CONFIG_FILE)
    env = {"resolve": R, "R": R, "fusion": R.Fusion(), "H": REG.get, "ser": ser, "result": None}
    exec(compile(req["code"], "<claude-exec>", "exec"), env)
    return ser(env.get("result"))


# ------------------------------------------------------------------ state snapshot

def marker_dict(m):
    return {str(int(k) if isinstance(k, float) and k.is_integer() else k): v for k, v in (m or {}).items()}


def graph_state(g):
    if g is None:
        return None
    n = safe(g.GetNumNodes, default=0) or 0
    nodes = []
    for i in range(1, n + 1):
        nodes.append({"index": i, "label": safe(g.GetNodeLabel, i), "lut": safe(g.GetLUT, i),
                      "tools": safe(g.GetToolsInNode, i), "cache_mode": safe(g.GetNodeCacheMode, i)})
    return {"num_nodes": n, "nodes": nodes}


def item_state(it, track_type, depth):
    d = {
        "h": REG.put(it)["$h"], "name": safe(it.GetName),
        "start": safe(it.GetStart), "end": safe(it.GetEnd), "duration": safe(it.GetDuration),
        "source_start": safe(it.GetSourceStartFrame), "source_end": safe(it.GetSourceEndFrame),
        "left_offset": safe(it.GetLeftOffset), "right_offset": safe(it.GetRightOffset),
        "enabled": safe(it.GetClipEnabled), "clip_color": safe(it.GetClipColor),
        "flags": safe(it.GetFlagList), "markers": marker_dict(safe(it.GetMarkers)),
    }
    mpi = safe(it.GetMediaPoolItem)
    d["media_pool_item"] = REG.put(mpi)["$h"] if mpi else None
    linked = safe(it.GetLinkedItems) or []
    d["linked"] = [REG.put(x)["$h"] for x in linked]
    if depth >= 2:
        d["properties"] = ser(safe(it.GetProperty))
        d["fusion_comps"] = safe(it.GetFusionCompNameList)
        d["takes"] = safe(it.GetTakesCount)
        if track_type == "video":
            cg = safe(it.GetColorGroup)
            d["color"] = {
                "version": safe(it.GetCurrentVersion),
                "local_versions": safe(it.GetVersionNameList, 0),
                "remote_versions": safe(it.GetVersionNameList, 1),
                "color_group": safe(cg.GetName) if cg else None,
                "graph": graph_state(safe(it.GetNodeGraph)) if depth >= 3 else None,
            }
    return d


def timeline_state(tl, p, depth, opts):
    cur = safe(p.GetCurrentTimeline)
    is_cur = cur is not None and safe(cur.GetUniqueId) == safe(tl.GetUniqueId)
    d = {
        "h": REG.put(tl)["$h"], "name": safe(tl.GetName), "current": is_cur,
        "start_frame": safe(tl.GetStartFrame), "end_frame": safe(tl.GetEndFrame),
        "start_timecode": safe(tl.GetStartTimecode),
        "playhead": safe(tl.GetCurrentTimecode) if is_cur else None,
        "markers": marker_dict(safe(tl.GetMarkers)),
        "mark_in_out": safe(tl.GetMarkInOut),
        "tracks": {},
    }
    if depth >= 3 and "settings" in opts["include"]:
        d["settings"] = ser(safe(tl.GetSetting))
    off, lim = opts["item_offset"], opts["item_limit"]
    for tt in ("video", "audio", "subtitle"):
        tracks = []
        for i in range(1, (safe(tl.GetTrackCount, tt) or 0) + 1):
            t = {"index": i, "name": safe(tl.GetTrackName, tt, i), "enabled": safe(tl.GetIsTrackEnabled, tt, i),
                 "locked": safe(tl.GetIsTrackLocked, tt, i)}
            if tt == "audio":
                t["subtype"] = safe(tl.GetTrackSubType, tt, i)
            items = safe(tl.GetItemListInTrack, tt, i) or []
            t["item_count"] = len(items)
            if depth >= 1 and "items" in opts["include"]:
                page = items[off:off + lim]
                t["items"] = [item_state(it, tt, depth) for it in page]
                if len(items) > off + lim:
                    t["items_truncated"] = {"next_offset": off + lim, "total": len(items)}
            tracks.append(t)
        d["tracks"][tt] = tracks
    return d


def folder_state(f, depth, opts, level=0):
    d = {"h": REG.put(f)["$h"], "name": safe(f.GetName), "clips": [], "subfolders": []}
    for c in safe(f.GetClipList) or []:
        cd = {"h": REG.put(c)["$h"], "name": safe(c.GetName), "media_id": safe(c.GetMediaId),
              "clip_color": safe(c.GetClipColor), "flags": safe(c.GetFlagList),
              "markers": marker_dict(safe(c.GetMarkers)), "mark_in_out": safe(c.GetMarkInOut)}
        props = safe(c.GetClipProperty) or {}
        if depth >= 2 and "clip_properties" in opts["include"]:
            cd["properties"] = ser(props)
            cd["metadata"] = ser(safe(c.GetMetadata))
        else:
            cd["properties"] = {k: props.get(k) for k in ("Type", "File Path", "Duration", "Frames", "FPS",
                                                         "Resolution", "Start TC", "In", "Out", "Usage")}
        d["clips"].append(cd)
    for s in safe(f.GetSubFolderList) or []:
        d["subfolders"].append(folder_state(s, depth, opts, level + 1))
    return d


def op_state(req):
    depth = int(req.get("depth", 2))
    opts = {
        "include": set(req.get("include") or
                       ["project", "settings", "timelines", "items", "media_pool", "render", "gallery", "color_groups"]),
        "item_offset": int(req.get("item_offset", 0)),
        "item_limit": int(req.get("item_limit", 200)),
    }
    only = set(req.get("timelines") or [])
    t0 = time.time()
    page0 = safe(R.GetCurrentPage)
    try:
        return _state(req, depth, opts, only, t0, page0)
    finally:
        # Project.GetCurrentRenderMode() silently switches Resolve to the Deliver page (measured on 21.0.4;
        # also recorded by samuelgursky/davinci-resolve-mcp api_truth). A read must not move the user.
        if page0 and safe(R.GetCurrentPage) != page0:
            safe(R.OpenPage, page0)


def _state(req, depth, opts, only, t0, page0):
    pm = R.GetProjectManager()
    p = pm.GetCurrentProject()
    out = {"captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "depth": depth,
           "resolve": {"version": safe(R.GetVersionString), "page": page0,
                       "keyframe_mode": safe(R.GetKeyframeMode)},
           "project_manager": {"database": safe(pm.GetCurrentDatabase), "folder": safe(pm.GetCurrentFolder),
                               "projects": safe(pm.GetProjectListInCurrentFolder),
                               "folders": safe(pm.GetFolderListInCurrentFolder)}}
    if not p:
        out["project"] = None
        return out
    proj = {"h": REG.put(p)["$h"], "name": safe(p.GetName), "timeline_count": safe(p.GetTimelineCount)}
    cur = safe(p.GetCurrentTimeline)
    proj["current_timeline"] = REG.put(cur)["$h"] if cur else None
    if "settings" in opts["include"]:
        proj["settings"] = ser(safe(p.GetSetting))
    if "render" in opts["include"]:
        proj["render"] = {"format_codec": safe(p.GetCurrentRenderFormatAndCodec), "mode": safe(p.GetCurrentRenderMode),
                          "in_progress": safe(p.IsRenderingInProgress), "jobs": ser(safe(p.GetRenderJobList)),
                          "presets": safe(p.GetRenderPresetList)}
        for j in proj["render"]["jobs"] or []:
            j["status"] = ser(safe(p.GetRenderJobStatus, j.get("JobId")))
    if "color_groups" in opts["include"]:
        proj["color_groups"] = [{"h": REG.put(g)["$h"], "name": safe(g.GetName)} for g in safe(p.GetColorGroupsList) or []]
    if "gallery" in opts["include"]:
        g = safe(p.GetGallery)
        albums = []
        if g:
            for kind, fn in (("still", g.GetGalleryStillAlbums), ("powergrade", g.GetGalleryPowerGradeAlbums)):
                for a in safe(fn) or []:
                    stills = safe(a.GetStills) or []
                    albums.append({"h": REG.put(a)["$h"], "kind": kind, "name": safe(g.GetAlbumName, a),
                                   "stills": [{"h": REG.put(s)["$h"], "label": safe(a.GetLabel, s)} for s in stills]})
        proj["gallery"] = albums
    out["project"] = proj
    if "timelines" in opts["include"]:
        tls = []
        for i in range(1, (safe(p.GetTimelineCount) or 0) + 1):
            tl = safe(p.GetTimelineByIndex, i)
            if tl is None:
                continue
            if only and safe(tl.GetName) not in only and REG.put(tl)["$h"] not in only:
                tls.append({"h": REG.put(tl)["$h"], "name": safe(tl.GetName), "skipped": True})
                continue
            tls.append(timeline_state(tl, p, depth, opts))
        out["timelines"] = tls
    if "media_pool" in opts["include"]:
        mp = p.GetMediaPool()
        out["media_pool"] = folder_state(mp.GetRootFolder(), depth, opts)
        cf = safe(mp.GetCurrentFolder)
        out["media_pool"]["current_folder"] = REG.put(cf)["$h"] if cf else None
    out["elapsed_s"] = round(time.time() - t0, 3)
    return out


def op_fusion_summary(req):
    """Tools, regIDs and input values of a Fusion composition, via the Fusion object API when present."""
    comp = REG.get(req["target"])
    tools = []
    tl = comp.GetToolList(False) if getattr(comp, "GetToolList", None) else None
    for _, tool in sorted((tl or {}).items()):
        attrs = safe(tool.GetAttrs) or {}
        t = {"h": REG.put(tool)["$h"], "name": attrs.get("TOOLS_Name"), "reg_id": attrs.get("TOOLS_RegID")}
        if req.get("inputs", True) and getattr(tool, "GetInputList", None):
            ins = {}
            for _, inp in sorted((safe(tool.GetInputList) or {}).items()):
                ia = safe(inp.GetAttrs) or {}
                iid = ia.get("INPS_ID")
                if not iid:
                    continue
                val = safe(tool.GetInput, iid)
                conn = safe(inp.GetConnectedOutput)
                ins[iid] = {"name": ia.get("INPS_Name"), "value": ser(val) if remote_info(val) is None else repr(val),
                            "connected": bool(conn)}
            t["inputs"] = ins
        tools.append(t)
    return {"tools": tools, "attrs": ser(safe(comp.GetAttrs) or {})}


def _tool_named(comp, name):
    t = comp.FindTool(name)
    return t if t else None


def _comp_index(item, comp_name=None):
    names = item.GetFusionCompNameList() or []
    if comp_name and comp_name in names:
        return names.index(comp_name) + 1
    return len(names)


def commit_comp(item, index):
    """Make script edits of a timeline item's Fusion comp count for playback/render.

    Measured on 21.0.4: tools/inputs changed through the Fusion object API are visible to GetInput and to
    ExportFusionComp, but Resolve keeps rendering the previously committed comp. Exporting the comp and
    re-importing it with ImportFusionComp commits it (verified by rendering frames before/after)."""
    path = os.path.join(STATE_DIR, "fusion_commit", "commit_%d.comp" % int(time.time() * 1000))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not item.ExportFusionComp(path, index):
        raise RuntimeError("ExportFusionComp failed during commit")
    before = item.GetFusionCompNameList() or []
    newc = item.ImportFusionComp(path)
    if newc is None:
        raise RuntimeError("ImportFusionComp failed during commit")
    after = item.GetFusionCompNameList() or []
    note = None
    if len(after) > len(before):
        added = [n for n in after if n not in before] or [after[-1]]
        item.LoadFusionCompByName(added[0])
        note = "commit added comp %r (now active); the previous version %r is kept as a backup" % (added[0], before[index - 1])
    return newc, path, note


def op_fusion_commit(req):
    item = REG.get(req["target"])
    idx = _comp_index(item, req.get("comp_name"))
    if idx == 0:
        raise RuntimeError("item has no Fusion comp")
    comp, path, note = commit_comp(item, idx)
    return {"comp": REG.put(comp)["$h"], "file": path, "note": note, "names": item.GetFusionCompNameList()}


def op_fusion_keyframes(req):
    """Animated transform + opacity on a timeline item through its Fusion comp (the Inspector has no keyframe API).

    Builds (idempotently) upstream -> ClaudeTransform -> ClaudeOpacity(Merge over transparent ClaudeBG) -> MediaOut1,
    where upstream is whatever already fed MediaOut1 (so existing Fusion work is kept). keys:
    [{"frame": clip-relative frame, "zoom": 1.0=100%, "pan": px, "tilt": px, "rotation": deg, "opacity": 0-100,
      "anchor_x": px, "anchor_y": px}]. Returns the values read back at each key frame and at midpoints."""
    item = REG.get(req["target"])
    comp = None
    if req.get("comp_name"):
        comp = item.GetFusionCompByName(req["comp_name"])
    if comp is None and item.GetFusionCompCount() > 0:
        comp = item.GetFusionCompByIndex(item.GetFusionCompCount())
    if comp is None:
        comp = item.AddFusionComp()
    if comp is None:
        raise RuntimeError("could not get or create a Fusion comp on this item")
    attrs = comp.GetAttrs()
    base = int(attrs.get("COMPN_RenderStart", 0))
    w = float(comp.GetPrefs("Comp.FrameFormat.Width") or 1920)
    h = float(comp.GetPrefs("Comp.FrameFormat.Height") or 1080)
    mo = comp.FindTool("MediaOut1")
    if mo is None:
        raise RuntimeError("comp has no MediaOut1")
    # No comp.Lock(): a SetInput made between Lock()/Unlock() reads back fine but is NOT rendered
    # (measured by samuelgursky/davinci-resolve-mcp, and matches our own renders). The commit below stays as a
    # second safety net.
    comp.StartUndo("Claude keyframes")
    try:
        tr = _tool_named(comp, "ClaudeTransform")
        mg = _tool_named(comp, "ClaudeOpacity")
        bg = _tool_named(comp, "ClaudeBG")
        if tr is None:
            up_out = mo.FindMainInput(1).GetConnectedOutput()
            up = up_out.GetTool() if up_out else comp.FindTool("MediaIn1")
            tr = comp.AddTool("Transform", -32768, -32768)
            tr.SetAttrs({"TOOLS_Name": "ClaudeTransform"})
            bg = comp.AddTool("Background", -32768, -32768)
            bg.SetAttrs({"TOOLS_Name": "ClaudeBG"})
            for a in ("TopLeftAlpha",):
                bg.SetInput(a, 0.0)
            mg = comp.AddTool("Merge", -32768, -32768)
            mg.SetAttrs({"TOOLS_Name": "ClaudeOpacity"})
            if not tr.ConnectInput("Input", up):
                raise RuntimeError("could not connect transform input")
            mg.ConnectInput("Background", bg)
            mg.ConnectInput("Foreground", tr)
            mo.ConnectInput("Input", mg)
        animated = {"Size": tr, "Angle": tr, "Center": tr, "Pivot": tr, "Blend": mg}
        wanted = set()
        for k in req["keys"]:
            if "zoom" in k: wanted.add("Size")
            if "rotation" in k: wanted.add("Angle")
            if "pan" in k or "tilt" in k: wanted.add("Center")
            if "anchor_x" in k or "anchor_y" in k: wanted.add("Pivot")
            if "opacity" in k: wanted.add("Blend")
        for inp in wanted:
            tool = animated[inp]
            ins = [x for x in tool.GetInputList().values() if x.GetAttrs().get("INPS_ID") == inp][0]
            if not ins.GetConnectedOutput():  # not animated yet
                tool.AddModifier(inp, "XYPath" if inp in ("Center", "Pivot") else "BezierSpline")
        written = []
        for k in sorted(req["keys"], key=lambda x: x["frame"]):
            t = base + int(k["frame"])
            if "zoom" in k:
                tr.SetInput("Size", float(k["zoom"]), t)
            if "rotation" in k:
                tr.SetInput("Angle", float(k["rotation"]), t)
            if "pan" in k or "tilt" in k:
                tr.SetInput("Center", {1: 0.5 + float(k.get("pan", 0)) / w, 2: 0.5 + float(k.get("tilt", 0)) / h}, t)
            if "anchor_x" in k or "anchor_y" in k:
                tr.SetInput("Pivot", {1: 0.5 + float(k.get("anchor_x", 0)) / w, 2: 0.5 + float(k.get("anchor_y", 0)) / h}, t)
            if "opacity" in k:
                mg.SetInput("Blend", float(k["opacity"]) / 100.0, t)
            written.append(t)
    finally:
        comp.EndUndo(True)
    # commit so Resolve actually renders the edits, then read back from the committed comp
    comp, commit_file, commit_note = commit_comp(item, _comp_index(item, req.get("comp_name")))
    tr, mg = comp.FindTool("ClaudeTransform"), comp.FindTool("ClaudeOpacity")
    if tr is None or mg is None:
        raise RuntimeError("committed comp lost the Claude tools - check %s" % commit_file)

    def read(t):
        c = tr.GetInput("Center", t) or {}
        pv = tr.GetInput("Pivot", t) or {}
        return {"zoom": tr.GetInput("Size", t), "rotation": tr.GetInput("Angle", t),
                "pan": round((c.get(1, 0.5) - 0.5) * w, 3), "tilt": round((c.get(2, 0.5) - 0.5) * h, 3),
                "anchor_x": round((pv.get(1, 0.5) - 0.5) * w, 3), "anchor_y": round((pv.get(2, 0.5) - 0.5) * h, 3),
                "opacity": round((mg.GetInput("Blend", t) or 0) * 100.0, 4)}
    times = sorted(set(written + [(a + b) // 2 for a, b in zip(written, written[1:])]))
    return {"comp": REG.put(comp)["$h"], "comp_name": safe(comp.GetAttrs).get("COMPS_Name") if safe(comp.GetAttrs) else None,
            "frame_offset": base, "frame_size": [w, h], "committed": True, "commit_note": commit_note,
            "readback": {str(t - base): read(t) for t in times}, "key_frames": [t - base for t in written]}


OPS = {"ping": op_ping, "roots": op_roots, "call": op_call, "batch": op_batch, "type": op_type,
       "has_methods": op_has_methods, "methods": op_methods, "consts": op_consts, "release": op_release, "exec": op_exec, "state": op_state,
       "fusion_summary": op_fusion_summary, "fusion_keyframes": op_fusion_keyframes, "fusion_commit": op_fusion_commit}




def handle(req):
    """Run one authenticated request; returns the response dict."""
    rid = req.get("id")
    fn = OPS.get(req.get("op"))
    if fn is None:
        return {"id": rid, "ok": False, "error": "unknown op %r" % req.get("op")}
    try:
        return {"id": rid, "ok": True, "result": fn(req)}
    except Exception as e:
        return {"id": rid, "ok": False, "error": "%s: %s" % (type(e).__name__, e), "traceback": traceback.format_exc()}
