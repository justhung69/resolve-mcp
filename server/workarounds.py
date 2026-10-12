"""Workarounds for edits the Resolve scripting API cannot do directly.

1. Edit lists: a timeline is read into an editable JSON list of "pieces" (one per clip on a track).
   Claude edits the list (razor, trim, slip, slide, ripple, reorder) and the list is rebuilt into a
   NEW timeline with MediaPool.AppendToTimeline, copying transform properties, clip colour, enabled
   state, item markers and (optionally) grades from the source items. The original is never touched.
2. OTIO: for things AppendToTimeline cannot express (transitions, gaps, timeline markers, speed),
   the pieces are written as an OpenTimelineIO file in the dialect Resolve itself exports
   (Clip.2 + media_references, plain Windows paths) and imported with MediaPool.ImportTimelineFromFile.

Measured on Resolve 21.0.4 Free: AppendToTimeline's endFrame is EXCLUSIVE (start 10, end 20 -> 10 frames),
while TimelineItem.GetSourceEndFrame is inconsistent (exclusive for subclips, inclusive for full clips),
so piece lengths always come from GetDuration().
"""
import json
import os
import re
import time

import core

TRACK_TYPES = ("video", "audio")
MEDIA_TYPE = {"video": 1, "audio": 2}


# --------------------------------------------------------------------------- edit lists

def extract_edit_list(timeline=None, track_types=TRACK_TYPES):
    """Read a timeline into editable pieces. Frames are relative to the timeline start."""
    tl = core.resolve_timeline(timeline)
    tl_start = core.get(tl, "GetStartFrame")
    pieces, unsupported, tracks = [], [], {}
    for tt in track_types:
        n = core.get(tl, "GetTrackCount", tt) or 0
        tracks[tt] = []
        for ti in range(1, n + 1):
            tracks[tt].append({"index": ti, "name": core.get(tl, "GetTrackName", tt, ti),
                               "subtype": core.get(tl, "GetTrackSubType", tt, ti) if tt == "audio" else None})
            for it in core.get(tl, "GetItemListInTrack", tt, ti) or []:
                name = core.get(it, "GetName")
                start = core.get(it, "GetStart")
                dur = core.get(it, "GetDuration")
                mpi = core.B.try_call(core.handle_id(it), "GetMediaPoolItem")
                base = {"track_type": tt, "track": ti, "record_start": start - tl_start, "duration": dur, "name": name,
                        "source_item": core.handle_id(it)}
                if not mpi:
                    unsupported.append(dict(base, reason="no media pool item (generator, title, Fusion comp or "
                                                         "transition) - cannot be re-appended through the API"))
                    continue
                if core.B.try_call(core.handle_id(mpi), "GetTimeline"):
                    unsupported.append(dict(base, reason="compound/nested timeline clip - cannot be re-appended "
                                                         "through the API"))
                    continue
                props = core.B.try_call(core.handle_id(mpi), "GetClipProperty") or {}
                # GetLeftOffset is exact at 100% speed; GetSourceStartFrame can read one frame off (samuelgursky
                # api_truth). Both are relative to the media's first frame.
                left = core.B.try_call(core.handle_id(it), "GetLeftOffset")
                base.update(media_pool_item=core.handle_id(mpi),
                            source_start=int(left) if isinstance(left, (int, float)) else core.get(it, "GetSourceStartFrame"),
                            file_path=props.get("File Path"),
                            media_fps=float(props.get("FPS") or 0) or None,
                            media_start_tc=props.get("Start TC") or "00:00:00:00")
                pieces.append(base)
    pieces.sort(key=lambda p: (p["track_type"], p["track"], p["record_start"]))
    return {"timeline": core.get(tl, "GetName"), "timeline_handle": core.handle_id(tl), "start_frame": tl_start,
            "end_frame": core.get(tl, "GetEndFrame"), "tracks": tracks, "pieces": pieces,
            "unsupported": unsupported}


def razor(edit, frame, track_types=None, tracks=None):
    """Split every piece crossing `frame` (relative to timeline start). Returns a new edit list dict."""
    out = []
    for p in edit["pieces"]:
        hit = p["record_start"] < frame < p["record_start"] + p["duration"]
        if hit and (track_types is None or p["track_type"] in track_types) and (tracks is None or p["track"] in tracks):
            first = frame - p["record_start"]
            a = dict(p, duration=first)
            b = dict(p, record_start=frame, source_start=p["source_start"] + first, duration=p["duration"] - first,
                     marker_offset=p.get("marker_offset", 0) + first)
            out += [a, b]
        else:
            out.append(p)
    return dict(edit, pieces=out)


def validate_pieces(pieces):
    problems = []
    by_track = {}
    for i, p in enumerate(pieces):
        for k in ("track_type", "track", "record_start", "duration", "source_start", "media_pool_item"):
            if k not in p:
                problems.append("piece %d missing %r" % (i, k))
        if p.get("duration", 1) <= 0:
            problems.append("piece %d has non-positive duration" % i)
        if p.get("source_start", 0) < 0:
            problems.append("piece %d has negative source_start" % i)
        by_track.setdefault((p.get("track_type"), p.get("track")), []).append((p.get("record_start", 0), p.get("duration", 0), i))
    for key, lst in by_track.items():
        lst.sort()
        for (s1, d1, i1), (s2, d2, i2) in zip(lst, lst[1:]):
            if s1 + d1 > s2:
                problems.append("pieces %d and %d overlap on %s track %s" % (i1, i2, key[0], key[1]))
    return problems


def build_from_edit_list(pieces, name, *, copy_attributes=True, copy_grades=True, audio_subtypes=None, dry_run=False):
    """Build a new timeline from pieces with AppendToTimeline; verify every placed item by read-back."""
    problems = validate_pieces(pieces)
    if problems:
        raise ValueError("invalid edit list: " + "; ".join(problems[:20]))
    plan = [{"track": "%s %d" % (p["track_type"], p["track"]), "record_start": p["record_start"],
             "source": [p["source_start"], p["source_start"] + p["duration"]], "name": p.get("name")} for p in pieces]
    if dry_run:
        return {"dry_run": True, "would_create_timeline": name, "pieces": plan}
    r = core.roots()
    mp = core.require(r.get("media_pool"), "media pool")
    p_h = r["project"]
    tl = core.get(mp, "CreateEmptyTimeline", name)
    if not tl:
        raise core.BridgeError("CreateEmptyTimeline(%r) failed - is the name already used?" % name)
    core.get(p_h, "SetCurrentTimeline", tl)
    tl_start = core.get(tl, "GetStartFrame")
    need = {tt: max([p["track"] for p in pieces if p["track_type"] == tt] or [0]) for tt in TRACK_TYPES}
    for tt in TRACK_TYPES:
        while (core.get(tl, "GetTrackCount", tt) or 0) < need[tt]:
            idx = (core.get(tl, "GetTrackCount", tt) or 0) + 1
            sub = (audio_subtypes or {}).get(str(idx)) or (audio_subtypes or {}).get(idx) if tt == "audio" else None
            ok = core.get(tl, "AddTrack", tt, sub) if sub else core.get(tl, "AddTrack", tt)
            if not ok:
                raise core.BridgeError("AddTrack(%s) failed" % tt)
    report = {"timeline": core.handle_id(tl), "name": name, "placed": [], "mismatches": [], "attribute_notes": []}
    for p in sorted(pieces, key=lambda x: (x["record_start"], x["track_type"], x["track"])):
        info = {"mediaPoolItem": {"$h": p["media_pool_item"]}, "startFrame": p["source_start"],
                "endFrame": p["source_start"] + p["duration"], "trackIndex": p["track"],
                "recordFrame": tl_start + p["record_start"], "mediaType": MEDIA_TYPE[p["track_type"]]}
        items = core.get(mp, "AppendToTimeline", [info]) or []
        if not items:
            report["mismatches"].append({"piece": p.get("name"), "error": "AppendToTimeline returned nothing"})
            continue
        new = items[0]
        got = {"start": core.get(new, "GetStart") - tl_start, "duration": core.get(new, "GetDuration"),
               "track": core.get(new, "GetTrackTypeAndIndex")}
        want = {"start": p["record_start"], "duration": p["duration"], "track": [p["track_type"], p["track"]]}
        placed = {"piece": p.get("name"), "item": core.handle_id(new), "requested": want, "actual": got,
                  "ok": got == want}
        report["placed"].append(placed)
        if not placed["ok"]:
            report["mismatches"].append(placed)
        if copy_attributes and p.get("source_item"):
            report["attribute_notes"] += copy_item_attributes(p["source_item"], new, p, copy_grades)
    report["ok"] = not report["mismatches"]
    core.oplog("build_from_edit_list", {"name": name, "pieces": len(pieces)}, report["ok"],
               result={"placed": len(report["placed"]), "mismatches": len(report["mismatches"])})
    return report


def copy_item_attributes(src, dst, piece, copy_grades):
    notes = []
    srcid, dstid = core.handle_id(src), core.handle_id(dst)
    try:
        props = core.B.try_call(srcid, "GetProperty") or {}
        cur = core.B.try_call(dstid, "GetProperty") or {}
        for k, v in props.items():
            if cur.get(k) != v and not core.B.try_call(dstid, "SetProperty", k, v):
                notes.append("%s: could not copy property %s" % (piece.get("name"), k))
        color = core.B.try_call(srcid, "GetClipColor")
        if color:
            core.B.try_call(dstid, "SetClipColor", color)
        if core.B.try_call(srcid, "GetClipEnabled") is False:
            core.B.try_call(dstid, "SetClipEnabled", False)
        off = piece.get("marker_offset", 0)
        for frame, m in (core.B.try_call(srcid, "GetMarkers") or {}).items():
            f = int(float(frame)) - off
            if 0 <= f < piece["duration"]:
                core.B.try_call(dstid, "AddMarker", f, m.get("color", "Blue"), m.get("name", ""), m.get("note", ""),
                                int(m.get("duration", 1) or 1), m.get("customData", ""))
        if copy_grades and piece["track_type"] == "video":
            if not core.B.try_call(srcid, "CopyGrades", [{"$h": dstid}]):
                notes.append("%s: CopyGrades returned False (grade not copied)" % piece.get("name"))
    except core.BridgeError as e:
        notes.append("%s: attribute copy failed: %s" % (piece.get("name"), e))
    return notes


# --------------------------------------------------------------------------- OTIO

SEQ_RE = re.compile(r"^(?P<dir>.*)[\\/](?P<prefix>[^\\/]*?)\[(?P<a>\d+)-(?P<b>\d+)\](?P<suffix>\.\w+)$")
OTIO_COLORS = {"Blue": "BLUE", "Cyan": "CYAN", "Green": "GREEN", "Yellow": "YELLOW", "Red": "RED", "Pink": "PINK",
               "Purple": "PURPLE", "Fuchsia": "MAGENTA", "Rose": "PINK", "Lavender": "PURPLE", "Sky": "CYAN",
               "Mint": "GREEN", "Lemon": "YELLOW", "Sand": "ORANGE", "Cocoa": "ORANGE", "Cream": "WHITE"}


def _rt(v, rate):
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": float(rate), "value": float(v)}


def _tr(start, dur, rate):
    return {"OTIO_SCHEMA": "TimeRange.1", "start_time": _rt(start, rate), "duration": _rt(dur, rate)}


def tc_frames(tc, fps):
    try:
        hh, mm, ss, ff = [int(x) for x in str(tc).replace(";", ":").split(":")]
    except ValueError:
        return 0
    f = int(round(float(fps)))
    return ((hh * 60 + mm) * 60 + ss) * f + ff


def media_origin(piece, rate):
    """First frame of the media in its own timecode (OTIO source ranges must be expressed against it: media starting
    at 01:00:00:00 has an available range starting at frame 86400 and import fails with 0-based offsets)."""
    return tc_frames(piece.get("media_start_tc") or "00:00:00:00", piece.get("media_fps") or rate)


def media_reference(path, rate, origin=0):
    m = SEQ_RE.match(path or "")
    if m:
        a, b = m.group("a"), m.group("b")
        return {"OTIO_SCHEMA": "ImageSequenceReference.1", "metadata": {}, "name": os.path.basename(path),
                "available_range": _tr(origin, int(b) - int(a) + 1, rate), "available_image_bounds": None,
                "target_url_base": m.group("dir"), "name_prefix": m.group("prefix"), "name_suffix": m.group("suffix"),
                "start_frame": int(a), "frame_step": 1, "rate": float(rate), "frame_zero_padding": len(a),
                "missing_frame_policy": "error"}
    return {"OTIO_SCHEMA": "ExternalReference.1", "metadata": {}, "name": os.path.basename(path or ""),
            "available_range": None, "available_image_bounds": None, "target_url": path}


def build_otio(pieces, rate, *, name="", start_frame=86400, transitions=(), markers=(), track_names=None):
    """pieces: as in edit lists, plus optional "speed" (percent). transitions: [{"track_type", "track",
    "at": cut frame relative to timeline start, "duration": frames, "name": "Cross Dissolve"}].
    markers: [{"frame", "name", "color", "duration", "note"}] (timeline markers)."""
    track_names = track_names or {}
    stack = []
    for tt in TRACK_TYPES:
        ntracks = max([p["track"] for p in pieces if p["track_type"] == tt] or [0])
        for ti in range(1, ntracks + 1):
            children, cursor = [], 0
            trs = sorted([t for t in transitions if t.get("track_type", "video") == tt and t.get("track", 1) == ti],
                         key=lambda t: t["at"])
            for p in sorted([p for p in pieces if p["track_type"] == tt and p["track"] == ti], key=lambda x: x["record_start"]):
                if p["record_start"] > cursor:
                    children.append({"OTIO_SCHEMA": "Gap.1", "metadata": {}, "name": "",
                                     "source_range": _tr(0, p["record_start"] - cursor, rate),
                                     "effects": [], "markers": [], "enabled": True})
                for t in trs:
                    if t["at"] == p["record_start"] and children and children[-1]["OTIO_SCHEMA"] == "Clip.2":
                        half = int(t.get("duration", 24)) // 2
                        children.append({"OTIO_SCHEMA": "Transition.1", "metadata": {}, "name": t.get("name", "Cross Dissolve"),
                                         "transition_type": "SMPTE_Dissolve", "in_offset": _rt(half, rate),
                                         "out_offset": _rt(int(t.get("duration", 24)) - half, rate)})
                speed = float(p.get("speed", 100.0)) / 100.0
                origin = media_origin(p, rate)
                clip = {"OTIO_SCHEMA": "Clip.2", "metadata": {}, "name": p.get("name") or os.path.basename(p.get("file_path", "")),
                        "source_range": _tr(origin + p["source_start"], p["duration"], rate),
                        "effects": [], "markers": [], "enabled": True,
                        "media_references": {"DEFAULT_MEDIA": media_reference(p["file_path"], rate, origin)},
                        "active_media_reference_key": "DEFAULT_MEDIA"}
                if speed != 1.0:
                    # Resolve treats source_range.duration as the RECORD length and consumes duration*speed
                    # source frames (measured on 21.0.4), so the range is left unscaled.
                    clip["effects"].append({"OTIO_SCHEMA": "LinearTimeWarp.1", "metadata": {}, "name": "",
                                            "effect_name": "LinearTimeWarp", "time_scalar": speed})
                children.append(clip)
                cursor = p["record_start"] + p["duration"]
            stack.append({"OTIO_SCHEMA": "Track.1", "metadata": {}, "name": track_names.get((tt, ti), "%s %d" % (tt.title(), ti)),
                          "source_range": None, "effects": [], "markers": [], "enabled": True,
                          "kind": "Video" if tt == "video" else "Audio", "children": children})
    otio_markers = [{"OTIO_SCHEMA": "Marker.2", "metadata": {}, "name": m.get("name", ""),
                     "color": OTIO_COLORS.get(m.get("color", "Blue"), "BLUE"), "comment": m.get("note", ""),
                     "marked_range": _tr(m["frame"], m.get("duration", 1), rate)} for m in markers]
    return {"OTIO_SCHEMA": "Timeline.1", "metadata": {}, "name": name, "global_start_time": _rt(start_frame, rate),
            "tracks": {"OTIO_SCHEMA": "Stack.1", "metadata": {}, "name": "", "source_range": None, "effects": [],
                       "markers": otio_markers, "enabled": True, "children": stack}}


def import_otio(otio_dict, name, *, import_source_clips=False):
    name = core.unique_timeline_name(name)  # an existing timelineName makes the import return None
    otio_dict = dict(otio_dict, name=name)
    path = os.path.join(core.STATE_DIR, "otio", "%s_%s.otio" % (re.sub(r"[^\w.-]+", "_", name), time.strftime("%Y%m%d-%H%M%S")))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(otio_dict, f, indent=1)
    r = core.roots()
    mp = core.require(r.get("media_pool"), "media pool")
    opts = {"timelineName": name, "importSourceClips": import_source_clips}
    if not import_source_clips:
        opts["sourceClipsFolders"] = [r["root_folder"]] + _all_subfolders(r["root_folder"])
    tl = core.get(mp, "ImportTimelineFromFile", path, opts, timeout=180)
    return path, tl, name


def _all_subfolders(folder):
    out = []
    for f in core.get(folder, "GetSubFolderList") or []:
        out.append(f)
        out += _all_subfolders(f)
    return out
