# Measured quirks on DaVinci Resolve 21.0.4 Free

These were measured on 21.0.4 Free; the tools already handle them. Knowing them helps explain odd results to
the user.

| Area | Quirk | What the tools do / what you should do |
|---|---|---|
| Timelines | `AppendToTimeline` end frames are **exclusive** | Edit lists use exclusive ends; don't "fix" an off-by-one |
| Timelines | `GetSourceEndFrame` is inclusive for full clips, exclusive for subclips | Use durations from `timeline_inspect` |
| Media | Importing with the `{"FilePath": ...}` dict form returns nothing for plain files | `media_import` imports file by file, in order |
| Media | Mark in/out "all" on an image sequence only sets the audio range | Set video and audio separately |
| Projects | A new project is not listed until it is saved | `project_create` saves it |
| Pages | The playhead API only works on Cut/Edit/Color/Fairlight/Deliver | Tools switch page and switch back |
| Render | Reading the render mode jumps to the Deliver page | Snapshots restore the user's page |
| Color | ExportLUT only writes on the Color page, asynchronously | `color_export_lut` waits for the file |
| Color | Generated DRX power windows from older codecs render an **empty mask** on 21.0.4 | `color_grade_drx` converts them to Resolve 21's format |
| Color | Window size below 50 is ≈ UI/50 internally; pan/tilt are timeline pixels (1 panel unit ≈ 82 px) | Use `center_x`/`center_y` |
| Color | Gain r/g/b only change balance; brightness is `gain.master` | Put exposure changes on `gain.master` |
| Fusion | Script edits sometimes don't render until the comp is exported and re-imported | Typed Fusion tools "commit" automatically |
| Fusion | Fusion works at timeline resolution; comp time 0 is the clip's first frame | Keyframes are relative to the clip |
| Interchange | FCPXML 1.10 exports as a bundle folder | `timeline_export` returns the folder |
| Interchange | OTIO import drops marker notes; speed uses the record duration | Add notes afterwards with `markers` on the new timeline |
| Preview | `frame_preview` fails if the Resolve window is minimized | Ask the user to restore the window |
| Edition | Studio-only tools (Magic Mask, Smart Reframe, …) show a blocking dialog on Free | They are refused before calling Resolve |
