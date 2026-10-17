---
name: davinci-resolve
description: Edit, color grade and render in DaVinci Resolve (Free, 21.0.4, Windows) through the resolve-mcp MCP server, verifying every change. Use when the user asks to do anything in DaVinci Resolve - import media, build or cut timelines, trim, add transitions or speed changes, add markers, color grade (LUT, wheels, power windows), keyframe zoom/position/opacity, fix audio levels, render/export, or inspect or clean up a Resolve project. Also use when the user mentions Resolve, DaVinci, a .drp project, the Edit/Color/Fusion/Fairlight/Deliver page, or asks why a Resolve change "did not stick".
---

# DaVinci Resolve via resolve-mcp

This skill drives DaVinci Resolve through the **resolve-mcp** MCP server
(<https://github.com/justhung69/resolve-mcp>). The server talks to a bridge script running *inside* Resolve.
It works on **Resolve 21.0.4 Free on Windows**. Resolve 21.1+ Free removed Python scripting, so never tell the
user to update Resolve.

Tool names below are the server's tool names. Your client may show them with a prefix, for example
`mcp__DaVinci_Resolve__Free___resolve_status`.

## 0. Before anything: is the bridge up?
1. Call `resolve_status`.
2. If it reports `bridge: not running`, ask the user to open a project in Resolve and click
   **Workspace → Scripts → ClaudeBridge**. This is needed once per Resolve launch; there is no autostart.
   Then call `resolve_status` again.
3. If no resolve-mcp tools exist at all, the server is not installed. Point the user to the README:
   run `install.ps1`, then install the `.mcpb` Desktop Extension from the latest release.

## 1. The working loop (always)
1. **Snapshot**: `get_full_state` (depth 1–2 is enough for most tasks). Keep the returned snapshot id.
2. **Act** with the most specific typed tool (table below). Use `api_call` only for documented methods that
   no typed tool covers. `api_catalog` lists what exists. Never invent method names.
3. **Check every result's `ok`.** Writes return `{requested, actual, ok}`. `ok: false` means Resolve ignored
   the change even if it returned True. Read `notes`, fix the input, or explain the limitation.
   `verified: null` means Resolve offers no getter for that value. Say so rather than claiming success.
4. **Confirm**: `diff_state(before_id)` shows exactly what changed. For anything visual, call `frame_preview`
   and look at the image before telling the user it worked.

## 2. Which tool for which job
| Task | Tools |
|---|---|
| Project: list/open/create/save/export .drp | `project_list`, `project_open`, `project_create`, `project_save`, `project_export` |
| Import media, browse bins, clip metadata | `media_import`, `media_pool_list`, `media_folder`, `clip_inspect`, `clip_update`, `media_storage_browse` |
| Timelines: create, append, insert, tracks, playhead | `timeline_create`, `timeline_append`, `timeline_insert`, `timeline_track`, `timeline_playhead`, `timeline_inspect`, `timeline_list`, `timeline_set_current` |
| Duplicate, rename, delete timeline; grab still; scene cuts | `timeline_action` |
| Markers | `markers` |
| Inspector values (zoom, position, crop, opacity, composite) | `item_inspect`, `item_set_properties`, `item_update` |
| **Cut / razor** | `timeline_razor`. Builds a NEW timeline, the original is untouched |
| **Trim, ripple, slip, slide, reorder, gaps** | `edit_list_get` → edit the list → `edit_list_build` (new timeline) |
| **Transitions, speed changes** | `timeline_build_otio` (new timeline) |
| Color: LUT, CDL, DRX still, versions, groups, copy grade | `color_apply`, `color_versions`, `color_groups`, `color_copy_grade`, `color_node_graph`, `gallery` |
| Color: wheels, curves, hue/sat, qualifiers as a LUT | `color_grade_lut` |
| Color: real Color-page grade with a **circle power window** | `color_grade_drx` |
| Keyframed zoom / position / rotation / opacity | `keyframe_transform` (via Fusion, verified by pixels) |
| Fusion comps and tools | `fusion_comp`, `fusion_comp_inspect`, `fusion_tool` |
| Audio gain, fades, volume envelope, normalize | `audio_process` (offline on the WAV, then import or replace) |
| Render / export | `render_presets`, `render_options`, `render_add_job`, `render_jobs`, `render_quick_export` |
| Interchange (EDL, FCPXML, OTIO, DRT, AAF) | `timeline_export`, `timeline_import` |
| What can't the API do? | `ui_fallback_guide` gives click-by-click UI steps for the user |

## 3. Rules that prevent damage
- **Destructive operations need `confirm=true`**: deleting projects, timelines, clips or stills, overwriting,
  and resetting grades. Ask the user first and pass `confirm` only after they agree. The server makes a
  `.drp` backup before destructive batch operations.
- Prefer `dry_run=true` first for batch edits and show the user the plan.
- Workarounds (razor, trims, transitions, speed) **create a new timeline**. Tell the user its name. Never
  delete their original timeline to "clean up".
- `color_grade_drx` and `color_copy_grade` **replace** the clip's grade. The previous grade is kept as a
  `Claude backup <time>` color version, so tell the user it exists.
- Do not enable `python_exec`. It is off by design (`allow_exec` in config).

## 4. Color grading tips
- Look first: `frame_preview` before and after.
- `color_grade_drx` takes panel numbers.
  - Brightness rides on **`gain.master`**. Gain r/g/b only change balance; uniform r=g=b does nothing.
  - Saturation is 0–100, with 50 neutral.
  - Place windows with **`center_x` / `center_y`** (frame fractions 0–1, y down) rather than pan/tilt:
    one pan unit ≈ 82 px, so pan 65 is already off-frame.
  - A size-50 circle has a radius of about 288 px; `soft1` softens the edge.
- Only circle windows and primaries are render-verified. Qualifiers, HSL curves, linear, gradient and polygon
  windows are written but unverified. Check with `frame_preview` and tell the user if they did not render.
- `color_grade_lut` is global, applies to one node and is display-referred. It suits looks (contrast, curves,
  hue-vs-sat, skin protection), but cannot mask a region.

## 5. Known limits (say so instead of trying)
These have no API on Resolve Free: tracking, adding nodes to an existing grade, OpenFX parameters,
Fairlight mixer/automation, and Studio-only AI tools (Magic Mask, Smart Reframe, voice isolation…).
Offer `ui_fallback_guide` steps or a workaround (Fusion masks, offline audio).

Version quirks are recorded in the repo's `COVERAGE.md`. Examples:
- `AppendToTimeline` end frames are exclusive.
- ExportLUT only works on the Color page.
- New projects are not listed until saved.

## 6. Troubleshooting
| Symptom | Fix |
|---|---|
| `bridge not running` / `token mismatch` | Click Workspace → Scripts → ClaudeBridge again, then retry |
| `frame_preview` fails | The Resolve window is minimized. Ask the user to restore it |
| A grade or Fusion change does not show in renders | Re-apply through the typed tool (it commits Fusion comps); verify with `frame_preview` |
| A Studio-only tool is refused | Expected on Free. Explain, offer a UI or manual alternative |
