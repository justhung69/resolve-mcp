"""End-to-end test of every tool group against the REAL running Resolve, through the MCP layer.

Creates a throwaway project "ClaudeMCP_Test_<timestamp>" (left in place for inspection), generates
its own media (PNG sequences + WAV, no ffmpeg), and never starts a render.

    .venv\\Scripts\\python.exe tests\\run_tests.py
"""
import asyncio
import json
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import make_media  # noqa: E402
import pngstat  # noqa: E402
import mcp_server  # noqa: E402

RESULTS = []
CTX = {}
TS = time.strftime("%Y%m%d_%H%M%S")
WORK = os.path.join(os.environ["LOCALAPPDATA"], "ResolveMCP", "test_run_" + TS)
os.makedirs(WORK, exist_ok=True)


class ToolFailed(Exception):
    pass


def t(_tool, **args):
    """Call a tool through the MCP server exactly like a client would."""
    try:
        r = asyncio.run(mcp_server.server.call_tool(_tool, args))
    except Exception as e:  # the SDK raises for exceptions inside tools; a real client receives them as is_error
        cause = e.__cause__ or e
        raise ToolFailed("%s -> %s: %s" % (_tool, type(cause).__name__, cause))
    text = "".join(getattr(c, "text", "") for c in r.content)
    if r.is_error:
        raise ToolFailed("%s -> %s" % (_tool, text[:600]))
    try:
        return json.loads(text)
    except ValueError:
        return text


def check(group, name, fn):
    t0 = time.time()
    try:
        ok, detail = fn()
        status = "PASS" if ok else "FAIL"
    except ToolFailed as e:
        status, detail = "FAIL", str(e)
    except Exception as e:
        status, detail = "FAIL", "%s: %s\n%s" % (type(e).__name__, e, traceback.format_exc(limit=3))
    RESULTS.append({"group": group, "test": name, "status": status, "detail": str(detail)[:400],
                    "s": round(time.time() - t0, 2)})
    print("%-5s %-14s %-48s %s" % (status, group, name, str(detail)[:110].replace("\n", " ")))
    return status == "PASS"


def h(x):
    return x["$h"] if isinstance(x, dict) else x


# ----------------------------------------------------------------------------- tests

def main():
    media = make_media.make_all(os.path.join(WORK, "media"))
    lut_dir = r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\LUT\ClaudeMCP"
    try:
        os.makedirs(lut_dir, exist_ok=True)
        lut_path = make_media.lut_cube(os.path.join(lut_dir, "claude_warm.cube"))
    except OSError:
        lut_path = make_media.lut_cube(os.path.join(WORK, "claude_warm.cube"))
    CTX["lut"] = lut_path

    check("status", "bridge running", lambda: (lambda s: (s.get("version", "").startswith("21"), s.get("version")))(t("resolve_status")))
    check("meta", "catalog parsed from installed README", lambda: (lambda c: (sum(c["classes"].values()) > 300, c["classes"]))(t("api_catalog")))
    check("meta", "catalog search", lambda: (lambda c: (len(c["matches"]) > 0, len(c["matches"])))(t("api_catalog", search="marker")))

    # --- project
    pname = "ClaudeMCP_Test_" + TS
    check("project", "create throwaway project", lambda: (lambda r: (r["ok"], r))(t("project_create", name=pname)))
    check("project", "list projects", lambda: (lambda r: (pname in r["projects"], len(r["projects"])))(t("project_list")))
    check("project", "settings get", lambda: (lambda r: ("timelineFrameRate" in r, r.get("timelineFrameRate")))(t("project_settings_get")))
    check("project", "settings set (frame rate 24, before timelines)",
          lambda: (lambda r: (r["ok"], r["results"][0].get("actual")))(t("project_settings_set", settings={"timelineFrameRate": "24"})))
    check("project", "open page media (verified)", lambda: (lambda r: (r["ok"], r.get("actual")))(t("open_page", page="media")))

    # --- media
    def imp():
        r = t("media_import", paths=[{"FilePath": media["seq_a"], "StartIndex": 0, "EndIndex": 47},
                                     {"FilePath": media["seq_b"], "StartIndex": 0, "EndIndex": 47}, media["wav"]])
        CTX["clipA"], CTX["clipB"], CTX["wav"] = [c["h"] for c in r["clips"]]
        return r["ok"] and r["imported"] == 3, [c["name"] for c in r["clips"]]
    check("media", "import 2 image sequences + wav", imp)

    def bin_():
        r = t("media_folder", action="create", name="Audio Bin")
        CTX["bin"] = h(r["result"])
        r2 = t("media_folder", action="move_clips", clips=[CTX["wav"]], folder=CTX["bin"])
        tree = t("media_pool_list")
        sub = next(s for s in tree["subfolders"] if s["name"] == "Audio Bin")
        return r["ok"] and r2["ok"] is not False and len(sub.get("clips", [])) == 1, "moved wav into Audio Bin"
    check("media", "create bin + move clip (verified in tree)", bin_)

    check("media", "clip update: color/metadata/flag/mark in-out", lambda: (lambda r: (r["ok"], [(x.get("method"), x.get("actual")) for x in r["results"]]))(
        t("clip_update", clip=CTX["clipA"], clip_color="Orange", metadata={"Comments": "from Claude"},
          flags_add=["Green"], mark_in=5, mark_out=30)))
    check("media", "clip marker add (verified)", lambda: (lambda r: (r["ok"], r.get("actual")))(
        t("markers", target=CTX["clipA"], action="add", frame=3, color="Red", name="clip mk", note="n")))
    check("media", "clip inspect", lambda: (lambda r: (r["clip_color"] == "Orange" and "3" in r["markers"], r["properties"].get("Frames")))(
        t("clip_inspect", clip=CTX["clipA"])))
    check("media", "clear mark in/out", lambda: (lambda r: (r["result"] is True, r))(
        t("api_call", target=CTX["clipA"], method="ClearMarkInOut", args=["all"])))

    # --- timeline
    def mk_tl():
        r = t("timeline_create", name="Test TL", clips=[CTX["clipA"], CTX["clipB"]])
        CTX["tl"] = h(r["result"])
        return r["ok"] and r["item_count"] == 2, r["item_count"]
    check("timeline", "create from clips (verified item count)", mk_tl)
    check("timeline", "list timelines", lambda: (lambda r: (any(x["name"] == "Test TL" and x["current"] for x in r["timelines"]), len(r["timelines"])))(t("timeline_list")))

    def items_v1():
        ins = t("timeline_inspect", timeline="Test TL")
        v1 = ins["tracks"]["video"][0]["items"]
        CTX["it1"], CTX["it2"] = v1[0]["h"], v1[1]["h"]
        CTX["tl_start"] = ins["start_frame"]
        return len(v1) == 2 and v1[1]["start"] == v1[0]["end"], [(i["name"], i["start"], i["duration"]) for i in v1]
    check("timeline", "inspect items (positions contiguous)", items_v1)
    check("timeline", "add video track (verified count)", lambda: (lambda r: (r["ok"], r["actual"]))(t("timeline_track", action="add", track_type="video")))
    check("timeline", "rename track (verified)", lambda: (lambda r: (r["ok"], r.get("actual")))(t("timeline_track", action="rename", track_type="video", index=2, value="Overlay")))
    check("timeline", "lock + unlock track", lambda: (lambda a, b: (a["ok"] and b["ok"], [a.get("actual"), b.get("actual")]))(
        t("timeline_track", action="lock", track_type="video", index=2, value=True),
        t("timeline_track", action="lock", track_type="video", index=2, value=False)))
    check("timeline", "disable + enable track", lambda: (lambda a, b: (a["ok"] and b["ok"], [a.get("actual"), b.get("actual")]))(
        t("timeline_track", action="enable", track_type="video", index=2, value=False),
        t("timeline_track", action="enable", track_type="video", index=2, value=True)))

    def append_v2():
        rec = CTX["tl_start"] + 10
        r = t("timeline_append", clips=[{"clip": CTX["clipA"], "start_frame": 0, "end_frame": 24, "track": 2,
                                         "record_frame": rec, "media_type": 1}])
        CTX["it_v2"] = r["items"][0]["item"] if r["items"] else None
        return r["ok"], r["items"]
    check("timeline", "append subclip to V2 at frame (read back)", append_v2)
    check("timeline", "timeline marker add (verified)", lambda: (lambda r: (r["ok"], r.get("actual")))(
        t("markers", target=CTX["tl"], action="add", frame=12, color="Blue", name="TL mk", note="hi", custom_data="cd1")))
    check("timeline", "marker get by custom data", lambda: (lambda r: (r["marker"].get("name") == "TL mk", r))(
        t("markers", target=CTX["tl"], action="get_custom", custom_data="cd1")))
    check("timeline", "open media page", lambda: (lambda r: (r["ok"], r.get("actual")))(t("open_page", page="media")))
    check("timeline", "playhead refused on Media page (honest error)", lambda: (_expect_fail(lambda: t("timeline_playhead", timecode="01:00:01:00")), "refused"))
    check("timeline", "open edit page", lambda: (lambda r: (r["ok"], r.get("actual")))(t("open_page", page="edit")))
    check("timeline", "playhead set (verified)", lambda: (lambda r: (r["ok"], r.get("actual")))(t("timeline_playhead", timecode="01:00:01:00")))
    check("timeline", "start timecode set (verified)", lambda: (lambda r: (r["ok"], r.get("actual")))(
        t("timeline_action", action="set_start_timecode", timecode="01:00:00:00")))

    # --- items
    props = {"ZoomX": 1.25, "ZoomY": 1.25, "Pan": 50.0, "Tilt": -20.0, "Opacity": 75.0, "RotationAngle": 10.0,
             "FlipX": True, "CropLeft": 12.0}
    check("item", "set 8 inspector properties (each read back)", lambda: (lambda r: (r["ok"], [(x["key"], x["actual"]) for x in r["results"]]))(
        t("item_set_properties", items=[CTX["it1"]], properties=props)))
    check("item", "set composite mode via constant", lambda: (lambda r: (r["ok"], r["results"][0]["actual"]))(
        t("item_set_properties", items=[CTX["it_v2"]], properties={"CompositeMode": {"$const": "COMPOSITE_SCREEN"}})))
    check("item", "out-of-range value detected (Opacity 150)", lambda: (lambda r: (r["results"][0]["actual"] != 150, r["results"][0]))(
        t("item_set_properties", items=[CTX["it_v2"]], properties={"Opacity": 150.0})))
    check("item", "rename/color/disable/enable/flag", lambda: (lambda r: (r["ok"], [(x.get("method"), x.get("actual")) for x in r["results"]]))(
        t("item_update", item=CTX["it2"], name="Blue shot", clip_color="Teal", enabled=False, flags_add=["Red"])))
    check("item", "re-enable item", lambda: (lambda r: (r["ok"], r["results"][0].get("actual")))(t("item_update", item=CTX["it2"], enabled=True)))
    check("item", "item marker add", lambda: (lambda r: (r["ok"], r.get("actual")))(
        t("markers", target=CTX["it1"], action="add", frame=2, color="Green", name="item mk")))
    check("item", "inspect item", lambda: (lambda r: (abs(r["properties"]["ZoomX"] - 1.25) < 1e-3, {k: r["properties"][k] for k in ("ZoomX", "Pan", "Opacity")}))(
        t("item_inspect", item=CTX["it1"])))

    # --- color
    check("color", "open color page", lambda: (lambda r: (r["ok"], r.get("actual")))(t("open_page", page="color")))
    check("color", "node graph read", lambda: (lambda r: (r.get("num_nodes", 0) >= 1, r))(t("color_node_graph", item=CTX["it1"])))
    check("color", "LUT refresh", lambda: (lambda r: (r["result"] is True, r["result"]))(t("lut_refresh")))
    check("color", "set LUT on node 1 (verified by GetLUT)", lambda: (lambda r: (r["ok"], [(x.get("requested"), x.get("actual")) for x in r["results"]]))(
        t("color_apply", items=[CTX["it1"]], lut=CTX["lut"], node=1)))
    check("color", "apply CDL (call accepted; no getter)", lambda: (lambda r: (r["results"][0]["result"] is True, r["results"][0]["notes"][-1][:60]))(
        t("color_apply", items=[CTX["it1"]], cdl={"Slope": "1.1 1.0 0.9", "Offset": "0.01 0 -0.01", "Power": "1 1 1", "Saturation": 1.1})))
    check("color", "add + load color version", lambda: (lambda a, b: (a["ok"] and b["ok"], [a.get("versions_after"), b.get("actual")]))(
        t("color_versions", item=CTX["it1"], action="add", name="Claude v2"),
        t("color_versions", item=CTX["it1"], action="load", name="Claude v2")))
    check("color", "load back Version 1", lambda: (lambda r: (r["ok"], r.get("actual")))(t("color_versions", item=CTX["it1"], action="load", name="Version 1")))

    def grp():
        r = t("color_groups", action="add", name="Claude Group")
        CTX["grp"] = h(r["result"])
        a = t("color_groups", action="assign", group=CTX["grp"], items=[CTX["it1"], CTX["it2"]])
        return r["ok"] and a["ok"], [x.get("actual") for x in a["results"]]
    check("color", "color group add + assign (verified)", grp)
    def copy_grade():
        r = t("color_copy_grade", source=CTX["it1"], targets=[CTX["it2"]])
        vers = t("color_versions", item=CTX["it2"])
        b = r["backups"][0]["backup_version"] if r.get("backups") else None
        return r["ok"] and b in vers["local"] and vers["current"]["versionName"] != b, {"comparison": r["comparison"], "backup": b, "versions": vers["local"]}
    check("color", "copy grade to 2nd item, old grade kept as backup version", copy_grade)
    check("color", "export LUT from grade", lambda: (lambda r: (r["ok"], r.get("file")))(
        t("color_export_lut", item=CTX["it1"], path=os.path.join(WORK, "exported_grade"), size="17")))
    check("color", "Studio-only tool refused on Free (no blocking dialog)", lambda: (
        _expect_fail(lambda: t("color_ai_tools", item=CTX["it1"], action="smart_reframe")), "refused before calling Resolve"))

    # --- gallery
    def still():
        r = t("timeline_action", action="grab_still")
        CTX["still"] = h(r["result"]) if r.get("result") else None
        g = t("gallery", action="albums")
        CTX["album"] = next((a["h"] for a in g["albums"] if a["kind"] == "still"), None)
        return bool(CTX["still"]) and bool(CTX["album"]), g["albums"]
    check("gallery", "grab still + list albums", still)
    check("gallery", "label still (verified)", lambda: (lambda r: (r["ok"], r.get("actual")))(
        t("gallery", action="label", album=CTX["album"], stills=[CTX["still"]], label="Claude still")))
    check("gallery", "export still as DRX (files written)", lambda: (lambda r: (r["ok"], r.get("files_written")))(
        t("gallery", action="export", album=CTX["album"], stills=[CTX["still"]], folder=os.path.join(WORK, "stills"), fmt="drx")))

    def drx():
        files = [f for f in os.listdir(os.path.join(WORK, "stills")) if f.endswith(".drx")]
        r = t("color_apply", items=[CTX["it_v2"]], drx=os.path.join(WORK, "stills", files[0]), grade_mode=0)
        return r["ok"], r["results"][0].get("result")
    check("color", "apply DRX grade to another item", drx)

    # --- generated DRX grade (advanced codec + Resolve-21 window fix), checked by readback AND pixels
    def drx_offline():
        sys.path.insert(0, os.path.join(ROOT, "server"))
        import drx_r21
        r = t("color_grade_drx", items=[], dry_run=True, grade={"gain": {"master": 0.5}, "window": {
            "type": 1, "size": 30, "center_x": 0.5, "center_y": 0.5}})
        w = r["requested"]["windows"]
        return r["ok"] and w and w[0]["corrector_type"] == drx_r21.CT_WINDOW_R21 and abs(w[0]["size"] - 30) < 0.05, w
    check("color", "DRX window written in Resolve-21 form (offline, size 30 exact)", drx_offline)

    def drx_window_pixels():
        r = t("timeline_create", name="DRX grade TL", clips=[CTX["clipA"]])
        it = t("timeline_inspect", timeline="DRX grade TL")["tracks"]["video"][0]["items"][0]["h"]
        b = t("frame_preview", path=os.path.join(WORK, "drx_before.png"), item=it)["file"]
        g = t("color_grade_drx", items=[it], grade={"gain": {"r": 1, "g": 1, "b": 1, "master": 0.3},
              "window": {"type": 1, "size": 20, "center_x": 0.3, "center_y": 0.35, "soft1": 0}})
        a = t("frame_preview", path=os.path.join(WORK, "drx_after.png"), item=it)["file"]
        t("timeline_set_current", timeline="Test TL")
        inside = pngstat.region_mean(a, 0.29, 0.34, 0.31, 0.36) / pngstat.region_mean(b, 0.29, 0.34, 0.31, 0.36)
        outside = pngstat.region_mean(a, 0.75, 0.70, 0.80, 0.80) / pngstat.region_mean(b, 0.75, 0.70, 0.80, 0.80)
        box = pngstat.dark_bbox(a, 0.6 * pngstat.region_mean(b, 0.75, 0.70, 0.80, 0.80))
        cx, cy = ((box["bbox"][0] + box["bbox"][2]) / 2, (box["bbox"][1] + box["bbox"][3]) / 2) if box else (None, None)
        ok = (g["ok"] and inside < 0.6 and 0.97 < outside < 1.03 and box
              and abs(cx - 0.3) < 0.03 and abs(cy - 0.35) < 0.04)
        return ok, {"readback_ok": g["ok"], "inside_ratio": round(inside, 3), "outside_ratio": round(outside, 3),
                    "dark_centre": (cx and round(cx, 3), cy and round(cy, 3)), "backups": g.get("backups")}
    check("color", "generated DRX: window renders where requested, outside untouched (pixels)", drx_window_pixels)

    # --- fusion
    check("fusion", "open edit page", lambda: (lambda r: (r["ok"], r.get("actual")))(t("open_page", page="edit")))

    def fus():
        r = t("fusion_comp", item=CTX["it1"], action="add")
        g = t("fusion_comp", item=CTX["it1"], action="get", index=1)
        CTX["comp"] = h(g["comp"])
        return r["ok"] and bool(CTX["comp"]), r.get("names_after")
    check("fusion", "add fusion comp to item (verified)", fus)

    def ftool():
        r = t("fusion_tool", comp=CTX["comp"], action="add", reg_id="Blur")
        CTX["blur"] = h(r["result"])
        s = t("fusion_tool", comp=CTX["comp"], action="set_input", tool=CTX["blur"], input_id="XBlurSize", value=40.0)
        return r["ok"] and s["ok"], [r.get("tool_attrs"), s.get("actual")]
    check("fusion", "add Blur tool + set input (read back)", ftool)

    def fblur_pixels():
        before = t("frame_preview", path=os.path.join(WORK, "it1_before_blur.png"), item=CTX["it1"], page="edit")
        tools = {x["name"]: x["h"] for x in t("fusion_comp_inspect", comp=CTX["comp"], inputs=False)["tools"]}
        t("fusion_tool", comp=CTX["comp"], action="connect", tool=CTX["blur"], input_id="Input", source_tool=tools["MediaIn1"])
        c = t("fusion_tool", comp=CTX["comp"], action="connect", tool=tools["MediaOut1"], input_id="Input",
              source_tool=CTX["blur"], item=CTX["it1"])            # item => commit so Resolve renders it
        CTX["comp"] = c["commit"]["comp"]
        after = t("frame_preview", path=os.path.join(WORK, "it1_after_blur.png"), item=CTX["it1"], page="edit")
        eb, ea = pngstat.edge_energy(before["file"]), pngstat.edge_energy(after["file"])
        # The frame also contains the sharp V2 overlay clip and the rotated/cropped frame border (Inspector transforms
        # run after Fusion), which the Blur cannot touch - so require a clear drop, not total softness.
        return ea < eb * 0.8, {"edge_energy_before": round(eb, 6), "edge_energy_after": round(ea, 6)}
    check("fusion", "Blur wired in + committed changes the rendered frame (pixels)", fblur_pixels)
    check("fusion", "inspect comp tools", lambda: (lambda r: (any(x.get("reg_id") == "Blur" for x in r["tools"]), [x.get("name") for x in r["tools"]]))(
        t("fusion_comp_inspect", comp=CTX["comp"], inputs=False)))
    check("fusion", "export comp to .comp file", lambda: (lambda r: (r["ok"], r.get("result")))(
        t("fusion_comp", item=CTX["it1"], action="export", path=os.path.join(WORK, "test.comp"), index=1)))
    check("fusion", "rename comp (verified)", lambda: (lambda r: (r["ok"], r.get("names_after")))(
        t("fusion_comp", item=CTX["it1"], action="rename", name=t("fusion_comp", item=CTX["it1"])["names"][0], new_name="Claude Comp")))

    # --- export / import timelines
    for fmt, ext in (("edl", "edl"), ("fcpxml", "fcpxml"), ("otio", "otio"), ("drt", "drt"), ("csv", "csv"), ("fcp7xml", "xml")):
        check("interchange", "export %s" % fmt, lambda fmt=fmt, ext=ext: (lambda r: (r["ok"], r.get("file_exists")))(
            t("timeline_export", path=os.path.join(WORK, "Test_TL." + ext), format=fmt)))
    check("interchange", "import FCPXML as new timeline", lambda: (lambda r: (r["ok"], r.get("timeline")))(
        t("timeline_import", path=os.path.join(WORK, "Test_TL.fcpxml"), name="Reimported FCPXML", import_source_clips=False)))
    check("interchange", "import OTIO as new timeline", lambda: (lambda r: (r["ok"], r.get("timeline")))(
        t("timeline_import", path=os.path.join(WORK, "Test_TL.otio"), name="Reimported OTIO", import_source_clips=False)))

    # --- workarounds
    check("workaround", "timeline_set_current back to Test TL", lambda: (lambda r: (r["ok"], r.get("actual")))(t("timeline_set_current", timeline="Test TL")))

    def razor():
        r = t("timeline_razor", at=30, timeline="Test TL", name="Test TL (razor@30)")
        return r["ok"] and r["split_count"] >= 1, {"splits": r["split_count"], "placed": len(r["placed"]),
                                                    "notes": r["attribute_notes"][:2], "not_rebuilt": len(r["not_rebuilt"])}
    check("workaround", "razor at frame 30 -> new timeline (verified)", razor)

    def razor_props():
        ins = t("timeline_inspect", timeline="Test TL (razor@30)")
        first = ins["tracks"]["video"][0]["items"][0]
        return abs(first["properties"]["ZoomX"] - 1.25) < 1e-3, {"ZoomX": first["properties"]["ZoomX"], "color": first.get("clip_color")}
    check("workaround", "razor kept transform properties", razor_props)

    def otio():
        el = t("edit_list_get", timeline="Test TL")
        v = [p for p in el["pieces"] if p["track_type"] == "video" and p["track"] == 1]
        a = dict(v[0], record_start=0, source_start=0, duration=36)
        b = dict(v[1], record_start=36, source_start=6, duration=36)
        s = dict(v[1], record_start=72, source_start=0, duration=20, speed=200.0)
        r = t("timeline_build_otio", pieces=[a, b, s], name="OTIO build",
              transitions=[{"track_type": "video", "track": 1, "at": 36, "duration": 12, "name": "Cross Dissolve"}],
              markers=[{"frame": 5, "name": "otio mk", "color": "Red"}])
        names = [i["name"] for i in r["items"]]
        return "Cross Dissolve" in names and bool(r.get("markers")), names
    check("workaround", "OTIO build: dissolve + 200% speed + marker", otio)

    def editlist():
        el = t("edit_list_get", timeline="Test TL")
        v1 = [dict(p) for p in el["pieces"] if p["track_type"] == "video" and p["track"] == 1]
        # slip clip 2 by 6 frames and close the gap after trimming clip 1 by 8 frames (a ripple trim)
        v1[0]["duration"] -= 8
        v1[1]["record_start"] -= 8
        v1[1]["source_start"] += 6
        v1[1]["duration"] -= 6
        r = t("edit_list_build", pieces=v1, name="Edit list ripple+slip", copy_grades=False)
        return r["ok"], [(p["actual"]["start"], p["actual"]["duration"]) for p in r["placed"]]
    check("workaround", "edit list: ripple trim + slip -> new timeline", editlist)

    # --- beyond the API: LUT grading, Fusion keyframes, offline audio
    t("timeline_set_current", timeline="Test TL")
    prev = os.path.join(WORK, "previews")
    os.makedirs(prev, exist_ok=True)
    check("beyond-api", "frame preview before grade", lambda: (lambda r: (r["ok"], r.get("file")))(
        t("frame_preview", path=os.path.join(prev, "it2_before.png"), item=CTX["it2"])))
    grade = {"lift": [0.0, 0.0, 0.02], "gamma": 1.05, "gain": [1.08, 1.0, 0.9], "contrast": 1.12, "saturation": 1.15,
             "curves": {"master": [[0, 0.02], [0.25, 0.21], [0.75, 0.8], [1, 0.98]]},
             "hue_vs_sat": [[120, 0.6], [220, 1.3]],
             "qualifiers": [{"hue": 220, "hue_width": 40, "sat_range": [0.2, 1.0], "adjust": {"hue_shift": -15, "saturation": 1.2}}]}
    check("beyond-api", "color_grade_lut: wheels+curves+qualifier on node 1 (GetLUT)", lambda: (lambda r: (r["ok"] and r["preview"]["ok"], {"lut": os.path.basename(r["lut_file"]), "actual": r["results"][0]["actual"], "sky": r["samples"]["sky_blue"]}))(
        t("color_grade_lut", items=[CTX["it2"]], grade=grade, name="test_look", preview_path=os.path.join(prev, "it2_after.png"))))
    check("beyond-api", "graded frame differs from ungraded", lambda: (
        open(os.path.join(prev, "it2_before.png"), "rb").read() != open(os.path.join(prev, "it2_after.png"), "rb").read(), "bytes differ"))
    check("beyond-api", "color_grade_lut refuses missing node honestly", lambda: (lambda r: (not r["ok"] and "cannot add nodes" in r["results"][0]["error"], r["results"][0]["error"]))(
        t("color_grade_lut", items=[CTX["it2"]], grade={"saturation": 0.5}, node=5)))
    keys = [{"frame": 0, "zoom": 1.0, "pan": 0, "tilt": 0, "rotation": 0, "opacity": 100},
            {"frame": 24, "zoom": 1.5, "pan": 60, "tilt": 30, "rotation": 12, "opacity": 40},
            {"frame": 47, "zoom": 1.0, "pan": 0, "tilt": 0, "rotation": 0, "opacity": 100}]
    def kf():
        r = t("keyframe_transform", item=CTX["it2"], keys=keys, preview_dir=os.path.join(prev, "keys"))
        mid = r["readback"].get("12", {})
        return r["ok"] and all(r["previews"]) and 1.0 < mid.get("zoom", 0) < 1.5, {"checks_ok": sum(c["ok"] for c in r["checks"]), "mid@12": mid}
    check("beyond-api", "keyframe_transform: zoom/pan/tilt/rot/opacity (read back + previews)", kf)

    def kf_pixels():
        # it2_after.png is frame 24 (clip middle) graded, without keyframes; key_0024.png is the same frame with
        # opacity 40% over transparent black -> mean luma should drop to roughly 40%.
        m0, _ = pngstat.luma_stats(os.path.join(prev, "it2_after.png"))
        m1, _ = pngstat.luma_stats(os.path.join(prev, "keys", "key_0024.png"))
        ratio = m1 / m0 if m0 else 0
        return 0.25 < ratio < 0.6, {"mean_before": round(m0, 3), "mean_key24": round(m1, 3), "ratio": round(ratio, 3)}
    check("beyond-api", "keyframes visible in the rendered frame (pixels, opacity 40%)", kf_pixels)
    check("beyond-api", "keyframe_transform re-run is idempotent", lambda: (lambda r, s: (r["ok"] and [x["name"] for x in s["tools"]].count("ClaudeTransform") == 1, r["readback"]["24"]["zoom"]))(
        *(lambda r: (r, t("fusion_comp_inspect", comp=r["comp"], inputs=False)))(t("keyframe_transform", item=CTX["it2"], keys=[{"frame": 24, "zoom": 1.7}]))))
    ops = {"gain_db": -2, "envelope": [[0.4, 0], [1.0, -12], [1.6, 0]], "fade_in": 0.1, "fade_out": 0.25,
           "normalize": {"mode": "peak", "target_dbfs": -3}}
    def aud():
        r = t("audio_process", source=CTX["wav"], operations=ops, action="import")
        CTX["wav2"] = r["imported"]["clips"][0]["h"]
        return r["ok"], {"after": r["after"], "env": r["envelope_check"]}
    check("beyond-api", "audio_process: envelope+fades+normalize, import (measured)", aud)
    check("beyond-api", "audio_process replace refused without confirm", lambda: (_expect_fail(lambda: t("audio_process", source=CTX["wav2"], operations={"gain_db": -6}, action="replace")), "refused"))
    check("beyond-api", "audio_process replace with confirm (file swapped, backup)", lambda: (lambda r: (r["ok"] and bool(r["replace"].get("backup")), os.path.basename(r["clip_file_after"])))(
        t("audio_process", source=CTX["wav2"], operations={"gain_db": -6}, action="replace", confirm=True)))

    # --- render (never started)
    def render():
        opts = t("render_options")
        fmts = opts["formats"]
        fmt = "mp4" if "mp4" in fmts.values() else list(fmts.values())[0]
        codecs = t("render_options", format=fmt)["codecs"]
        codec = next((c for c in codecs.values() if "264" in c), list(codecs.values())[0])
        r = t("render_add_job", timeline="Test TL", format=fmt, codec=codec, mode=1,
              settings={"TargetDir": os.path.join(WORK, "render"), "CustomName": "claude_test", "SelectAllFrames": True,
                        "FormatWidth": 1280, "FormatHeight": 720})
        CTX["job"] = r["job_id"]
        return r["ok"], [(c["key"], c["actual"], c["ok"]) for c in r["checks"]]
    check("render", "add render job (not started), settings read back", render)
    def render_page():
        t("open_page", page="edit")
        t("render_options")
        return t("resolve_status")["page"] == "edit", t("resolve_status")["page"]
    check("render", "render tools put the user back on their page", render_page)
    check("render", "render queue status", lambda: (lambda r: (any(j["JobId"] == CTX["job"] for j in r["jobs"]) and not r["in_progress"], [j.get("status") for j in r["jobs"]]))(
        t("render_jobs", action="list")))
    check("render", "start render refused without confirm", lambda: (lambda: (_expect_fail(lambda: t("render_jobs", action="start", job_ids=[CTX["job"]])), "refused"))())
    check("render", "delete job with confirm (backup made)", lambda: (lambda r: (r["ok"], r["results"][0].get("backup")))(
        t("render_jobs", action="delete", job_ids=[CTX["job"]], confirm=True)))

    # --- state & diff
    def state_diff():
        s = t("get_full_state", depth=2, max_chars=200000)
        sid = s["snapshot_id"]
        t("markers", target=CTX["tl"], action="add", frame=40, color="Yellow", name="diff me")
        t("item_set_properties", items=[CTX["it2"]], properties={"Opacity": 60.0})
        d = t("diff_state", before=sid)
        paths = [c["path"] for c in d["changes"]]
        hit_marker = any("/markers/40" in p for p in paths)
        hit_prop = any("Opacity" in p for p in paths)
        return hit_marker and hit_prop, {"changes": d["change_count"], "sample": paths[:6]}
    check("state", "full state snapshot + diff detects edits", state_diff)
    def snap_page():
        t("open_page", page="edit")
        t("get_full_state", depth=0, include=["project", "render"])
        page = t("resolve_status")["page"]
        return page == "edit", page
    check("state", "snapshot does not move the user's page (GetCurrentRenderMode side effect)", snap_page)

    def dup_keeps_current():
        before = t("timeline_list")
        cur = next(x["name"] for x in before["timelines"] if x["current"])
        r = t("timeline_action", action="duplicate", name="dup check")
        return r["ok"] and r.get("current_timeline_restored") == cur, {"current": cur, "restored": r.get("current_timeline_restored")}
    check("timeline", "duplicate keeps the current timeline", dup_keeps_current)
    check("state", "state paging (item_limit=1)", lambda: (lambda r: ("items_truncated" in json.dumps(r), r["snapshot_id"]))(
        t("get_full_state", depth=1, include=["timelines", "items"], timelines=["Test TL"], item_limit=1)))

    # --- safety
    check("safety", "destructive call refused without confirm", lambda: (_expect_fail(lambda: t("timeline_action", action="delete", timeline="Edit list ripple+slip")), "refused"))
    check("safety", "dry run shows plan, changes nothing", lambda: (lambda r: (r.get("dry_run") is True and r.get("destructive"), r.get("would_call")))(
        t("api_call", target=t("get_roots")["media_pool"], method="DeleteTimelines", args=[[{"$h": CTX["tl"]}]], dry_run=True)))
    check("safety", "unknown method rejected with suggestions", lambda: (_expect_fail(lambda: t("api_call", target=CTX["tl"], method="AddMarkr")), "rejected"))
    check("safety", "confirmed delete makes .drp backup first", lambda: (lambda r: (r["ok"] is not False and r.get("backup") and os.path.exists(r["backup"][0]), r.get("backup")))(
        t("timeline_action", action="delete", timeline="Edit list ripple+slip", confirm=True)))
    check("meta", "live methods vs README", lambda: (lambda r: (len(r["methods"]) > 30, {"undocumented": r["undocumented"], "missing": r["documented_but_missing"]}))(
        t("api_methods_live", target=CTX["tl"])))
    check("meta", "operation log written", lambda: (lambda r: (len(r["entries"]) > 10, len(r["entries"])))(t("op_log", limit=200)))
    check("project", "save project", lambda: (lambda r: (r["result"] is True, r["result"]))(t("project_save")))

    # --- report
    passed = sum(r["status"] == "PASS" for r in RESULTS)
    print("\n%d/%d passed  (project %s, work dir %s)" % (passed, len(RESULTS), pname, WORK))
    with open(os.path.join(ROOT, "tests", "last_results.json"), "w", encoding="utf-8") as f:
        json.dump({"when": TS, "project": pname, "passed": passed, "total": len(RESULTS), "results": RESULTS}, f, indent=1)
    return passed == len(RESULTS)


def _expect_fail(fn):
    try:
        fn()
    except ToolFailed:
        return True
    return False


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
