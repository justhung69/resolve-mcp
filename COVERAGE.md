# Coverage — what Claude can and cannot do in DaVinci Resolve

Measured on **DaVinci Resolve 21.0.4.5 Free, Windows 11**, Python 3.14.5, bridge 1.0.0, 2026-10-12.
API source of truth: the README installed with Resolve
(`C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\README.txt`).
The full per-method table (all 382 documented methods, checked against live objects) is generated into
[docs/coverage_methods.md](docs/coverage_methods.md) by `tools/gen_coverage.py`.

Legend: **API** = direct, read back where a getter exists · **Workaround** = achieved another way, verified ·
**UI** = only through the optional computer-use fallback ([docs/UI_FALLBACK.md](docs/UI_FALLBACK.md)) ·
**No** = not possible.

## Why the architecture looks like this (Phase 0 results)

| Test | Resolve 21.1.1 Free | Resolve 21.0.4 Free |
|---|---|---|
| External `import DaVinciResolveScript` → `scriptapp("Resolve")` | `None` (blocked) | `None` (blocked) |
| Python script from Workspace > Scripts | **never listed** (Python gated to Studio in 21.1) | runs in `fuscript.exe`, `resolve` works |
| Lua script from Workspace > Scripts | runs, but sandboxed: no `io`, `require`, `ffi`, `os.execute` | runs |
| Background thread, script returns | — | **killed** when the script returns |
| Blocking TCP loop on the script's main thread | — | **works**: ~2 ms/call, UI responsive, 10-min soak 0 failures |

So: the user rolled back to 21.0.4, and `ClaudeBridge.py` (started once per session from the Scripts menu)
serves a loopback socket that the MCP server talks to. Do **not** update Resolve to 21.1+ Free — it removes
in-app Python and this tool stops working (Studio 21.1+ would work, with external scripting even simpler).

## Feature matrix

### Projects & app
| Feature | Status | Notes / workaround |
|---|---|---|
| List / create / load / save / close / delete projects | API | create auto-saves so the project is listed; delete needs `confirm` + .drp backup |
| Import / export / archive / restore .drp / .dra | API | export verified by file existence |
| Project folders, databases (list/switch) | API | switch needs `confirm` |
| Project settings get / set (all keys) | API | each key read back; e.g. frame rate refuses changes once timelines exist → `ok:false` |
| Switch page | API | verified by `GetCurrentPage` |
| Layout / burn-in / user-preference / render presets | API | via `api_call` / `render_presets` |
| Keyboard presets | No (21.1-only API) | UI |
| Undo / redo | No | restore an auto-made .drp backup (`%LOCALAPPDATA%\ResolveMCP\backups`) |
| Cloud projects | API (untested: needs Blackmagic Cloud) | `api_call` |

### Media pool & media storage
| Feature | Status | Notes / workaround |
|---|---|---|
| Import files / folders / image sequences | API | **quirk**: `{"FilePath": file}` dict form returns `[]` for plain files; tool sends strings, one entry per call |
| Bins: create / move clips & folders / delete / DRB export-import | API | verified in the media pool tree |
| Clip properties, metadata, third-party metadata | API | each key read back |
| Clip color, flags, markers (+customData) | API | verified |
| Mark in/out on clips | API | **quirk**: `SetMarkInOut(a,b,"all")` on an image-sequence clip only sets the *audio* range — read-back reports `ok:false` |
| Relink / unlink / replace / proxy link | API | destructive ones need `confirm` |
| Subclips | Workaround | `timeline_append` / `AppendToTimeline` with start/end frames (no "make subclip" API) |
| Multicam clip creation, smart switch | No (21.1-only API) | UI |
| Auto-sync audio | API (untested) | `api_call MediaPool.AutoSyncAudio` |
| Transcription, audio classification, IntelliSearch, slate | API, Studio/AI | returns False on Free |
| Stereo clips, mattes | API | `api_call` |

### Timelines
| Feature | Status | Notes / workaround |
|---|---|---|
| Create empty / from clips / with clipInfo (start, end, record, track) | API | **measured**: `endFrame` is EXCLUSIVE (10→20 = 10 frames) |
| Append to timeline at frame/track, video-only/audio-only | API | each placed item read back (start, duration, track) |
| Import AAF / EDL / XML / FCPXML / DRT / OTIO | API | **quirk**: FCPXML 1.10 export is a *folder* `X.fcpxml/Info.fcpxml`; import handles the folder. With `importSourceClips=false` the tool passes all bins as `sourceClipsFolders` (otherwise FCPXML import returns None) |
| Export AAF / EDL (+CDL/SDL) / FCP7 XML / FCPXML 1.8-1.10 / OTIO / DRT / CSV / ALE / HDR10 / Dolby | API | file existence verified |
| Tracks: add / delete / rename / enable / lock / audio subtype | API | verified by getters |
| Markers (+customData) | API | verified; frames relative to timeline start |
| Playhead get/set, current video item | API | **only on Cut, Edit, Color, Fairlight, Deliver pages** — tool refuses elsewhere |
| Start timecode, mark in/out, settings (custom) | API | verified |
| Duplicate / rename / delete timeline | API | delete needs `confirm` |
| Generators, titles, Fusion titles/generators/compositions, OFX generators | API | inserted at playhead |
| Compound clip, Fusion clip | API | |
| Scene cut detection | API (untested on Free) | |
| Subtitles from audio | API, Studio/AI | False on Free |
| Grab still(s) | API | |
| Delete clips (lift / ripple) | API | `confirm`; verified that items are gone |
| Link / unlink clips | API | |
| **Razor / split at playhead** | **Workaround** | `timeline_razor`: rebuilds into a NEW timeline (original untouched), copies transform props, clip colour, enabled, item markers, grades. Titles/generators/compound clips/transitions/OFX on clips are **reported, not rebuilt** |
| **Ripple / roll / slip / slide trims, move, reorder** | **Workaround** | `edit_list_get` → edit JSON → `edit_list_build` (new timeline, verified piece by piece) |
| **Transitions** | **Workaround** | `timeline_build_otio` with `transitions` (Cross Dissolve verified; needs media handles). Direct `AddTransition` exists only in 21.1 |
| **Speed changes / retime** | **Workaround** | `timeline_build_otio` piece `"speed"`: 200% and 50% verified (Resolve treats OTIO `source_range.duration` as *record* length). No speed ramps. Direct `SetSpeed` is 21.1-only |
| Gaps / precise placement | Workaround | OTIO builder inserts gaps; verified |
| Timeline markers via OTIO | Workaround | names/colors import; **marker notes are dropped by Resolve's OTIO import** |
| Fades (clip fade handles) | No (21.1-only API) | dissolve to/from a gap via OTIO, or UI |
| Output blanking | No (21.1-only API) | UI |
| Voice isolation (track/clip) | API, Studio | False on Free |
| Audio normalization, auto-align | No (21.1-only API) | UI |

### Timeline items (Inspector)
| Feature | Status | Notes / workaround |
|---|---|---|
| Pan, Tilt, ZoomX/Y, ZoomGang, Rotation, Anchor, Pitch, Yaw, Flip | API | read back per key; Zoom 1.0 = 100% |
| Crop L/R/T/B, softness, retain | API | |
| Composite mode, Opacity | API | constants resolved for comparison; out-of-range values are clipped → `ok:false` shows actual |
| Distortion, Retime process, Motion estimation, Scaling, Resize filter, Dynamic zoom ease | API | |
| Clip name, colour, enabled, flags, markers | API | verified |
| Takes (add/select/delete/finalize) | API | |
| Render cache (color/fusion output) | API | |
| **Keyframes** for zoom, position, rotation, anchor, opacity | **Workaround** | `keyframe_transform`: builds ClaudeTransform → ClaudeOpacity (Merge over transparent BG) in the clip's Fusion comp, keyframes it, commits it; values read back and opacity verified by pixels (40% → measured 0.399). Other Inspector keyframes (crop, composite…) → UI |
| Audio volume / pan / pitch per clip | No in 21.0 (21.1 adds them) | UI |
| Resolve FX / OpenFX effects on clips | No | UI |
| Stabilize, Magic Mask, Smart Reframe | API, mostly Studio | returns False on Free (reported honestly) |

### Color
| Feature | Status | Notes / workaround |
|---|---|---|
| Node graph read: count, labels, LUT per node, tools per node, cache mode | API | |
| Set LUT on node | API | verified with GetLUT (relative path). Copy .cube into the LUT folder + `lut_refresh` first |
| CDL (slope/offset/power/sat) | API (write-only) | no getter exists → `verified:null`; check via EDL+CDL export |
| Apply DRX grade (still) | API | |
| Node enable/disable | API (write-only) | no getter |
| Reset all grades | API | `confirm` |
| Copy grade to clips | API | compared via node count + LUTs |
| Color versions (local/remote): add/load/rename/delete/list | API | verified |
| Color groups: add/rename/delete/assign/remove, pre/post-clip graphs | API | verified |
| Export LUT from grade | API | **quirk**: only writes on the Color page and asynchronously; tool switches page and waits for the file |
| Primary wheels (lift/gamma/gain/offset), contrast, temperature/tint, curves (master/R/G/B), hue-vs-hue, hue-vs-sat, lum-vs-sat, saturation, soft HSL qualifiers | **Workaround** | `color_grade_lut`: the numeric grade is compiled into a 33-pt 3D LUT and set on a node (read back with GetLUT; graded frame verified by pixels). Global operations only, display-referred, replaces that node's LUT |
| Real Color-page grade: wheels (lift/gamma/gain/offset), saturation, contrast/pivot, **circle power window** (size/pan/tilt/aspect/rotate/soft/opacity) | **Workaround (generated DRX)** | `color_grade_drx`: the .drx is written offline by the samuelgursky advanced codec (Node, MIT), converted to the Resolve-21 window format by `server/drx_r21.py`, applied with `ApplyGradeFromDRX`, then read back from Resolve's own export of the applied grade (grab still → export .drx → decode). Window position/size/dimming verified by pixels in the tests. Replaces the item's node graph (single node); old grade kept as a backup version |
| Qualifiers (HSL/RGB/luma), HSL curves, custom curves, HDR zones, linear/gradient/polygon windows via generated DRX | **Partial** | written by the codec and accepted by Resolve, but **render not verified on 21.0.4** (only the circle window and the primaries are). Check with `frame_preview` |
| Tracking, adding nodes to an existing graph, node labels (set) | **No** | windows/masks also possible via Fusion comp (`fusion_tool` Ellipse/Rectangle masks); or UI fallback |
| Gallery: albums, stills, labels, import/export (dpx/tif/jpg/png/drx…), PowerGrade albums | API | export verified by files written |

### Fusion
| Feature | Status | Notes / workaround |
|---|---|---|
| Comps on a timeline item: add / import / export / load / rename / delete / list | API | verified via name list / file |
| Tools inside a comp: list, RegID, inputs & values, connections | Fusion object API | `fusion_comp_inspect` (outside the Resolve README) |
| Add tool, set/get input (optionally at a time = keyframe), connect/disconnect, delete | Fusion object API | `fusion_tool`; set_input read back |
| Viewer interaction, spline drawing, on-screen controls | No | UI |
| Making script edits render | Workaround | **measured**: some script-built comp graphs (seen when tools were added and wired in one go) are readable via GetInput/ExportFusionComp but are *not rendered* until the comp is re-imported. `fusion_comp commit` / `fusion_tool item=` export + `ImportFusionComp` (verified by rendering frames before/after); `keyframe_transform` always commits. Verify visually with `frame_preview` |

### Fairlight / audio
| Feature | Status | Notes / workaround |
|---|---|---|
| Audio tracks: add (with format), rename, enable, lock | API | |
| Insert audio at playhead (Fairlight page) | API | `api_call Project.InsertAudioToCurrentTrackAtPlayhead` |
| Fairlight presets | API | `api_call` |
| Audio mapping (read) | API | write is 21.1-only |
| Clip gain, volume automation (envelope), fades, peak/RMS normalisation, trim | **Workaround** | `audio_process`: offline on the clip's PCM WAV (stdlib), new file only, measured back (normalise exact; envelope within ~0.3 dB), then import or `ReplaceClip` (confirm + backup). WAV only |
| Mixer, faders, EQ, dynamics, buses, plug-ins, real-time automation lanes | **No** | UI (EQ/compression could be added offline with extra libraries) |

### Render / Deliver
| Feature | Status | Notes / workaround |
|---|---|---|
| Formats, codecs, resolutions | API | |
| Set format/codec, mode, settings (target dir, name, size, fps, audio, in/out…) | API | job read back from GetRenderJobList and compared |
| Add / delete / list / status jobs, start (`confirm`) / stop | API | tests never start a render |
| Render presets: load / save / delete / import / export; Quick Export | API | |

### State & safety (tool features)
| Feature | Status |
|---|---|
| `get_full_state` (depth 0–3, include filters, timeline filter, item paging) | done — saved as snapshots |
| `diff_state(before, after?)` | done — keyed by stable handles (GetUniqueId) |
| Read-back on every write: `{requested, actual, ok}` | done — 30+ setter/getter pairs, plus file/list checks |
| Confirm + .drp backup before destructive calls, dry-run | done |
| Operation log (JSONL) | `%LOCALAPPDATA%\ResolveMCP\oplog.jsonl` |
| Live method check vs README (`api_methods_live`) | done |

## Version-specific behaviours recorded on 21.0.4 Free
1. `AppendToTimeline` / `CreateTimelineFromClips`: `endFrame` exclusive.
2. `TimelineItem.GetSourceEndFrame`: exclusive for subclips, **inclusive** for full clips → always use `GetDuration`.
3. `MediaPool.ImportMedia([{"FilePath": plain_file}])` → `[]`; strings work; one bad entry fails the whole list.
4. `MediaPoolItem.SetMarkInOut(..., "all")` on an image sequence sets only the audio range.
5. Python proxies: `repr()` is generic; `str()` gives labels such as "Media pool item", "Timeline item", "Color Group".
6. `Timeline.Export(..., EXPORT_FCPXML_1_10)` writes a bundle folder with `Info.fcpxml` inside.
7. `ImportTimelineFromFile(fcpxml, {"importSourceClips": False})` returns None unless `sourceClipsFolders` is given.
8. `TimelineItem.ExportLUT` returns True but writes nothing outside the Color page; the file appears ~3 s later.
9. OTIO import: Resolve's dialect is Clip.2 + `media_references` (plain Windows paths, `ImageSequenceReference` for
   sequences); transitions import as items; `LinearTimeWarp.time_scalar` works with *record* duration in
   `source_range`; marker comments are dropped.
10. A newly created project is not listed in the project manager until saved once.
11. Methods present but undocumented: `Resolve.SetHighPriority`, `ProjectManager.GetProjectLastModifiedTime`.
12. `Project.GetSetting()` without a key returns all settings (used by snapshots).
13. Fusion comp of a timeline item: comp time 0 = first frame of the clip (also for subclips); MediaIn is processed at
    timeline resolution although `Comp.FrameFormat` reports the source size.
14. Script edits to a Fusion comp are not always rendered until the comp is exported and re-imported (see Fusion).
15. `ExportCurrentFrameAsStill` reflects grades and committed Fusion comps — used by `frame_preview` for visual checks.
16. `CreateTimelineFromClips` with the same media pool item several times creates only one item; append instead.

## Behaviours adopted from samuelgursky/davinci-resolve-mcp (MIT) — `src/utils/api_truth.py`
That project keeps a ledger of 121 live-measured API traps. The ones that affected this tool were checked here and
are now handled (each verified by `tests/run_tests.py`, 97/97 on 21.0.4.5 Free):

| Trap (theirs) | Confirmed here? | What this tool now does |
|---|---|---|
| `Project.GetCurrentRenderMode` switches the UI to the Deliver page | **yes** — `get_full_state` was moving the user to Deliver | snapshots and all render tools restore the user's page |
| `Composition.Lock()`: value writes inside Lock/Unlock read back but are not rendered | consistent with our Fusion renders | `keyframe_transform` no longer locks the comp; the export/import commit stays as a safety net |
| `TimelineItem.CopyGrades` replaces the target grade, returns True, makes no version | not re-measured (destructive) | `color_copy_grade` first adds a "Claude backup <time>" colour version on every target, then copies |
| Studio-gated calls on Free return False **and** open a modal that breaks later calls | not re-triggered on purpose | Studio/AI-listed methods are refused on Free unless `force=true` |
| `CreateProject`/`LoadProject` discard an unsaved current project | — | `project_create`/`project_open` save the current project first; refuse to drop "Untitled Project" without `confirm` |
| `SaveProject` on "Untitled Project" cannot succeed (blocks headless) | — | `project_save` and backups refuse it with a clear message |
| `ImportTimelineFromFile` returns None if `timelineName` already exists | — | imports pick a free name (`Name 2`, `Name 3`, ...) |
| `DuplicateTimeline` silently makes the duplicate current | **yes** | the user's current timeline is restored |
| `DeleteClips` is Edit-page-gated and does not delete linked audio | — | runs on the Edit page; linked items included by default |
| `AddVersion` / `ApplyGradeFromDRX` / `ExportLUT` are Color-page-gated | `ExportLUT` **yes** | colour tools switch to Color and back |
| `SetRenderSettings` with an empty `CustomName` rejects the whole payload | — | refused before calling Resolve |
| `GalleryStillAlbum.ExportStills` needs the Gallery panel visible | — | a False result explains it |
| OTIO `source_range` must use the media's own timecode origin | (our test media starts at 00:00:00:00) | the OTIO builder offsets by each clip's Start TC and reports offline items |
| `GetSourceStartFrame` can be one frame off; `GetLeftOffset` is exact | — | edit lists use `GetLeftOffset` |

## DRX grades on 21.0.4 (measured 2026-10-12)
* The advanced codec was calibrated on Resolve 19. On 21.0.4 a power window it writes (corrector type 4, only the
  non-neutral parameters) loads into the Window palette with correct numbers but **renders an empty mask**: the
  whole node's grade disappears from the frame. Resolve 21's own export stores the circle/linear transform as
  corrector type **65540** with the full parameter set (incl. `0x8850000D` = 288.0 and an identity 3×3 at
  `0x88500010`) and lists 65540 in the node's corrector-type list. `drx_r21.to_r21` rewrites the block into
  exactly that form → the window renders, matching a window drawn by hand in the UI pixel for pixel.
* Window **Size** scale: raw = 1 + (UI−50)×0.08 above 50 (codec is right), but below 50 Resolve uses
  raw ≈ UI/50 (panel: raw 0.2 → 9.92, 0.5 → 24.95). The codec's formula goes negative below 37.5 → Size 0.
* **Pan/Tilt** raw values are timeline pixels from the frame centre (tilt + = up); the panel shows 50 + raw/4096×50,
  so one panel unit ≈ 82 px on a 1920×1080 timeline and Pan 65 is already off-frame. `color_grade_drx` accepts
  `center_x`/`center_y` as frame fractions. A size-50 circle has a radius of ≈ 288 px.
* Gain wheel: r/g/b are colour **balance** (luma-renormalised): uniform r=g=b with master 1 is a no-op. Brightness
  rides on `gain.master` (samuelgursky's wire-semantics finding, re-confirmed here by pixels).
* In `space:"drx"` the codec's saturation default is 50 = 50× saturation; use the default `space:"ui"` (0–100).
