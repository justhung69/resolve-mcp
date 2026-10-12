"""MCP server for DaVinci Resolve (Free or Studio) via the in-Resolve ClaudeBridge.

Run:  .venv\\Scripts\\python.exe server\\mcp_server.py   (stdio transport)

Conventions for every tool:
- Resolve objects are passed around as handles {"$h": "<id>", "type": "<Class>"}; a bare handle id
  string is accepted where a parameter is documented as a handle.
- Write tools read the value back and return {"requested", "actual", "ok"}; ok=false means Resolve
  silently ignored the change even if the call itself returned True.
- Destructive tools need confirm=true and write a .drp backup of the current project first.
- Frames are timeline-absolute unless a parameter says "relative".
"""
import functools
import json
import re
import time
from typing import Any
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

import catalog  # noqa: E402
import core  # noqa: E402
import workarounds as W  # noqa: E402
from bridge_client import BridgeError, handle_id  # noqa: E402

INSTRUCTIONS = """Controls DaVinci Resolve through the ClaudeBridge script running inside Resolve.
Start with resolve_status. If it says the bridge is not running, ask the user to click
Workspace > Scripts > ClaudeBridge in Resolve (or click it with computer use).
Use get_full_state to learn the true project state, and diff_state(before_id) after edits to
confirm what actually changed. Prefer the typed tools; use api_call for anything else in the
installed README (api_catalog lists it). Things the API cannot do (razor, trims, transitions,
speed) have workarounds: edit_list_get/edit_list_build, timeline_razor and timeline_build_otio.
Destructive operations need confirm=true and back the project up first."""

server = MCPServer("davinci-resolve", instructions=INSTRUCTIONS, version="1.0.0")

RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
RW = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
DESTR = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)


def page_guard(page=None):
    """Run the tool on `page` (None = any page) and always put the user back on the page they were on."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            with core.on_page(page):
                return fn(*a, **kw)
        return wrapper
    return deco


def H(h):
    return core.as_handle(h)


def C(target, method, *args, **kw):
    return core.call(H(target), method, list(args), **kw)


def frames_from_tc(tc, fps):
    """'HH:MM:SS:FF' (non-drop) -> absolute frame number."""
    hh, mm, ss, ff = [int(x) for x in tc.replace(";", ":").split(":")]
    fps_i = int(round(float(fps)))
    return ((hh * 60 + mm) * 60 + ss) * fps_i + ff


def project_fps():
    p = core.require(core.roots().get("project"), "project")
    return float(core.get(p, "GetSetting", "timelineFrameRate"))


def summarize(results):
    return {"ok": all(r.get("ok", True) is not False for r in results), "results": results}


# ======================================================================= status & meta

@server.tool(annotations=RO)
def resolve_status() -> dict:
    """Bridge + Resolve status: version, page, open project/timeline, and the README in use."""
    try:
        ping = core.B.request("ping")
    except Exception as e:
        return {"bridge": "not running", "error": str(e)}
    cat = catalog.get_catalog()
    ping["readme"] = cat["path"]
    ping["documented_methods"] = sum(len(v) for v in cat["classes"].values())
    return ping


@server.tool(annotations=RO)
def get_roots() -> dict:
    """Handles for the current Resolve objects: project_manager, project, media_pool, root_folder,
    current_folder, timeline, media_storage, gallery, fusion, current_video_item."""
    return core.roots()


@server.tool(annotations=RO)
def api_catalog(class_name: str | None = None, method: str | None = None, search: str | None = None) -> dict:
    """The scripting API as documented by the README installed with THIS Resolve.
    No args: classes with method counts. class_name: its methods. method: full doc. search: substring match."""
    cat = catalog.get_catalog()
    cls = cat["classes"]
    if search:
        s = search.lower()
        return {"matches": [{"class": c, "method": m, "signature": e["signatures"], "doc": e["doc"][:300]}
                            for c, ms in cls.items() for m, e in ms.items()
                            if s in m.lower() or s in e["doc"].lower()][:80]}
    if class_name and method:
        return {"class": class_name, "method": method, **(cls.get(class_name, {}).get(method) or {"error": "not documented"})}
    if class_name:
        return {"class": class_name, "methods": {m: e["signatures"] for m, e in cls.get(class_name, {}).items()}}
    return {"readme": cat["path"], "classes": {c: len(v) for c, v in cls.items()}, "constants": cat["constants"]}


@server.tool(annotations=RO)
def api_methods_live(target: str) -> dict:
    """Method names the live object really has in this Resolve build (dir() of the proxy), plus which of
    them are documented in the README. Works for Fusion objects too."""
    live = core.B.request("methods", target=handle_id(target))
    doc = catalog.get_catalog()["classes"].get(live["type"], {})
    live["undocumented"] = [m for m in live["methods"] if doc and m not in doc]
    live["documented_but_missing"] = [m for m, e in doc.items() if m not in live["methods"] and not e.get("deprecated")
                                      and not e.get("unsupported")]
    return live


@server.tool(annotations=RW)
def api_call(target: str | dict, method: str, args: list | None = None, confirm: bool = False,
             dry_run: bool = False, timeout: float = 60, force: bool = False) -> dict:
    """Call ANY documented Resolve API method. target: a handle (or "resolve").
    args may contain handles {"$h": id} and constants {"$const": "EXPORT_EDL"}.
    Setters with a matching getter are read back (verified / requested / actual).
    Destructive methods need confirm=true (a .drp backup is made first); dry_run previews.
    Studio-only methods are refused on Resolve Free (they trip a blocking dialog) unless force=true."""
    return core.call(H(target), method, args or [], confirm=confirm, dry_run=dry_run, timeout=timeout, force=force)


@server.tool(annotations=RW)
def api_batch(calls: list, confirm: bool = False, dry_run: bool = False, stop_on_error: bool = True) -> dict:
    """Run several api_call-style calls [{"target", "method", "args"}] in order, each catalog-checked and
    verified. Use {"$ref": i} inside args to pass the result of call i. confirm/dry_run apply to all."""
    results, refs = [], []

    def subst(v):
        if isinstance(v, dict) and "$ref" in v:
            return refs[v["$ref"]]
        if isinstance(v, dict):
            return {k: subst(x) for k, x in v.items()}
        if isinstance(v, list):
            return [subst(x) for x in v]
        return v

    for i, c in enumerate(calls):
        try:
            r = core.call(H(subst(c["target"])), c["method"], subst(c.get("args") or []), confirm=confirm, dry_run=dry_run)
            refs.append(r.get("result"))
            results.append(dict(r, i=i))
            if stop_on_error and r.get("ok") is False:
                break
        except Exception as e:
            refs.append(None)
            results.append({"i": i, "ok": False, "error": "%s: %s" % (type(e).__name__, e)})
            if stop_on_error:
                break
    return summarize(results)


@server.tool(annotations=DESTR)
def python_exec(code: str) -> dict:
    """ADVANCED escape hatch: run Python inside Resolve (globals: resolve, fusion, H(handle_id)); set
    `result` to return a value. Disabled unless %LOCALAPPDATA%\\ResolveMCP\\config.json has
    "allow_exec": true. Bypasses all safety checks - prefer typed tools."""
    res = core.B.request("exec", code=code, timeout=120)
    core.oplog("python_exec", {"code": code[:2000]}, True, result=res)
    return {"result": res}


@server.tool(annotations=RW)
def bridge_reload() -> dict:
    """Reload bridge_core.py inside Resolve (after updating the tool) without restarting the bridge."""
    return core.B.request("reload")


# ======================================================================= state

@server.tool(annotations=RO)
def get_full_state(depth: int = 2, include: list[str] | None = None, timelines: list[str] | None = None,
                   item_offset: int = 0, item_limit: int = 200, max_chars: int = 60000) -> dict:
    """Snapshot of the real project state, saved for diff_state. depth 0: structure only; 1: + items;
    2: + item properties, fusion comps, color versions; 3: + node graphs, timeline settings, all clip
    properties/metadata. include subset of: project, settings, timelines, items, media_pool, render,
    gallery, color_groups, clip_properties. timelines: names/handles to expand (others summarized).
    item_offset/item_limit page through big tracks. Returns snapshot_id + (possibly truncated) state."""
    params = {"depth": depth, "include": include, "timelines": timelines, "item_offset": item_offset,
              "item_limit": item_limit}
    st = core.B.request("state", timeout=600, **{k: v for k, v in params.items() if v is not None})
    st = core.compact(st)
    sid = core.save_snapshot(st, params)
    text = json.dumps(st, ensure_ascii=False, separators=(",", ":"))
    if len(text) > max_chars:
        return {"snapshot_id": sid, "truncated": True, "chars": len(text),
                "hint": "Too big - narrow with include=[...], timelines=[...], lower depth or page with item_offset. "
                        "The full snapshot is saved and usable in diff_state.",
                "state_prefix": text[:max_chars]}
    return {"snapshot_id": sid, "state": st}


@server.tool(annotations=RO)
def diff_state(before: str, after: str | None = None, limit: int = 300) -> dict:
    """Changes between two snapshots (ids from get_full_state). after=None takes a fresh snapshot with the
    same parameters as `before`, so: snap -> edit -> diff_state(snap) shows exactly what changed."""
    a = core.load_snapshot(before)
    if after is None:
        params = a["params"]
        st = core.compact(core.B.request("state", timeout=600, **{k: v for k, v in params.items() if v is not None}))
        after = core.save_snapshot(st, params)
        b = {"state": st}
    else:
        b = core.load_snapshot(after)
    changes = core.diff(a["state"], b["state"], limit=limit)
    return {"before": before, "after": after, "change_count": len(changes), "changes": changes}


@server.tool(annotations=RO)
def list_snapshots(limit: int = 20) -> dict:
    """Saved state snapshots, newest first."""
    files = sorted((f for f in os.listdir(core.SNAP_DIR) if f.endswith(".json")), reverse=True)[:limit]
    return {"snapshots": [f[:-5] for f in files], "folder": core.SNAP_DIR}


@server.tool(annotations=RO)
def op_log(limit: int = 50) -> dict:
    """Last entries of the JSONL operation log (every write call with args, result, timing)."""
    if not os.path.exists(core.OPLOG):
        return {"entries": []}
    with open(core.OPLOG, encoding="utf-8") as f:
        lines = f.readlines()[-limit:]
    return {"log_file": core.OPLOG, "entries": [json.loads(x) for x in lines]}


# ======================================================================= projects

@server.tool(annotations=RO)
def project_list() -> dict:
    """Projects and folders in the current project-manager folder, with attributes, plus the database."""
    pm = core.roots()["project_manager"]
    return {"database": core.get(pm, "GetCurrentDatabase"), "folder": core.get(pm, "GetCurrentFolder"),
            "projects": core.get(pm, "GetProjectListInCurrentFolder"),
            "attributes": core.B.try_call(handle_id(pm), "GetProjectAttributesInCurrentFolder"),
            "folders": core.get(pm, "GetFolderListInCurrentFolder")}


def _save_before_switch(pm, confirm):
    """CreateProject/LoadProject replace the current project; an unsaved one is simply gone (samuelgursky api_truth)."""
    cur = core.current_project_name()
    if cur == "Untitled Project" and not confirm:
        raise PermissionError("The current project is the unsaved 'Untitled Project'; switching would discard it. "
                              "Save it under a name in Resolve, or pass confirm=true to discard it.")
    if cur and cur != "Untitled Project":
        core.get(pm, "SaveProject")
    return cur


@server.tool(annotations=RW)
def project_open(name: str, confirm: bool = False) -> dict:
    """Load a project by name (current folder). Saves the current project first. Verifies the loaded name."""
    pm = core.roots()["project_manager"]
    _save_before_switch(pm, confirm)
    res = core.call(pm, "LoadProject", [name], tool="project_open")
    actual = core.get(core.roots()["project"], "GetName") if core.roots().get("project") else None
    return {"requested": name, "actual": actual, "ok": actual == name, "project": res.get("result")}


@server.tool(annotations=RW)
def project_create(name: str, media_location: str | None = None, confirm: bool = False) -> dict:
    """Create (and open) a new project. Saves the current project first. Fails if the name exists."""
    pm = core.roots()["project_manager"]
    _save_before_switch(pm, confirm)
    args = [name] + ([media_location] if media_location else [])
    res = core.call(pm, "CreateProject", args, tool="project_create")
    if res.get("result"):
        core.get(pm, "SaveProject")  # a new project is not listed in the project manager until saved once
    actual = core.get(core.roots()["project"], "GetName") if core.roots().get("project") else None
    return {"requested": name, "actual": actual, "ok": bool(res.get("result")) and actual == name,
            "listed": name in (core.get(pm, "GetProjectListInCurrentFolder") or [])}


@server.tool(annotations=RW)
def project_save() -> dict:
    """Save the current project. Refuses the never-saved 'Untitled Project' (SaveProject cannot succeed there)."""
    if core.current_project_name() == "Untitled Project":
        raise PermissionError("'Untitled Project' has no location and SaveProject cannot save it - create a named "
                              "project instead (project_create).")
    return core.call(core.roots()["project_manager"], "SaveProject", [], tool="project_save")


@server.tool(annotations=RW)
def project_export(path: str, name: str | None = None, with_stills_and_luts: bool = True) -> dict:
    """Export a project (default: current) to a .drp file. Verifies the file exists."""
    r = core.roots()
    name = name or core.get(r["project"], "GetName")
    res = core.call(r["project_manager"], "ExportProject", [name, path, with_stills_and_luts], tool="project_export")
    res["file_exists"] = os.path.exists(path)
    res["ok"] = bool(res.get("result")) and res["file_exists"]
    return res


@server.tool(annotations=RW)
def project_import(path: str, name: str | None = None) -> dict:
    """Import a .drp project file into the current project-manager folder."""
    pm = core.roots()["project_manager"]
    res = core.call(pm, "ImportProject", [path] + ([name] if name else []), tool="project_import")
    res["now_listed"] = (name or os.path.splitext(os.path.basename(path))[0]) in (core.get(pm, "GetProjectListInCurrentFolder") or [])
    return res


@server.tool(annotations=DESTR)
def project_delete(name: str, confirm: bool = False) -> dict:
    """Delete a project (must not be loaded). confirm=true required; a .drp backup is exported first."""
    pm = core.roots()["project_manager"]
    res = core.call(pm, "DeleteProject", [name], confirm=confirm, tool="project_delete")
    res["still_listed"] = name in (core.get(pm, "GetProjectListInCurrentFolder") or [])
    res["ok"] = bool(res.get("result")) and not res["still_listed"]
    return res


@server.tool(annotations=RW)
def project_folder(action: str, name: str | None = None, confirm: bool = False) -> dict:
    """Project-manager folders. action: list | open | up | root | create | delete (delete needs confirm)."""
    pm = core.roots()["project_manager"]
    if action == "list":
        return {"current": core.get(pm, "GetCurrentFolder"), "folders": core.get(pm, "GetFolderListInCurrentFolder")}
    method = {"open": "OpenFolder", "up": "GotoParentFolder", "root": "GotoRootFolder", "create": "CreateFolder",
              "delete": "DeleteFolder"}[action]
    res = core.call(pm, method, [name] if name else [], confirm=confirm, tool="project_folder")
    res["current_folder"] = core.get(pm, "GetCurrentFolder")
    return res


@server.tool(annotations=RO)
def project_settings_get(keys: list[str] | None = None) -> dict:
    """Project settings (all, or the given keys)."""
    p = core.require(core.roots().get("project"), "project")
    if keys:
        return {k: core.get(p, "GetSetting", k) for k in keys}
    return core.get(p, "GetSetting")


@server.tool(annotations=RW)
def project_settings_set(settings: dict) -> dict:
    """Set project settings {key: value}; each is read back. timelineFrameRate etc. can only change before
    a timeline exists - such silent refusals show up as ok=false."""
    p = core.require(core.roots().get("project"), "project")
    return summarize([core.call(p, "SetSetting", [k, v], tool="project_settings_set") for k, v in settings.items()])


@server.tool(annotations=RW)
def timeline_settings_set(settings: dict, timeline: str | None = None) -> dict:
    """Set timeline settings {key: value} with read-back. Timeline-specific values usually require
    useCustomSettings='1' first."""
    tl = core.resolve_timeline(timeline)
    return summarize([core.call(tl, "SetSetting", [k, v], tool="timeline_settings_set") for k, v in settings.items()])


@server.tool(annotations=RW)
def open_page(page: str) -> dict:
    """Switch page: media, cut, edit, fusion, color, fairlight, deliver. Verified by GetCurrentPage."""
    return C("resolve", "OpenPage", page, tool="open_page")


@server.tool(annotations=RW)
def database(action: str = "current", db_info: dict | None = None, confirm: bool = False) -> dict:
    """Project databases. action: current | list | switch (db_info {"DbType","DbName"}; closes the open
    project, needs confirm)."""
    pm = core.roots()["project_manager"]
    if action == "current":
        return core.get(pm, "GetCurrentDatabase")
    if action == "list":
        return {"databases": core.get(pm, "GetDatabaseList")}
    return core.call(pm, "SetCurrentDatabase", [db_info], confirm=confirm, tool="database")


# ======================================================================= media pool

@server.tool(annotations=RW)
def media_import(paths: list, folder: str | None = None) -> dict:
    """Import files/folders into the media pool (current folder, or `folder` handle). Items may be paths
    or {"FilePath": "C:/seq/frame_%04d.png", "StartIndex": 0, "EndIndex": 99} for image sequences.
    Returns the created clip handles and verifies the count."""
    r = core.roots()
    mp = core.require(r.get("media_pool"), "media pool")
    if folder:
        core.call(mp, "SetCurrentFolder", [H(folder)], tool="media_import")
    # Measured on 21.0.4: ImportMedia([{"FilePath": plain_file}]) returns [] - the dict form only works for
    # image sequences (with StartIndex/EndIndex) - and one bad entry makes the whole call return [].
    # So plain files go as strings and each entry is imported on its own, which keeps the returned clips in
    # request order and pinpoints failures. (A folder path may yield several clips.)
    plain = [p["FilePath"] if isinstance(p, dict) and "StartIndex" not in p else p for p in paths]
    clips, failed = [], []
    for p in plain:
        got = core.call(mp, "ImportMedia", [[p]], tool="media_import", timeout=300).get("result") or []
        clips += got
        if not got:
            failed.append(p)
    return {"requested": len(paths), "imported": len(clips), "ok": not failed and len(clips) >= len(paths),
            "clips": [{"h": handle_id(c), "name": core.get(c, "GetName")} for c in clips],
            "failed": failed or None,
            "note": None if not failed else "Not imported: missing/unsupported file, or already in this media pool folder."}


@server.tool(annotations=RO)
def media_pool_list(folder: str | None = None, recursive: bool = True, details: bool = False) -> dict:
    """Media pool tree (from `folder` or root): clips with key properties; details=true adds all clip
    properties and metadata."""
    r = core.roots()
    st = core.B.request("state", include=["media_pool"] + (["clip_properties"] if details else []),
                        depth=2 if details else 1, timeout=300)
    tree = st.get("media_pool")
    if folder:
        def find(f):
            if f["h"] == handle_id(folder):
                return f
            for s in f.get("subfolders", []):
                x = find(s)
                if x:
                    return x
        tree = find(tree)
    if tree and not recursive:
        tree = dict(tree, subfolders=[{"h": s["h"], "name": s["name"]} for s in tree.get("subfolders", [])])
    return core.compact(tree or {})


@server.tool(annotations=RW)
def media_folder(action: str, name: str | None = None, parent: str | None = None, folder: str | None = None,
                 clips: list | None = None, folders: list | None = None, confirm: bool = False) -> dict:
    """Media pool folders. action: create (name, parent?) | set_current (folder) | move_clips (clips, folder) |
    move_folders (folders, folder) | delete (folders, confirm) | export_drb (folder, name=path) |
    import_drb (name=path)."""
    r = core.roots()
    mp = r["media_pool"]
    if action == "create":
        res = core.call(mp, "AddSubFolder", [H(parent) if parent else r["root_folder"], name], tool="media_folder")
        res["ok"] = bool(res.get("result")) and core.get(res["result"], "GetName") == name
        return res
    if action == "set_current":
        return core.call(mp, "SetCurrentFolder", [H(folder)], tool="media_folder")
    if action == "move_clips":
        return core.call(mp, "MoveClips", [[H(c) for c in clips], H(folder)], tool="media_folder")
    if action == "move_folders":
        return core.call(mp, "MoveFolders", [[H(f) for f in folders], H(folder)], tool="media_folder")
    if action == "delete":
        return core.call(mp, "DeleteFolders", [[H(f) for f in folders]], confirm=confirm, tool="media_folder")
    if action == "export_drb":
        res = core.call(H(folder), "Export", [name], tool="media_folder")
        res["ok"] = bool(res.get("result")) and os.path.exists(name)
        return res
    if action == "import_drb":
        return core.call(mp, "ImportFolderFromFile", [name], tool="media_folder")
    raise ValueError("unknown action %r" % action)


@server.tool(annotations=RO)
def clip_inspect(clip: str) -> dict:
    """Everything about a media pool clip: properties, metadata, third-party metadata, markers, flags,
    color, mark in/out, audio mapping, mattes."""
    c = H(clip)
    g = lambda m, *a: core.B.try_call(handle_id(c), m, *a)
    return core.compact({"h": handle_id(c), "name": g("GetName"), "media_id": g("GetMediaId"),
                         "properties": g("GetClipProperty"), "metadata": g("GetMetadata"),
                         "third_party_metadata": g("GetThirdPartyMetadata"), "markers": g("GetMarkers"),
                         "flags": g("GetFlagList"), "clip_color": g("GetClipColor"), "mark_in_out": g("GetMarkInOut"),
                         "audio_mapping": g("GetAudioMapping"),
                         "mattes": core.B.try_call(handle_id(core.roots()["media_pool"]), "GetClipMatteList", c)})


@server.tool(annotations=RW)
def clip_update(clip: str, name: str | None = None, clip_color: str | None = None, properties: dict | None = None,
                metadata: dict | None = None, third_party_metadata: dict | None = None,
                flags_add: list[str] | None = None, flags_clear: str | None = None,
                mark_in: int | None = None, mark_out: int | None = None, mark_type: str = "all") -> dict:
    """Change a media pool clip; every change is read back. clip_color '' clears it. properties are
    Clip Attributes keys (see clip_inspect). flags_clear: a color or 'All'."""
    c = H(clip)
    out = []
    if name is not None:
        out.append(core.call(c, "SetName", [name], tool="clip_update"))
    if clip_color is not None:
        out.append(core.call(c, "ClearClipColor" if clip_color == "" else "SetClipColor", [] if clip_color == "" else [clip_color], tool="clip_update"))
    for k, v in (properties or {}).items():
        out.append(core.call(c, "SetClipProperty", [k, v], tool="clip_update"))
    for k, v in (metadata or {}).items():
        out.append(core.call(c, "SetMetadata", [k, v], tool="clip_update"))
    for k, v in (third_party_metadata or {}).items():
        out.append(core.call(c, "SetThirdPartyMetadata", [k, v], tool="clip_update"))
    if flags_clear:
        r = core.call(c, "ClearFlags", [flags_clear], tool="clip_update")
        flags = core.get(c, "GetFlagList") or []
        r.update(actual=flags, verified=(not flags) if flags_clear == "All" else flags_clear not in flags)
        out.append(r)
    for fl in flags_add or []:
        r = core.call(c, "AddFlag", [fl], tool="clip_update")
        r["actual"] = core.get(c, "GetFlagList")
        r["verified"] = fl in (r["actual"] or [])
        r["ok"] = r["verified"]
        out.append(r)
    if mark_in is not None and mark_out is not None:
        out.append(core.call(c, "SetMarkInOut", [mark_in, mark_out, mark_type], tool="clip_update"))
    return summarize(out)


@server.tool(annotations=DESTR)
def clip_media_link(action: str, clips: list, path: str | None = None, confirm: bool = False) -> dict:
    """Relink / replace / proxy operations. action: relink (clips, path=folder) | unlink (clips) |
    replace (one clip, path=file) | replace_preserve_subclip | link_proxy (one clip, path) | unlink_proxy |
    link_full_res (one clip, path) | monitor_growing_file. Destructive ones need confirm."""
    mp = core.roots()["media_pool"]
    hs = [H(c) for c in clips]
    if action == "relink":
        return core.call(mp, "RelinkClips", [hs, path], confirm=confirm, tool="clip_media_link")
    if action == "unlink":
        return core.call(mp, "UnlinkClips", [hs], confirm=confirm, tool="clip_media_link")
    method = {"replace": "ReplaceClip", "replace_preserve_subclip": "ReplaceClipPreserveSubClip",
              "link_proxy": "LinkProxyMedia", "unlink_proxy": "UnlinkProxyMedia",
              "link_full_res": "LinkFullResolutionMedia", "monitor_growing_file": "MonitorGrowingFile"}[action]
    res = core.call(hs[0], method, [path] if path else [], confirm=confirm, tool="clip_media_link")
    res["clip_after"] = {k: core.B.try_call(handle_id(hs[0]), "GetClipProperty", k) for k in ("File Path", "Proxy", "Proxy Media Path")}
    return res


@server.tool(annotations=RO)
def media_storage_browse(path: str | None = None) -> dict:
    """Media Storage: mounted volumes (no path) or the subfolders/files of an absolute folder path."""
    ms = core.roots()["media_storage"]
    if not path:
        return {"volumes": core.get(ms, "GetMountedVolumeList")}
    return {"path": path, "subfolders": core.get(ms, "GetSubFolderList", path), "files": core.get(ms, "GetFileList", path)}


# ======================================================================= timelines

@server.tool(annotations=RO)
def timeline_list() -> dict:
    """All timelines in the project with frame range, track counts and which one is current."""
    st = core.B.request("state", include=["timelines"], depth=0)
    return {"timelines": [{k: t.get(k) for k in ("h", "name", "current", "start_frame", "end_frame", "start_timecode")}
                          | {"tracks": {tt: len(v) for tt, v in t.get("tracks", {}).items()}} for t in st.get("timelines", [])]}


@server.tool(annotations=RW)
def timeline_create(name: str, clips: list | None = None) -> dict:
    """Create a timeline (empty, or from clips). clips: [{"clip": handle, "start_frame": int, "end_frame": int
    (exclusive), "record_frame": int}] or plain clip handles. Becomes current. Verifies item count."""
    r = core.roots()
    mp = core.require(r.get("media_pool"), "media pool")
    if not clips:
        res = core.call(mp, "CreateEmptyTimeline", [name], tool="timeline_create")
    else:
        infos = []
        for c in clips:
            if isinstance(c, dict) and "clip" in c:
                d = {"mediaPoolItem": H(c["clip"])}
                for a, b in (("start_frame", "startFrame"), ("end_frame", "endFrame"), ("record_frame", "recordFrame")):
                    if a in c:
                        d[b] = c[a]
                infos.append(d)
            else:
                infos.append({"mediaPoolItem": H(c)})
        res = core.call(mp, "CreateTimelineFromClips", [name, infos], tool="timeline_create", timeout=300)
    tl = res.get("result")
    res["ok"] = bool(tl) and core.get(tl, "GetName") == name
    if tl:
        res["item_count"] = sum(len(core.get(tl, "GetItemListInTrack", "video", i) or [])
                                for i in range(1, (core.get(tl, "GetTrackCount", "video") or 0) + 1))
        if clips:
            res["ok"] = res["ok"] and res["item_count"] >= len(clips)
    return res


@server.tool(annotations=RW)
def timeline_set_current(timeline: str) -> dict:
    """Make a timeline current (by name or handle). Verified."""
    tl = core.resolve_timeline(timeline)
    return core.call(core.roots()["project"], "SetCurrentTimeline", [tl], tool="timeline_set_current")


@server.tool(annotations=RW)
def timeline_append(clips: list) -> dict:
    """Append clips to the CURRENT timeline. clips: [{"clip": handle, "start_frame": int, "end_frame": int
    (EXCLUSIVE), "track": int, "record_frame": int (timeline-absolute), "media_type": 1 video-only | 2 audio-only}]
    or bare handles. Every placed item is read back (start, duration, track)."""
    mp = core.roots()["media_pool"]
    infos, want = [], []
    for c in clips:
        if isinstance(c, dict) and "clip" in c:
            d = {"mediaPoolItem": H(c["clip"])}
            for a, b in (("start_frame", "startFrame"), ("end_frame", "endFrame"), ("track", "trackIndex"),
                         ("record_frame", "recordFrame"), ("media_type", "mediaType")):
                if a in c:
                    d[b] = c[a]
            infos.append(d)
            want.append(c)
        else:
            infos.append({"mediaPoolItem": H(c)})
            want.append({})
    res = core.call(mp, "AppendToTimeline", [infos], tool="timeline_append", timeout=300)
    items = res.get("result") or []
    placed = []
    for it, w in zip(items, want):
        got = {"start": core.get(it, "GetStart"), "duration": core.get(it, "GetDuration"),
               "track": core.get(it, "GetTrackTypeAndIndex")}
        ok = True
        if "record_frame" in w:
            ok &= got["start"] == w["record_frame"]
        if "start_frame" in w and "end_frame" in w:
            ok &= got["duration"] == w["end_frame"] - w["start_frame"]
        if "track" in w:
            ok &= got["track"][1] == w["track"]
        placed.append({"item": handle_id(it), "requested": w, "actual": got, "ok": ok})
    return {"ok": len(items) >= len(infos) and all(p["ok"] for p in placed), "appended": len(items),
            "requested": len(infos), "items": placed}


@server.tool(annotations=RO)
def timeline_inspect(timeline: str | None = None, track_type: str | None = None, track: int | None = None,
                     depth: int = 2, item_offset: int = 0, item_limit: int = 200) -> dict:
    """One timeline in detail: tracks (name/enabled/locked/subtype), items with positions, source range,
    properties, markers, flags, fusion comps, color versions (depth 3 adds node graphs and settings)."""
    tl = core.resolve_timeline(timeline)
    st = core.B.request("state", include=["timelines", "items", "settings"], timelines=[handle_id(tl)], depth=depth,
                        item_offset=item_offset, item_limit=item_limit, timeout=600)
    t = next(x for x in st["timelines"] if x["h"] == handle_id(tl))
    if track_type:
        t["tracks"] = {track_type: [x for x in t["tracks"][track_type] if track is None or x["index"] == track]}
    return core.compact(t)


@server.tool(annotations=RW)
def timeline_track(action: str, track_type: str = "video", index: int | None = None, value: str | bool | None = None,
                   audio_type: str | None = None, timeline: str | None = None, confirm: bool = False) -> dict:
    """Tracks. action: add (track_type, audio_type e.g. 'stereo') | delete (index, confirm) | rename (index,
    value) | enable (index, value bool) | lock (index, value bool) | voice_isolation (index, value
    {"isEnabled","amount"}). Verified by read-back."""
    tl = core.resolve_timeline(timeline)
    if action == "add":
        before = core.get(tl, "GetTrackCount", track_type)
        res = core.call(tl, "AddTrack", [track_type] + ([audio_type] if audio_type else []), tool="timeline_track")
        res["actual"] = core.get(tl, "GetTrackCount", track_type)
        res["ok"] = res["actual"] == before + 1
        return res
    if action == "delete":
        before = core.get(tl, "GetTrackCount", track_type)
        res = core.call(tl, "DeleteTrack", [track_type, index], confirm=confirm, tool="timeline_track")
        if not res.get("dry_run"):
            res["actual"] = core.get(tl, "GetTrackCount", track_type)
            res["ok"] = res["actual"] == before - 1
        return res
    if action == "voice_isolation":
        res = core.call(tl, "SetVoiceIsolationState", [index, value], tool="timeline_track")
        res["actual"] = core.B.try_call(handle_id(tl), "GetVoiceIsolationState", index)
        res["ok"] = core.same(value, res["actual"])
        return res
    method = {"rename": "SetTrackName", "enable": "SetTrackEnable", "lock": "SetTrackLock"}[action]
    return core.call(tl, method, [track_type, index, value], tool="timeline_track")


@server.tool(annotations=RW)
def timeline_playhead(timecode: str | None = None, timeline: str | None = None) -> dict:
    """Get (no timecode) or set the playhead of the current timeline ('HH:MM:SS:FF'). Works on Cut, Edit,
    Color, Fairlight and Deliver pages. Also returns the current video item."""
    tl = core.resolve_timeline(timeline)
    page = core.get("resolve", "GetCurrentPage")
    if page not in ("cut", "edit", "color", "fairlight", "deliver"):
        raise ValueError("The playhead API only works on the Cut, Edit, Color, Fairlight and Deliver pages; current "
                         "page is %r. Call open_page('edit') first." % page)
    out = core.call(tl, "SetCurrentTimecode", [timecode], tool="timeline_playhead") if timecode else \
        {"timecode": core.get(tl, "GetCurrentTimecode")}
    out["current_video_item"] = core.B.try_call(handle_id(tl), "GetCurrentVideoItem")
    return out


@server.tool(annotations=RW)
def timeline_insert(kind: str, name: str | None = None) -> dict:
    """Insert at the playhead of the current timeline. kind: generator | fusion_generator | ofx_generator |
    title | fusion_title | fusion_composition. name e.g. '10 Step', 'Solid Color', 'Text', 'Text+'."""
    tl = core.resolve_timeline()
    method = {"generator": "InsertGeneratorIntoTimeline", "fusion_generator": "InsertFusionGeneratorIntoTimeline",
              "ofx_generator": "InsertOFXGeneratorIntoTimeline", "title": "InsertTitleIntoTimeline",
              "fusion_title": "InsertFusionTitleIntoTimeline",
              "fusion_composition": "InsertFusionCompositionIntoTimeline"}[kind]
    res = core.call(tl, method, [name] if name else [], tool="timeline_insert")
    it = res.get("result")
    res["ok"] = bool(it)
    if it:
        res["item"] = {"name": core.get(it, "GetName"), "start": core.get(it, "GetStart"), "duration": core.get(it, "GetDuration"),
                       "track": core.get(it, "GetTrackTypeAndIndex")}
    return res


EXPORT_TYPES = {"aaf": "EXPORT_AAF", "drt": "EXPORT_DRT", "edl": "EXPORT_EDL", "fcp7xml": "EXPORT_FCP_7_XML",
                "fcpxml1.8": "EXPORT_FCPXML_1_8", "fcpxml1.9": "EXPORT_FCPXML_1_9", "fcpxml1.10": "EXPORT_FCPXML_1_10",
                "fcpxml": "EXPORT_FCPXML_1_10", "csv": "EXPORT_TEXT_CSV", "tab": "EXPORT_TEXT_TAB", "otio": "EXPORT_OTIO",
                "ale": "EXPORT_ALE", "ale_cdl": "EXPORT_ALE_CDL", "hdr10a": "EXPORT_HDR_10_PROFILE_A",
                "hdr10b": "EXPORT_HDR_10_PROFILE_B", "dolby2.9": "EXPORT_DOLBY_VISION_VER_2_9",
                "dolby4.0": "EXPORT_DOLBY_VISION_VER_4_0", "dolby5.1": "EXPORT_DOLBY_VISION_VER_5_1"}


@server.tool(annotations=RW)
def timeline_export(path: str, format: str, subtype: str = "none", timeline: str | None = None) -> dict:
    """Export a timeline. format: aaf | drt | edl | fcp7xml | fcpxml (1.10) | fcpxml1.8 | fcpxml1.9 | csv | tab |
    otio | ale | ale_cdl | hdr10a | hdr10b | dolby2.9/4.0/5.1. subtype (AAF: aaf_new | aaf_existing; EDL: none |
    cdl | sdl | missing_clips). Verifies the file was written."""
    tl = core.resolve_timeline(timeline)
    sub = {"none": "EXPORT_NONE", "aaf_new": "EXPORT_AAF_NEW", "aaf_existing": "EXPORT_AAF_EXISTING", "cdl": "EXPORT_CDL",
           "sdl": "EXPORT_SDL", "missing_clips": "EXPORT_MISSING_CLIPS"}[subtype]
    if format == "aaf" and subtype == "none":
        sub = "EXPORT_AAF_NEW"
    res = core.call(tl, "Export", [path, {"$const": EXPORT_TYPES[format]}, {"$const": sub}], tool="timeline_export", timeout=300)
    res["file_exists"] = os.path.exists(path)
    if os.path.isdir(path):
        res["notes"].append("Written as a bundle folder (FCPXML 1.10 style); timeline_import accepts the folder.")
    res["ok"] = bool(res.get("result")) and res["file_exists"]
    return res


@server.tool(annotations=RW)
def timeline_import(path: str, name: str | None = None, import_source_clips: bool = True,
                    source_clips_path: str | None = None) -> dict:
    """Import AAF / EDL / XML / FCPXML / DRT / ADL / OTIO as a new timeline. Verifies the timeline exists."""
    r = core.roots()
    mp = r["media_pool"]
    if os.path.isdir(path):
        # Resolve exports FCPXML 1.10 as a bundle folder "<name>.fcpxml/Info.fcpxml"; import the inner document.
        inner = [os.path.join(dp, f) for dp, _, fs in os.walk(path) for f in fs if f.lower().endswith((".fcpxml", ".xml"))]
        if not inner:
            raise ValueError("%s is a folder without an .fcpxml document inside" % path)
        path = inner[0]
    opts = {"importSourceClips": import_source_clips}
    if not import_source_clips:
        # Without this, FCPXML/XML import with importSourceClips=False finds no media and returns None (21.0.4).
        opts["sourceClipsFolders"] = [r["root_folder"]] + W._all_subfolders(r["root_folder"])
    if name:
        opts["timelineName"] = core.unique_timeline_name(name)  # an existing name makes the import return None
    if source_clips_path:
        opts["sourceClipsPath"] = source_clips_path
    res = core.call(mp, "ImportTimelineFromFile", [path, opts], tool="timeline_import", timeout=600)
    tl = res.get("result")
    res["ok"] = bool(tl)
    if tl:
        res["timeline"] = {"name": core.get(tl, "GetName"), "start": core.get(tl, "GetStartFrame"), "end": core.get(tl, "GetEndFrame")}
    return res


@server.tool(annotations=RW)
def timeline_action(action: str, timeline: str | None = None, name: str | None = None, timecode: str | None = None,
                    items: list | None = None, mark_in: int | None = None, mark_out: int | None = None,
                    options: dict | None = None, still_source: int = 1, confirm: bool = False) -> dict:
    """Timeline-level operations. action: rename (name) | duplicate (name) | delete (confirm) | set_start_timecode
    (timecode) | set_mark_in_out (mark_in, mark_out relative frames) | clear_mark_in_out | detect_scene_cuts |
    create_subtitles_from_audio (options) | grab_still | grab_all_stills (still_source 1 first/2 middle) |
    create_compound_clip (items, options {"name","startTimecode"}) | create_fusion_clip (items) |
    link (items) | unlink (items) | delete_items (items, options {"ripple": bool, "include_linked": true}, confirm) |
    convert_to_stereo | output_blanking? (not in 21.0 API)."""
    tl = core.resolve_timeline(timeline)
    hs = [H(i) for i in items or []]
    simple = {"rename": ("SetName", [name]), "set_start_timecode": ("SetStartTimecode", [timecode]),
              "detect_scene_cuts": ("DetectSceneCuts", []), "grab_still": ("GrabStill", []),
              "grab_all_stills": ("GrabAllStills", [still_source]), "clear_mark_in_out": ("ClearMarkInOut", []),
              "convert_to_stereo": ("ConvertTimelineToStereo", []),
              "create_subtitles_from_audio": ("CreateSubtitlesFromAudio", [options] if options else []),
              "create_fusion_clip": ("CreateFusionClip", [hs]), "link": ("SetClipsLinked", [hs, True]),
              "unlink": ("SetClipsLinked", [hs, False]),
              "set_mark_in_out": ("SetMarkInOut", [mark_in, mark_out])}
    if action in simple:
        m, a = simple[action]
        return core.call(tl, m, a, tool="timeline_action", timeout=600)
    if action == "duplicate":
        cur = core.roots().get("timeline")
        res = core.call(tl, "DuplicateTimeline", [name] if name else [], tool="timeline_action")
        res["ok"] = bool(res.get("result")) and (not name or core.get(res["result"], "GetName") == name)
        if cur:  # DuplicateTimeline silently makes the duplicate current; put the user's timeline back
            core.get(core.roots()["project"], "SetCurrentTimeline", cur)
            res["current_timeline_restored"] = core.get(core.roots()["timeline"], "GetName")
        return res
    if action == "delete":
        return core.call(core.roots()["media_pool"], "DeleteTimelines", [[tl]], confirm=confirm, tool="timeline_action")
    if action == "create_compound_clip":
        return core.call(tl, "CreateCompoundClip", [hs] + ([options] if options else []), tool="timeline_action")
    if action == "delete_items":
        if (options or {}).get("include_linked", True):
            # the API deletes exactly what it is given - linked audio is NOT removed with its video
            extra = []
            for h0 in list(hs):
                for li in core.B.try_call(handle_id(h0), "GetLinkedItems") or []:
                    if handle_id(li) not in {handle_id(x) for x in hs + extra}:
                        extra.append(H(li))
            hs = hs + extra
        before = {handle_id(h) for h in hs}
        with core.on_page("edit"):  # DeleteClips returns False on other pages
            res = core.call(tl, "DeleteClips", [hs, bool((options or {}).get("ripple"))], confirm=confirm,
                            tool="timeline_action")
        if not res.get("dry_run") and res.get("result"):
            remaining = set()
            for tt in ("video", "audio", "subtitle"):
                for i in range(1, (core.get(tl, "GetTrackCount", tt) or 0) + 1):
                    remaining |= {handle_id(x) for x in core.get(tl, "GetItemListInTrack", tt, i) or []}
            res["still_present"] = sorted(before & remaining)
            res["ok"] = not res["still_present"]
        return res
    raise ValueError("unknown action %r" % action)


@server.tool(annotations=RW)
def markers(target: str, action: str = "list", frame: float | None = None, color: str = "Blue", name: str = "",
            note: str = "", duration: int = 1, custom_data: str | None = None, confirm: bool = False) -> dict:
    """Markers on a Timeline, TimelineItem or MediaPoolItem handle. action: list | add (frame, color, name,
    note, duration, custom_data) | delete_at (frame) | delete_color (color or 'All') | delete_custom
    (custom_data) | get_custom (custom_data) | update_custom (frame, custom_data). Frames are relative to
    the timeline/clip start. Adds are verified by GetMarkers. Deletes need confirm."""
    t = H(target)
    if action == "list":
        return {"markers": core.get(t, "GetMarkers")}
    if action == "add":
        args = [frame, color, name, note, duration] + ([custom_data] if custom_data else [])
        res = core.call(t, "AddMarker", args, tool="markers")
        ms = core.get(t, "GetMarkers") or {}
        key = str(int(frame)) if float(frame).is_integer() else str(frame)
        got = ms.get(key)
        res["actual"] = got
        res["verified"] = bool(got) and got.get("color") == color and got.get("name") == name
        res["ok"] = bool(res.get("result")) and res["verified"]
        return res
    if action == "get_custom":
        return {"marker": core.get(t, "GetMarkerByCustomData", custom_data)}
    method, args = {"delete_at": ("DeleteMarkerAtFrame", [frame]), "delete_color": ("DeleteMarkersByColor", [color]),
                    "delete_custom": ("DeleteMarkerByCustomData", [custom_data]),
                    "update_custom": ("UpdateMarkerCustomData", [frame, custom_data])}[action]
    res = core.call(t, method, args, confirm=confirm, tool="markers")
    if not res.get("dry_run"):
        res["markers_after"] = core.get(t, "GetMarkers")
    return res


# ======================================================================= timeline items

@server.tool(annotations=RO)
def item_inspect(item: str) -> dict:
    """Everything about a timeline item: position, source range, offsets, properties, markers, flags, color,
    enabled, linked items, fusion comps, takes, color versions/group, node graph, LUTs, cache modes."""
    it = H(item)
    i = handle_id(it)
    g = lambda m, *a: core.B.try_call(i, m, *a)
    graph = g("GetNodeGraph")
    nodes = []
    if graph:
        for n in range(1, (core.B.try_call(handle_id(graph), "GetNumNodes") or 0) + 1):
            gh = handle_id(graph)
            nodes.append({"index": n, "label": core.B.try_call(gh, "GetNodeLabel", n), "lut": core.B.try_call(gh, "GetLUT", n),
                          "tools": core.B.try_call(gh, "GetToolsInNode", n), "cache_mode": core.B.try_call(gh, "GetNodeCacheMode", n)})
    cg = g("GetColorGroup")
    return core.compact({
        "h": i, "name": g("GetName"), "track": g("GetTrackTypeAndIndex"), "start": g("GetStart"), "end": g("GetEnd"),
        "duration": g("GetDuration"), "source_start": g("GetSourceStartFrame"), "source_end": g("GetSourceEndFrame"),
        "left_offset": g("GetLeftOffset"), "right_offset": g("GetRightOffset"), "enabled": g("GetClipEnabled"),
        "clip_color": g("GetClipColor"), "flags": g("GetFlagList"), "markers": g("GetMarkers"), "properties": g("GetProperty"),
        "media_pool_item": g("GetMediaPoolItem"), "linked": g("GetLinkedItems"), "fusion_comps": g("GetFusionCompNameList"),
        "takes": {"count": g("GetTakesCount"), "selected": g("GetSelectedTakeIndex")},
        "color": {"current_version": g("GetCurrentVersion"), "local_versions": g("GetVersionNameList", 0),
                  "remote_versions": g("GetVersionNameList", 1), "group": core.B.try_call(handle_id(cg), "GetName") if cg else None,
                  "nodes": nodes, "color_output_cache": g("GetIsColorOutputCacheEnabled"),
                  "fusion_output_cache": g("GetIsFusionOutputCacheEnabled")},
        "voice_isolation": g("GetVoiceIsolationState"), "audio_mapping": g("GetSourceAudioChannelMapping")})


@server.tool(annotations=RW)
def item_set_properties(items: list, properties: dict) -> dict:
    """Set Inspector properties on one or more timeline items, e.g. {"ZoomX": 1.2, "Pan": 100, "Opacity": 50,
    "RotationAngle": 15, "CropLeft": 20, "CompositeMode": {"$const": "COMPOSITE_SCREEN"}, "FlipX": true}.
    Zoom 1.0 = 100%; Opacity 0-100. Each key is read back per item; out-of-range values get clipped by
    Resolve and show up as actual != requested."""
    out = []
    for it in items:
        for k, v in properties.items():
            r = core.call(H(it), "SetProperty", [k, v], tool="item_set_properties")
            out.append({"item": handle_id(it), "key": k, "requested": r.get("requested"), "actual": r.get("actual"),
                        "ok": r.get("ok"), "notes": r.get("notes")})
    return summarize(out)


@server.tool(annotations=RW)
def item_update(item: str, name: str | None = None, clip_color: str | None = None, enabled: bool | None = None,
                flags_add: list[str] | None = None, flags_clear: str | None = None,
                color_output_cache: bool | None = None, fusion_output_cache: str | None = None,
                voice_isolation: dict | None = None) -> dict:
    """Change a timeline item (each change read back): name, clip_color ('' clears), enabled, flags,
    render-cache toggles, voice isolation {"isEnabled", "amount"}."""
    it = H(item)
    out = []
    if name is not None:
        out.append(core.call(it, "SetName", [name], tool="item_update"))
    if clip_color is not None:
        out.append(core.call(it, "ClearClipColor" if clip_color == "" else "SetClipColor", [] if clip_color == "" else [clip_color], tool="item_update"))
    if enabled is not None:
        out.append(core.call(it, "SetClipEnabled", [enabled], tool="item_update"))
    if flags_clear:
        out.append(core.call(it, "ClearFlags", [flags_clear], tool="item_update"))
    for fl in flags_add or []:
        r = core.call(it, "AddFlag", [fl], tool="item_update")
        r["actual"] = core.get(it, "GetFlagList")
        r["ok"] = fl in (r["actual"] or [])
        out.append(r)
    if color_output_cache is not None:
        r = core.call(it, "SetColorOutputCache", [color_output_cache], tool="item_update")
        r["actual"] = core.B.try_call(handle_id(it), "GetIsColorOutputCacheEnabled")
        out.append(r)
    if fusion_output_cache is not None:
        r = core.call(it, "SetFusionOutputCache", [fusion_output_cache], tool="item_update")
        r["actual"] = core.B.try_call(handle_id(it), "GetIsFusionOutputCacheEnabled")
        out.append(r)
    if voice_isolation is not None:
        r = core.call(it, "SetVoiceIsolationState", [voice_isolation], tool="item_update")
        r["actual"] = core.B.try_call(handle_id(it), "GetVoiceIsolationState")
        r["ok"] = core.same(voice_isolation, r["actual"])
        out.append(r)
    return summarize(out)


@server.tool(annotations=RW)
def item_takes(item: str, action: str = "list", clip: str | None = None, index: int | None = None,
               start_frame: int | None = None, end_frame: int | None = None, confirm: bool = False) -> dict:
    """Take selector. action: list | add (clip, start_frame?, end_frame?) | select (index) | delete (index,
    confirm) | finalize (confirm)."""
    it = H(item)
    if action == "list":
        n = core.get(it, "GetTakesCount") or 0
        return {"count": n, "selected": core.get(it, "GetSelectedTakeIndex"),
                "takes": [core.get(it, "GetTakeByIndex", i) for i in range(1, n + 1)]}
    if action == "add":
        args = [H(clip)] + ([start_frame, end_frame] if start_frame is not None else [])
        before = core.get(it, "GetTakesCount") or 0
        res = core.call(it, "AddTake", args, tool="item_takes")
        res["actual"] = core.get(it, "GetTakesCount")
        res["ok"] = res["actual"] == max(before, 1) + 1 or res["actual"] == before + 1
        return res
    method, args = {"select": ("SelectTakeByIndex", [index]), "delete": ("DeleteTakeByIndex", [index]),
                    "finalize": ("FinalizeTake", [])}[action]
    return core.call(it, method, args, confirm=confirm, tool="item_takes")


# ======================================================================= color

def _graph(item=None, layer=None, color_group=None, which="clip"):
    if color_group:
        return core.get(H(color_group), "GetPreClipNodeGraph" if which == "pre" else "GetPostClipNodeGraph")
    if item:
        return core.get(H(item), "GetNodeGraph", *([layer] if layer else []))
    return core.get(core.resolve_timeline(), "GetNodeGraph")


@server.tool(annotations=RO)
def color_node_graph(item: str | None = None, layer: int | None = None, color_group: str | None = None,
                     which: str = "clip") -> dict:
    """Node graph of a timeline item (layer optional), of the timeline (no item), or of a color group
    (which: pre | post): node count, labels, LUTs, tools per node, cache modes."""
    g = _graph(item, layer, color_group, which)
    if not g:
        return {"error": "no node graph (item not graded on Color page yet, or audio item)"}
    gh = handle_id(g)
    n = core.get(g, "GetNumNodes") or 0
    return {"graph": gh, "num_nodes": n, "nodes": [
        {"index": i, "label": core.B.try_call(gh, "GetNodeLabel", i), "lut": core.B.try_call(gh, "GetLUT", i),
         "tools": core.B.try_call(gh, "GetToolsInNode", i), "cache_mode": core.B.try_call(gh, "GetNodeCacheMode", i)}
        for i in range(1, n + 1)]}


@server.tool(annotations=RW)
@page_guard("color")
def color_apply(items: list | None = None, lut: str | None = None, node: int = 1, cdl: dict | None = None,
                drx: str | None = None, grade_mode: int = 0, node_enabled: bool | None = None,
                cache_mode: int | None = None, reset_all: bool = False, layer: int | None = None,
                color_group: str | None = None, which: str = "clip", confirm: bool = False) -> dict:
    """Grade through the API paths that exist: LUT on a node (verified via GetLUT; call lut_refresh first for
    new files), CDL {"Slope": "r g b", "Offset": "r g b", "Power": "r g b", "Saturation": 1.0} on `node`,
    DRX still (grade_mode 0 no keyframes / 1 source TC / 2 start frames), node enable, node cache mode,
    reset_all (confirm). Applies to each item, or to a color group's pre/post graph.
    No API exists for color wheels/curves/qualifiers/windows - use DRX/LUT/CDL or ui_fallback_guide."""
    out = []
    targets = [None] if color_group else (items or [])
    for it in targets:
        g = _graph(it, layer, color_group, which)
        if not g:
            out.append({"item": it, "ok": False, "error": "no node graph"})
            continue
        if lut:
            out.append(dict(core.call(g, "SetLUT", [node, lut], tool="color_apply"), item=it))
        if cdl is not None:
            d = dict(cdl, NodeIndex=str(node))
            if not it:
                out.append({"ok": False, "error": "CDL needs a timeline item"})
            else:
                r = core.call(H(it), "SetCDL", [d], tool="color_apply")
                r["notes"].append("No CDL getter exists in the API - CDL cannot be read back; check visually or via "
                                  "timeline_export(format='edl', subtype='cdl').")
                out.append(dict(r, item=it))
        if drx:
            out.append(dict(core.call(g, "ApplyGradeFromDRX", [drx, grade_mode], tool="color_apply"), item=it))
        if node_enabled is not None:
            r = core.call(g, "SetNodeEnabled", [node, node_enabled], tool="color_apply")
            r["notes"].append("No node-enabled getter exists in the API - not verifiable.")
            out.append(dict(r, item=it))
        if cache_mode is not None:
            out.append(dict(core.call(g, "SetNodeCacheMode", [node, cache_mode], tool="color_apply"), item=it))
        if reset_all:
            out.append(dict(core.call(g, "ResetAllGrades", [], confirm=confirm, tool="color_apply"), item=it))
    return summarize(out)


@server.tool(annotations=RW)
def lut_refresh() -> dict:
    """Rescan LUT folders (needed before SetLUT can use a newly copied .cube)."""
    return core.call(core.roots()["project"], "RefreshLUTList", [], tool="lut_refresh")


@server.tool(annotations=RW)
@page_guard("color")
def color_versions(item: str, action: str = "list", name: str | None = None, new_name: str | None = None,
                   version_type: int = 0, confirm: bool = False) -> dict:
    """Color versions of an item. version_type 0 local, 1 remote. action: list | add | load | rename | delete
    (confirm). Verified via GetVersionNameList / GetCurrentVersion."""
    it = H(item)
    if action == "list":
        return {"current": core.get(it, "GetCurrentVersion"), "local": core.get(it, "GetVersionNameList", 0),
                "remote": core.get(it, "GetVersionNameList", 1)}
    method, args = {"add": ("AddVersion", [name, version_type]), "load": ("LoadVersionByName", [name, version_type]),
                    "rename": ("RenameVersionByName", [name, new_name, version_type]),
                    "delete": ("DeleteVersionByName", [name, version_type])}[action]
    res = core.call(it, method, args, confirm=confirm, tool="color_versions")
    if not res.get("dry_run"):
        names = core.get(it, "GetVersionNameList", version_type) or []
        res["versions_after"] = names
        if action == "add":
            res["ok"] = name in names
        elif action == "rename":
            res["ok"] = new_name in names and name not in names
        elif action == "delete":
            res["ok"] = name not in names
    return res


@server.tool(annotations=RW)
@page_guard("color")
def color_copy_grade(source: str, targets: list, backup_versions: bool = True) -> dict:
    """Copy the current node-stack-layer grade of `source` to target items (CopyGrades). Verified by
    comparing node counts and LUTs afterwards (the API exposes no full grade read-back)."""
    # CopyGrades REPLACES each target's grade and creates no version (recorded by samuelgursky api_truth), so by
    # default each target first gets a backup version holding its old grade, then goes back to its own version.
    backups = []
    if backup_versions:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for t in targets:
            cur = core.get(H(t), "GetCurrentVersion") or {}
            bname = "Claude backup %s" % stamp
            if core.get(H(t), "AddVersion", bname, 0):
                core.get(H(t), "LoadVersionByName", cur.get("versionName"), cur.get("versionType", 0))
                backups.append({"target": handle_id(t), "backup_version": bname, "graded_version": cur.get("versionName")})
            else:
                raise core.BridgeError("could not create a backup version on %s - aborting before CopyGrades" % handle_id(t))
    res = core.call(H(source), "CopyGrades", [[H(t) for t in targets]], tool="color_copy_grade")
    res["backups"] = backups

    def sig(i):
        g = core.B.try_call(handle_id(i), "GetNodeGraph")
        if not g:
            return None
        n = core.B.try_call(handle_id(g), "GetNumNodes") or 0
        return [n, [core.B.try_call(handle_id(g), "GetLUT", k) for k in range(1, n + 1)]]

    src = sig(H(source))
    res["comparison"] = [{"target": handle_id(t), "matches_source": sig(H(t)) == src} for t in targets]
    res["ok"] = bool(res.get("result")) and all(c["matches_source"] for c in res["comparison"])
    return res


@server.tool(annotations=RW)
def color_groups(action: str = "list", name: str | None = None, group: str | None = None, items: list | None = None,
                 new_name: str | None = None, confirm: bool = False) -> dict:
    """Color groups. action: list | add (name) | rename (group, new_name) | delete (group, confirm) |
    assign (group, items) | remove (items) | clips (group)."""
    p = core.roots()["project"]
    if action == "list":
        return {"groups": [{"h": handle_id(g), "name": core.get(g, "GetName")} for g in core.get(p, "GetColorGroupsList") or []]}
    if action == "add":
        res = core.call(p, "AddColorGroup", [name], tool="color_groups")
        res["ok"] = bool(res.get("result")) and core.get(res["result"], "GetName") == name
        return res
    if action == "rename":
        return core.call(H(group), "SetName", [new_name], tool="color_groups")
    if action == "delete":
        return core.call(p, "DeleteColorGroup", [H(group)], confirm=confirm, tool="color_groups")
    if action == "clips":
        return {"items": core.get(H(group), "GetClipsInTimeline")}
    out = []
    for it in items or []:
        if action == "assign":
            r = core.call(H(it), "AssignToColorGroup", [H(group)], tool="color_groups")
            g = core.B.try_call(handle_id(it), "GetColorGroup")
            r["actual"] = core.get(g, "GetName") if g else None
            r["ok"] = bool(g) and r["actual"] == core.get(H(group), "GetName")
        else:
            r = core.call(H(it), "RemoveFromColorGroup", [], tool="color_groups")
            r["ok"] = not core.B.try_call(handle_id(it), "GetColorGroup")
        out.append(r)
    return summarize(out)


@server.tool(annotations=RW)
def color_export_lut(item: str, path: str, size: str = "33") -> dict:
    """Export the item's grade as a LUT. size: 17 | 33 | 65 | vlt (Panasonic). Verifies the file exists.
    Switches to the Color page first: on 21.0.4 ExportLUT returns True but writes nothing on other pages."""
    const = {"17": "EXPORT_LUT_17PTCUBE", "33": "EXPORT_LUT_33PTCUBE", "65": "EXPORT_LUT_65PTCUBE",
             "vlt": "EXPORT_LUT_PANASONICVLUT"}[str(size)]
    page = core.get("resolve", "GetCurrentPage")
    if page != "color":
        core.get("resolve", "OpenPage", "color")
    res = core.call(H(item), "ExportLUT", [{"$const": const}, path], tool="color_export_lut")
    cands = [path] + [path + ext for ext in (".cube", ".vlt")]
    deadline = time.time() + 15  # the file can appear a little after the call returns
    res["file"] = None
    while res.get("result") and not res["file"] and time.time() < deadline:
        res["file"] = next((c for c in cands if os.path.isfile(c)), None)
        if not res["file"]:
            time.sleep(0.5)
    res["ok"] = bool(res.get("result")) and bool(res["file"])
    if res.get("result") and not res["file"]:
        res["notes"].append("ExportLUT returned True but no file was written (silent failure).")
    if page != "color":
        core.get("resolve", "OpenPage", page)
    return res


@server.tool(annotations=RW)
def color_ai_tools(item: str, action: str, mode: str = "F", force: bool = False) -> dict:
    """AI/analysis tools on an item: stabilize | magic_mask (mode F/B/BI) | regenerate_magic_mask |
    smart_reframe. Most are Studio-only: on Resolve Free they return False."""
    method, args = {"stabilize": ("Stabilize", []), "magic_mask": ("CreateMagicMask", [mode]),
                    "regenerate_magic_mask": ("RegenerateMagicMask", []), "smart_reframe": ("SmartReframe", [])}[action]
    return core.call(H(item), method, args, tool="color_ai_tools", timeout=600, force=force)


# ======================================================================= fusion

@server.tool(annotations=RW)
def fusion_comp(item: str, action: str = "list", name: str | None = None, new_name: str | None = None,
                path: str | None = None, index: int = 1, confirm: bool = False) -> dict:
    """Fusion compositions of a timeline item. action: list | add | import (path .comp) | export (path, index) |
    load (name) | rename (name, new_name) | delete (name, confirm) | get (name or index -> comp handle) |
    commit (name? - make script edits render; see fusion_tool). Verified via GetFusionCompNameList."""
    it = H(item)
    names = lambda: core.get(it, "GetFusionCompNameList") or []
    if action == "list":
        return {"count": core.get(it, "GetFusionCompCount"), "names": names()}
    if action == "commit":
        res = core.B.request("fusion_commit", target=handle_id(it), comp_name=name, timeout=120)
        core.oplog("fusion_comp", {"item": handle_id(it), "action": "commit"}, True, result=res)
        return dict(res, ok=True)
    if action == "get":
        comp = core.get(it, "GetFusionCompByName", name) if name else core.get(it, "GetFusionCompByIndex", index)
        return {"comp": comp}
    before = names()
    method, args = {"add": ("AddFusionComp", []), "import": ("ImportFusionComp", [path]),
                    "export": ("ExportFusionComp", [path, index]), "load": ("LoadFusionCompByName", [name]),
                    "rename": ("RenameFusionCompByName", [name, new_name]),
                    "delete": ("DeleteFusionCompByName", [name])}[action]
    res = core.call(it, method, args, confirm=confirm, tool="fusion_comp")
    if res.get("dry_run"):
        return res
    after = names()
    res["names_after"] = after
    if action in ("add", "import"):
        res["ok"] = len(after) == len(before) + 1
    elif action == "rename":
        res["ok"] = new_name in after and name not in after
    elif action == "delete":
        res["ok"] = name not in after
    elif action == "export":
        res["ok"] = bool(res.get("result")) and os.path.exists(path)
    return res


@server.tool(annotations=RO)
def fusion_comp_inspect(comp: str, inputs: bool = True) -> dict:
    """Tools inside a Fusion comp (handle from fusion_comp get): name, RegID, and input values/connections.
    Uses the Fusion object API (GetToolList/GetAttrs/GetInputList), which is outside the Resolve README."""
    return core.B.request("fusion_summary", target=handle_id(comp), inputs=inputs, timeout=120)


@server.tool(annotations=RW)
def fusion_tool(comp: str, action: str, tool: str | None = None, reg_id: str | None = None, input_id: str | None = None,
                value: Any = None, time: float | None = None, source_tool: str | None = None,
                x: int = -32768, y: int = -32768, item: str | None = None) -> dict:
    """Edit a Fusion comp through the Fusion object API (unvalidated by the Resolve README; results are
    read back). action: add (reg_id e.g. 'Blur', 'Transform', 'TextPlus', 'Background', 'Merge', x, y) |
    set_input (tool, input_id, value, time? for a keyframe) | get_input (tool, input_id, time?) |
    connect (tool, input_id, source_tool) | disconnect (tool, input_id) | delete (tool) |
    lock / unlock (comp-level undo grouping).
    IMPORTANT (measured on 21.0.4): edits made this way are readable but NOT rendered until the comp is
    committed. Pass `item` (the timeline item owning the comp) to commit automatically after the edit, or call
    fusion_comp(item, action="commit") after a batch of edits. A commit re-imports the comp, so tool/comp
    handles from before the commit are stale - re-fetch them with fusion_comp get / fusion_comp_inspect."""
    c = handle_id(comp)

    def finish(res):
        if item and res.get("ok", True) is not False:
            res["commit"] = core.B.request("fusion_commit", target=handle_id(item), timeout=120)
        elif not item:
            res.setdefault("notes", []).append("Not committed yet: call fusion_comp(item, action='commit') so Resolve "
                                               "renders this change.")
        return res

    if action == "add":
        res = core.call(c, "AddTool", [reg_id, x, y], verify=False, tool="fusion_tool")
        t = res.get("result")
        res["ok"] = bool(t)
        if t:
            res["tool_attrs"] = {k: v for k, v in (core.get(t, "GetAttrs") or {}).items() if k in ("TOOLS_Name", "TOOLS_RegID")}
        return finish(res)
    if action in ("lock", "unlock"):
        return core.call(c, "Lock" if action == "lock" else "Unlock", [], verify=False, tool="fusion_tool")
    t = handle_id(tool)
    if action == "get_input":
        return {"value": core.get(t, "GetInput", *([input_id] + ([time] if time is not None else [])))}
    if action == "set_input":
        args = [input_id, value] + ([time] if time is not None else [])
        res = core.call(t, "SetInput", args, verify=False, tool="fusion_tool")
        res["actual"] = core.B.try_call(t, "GetInput", *([input_id] + ([time] if time is not None else [])))
        res["requested"] = value
        res["ok"] = core.same(value, res["actual"])
        return finish(res)
    if action == "connect":
        res = core.call(t, "ConnectInput", [input_id, {"$h": handle_id(source_tool)}], verify=False, tool="fusion_tool")
        return finish(res)
    if action == "disconnect":
        return finish(core.call(t, "ConnectInput", [input_id, None], verify=False, tool="fusion_tool"))
    if action == "delete":
        return finish(core.call(t, "Delete", [], verify=False, tool="fusion_tool"))
    raise ValueError("unknown action %r" % action)


# ======================================================================= render

@server.tool(annotations=RO)
@page_guard(None)
def render_options(format: str | None = None, codec: str | None = None) -> dict:
    """Render formats (no args), codecs of a format, and resolutions of format+codec; also presets, current
    format/codec and render mode."""
    p = core.roots()["project"]
    out = {"current": core.get(p, "GetCurrentRenderFormatAndCodec"), "mode": core.get(p, "GetCurrentRenderMode"),
           "presets": core.get(p, "GetRenderPresetList")}
    if not format:
        out["formats"] = core.get(p, "GetRenderFormats")
    else:
        out["codecs"] = core.get(p, "GetRenderCodecs", format)
        if codec:
            out["resolutions"] = core.get(p, "GetRenderResolutions", format, codec)
    return out


JOB_FIELDS = {"TargetDir": "TargetDir", "FormatWidth": "FormatWidth", "FormatHeight": "FormatHeight",
              "FrameRate": "FrameRate", "ExportVideo": "IsExportVideo", "ExportAudio": "IsExportAudio",
              "MarkIn": "MarkIn", "MarkOut": "MarkOut", "AudioBitDepth": "AudioBitDepth",
              "AudioSampleRate": "AudioSampleRate", "ExportAlpha": "ExportAlpha"}


@server.tool(annotations=RW)
@page_guard(None)
def render_add_job(settings: dict | None = None, format: str | None = None, codec: str | None = None,
                   preset: str | None = None, mode: int | None = None, timeline: str | None = None) -> dict:
    """Configure and queue a render job (does NOT start it). Order: timeline -> preset -> format/codec ->
    mode (0 individual clips, 1 single clip) -> settings (TargetDir, CustomName, FormatWidth, FormatHeight,
    FrameRate, ExportVideo, ExportAudio, MarkIn, MarkOut, SelectAllFrames, VideoQuality, AudioCodec, ...).
    The queued job is read back from GetRenderJobList and compared with what was requested."""
    r = core.roots()
    p = r["project"]
    steps = []
    if timeline:
        steps.append(core.call(p, "SetCurrentTimeline", [core.resolve_timeline(timeline)], tool="render_add_job"))
    if preset:
        steps.append(core.call(p, "LoadRenderPreset", [preset], tool="render_add_job"))
    if format and codec:
        steps.append(core.call(p, "SetCurrentRenderFormatAndCodec", [format, codec], tool="render_add_job"))
    if mode is not None:
        steps.append(core.call(p, "SetCurrentRenderMode", [mode], tool="render_add_job"))
    if settings:
        if "CustomName" in settings and not str(settings["CustomName"] or "").strip():
            raise ValueError("An empty CustomName makes SetRenderSettings reject the WHOLE payload - omit the key.")
        steps.append(core.call(p, "SetRenderSettings", [settings], tool="render_add_job"))
    res = core.call(p, "AddRenderJob", [], tool="render_add_job")
    job_id = res.get("result")
    job = next((j for j in core.get(p, "GetRenderJobList") or [] if j.get("JobId") == job_id), None)
    checks = []
    for k, v in (settings or {}).items():
        if k in JOB_FIELDS and job is not None:
            actual = job.get(JOB_FIELDS[k])
            if k == "TargetDir":
                ok = os.path.normcase(os.path.normpath(str(actual or ""))) == os.path.normcase(os.path.normpath(str(v)))
            else:
                ok = core.same(v, actual)
            checks.append({"key": k, "requested": v, "actual": actual, "ok": ok})
        elif k == "CustomName" and job is not None:
            checks.append({"key": k, "requested": v, "actual": job.get("OutputFilename"),
                           "ok": str(job.get("OutputFilename", "")).startswith(str(v))})
    if format and job is not None:
        checks.append({"key": "format/codec", "requested": [format, codec],
                       "actual": [job.get("VideoFormat"), job.get("VideoCodec")], "ok": None})
    return {"ok": bool(job_id) and all(c["ok"] is not False for c in checks) and all(s.get("ok", True) is not False for s in steps),
            "job_id": job_id, "job": job, "checks": checks, "steps": steps}


@server.tool(annotations=RW)
@page_guard(None)
def render_jobs(action: str = "list", job_ids: list[str] | None = None, interactive: bool = False,
                confirm: bool = False) -> dict:
    """Render queue. action: list (with status) | status (job_ids) | delete (job_ids, confirm) | delete_all
    (confirm) | start (job_ids or all, confirm) | stop | in_progress."""
    p = core.roots()["project"]
    if action in ("list", "status"):
        jobs = core.get(p, "GetRenderJobList") or []
        if job_ids:
            jobs = [j for j in jobs if j.get("JobId") in job_ids]
        for j in jobs:
            j["status"] = core.B.try_call(handle_id(p), "GetRenderJobStatus", j.get("JobId"))
        return {"in_progress": core.get(p, "IsRenderingInProgress"), "jobs": jobs}
    if action == "in_progress":
        return {"in_progress": core.get(p, "IsRenderingInProgress")}
    if action == "stop":
        return core.call(p, "StopRendering", [], tool="render_jobs")
    if action == "delete":
        return summarize([core.call(p, "DeleteRenderJob", [j], confirm=confirm, tool="render_jobs") for j in job_ids or []])
    if action == "delete_all":
        return core.call(p, "DeleteAllRenderJobs", [], confirm=confirm, tool="render_jobs")
    if action == "start":
        args = [job_ids, interactive] if job_ids else [interactive]
        res = core.call(p, "StartRendering", args, confirm=confirm, backup=False, tool="render_jobs")
        if not res.get("dry_run"):
            res["in_progress"] = core.get(p, "IsRenderingInProgress")
        return res
    raise ValueError("unknown action %r" % action)


@server.tool(annotations=RW)
@page_guard(None)
def render_presets(action: str = "list", name: str | None = None, path: str | None = None, confirm: bool = False) -> dict:
    """Render presets. action: list | load (name) | save_new (name) | delete (name, confirm) | import (path) |
    export (name, path) | quick_export_list | burn_in_list | load_burn_in (name)."""
    p = core.roots()["project"]
    if action == "list":
        return {"presets": core.get(p, "GetRenderPresetList")}
    if action == "quick_export_list":
        return {"presets": core.get(p, "GetQuickExportRenderPresets")}
    if action == "burn_in_list":
        return {"presets": core.get("resolve", "GetBurnInPresetList")}
    target, method, args = {"load": (p, "LoadRenderPreset", [name]), "save_new": (p, "SaveAsNewRenderPreset", [name]),
                            "delete": (p, "DeleteRenderPreset", [name]),
                            "import": ("resolve", "ImportRenderPreset", [path]),
                            "export": ("resolve", "ExportRenderPreset", [name, path]),
                            "load_burn_in": (p, "LoadBurnInPreset", [name])}[action]
    res = core.call(H(target), method, args, confirm=confirm, tool="render_presets")
    if action in ("save_new", "delete", "import") and not res.get("dry_run"):
        lst = core.get(p, "GetRenderPresetList") or []
        res["presets_after"] = lst
        if action == "save_new":
            res["ok"] = name in lst
        elif action == "delete":
            res["ok"] = name not in lst
    if action == "export" and not res.get("dry_run"):
        res["ok"] = bool(res.get("result")) and os.path.exists(path)
    return res


@server.tool(annotations=RW)
@page_guard(None)
def render_quick_export(preset: str, target_dir: str | None = None, custom_name: str | None = None,
                        video_quality: int | None = None) -> dict:
    """Render the current timeline with a Quick Export preset (blocking; see render_presets quick_export_list)."""
    params = {k: v for k, v in (("TargetDir", target_dir), ("CustomName", custom_name), ("VideoQuality", video_quality)) if v is not None}
    return core.call(core.roots()["project"], "RenderWithQuickExport", [preset, params], tool="render_quick_export", timeout=3600)


# ======================================================================= gallery

@server.tool(annotations=RW)
def gallery(action: str = "albums", album: str | None = None, name: str | None = None, stills: list | None = None,
            label: str | None = None, folder: str | None = None, prefix: str = "still", fmt: str = "drx",
            paths: list[str] | None = None, confirm: bool = False) -> dict:
    """Gallery stills & PowerGrades. action: albums | stills (album) | create_album | create_powergrade_album |
    rename_album (album, name) | set_current (album) | label (album, stills[0], label) | export (album, stills,
    folder, prefix, fmt dpx/cin/tif/jpg/png/ppm/bmp/xpm/drx) | import (album, paths) | delete (album, stills,
    confirm). Grab stills with timeline_action grab_still."""
    g = core.roots()["gallery"]
    if action == "albums":
        out = []
        for kind, m in (("still", "GetGalleryStillAlbums"), ("powergrade", "GetGalleryPowerGradeAlbums")):
            for a in core.get(g, m) or []:
                out.append({"h": handle_id(a), "kind": kind, "name": core.get(g, "GetAlbumName", a),
                            "stills": len(core.get(a, "GetStills") or [])})
        return {"current": core.get(g, "GetCurrentStillAlbum"), "albums": out}
    if action == "stills":
        st = core.get(H(album), "GetStills") or []
        return {"stills": [{"h": handle_id(s), "label": core.get(H(album), "GetLabel", s)} for s in st]}
    if action in ("create_album", "create_powergrade_album"):
        res = core.call(g, "CreateGalleryStillAlbum" if action == "create_album" else "CreateGalleryPowerGradeAlbum", [], tool="gallery")
        res["ok"] = bool(res.get("result"))
        return res
    if action == "rename_album":
        return core.call(g, "SetAlbumName", [H(album), name], tool="gallery")
    if action == "set_current":
        return core.call(g, "SetCurrentStillAlbum", [H(album)], tool="gallery")
    if action == "label":
        return core.call(H(album), "SetLabel", [H(stills[0]), label], tool="gallery")
    if action == "export":
        os.makedirs(folder, exist_ok=True)
        before = set(os.listdir(folder))
        with core.on_page("color"):
            res = core.call(H(album), "ExportStills", [[H(s) for s in stills], folder, prefix, fmt], tool="gallery")
        if res.get("result") is False:
            res["notes"].append("ExportStills returns False when the Gallery panel is not open on the Color page - "
                                "ask the user to open it (Workspace > Gallery).")
        res["files_written"] = sorted(set(os.listdir(folder)) - before)
        res["ok"] = bool(res.get("result")) and bool(res["files_written"])
        return res
    if action == "import":
        before = len(core.get(H(album), "GetStills") or [])
        res = core.call(H(album), "ImportStills", [paths], tool="gallery")
        res["stills_after"] = len(core.get(H(album), "GetStills") or [])
        res["ok"] = res["stills_after"] > before
        return res
    if action == "delete":
        return core.call(H(album), "DeleteStills", [[H(s) for s in stills]], confirm=confirm, tool="gallery")
    raise ValueError("unknown action %r" % action)


# ======================================================================= workarounds

@server.tool(annotations=RO)
def edit_list_get(timeline: str | None = None) -> dict:
    """WORKAROUND base: read a timeline into editable pieces {track_type, track, record_start (frames from
    timeline start), duration, source_start, media_pool_item, source_item, file_path}. Edit the list (razor,
    trim, slip, slide, ripple, reorder, move between tracks) and pass it to edit_list_build or
    timeline_build_otio. Items the API cannot re-create (titles, generators, compound clips) are listed
    under `unsupported`."""
    return W.extract_edit_list(timeline)


@server.tool(annotations=RW)
def edit_list_build(pieces: list, name: str, copy_attributes: bool = True, copy_grades: bool = True,
                    dry_run: bool = False) -> dict:
    """WORKAROUND: build a NEW timeline from edited pieces using AppendToTimeline (original untouched).
    Copies transform properties, clip colour, enabled state, item markers and grades from each piece's
    source_item. Every placed item is read back; mismatches are reported. Pieces may not overlap per track."""
    return W.build_from_edit_list(pieces, name, copy_attributes=copy_attributes, copy_grades=copy_grades, dry_run=dry_run)


@server.tool(annotations=RW)
def timeline_razor(at: int | str, timeline: str | None = None, track_types: list[str] | None = None,
                   tracks: list[int] | None = None, name: str | None = None, dry_run: bool = False) -> dict:
    """WORKAROUND for 'razor/split at playhead' (no API exists): split every clip crossing `at` (frame relative
    to timeline start, or 'HH:MM:SS:FF' / 'playhead') and rebuild into a NEW timeline (default
    '<name> (cut)'), keeping properties, colours, markers and grades. Titles/generators/transitions/effects
    that the API cannot re-create are reported, not silently dropped."""
    edit = W.extract_edit_list(timeline)
    tl = core.as_handle(edit["timeline_handle"])
    if at == "playhead":
        at = core.get(tl, "GetCurrentTimecode")
    if isinstance(at, str):
        at = frames_from_tc(at, project_fps()) - edit["start_frame"]
    cut = W.razor(edit, int(at), track_types=track_types, tracks=tracks)
    new_name = name or "%s (cut)" % edit["timeline"]
    rep = W.build_from_edit_list(cut["pieces"], new_name, dry_run=dry_run)
    rep["cut_at_relative_frame"] = int(at)
    rep["split_count"] = len(cut["pieces"]) - len(edit["pieces"])
    rep["not_rebuilt"] = edit["unsupported"]
    return rep


@server.tool(annotations=RW)
def timeline_build_otio(pieces: list, name: str, transitions: list | None = None, markers: list | None = None,
                        fps: float | None = None, import_source_clips: bool = False, dry_run: bool = False) -> dict:
    """WORKAROUND for transitions, gaps, speed changes and timeline markers: write pieces (as from
    edit_list_get; needs file_path; optional "speed" percent, e.g. 200 or 50) to an OTIO file in Resolve's own
    dialect and import it as a new timeline. transitions: [{"track_type": "video", "track": 1, "at": cut frame
    relative to timeline start, "duration": 12, "name": "Cross Dissolve"}] - clips need enough handles.
    markers: [{"frame", "name", "color", "duration", "note"}] (notes are not imported by Resolve 21.0).
    The created timeline is read back and compared with the request."""
    fps = fps or project_fps()
    otio = W.build_otio(pieces, fps, name=name, transitions=transitions or [], markers=markers or [])
    if dry_run:
        return {"dry_run": True, "otio": otio}
    path, tl, name = W.import_otio(otio, name, import_source_clips=import_source_clips)
    if not tl:
        return {"ok": False, "otio_file": path, "error": "ImportTimelineFromFile returned None"}
    start = core.get(tl, "GetStartFrame")
    got = []
    for tt in ("video", "audio"):
        for i in range(1, (core.get(tl, "GetTrackCount", tt) or 0) + 1):
            for it in core.get(tl, "GetItemListInTrack", tt, i) or []:
                mpi = core.B.try_call(handle_id(it), "GetMediaPoolItem")
                got.append({"track": [tt, i], "name": core.get(it, "GetName"), "record_start": core.get(it, "GetStart") - start,
                            "duration": core.get(it, "GetDuration"),
                            "online": (core.get(mpi, "GetClipProperty", "Online Status") == "Online") if mpi else None})
    clips_got = [g for g in got if not any(t.get("name", "Cross Dissolve") == g["name"] for t in transitions or [])]
    mism = []
    for p in pieces:
        m = next((g for g in clips_got if g["track"] == [p["track_type"], p["track"]] and g["record_start"] == p["record_start"]), None)
        if not m or m["duration"] != p["duration"]:
            if not transitions:  # transitions legitimately shift neighbouring edit points
                mism.append({"piece": p.get("name"), "record_start": p["record_start"], "found": m})
    offline = [g for g in got if g.get("online") is False]
    if offline:
        mism.append({"offline_items": [g["name"] for g in offline],
                     "hint": "media did not relink - check file paths / media timecode origin"})
    return {"ok": not mism, "otio_file": path, "timeline": tl, "name": name, "items": got, "mismatches": mism,
            "markers": core.get(tl, "GetMarkers")}


# ======================================================================= beyond the API: grading LUTs,
# keyframes through Fusion, offline audio, frame previews

# ===================================================================== DRX grades (advanced codec)

import subprocess  # noqa: E402
import tempfile  # noqa: E402
import drx_r21  # noqa: E402

ADV_REPO = os.environ.get("RESOLVE_MCP_ADVANCED") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "resolve-mcp-advanced")
DRX_HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drx_gen.mjs")
DRX_DIR = os.path.join(core.STATE_DIR, "drx")
PRIMARY_KEYS = ("lift", "gamma", "gain", "offset", "saturation", "contrast", "pivot")


def _drx_codec(action, path, params=None):
    if not os.path.isfile(os.path.join(ADV_REPO, "resolve-advanced", "server", "tools", "drx.mjs")):
        raise core.BridgeError("advanced DRX codec not found at %s (clone samuelgursky/davinci-resolve-mcp there and "
                               "run npm install in resolve-advanced, or set RESOLVE_MCP_ADVANCED)" % ADV_REPO)
    cmd = ["node", DRX_HELPER, ADV_REPO, action, path] + ([json.dumps(params)] if params is not None else [])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8")
    if r.returncode:
        raise core.BridgeError("DRX codec failed: %s" % (r.stderr.strip().splitlines() or ["?"])[-1])
    return json.loads(r.stdout)


def _primaries(parsed):
    n = (parsed.get("nodes") or [{}])[0].get("params") or {}
    return {k: n.get(k) for k in PRIMARY_KEYS if k in n}


def _close(a, b, tol=2e-3):
    if isinstance(a, dict) and isinstance(b, dict):
        return all(_close(a.get(k), b.get(k), tol) for k in set(a) | set(b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= tol * max(1.0, abs(a), abs(b))
    return a == b


def _grade_readback(item):
    """Grab a still of `item` (playhead moved to its first frame), export it as .drx, delete that temporary still.
    Returns the exported path. This is Resolve's own serialization of the grade it is actually using."""
    tl = core.roots()["timeline"]
    fps = core.get(tl, "GetSetting", "timelineFrameRate")
    core.get(tl, "SetCurrentTimecode", tc_from_frames(core.get(H(item), "GetStart"), fps))
    still = core.get(tl, "GrabStill")
    if not still:
        raise core.BridgeError("GrabStill failed - is the item on the current timeline?")
    album = core.get(core.roots()["gallery"], "GetCurrentStillAlbum")
    folder = tempfile.mkdtemp(prefix="drx_readback_", dir=DRX_DIR)
    try:
        ok = core.get(album, "ExportStills", [still], folder, "readback", "drx")
        files = [f for f in os.listdir(folder) if f.endswith(".drx")]
        if not ok or not files:
            raise core.BridgeError("ExportStills wrote no .drx (open the Gallery panel on the Color page)")
        return os.path.join(folder, files[0])
    finally:
        core.get(album, "DeleteStills", [still])  # the temporary still this function created


@server.tool(annotations=RW)
@page_guard("color")
def color_grade_drx(items: list, grade: dict, verify: bool = True, backup_versions: bool = True,
                    dry_run: bool = False) -> dict:
    """Real Color-page grade (single serial node, replaces the item's node graph) built offline as a .drx with
    the samuelgursky advanced codec, made Resolve-21 compatible, applied with ApplyGradeFromDRX and read back
    from Resolve's own export of the result.
    grade (panel/UI numbers): lift/gamma/offset/gain {r,g,b,master}; NOTE gain r/g/b are colour BALANCE only -
    brightness rides on gain.master; saturation 0-100 (50 neutral); contrast, pivot;
    window {type 1 circle | 2 linear, size, aspect, pan, tilt, rotate, soft1, opacity} (panel numbers; pan/tilt
    panel units are ~82 timeline px each - prefer center_x/center_y = frame fractions 0..1, y down; a size-50
    circle has a radius of ~288 px);
    qualifier {hueCenter, hueWidth, hueSoft, satLow, satHigh, lumLow, lumHigh}; rgbQualifier; lumaQualifier;
    hslCurves; customCurves; hdr zones (see the codec docs). Gradient/polygon windows: written as-is, render
    not verified on 21.0.4.
    backup_versions keeps each item's previous grade as a 'Claude backup <time>' version first."""
    os.makedirs(DRX_DIR, exist_ok=True)
    g = json.loads(json.dumps(grade))
    g.setdefault("space", "ui")
    win = g.get("window")
    size_ui = None
    if isinstance(win, dict) and ("center_x" in win or "center_y" in win):
        # Measured on 21.0.4 (1920x1080 timeline): pan/tilt raw = timeline pixels from frame centre, tilt + = up;
        # panel number = 50 + raw / 4096 * 50, so one panel unit is ~82 px and pan 65 is already off-frame.
        tl = core.roots()["timeline"]
        tw = float(core.get(tl, "GetSetting", "timelineResolutionWidth") or 1920)
        th = float(core.get(tl, "GetSetting", "timelineResolutionHeight") or 1080)
        win["pan"] = 50 + (float(win.pop("center_x", 0.5)) - 0.5) * tw / 4096 * 50
        win["tilt"] = 50 + (0.5 - float(win.pop("center_y", 0.5))) * th / 4096 * 50
    if isinstance(win, dict) and win.get("type") in (1, 2) and "size" in win:
        size_ui = float(win["size"])
        win["size"] = 50  # the codec's size scale is wrong below 50 on 21.0.4 - written exactly below
    stamp = time.strftime("%Y%m%d-%H%M%S")
    src = os.path.join(DRX_DIR, "gen_%s.drx" % stamp)
    _drx_codec("generate", src, g)
    path = os.path.join(DRX_DIR, "claude_grade_%s.drx" % stamp)
    overrides = {drx_r21.SIZE: drx_r21.size_to_raw(size_ui)} if size_ui is not None else None
    changed = drx_r21.to_r21(src, path, overrides)
    requested = {"primaries": _primaries(_drx_codec("parse", path)), "windows": drx_r21.read_windows(path)}
    res = {"drx": path, "window_blocks_converted": changed, "requested": requested, "notes": []}
    if dry_run:
        res.update(ok=True, dry_run=True)
        return res
    backups, results = [], []
    for it in items:
        if backup_versions:
            cur = core.get(H(it), "GetCurrentVersion") or {}
            bname = "Claude backup %s" % stamp
            if not core.get(H(it), "AddVersion", bname, 0):
                raise core.BridgeError("could not create a backup version on %s - nothing applied" % it)
            core.get(H(it), "LoadVersionByName", cur.get("versionName"), cur.get("versionType", 0))
            backups.append({"item": it, "backup_version": bname})
        graph = _graph(it)
        r = core.call(graph, "ApplyGradeFromDRX", [path, 0], tool="color_grade_drx")
        entry = {"item": it, "applied": bool(r.get("result"))}
        if verify and entry["applied"]:
            back = _grade_readback(it)
            actual = {"primaries": _primaries(_drx_codec("parse", back)), "windows": drx_r21.read_windows(back)}
            wins_ok = len(actual["windows"]) == len(requested["windows"]) and all(
                _close({k: v for k, v in a.items() if k != "corrector_type"},
                       {k: v for k, v in b.items() if k != "corrector_type"}, 0.02)
                for a, b in zip(actual["windows"], requested["windows"]))
            entry.update(actual=actual, primaries_match=_close(actual["primaries"], requested["primaries"]),
                         windows_match=wins_ok)
            entry["ok"] = entry["primaries_match"] and wins_ok
        else:
            entry["ok"] = entry["applied"]
        results.append(entry)
    res.update(results=results, backups=backups, ok=all(r["ok"] for r in results))
    res["notes"].append("Readback = Resolve's own .drx export of the applied grade. Pixels are not checked here - "
                        "use frame_preview to look at the result.")
    return res


LUT_DIR = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                       r"Blackmagic Design\DaVinci Resolve\Support\LUT\ClaudeMCP")


def tc_from_frames(frames, fps):
    f = int(round(float(fps)))
    frames = int(frames)
    return "%02d:%02d:%02d:%02d" % (frames // (3600 * f), frames // (60 * f) % 60, frames // f % 60, frames % f)


@server.tool(annotations=RW)
def frame_preview(path: str, timecode: str | None = None, item: str | None = None, page: str = "color") -> dict:
    """Export the current (or given) frame as an image so Claude can LOOK at the result (open the file with an
    image viewer / Read tool). item: put the playhead in the middle of that timeline item. path must end in
    .png/.jpg/.tif/.dpx. Switches to `page` (default color, where grades show) and restores the old page."""
    old = core.get("resolve", "GetCurrentPage")
    if old != page:
        core.get("resolve", "OpenPage", page)
    tl = core.resolve_timeline()
    if item:
        mid = core.get(H(item), "GetStart") + core.get(H(item), "GetDuration") // 2
        timecode = tc_from_frames(mid, project_fps())
    if timecode:
        core.get(tl, "SetCurrentTimecode", timecode)
    if os.path.exists(path):
        os.remove(path)
    res = core.call(core.roots()["project"], "ExportCurrentFrameAsStill", [path], tool="frame_preview")
    deadline = time.time() + 15
    while res.get("result") and not os.path.isfile(path) and time.time() < deadline:
        time.sleep(0.3)
    res["file"] = path if os.path.isfile(path) else None
    res["ok"] = bool(res.get("result")) and bool(res["file"])
    res["timecode"] = core.get(tl, "GetCurrentTimecode")
    if old != page:
        core.get("resolve", "OpenPage", old)
    return res


@server.tool(annotations=RW)
@page_guard("color")
def color_grade_lut(items: list, grade: dict, node: int = 1, name: str | None = None, size: int = 33,
                    layer: int | None = None, preview_path: str | None = None) -> dict:
    """WORKAROUND for colour wheels / curves / qualifiers (no API): compile a numeric grade into a 3D LUT and put it
    on `node` of each item (read back with GetLUT). Values are display-referred 0..1. grade keys (all optional):
      offset, lift, gamma, gain: number (master) or [r, g, b] or {"master", "r", "g", "b"} - Resolve-style primaries
        (lift 0 / gamma 1 / gain 1 = neutral; e.g. gain [1.05, 1, 0.95] warms highlights)
      contrast (1 = neutral), pivot (0.435), temperature / tint (-1..1, simple warm/cool and green/magenta),
      curves: {"master"|"r"|"g"|"b": [[x, y], ...]} monotone cubic through points (0..1),
      hue_vs_hue: [[hue_deg, shift_deg], ...], hue_vs_sat: [[hue_deg, multiplier], ...] (periodic),
      lum_vs_sat: [[luma, multiplier], ...],
      qualifiers: [{"hue": deg, "hue_width": deg, "hue_soft": deg, "sat_range": [lo, hi], "lum_range": [lo, hi],
                    "soft": 0.1, "adjust": {"saturation", "hue_shift", "gain", "lift"}}]  (soft HSL key, e.g. skin),
      saturation (1 = neutral).
    The node's existing LUT is replaced (the API cannot create nodes - use a node you reserve for this).
    Not possible as a LUT: power windows / tracking / anything spatial or keyframed (use Fusion masks).
    Returns sample colours in->out, and optionally a rendered preview image (preview_path .png)."""
    import grading
    f = grading.build_transform(grade)
    name = re.sub(r"[^\w.-]+", "_", name or "claude_grade_%s" % time.strftime("%Y%m%d_%H%M%S"))
    try:
        path = grading.write_cube(os.path.join(LUT_DIR, name + ".cube"), f, size, title=name)
    except OSError:
        path = grading.write_cube(os.path.join(core.STATE_DIR, "luts", name + ".cube"), f, size, title=name)
    core.get(core.roots()["project"], "RefreshLUTList")
    results = []
    for it in items:
        g = core.get(H(it), "GetNodeGraph", *([layer] if layer else []))
        if not g:
            results.append({"item": it, "ok": False, "error": "no node graph (audio item?)"})
            continue
        n = core.get(g, "GetNumNodes") or 0
        if node > n:
            results.append({"item": it, "ok": False,
                            "error": "node %d does not exist (clip has %d nodes; the API cannot add nodes)" % (node, n)})
            continue
        r = core.call(g, "SetLUT", [node, path], tool="color_grade_lut")
        results.append({"item": handle_id(it), "ok": r.get("ok"), "requested": r.get("requested"),
                        "actual": r.get("actual"), "notes": r.get("notes")})
    out = {"ok": bool(results) and all(r.get("ok") for r in results), "lut_file": path, "results": results,
           "samples": grading.sample_report(f), "grade": grade}
    if preview_path and items:
        out["preview"] = frame_preview(preview_path, item=items[0])
    return out


@server.tool(annotations=RW)
def keyframe_transform(item: str, keys: list, comp_name: str | None = None, preview_dir: str | None = None) -> dict:
    """WORKAROUND for Inspector keyframes (no API): animate zoom / position / rotation / anchor / opacity of a
    timeline item through its Fusion comp. keys: [{"frame": clip-relative frame (0 = first frame of the clip),
    "zoom": 1.0 = 100%, "pan": px right, "tilt": px up, "rotation": deg, "anchor_x": px, "anchor_y": px,
    "opacity": 0-100}] - only the given properties are keyframed; Fusion interpolates smoothly between keys.
    Idempotent: re-running updates the same ClaudeTransform / ClaudeOpacity tools; existing Fusion tools in the
    comp are kept (the chain is inserted before MediaOut1). Values are read back at every key and midpoint.
    preview_dir: also export the frame at each key as PNG so the motion can be checked visually."""
    res = core.B.request("fusion_keyframes", target=handle_id(item), keys=keys, comp_name=comp_name, timeout=180)
    checks = []
    for k in keys:
        got = res["readback"].get(str(int(k["frame"])), {})
        for prop in ("zoom", "pan", "tilt", "rotation", "anchor_x", "anchor_y", "opacity"):
            if prop in k:
                checks.append({"frame": k["frame"], "property": prop, "requested": k[prop], "actual": got.get(prop),
                               "ok": core.same(float(k[prop]), got.get(prop), tol=1e-2)})
    res["checks"] = checks
    res["ok"] = bool(checks) and all(c["ok"] for c in checks)
    core.oplog("keyframe_transform", {"item": handle_id(item), "keys": keys}, res["ok"], result={"checks": checks})
    if preview_dir:
        os.makedirs(preview_dir, exist_ok=True)
        start = core.get(H(item), "GetStart")
        fps = project_fps()
        res["previews"] = []
        for k in keys:
            p = os.path.join(preview_dir, "key_%04d.png" % int(k["frame"]))
            pr = frame_preview(p, timecode=tc_from_frames(start + int(k["frame"]), fps), page="edit")
            res["previews"].append(pr.get("file"))
    return res


@server.tool(annotations=RW)
def audio_process(source: str, operations: dict, action: str = "new_file", out_path: str | None = None,
                  confirm: bool = False) -> dict:
    """WORKAROUND for Fairlight mixing/automation (no API): process a clip's WAV offline and bring it back.
    source: MediaPoolItem / TimelineItem handle (its media file is used) or a .wav path.
    operations: {"trim": [start_s, end_s], "gain_db": -3, "envelope": [[t_sec, gain_db], ...] (volume automation,
    linear in dB), "fade_in": sec, "fade_out": sec, "fade_shape": "equal_power"|"linear"|"log",
    "normalize": {"mode": "peak"|"rms", "target_dbfs": -1}, "limit_dbfs": -0.1}.
    action: new_file (just write <name>_claudeN.wav) | import (also import it into the media pool) |
    replace (swap the clip's media to the new file with ReplaceClip - needs confirm, backup first).
    The original file is never modified. Levels are measured back (peak/RMS, gain at each envelope point).
    Only PCM WAV (16/24/32-bit int) is supported; for other media render/export the audio to WAV first."""
    import audio
    mpi = None
    path = source
    if not (isinstance(source, str) and source.lower().endswith(".wav") and os.path.exists(source)):
        h = H(source)
        if core.B.request("type", target=handle_id(h))["type"] == "TimelineItem":
            h = core.get(h, "GetMediaPoolItem")
        mpi = h
        path = core.get(mpi, "GetClipProperty", "File Path")
    if not path or not path.lower().endswith(".wav"):
        raise ValueError("source media %r is not a WAV file - only PCM WAV can be processed offline" % path)
    rep = audio.process(path, out_path, **operations)
    rep["action"] = action
    if action == "import":
        imp = media_import([rep["output"]])
        rep["imported"] = imp
        rep["ok"] = imp["ok"]
    elif action == "replace":
        if mpi is None:
            raise ValueError("replace needs a media pool / timeline item as source")
        r = core.call(mpi, "ReplaceClip", [rep["output"]], confirm=confirm, tool="audio_process")
        rep["replace"] = r
        if not r.get("dry_run"):
            now = core.get(mpi, "GetClipProperty", "File Path")
            rep["clip_file_after"] = now
            rep["ok"] = os.path.normcase(now or "") == os.path.normcase(rep["output"])
    else:
        rep["ok"] = os.path.isfile(rep["output"])
    if "normalize_check" in rep:
        rep["ok"] = rep["ok"] and rep["normalize_check"]["ok"]
    core.oplog("audio_process", {"source": str(source), "operations": operations, "action": action}, rep["ok"],
               result={k: rep[k] for k in ("output", "before", "after")})
    return rep


UI_FALLBACK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "UI_FALLBACK.md")


@server.tool(annotations=RO)
def ui_fallback_guide(topic: str | None = None) -> dict:
    """LAST RESORT for things no API can reach (color wheels/curves, OpenFX parameters, Fairlight mixing &
    automation, keyframing the Inspector, trims in the UI...): returns the computer-use guide with Resolve
    keyboard shortcuts. Slow and less reliable than the API - use only when the coverage says 'not possible'."""
    with open(UI_FALLBACK, encoding="utf-8") as f:
        text = f.read()
    if topic:
        parts = [s for s in text.split("\n## ") if topic.lower() in s.lower()]
        return {"topic": topic, "sections": ["## " + s for s in parts] or ["(no section matched - full guide follows)", text]}
    return {"guide": text}


if __name__ == "__main__":
    server.run("stdio")
