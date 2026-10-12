# UI fallback (computer use) — last resort

Use this only for things the scripting API cannot reach (see COVERAGE.md, "not possible via API").
It is slow and less reliable than the API: every step needs a screenshot, and layouts differ per screen.

## Rules

1. **Before**: `get_full_state` (save the snapshot id). Put Resolve on the right page with `open_page`, the right
   timeline with `timeline_set_current`, and the playhead with `timeline_playhead` — use the API for all
   positioning, then do only the irreducible UI step by hand.
2. **During**: one action, one screenshot. Never type into a field you have not just seen focused.
3. **After**: `diff_state(snapshot_id)` to confirm the UI action changed exactly what was intended.
   UI-only changes that the API cannot read (wheels, curves, OpenFX, Fairlight faders) must be confirmed visually.
4. Shortcuts below are Resolve's **default** keyboard preset. The user may have changed them: check
   *DaVinci Resolve > Keyboard Customization* (Ctrl+Alt+K) or `api_call(resolve, ...)` is not available for
   keyboard presets in 21.0 — if a shortcut does nothing, look it up there. Entries marked (verify) are less certain.
5. Click inside the Resolve window first so keystrokes reach it; Resolve ignores keys while a text field has focus.

## Pages

| Page | Shortcut | API alternative |
|---|---|---|
| Media | Shift+2 | `open_page("media")` |
| Cut | Shift+3 | `open_page("cut")` |
| Edit | Shift+4 | `open_page("edit")` |
| Fusion | Shift+5 | `open_page("fusion")` |
| Color | Shift+6 | `open_page("color")` |
| Fairlight | Shift+7 | `open_page("fairlight")` |
| Deliver | Shift+8 | `open_page("deliver")` |

## Edit page — editing

| Action | Shortcut | API alternative |
|---|---|---|
| Play / stop | Space | — |
| Shuttle reverse / stop / forward | J / K / L | — |
| Step one frame | Left / Right arrow | `timeline_playhead` |
| Previous / next edit point | Up / Down arrow | `timeline_playhead` + `timeline_inspect` |
| Mark in / out | I / O | `timeline_action set_mark_in_out` |
| Clear in and out | Alt+X | `timeline_action clear_mark_in_out` |
| Selection mode | A | — |
| Trim edit mode | T | — |
| Blade (razor) mode | B | — |
| **Split clip at playhead** | Ctrl+B (also Ctrl+\\) | `timeline_razor` (rebuilds a new timeline) |
| Delete (lift, leaves gap) | Delete | `timeline_action delete_items` |
| Ripple delete | Shift+Delete / Backspace (verify) | `timeline_action delete_items` with `{"ripple": true}` |
| Nudge selected clip/edit 1 frame | , / . | edit list (`edit_list_get` / `edit_list_build`) |
| Nudge 5 frames (verify) | Shift+, / Shift+. | edit list |
| Add default transition at selected edit | Ctrl+T | `timeline_build_otio` with `transitions` |
| Video-only / audio-only transition (verify) | Alt+T / Shift+T | `timeline_build_otio` |
| Add marker | M | `markers add` |
| Add + edit marker | Ctrl+M (verify) | `markers add` |
| Retime controls | Ctrl+R | `timeline_build_otio` piece `speed` |
| Linked selection on/off | Ctrl+Shift+L | `timeline_action link/unlink` |
| Snapping on/off | N | — |
| Zoom timeline in / out | Ctrl+= / Ctrl+- | — |
| Zoom to fit (verify) | Shift+Z | — |
| Undo / redo | Ctrl+Z / Ctrl+Shift+Z | — (there is no undo API; restore the .drp backup instead) |
| Save project | Ctrl+S | `project_save` |

## Color page — grading the API cannot do

The API can only: set/read LUTs per node, apply CDL, apply a DRX still, copy grades, enable/disable a node,
manage versions and color groups, reset grades, export a LUT. **Try `color_grade_lut` first**: wheels, curves,
hue/sat curves and soft qualifiers are compiled into a LUT. **Power windows, tracking, node creation and node labels
remain UI-only** (or Fusion masks).

| Action | Shortcut |
|---|---|
| Add serial node after current | Alt+S |
| Add parallel node | Alt+P |
| Add layer node | Alt+L |
| Enable / disable current node | Ctrl+D |
| Bypass all grades (verify) | Shift+D |
| Previous / next clip | Up / Down arrow |
| Grab still | Ctrl+Alt+G (verify) — API: `timeline_action grab_still` |
| Apply grade from still | middle-click the still in the gallery (verify) — API: `color_apply drx=` |

Preferred non-UI route for a "look": grade one clip by hand once, `gallery export fmt="drx"`, then apply the DRX
to many clips with `color_apply(items=[...], drx=...)`.

## Inspector (Edit page, top right)

Transform/crop/composite/retime-scaling values are fully settable through `item_set_properties`. Keyframed zoom,
position, rotation, anchor and opacity: use `keyframe_transform` (Fusion). Use the UI only for keyframes of other
properties (crop, composite…) and for parameters of Resolve FX/OpenFX effects (no API).

## Fusion page

Tools, inputs and connections are reachable through `fusion_tool` / `fusion_comp_inspect` (Fusion object API).
Use the UI for: viewer interaction, spline editing, and anything a tool exposes only as an on-screen control.

## Fairlight page

No mixer/fader/EQ/dynamics/automation API exists. For gain, volume automation, fades and normalisation use
`audio_process` (offline on the WAV) first. Clip volume, pan and pitch are not exposed in 21.0's
`TimelineItem` properties either. Use the UI: select clip, adjust in Inspector (Volume) or the Mixer.
Voice isolation per track/clip *is* in the API (`timeline_track voice_isolation`, `item_update voice_isolation`),
but it is a Studio feature.

## Deliver page

Fully scriptable (`render_add_job`, `render_jobs`, `render_presets`). Use the UI only to inspect render settings
that `SetRenderSettings` does not cover (e.g. advanced codec options), then save them as a render preset and
load the preset by name from the API.
