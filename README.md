# DaVinci Resolve MCP (works on Resolve **Free**)

[![Release](https://img.shields.io/github/v/release/justhung69/resolve-mcp)](https://github.com/justhung69/resolve-mcp/releases/latest)
[![License: MIT](https://img.shields.io/github/license/justhung69/resolve-mcp)](LICENSE)
![DaVinci Resolve](https://img.shields.io/badge/DaVinci%20Resolve-21.0.4%20Free-233A51)
![Platform](https://img.shields.io/badge/platform-Windows%2011-0078D6)
![Python](https://img.shields.io/badge/python-3.14-3776AB)
![MCP](https://img.shields.io/badge/MCP%20SDK-2.3.0-6E56CF)
[![Local tests](https://img.shields.io/badge/local%20tests-99%2F99%20passing-brightgreen)](#tests)
[![Coverage matrix](https://img.shields.io/badge/coverage-honest%20matrix-blue)](COVERAGE.md)

Let Claude (Claude Desktop / Claude Code) control DaVinci Resolve through the official scripting API, verify every
change by reading it back, and always know the real state of the project.

**AI video editing for DaVinci Resolve Free**: automate editing, color grading (including power windows) and
rendering in DaVinci Resolve 21.0.4 Free with Claude AI through the Model Context Protocol (MCP).
Website: <https://justhung69.github.io/resolve-mcp/>

*Tiếng Việt ở phía dưới.*

---

## English

### How it works
Resolve **Free** blocks scripts that run outside Resolve, but it still runs Python scripts started from its own
**Workspace > Scripts** menu. So there are two parts:

```
Claude ──MCP (stdio)──> server/mcp_server.py ──JSON over 127.0.0.1:47750──> ClaudeBridge.py (inside Resolve) ──> Resolve API
```

* `bridge/ClaudeBridge.py` – you start it once per Resolve session from the Scripts menu. It never returns: it
  serves a loopback-only socket protected by a random token. Its logic lives in `bridge_core.py`, which can be
  hot-reloaded (`bridge_reload`) without clicking again.
* `server/mcp_server.py` – the MCP server Claude talks to (75 tools).

> **Version note (measured):** this needs **Resolve 21.0.x Free** (tested on 21.0.4.5) or any Studio version.
> Resolve **21.1+ Free** removed Python from the Scripts menu, so the bridge cannot start there. Keep
> *Preferences > System > General > Automatically check for updates* **off**.

### Install (one time)
1. Install DaVinci Resolve 21.0.x (Free) and 64-bit Python 3.10+ from python.org.
2. In this folder run:
   ```
   powershell -ExecutionPolicy Bypass -File .\install.ps1
   ```
   It creates `.venv`, installs the `mcp` package (asks first), copies the bridge into Resolve's Scripts folder,
   and registers the server in Claude Desktop (backing up your config first). It prints the `claude mcp add ...`
   command for Claude Code.
3. **Recommended: install the Desktop Extension** (survives Claude Desktop rewriting its config file, and is the
   way to reach Cowork): download `resolve-mcp-<version>.mcpb` from the
   [latest release](https://github.com/justhung69/resolve-mcp/releases/latest), then in Claude Desktop open
   **Settings → Extensions → Advanced settings → Install Extension…**, pick the file, and set *resolve-mcp folder* to
   this folder. (Build it yourself with `.venv\Scripts\python.exe tools\build_mcpb.py`.)
   If you keep the config-file entry from step 2 as well, remove one of them so the tools are not listed twice.
4. Restart Claude Desktop.

> **Note:** Claude Desktop rewrites `claude_desktop_config.json` from memory when it saves settings, so entries
> added while the app is running can disappear. Edit the file only with Claude Desktop fully closed, or use the
> extension. **Cowork** runs in a VM; whether it receives local extensions depends on the Claude Desktop version
> (a forwarding bug was reported in anthropics/claude-code#26259). The server itself must run on Windows, next to
> Resolve.

### Every session
1. Open Resolve and a project.
2. Click **Workspace > Scripts > ClaudeBridge**. (Stop it with *ClaudeBridge_Stop*, or just quit Resolve.)
3. Ask Claude. Start with: *"Check the Resolve status and give me a summary of the project."*

### What Claude can do
* Projects, settings, pages, databases, media pool (import, bins, metadata, markers, flags, relink, proxies).
* Timelines: create/append with exact frames, tracks, markers, playhead, titles/generators, import/export
  (EDL, FCPXML, AAF, OTIO, DRT, CSV, …), compound/Fusion clips, delete/ripple delete.
* Timeline items: every Inspector property the API exposes (transform, crop, composite, opacity, retime/scaling),
  names, colors, enable, flags, takes.
* Color: LUT per node, CDL, DRX grades, copy grades, versions, color groups, LUT export, gallery stills.
* Fusion: comps on clips, add tools, set/read inputs (incl. keyframes at a time), connect tools.
* Render: formats/codecs, settings, add jobs (read back), presets; starting a render needs `confirm=true`.
* **Workarounds** for what the API cannot do: razor/split, trims (ripple/roll/slip/slide), transitions, speed
  changes — by building a *new* timeline from an edited list or an OTIO file. The original is never modified.
* **Beyond the API**: colour wheels/curves/qualifiers compiled into a LUT (`color_grade_lut`), keyframed zoom/position/
  rotation/opacity through Fusion (`keyframe_transform`), offline audio gain/automation/fades/normalise
  (`audio_process`), and `frame_preview` so Claude can *look* at the result.
* **Real Color-page grades with power windows** (`color_grade_drx`): wheels/saturation/contrast plus circle windows,
  written offline as a .drx by the optional [samuelgursky advanced codec](https://github.com/samuelgursky/davinci-resolve-mcp)
  (Node, MIT), fixed up for Resolve 21's window format, applied, and read back from Resolve's own export. Needs Node and
  that repo cloned next to this one (`..
esolve-mcp-advanced`, `npm install` in `resolve-advanced`) or
  `RESOLVE_MCP_ADVANCED` set. Its own MCP server can be registered too (`davinci-resolve-advanced`), but use
  `color_grade_drx` for windows: its raw windows do not render on 21.0.4 (see COVERAGE.md).
* **No autostart**: Resolve 21.0.4 does not run scripts from `Fusion\Scripts\Start` (tested), so the bridge is
  started from the menu after every Resolve launch.
* **State**: `get_full_state` (saved snapshots) and `diff_state` to see exactly what changed.
* See [COVERAGE.md](COVERAGE.md) for the honest yes/no/workaround matrix and the quirks measured on 21.0.4.

### Comparison with other tools
Based on reviewing the repos in October 2026 (Tooflex only from its directory listings).

| | **resolve-mcp** (this) | [samuelgursky](https://github.com/samuelgursky/davinci-resolve-mcp) | [kerwilgil](https://github.com/kerwilgil/davinci-resolve-mcp) | Tooflex | Computer-use (clicking the UI) |
|---|---|---|---|---|---|
| Runs on Resolve **Free** | ✅ 21.0.4, in-app bridge | ✅ has a Free bridge | ✅ | ❌ needs Studio | ✅ any version |
| API coverage | All 381 live methods via `api_call` + 75 tools | Very broad | Medium | Basic (project, timeline, media, color) | Anything visible on screen |
| Read-back after every write | ✅ | Partial (large list of measured API traps) | ❌ | ❌ | Needs screenshots |
| Pixel verification | ✅ `frame_preview` + pixel tests | Scope reads for grades | ❌ | ❌ | Looks at screenshots |
| State snapshots / diff / op log | ✅ | Partial | ❌ | ❌ | ❌ |
| Safety: confirm for destructive ops, .drp backup, dry-run | ✅ | Partial | ❌ bridge has no auth + CORS `*` | Unknown | Risk of misclicks |
| Power windows / qualifiers | ✅ circle window render-verified on 21.0.4 | ✅ very complete DRX writer, but windows do not render on 21.0.4 as written | ❌ | ❌ | ✅ by hand, slow |
| Workarounds for API gaps (razor, transitions, speed, keyframes, audio) | ✅ verified | ✅ many | Few | ❌ | ✅ slow, error-prone |
| Speed | Fast (~2 ms per call) | Fast | Fast | Fast | Very slow |

### Safety
* Every write tool reads the value back and returns `{requested, actual, ok}`. `ok:false` means Resolve ignored
  the change even if it said "True".
* Deleting / overwriting needs `confirm=true`; the project is saved and exported as `.drp` to
  `%LOCALAPPDATA%\ResolveMCP\backups` first. `dry_run=true` previews.
* Every call is logged to `%LOCALAPPDATA%\ResolveMCP\oplog.jsonl`.
* Socket listens on 127.0.0.1 only, with a per-session token. No cloud calls, no telemetry.
* `python_exec` (raw Python inside Resolve) is **off** unless you set `"allow_exec": true` in
  `%LOCALAPPDATA%\ResolveMCP\config.json`.
* Found a security problem? See [SECURITY.md](SECURITY.md) - please report it privately.
* Want to help? See [CONTRIBUTING.md](CONTRIBUTING.md).

### Tests
```
.venv\Scripts\python.exe tests\run_tests.py
```
Creates a throwaway project `ClaudeMCP_Test_<time>`, generates its own media, exercises every tool group through
the MCP layer, never starts a render, and prints a PASS/FAIL table (last run: **99/99 passed** on 21.0.4.5 Free,
including pixel checks of graded and keyframed frames).
`tools\gen_coverage.py` regenerates the per-method coverage table against your live Resolve.

### Troubleshooting
* *"ClaudeBridge is not running"* → click Workspace > Scripts > ClaudeBridge (a project must be open).
* *ClaudeBridge not in the Scripts menu* → re-run `install.ps1`; Python must be installed for all users / on PATH
  (Resolve picks the system Python itself).
* *Calls time out* → a modal dialog may be open in Resolve, or a long render is running.
* Bridge log: `%LOCALAPPDATA%\ResolveMCP\bridge.log`.

---

## Tiếng Việt

**Dựng phim bằng AI trên DaVinci Resolve bản Free**: dùng Claude AI để tự động cắt ghép, chỉnh màu (có power window) và
xuất video trong DaVinci Resolve 21.0.4 Free, qua giao thức MCP.

### Cách hoạt động
Bản Resolve **Free** chặn script chạy từ bên ngoài, nhưng vẫn cho chạy script Python khi bấm từ menu
**Workspace > Scripts** của Resolve. Vì vậy công cụ có 2 phần:

* `ClaudeBridge.py` – chạy **bên trong** Resolve (bạn bấm 1 lần mỗi khi mở Resolve). Nó mở một cổng chỉ dành cho
  máy của bạn (127.0.0.1, có mã bảo vệ) để nhận lệnh.
* `server/mcp_server.py` – máy chủ MCP mà Claude kết nối tới (75 công cụ).

> **Lưu ý phiên bản (đã kiểm tra thực tế):** cần **Resolve 21.0.x Free** (đã test 21.0.4.5) hoặc bản Studio.
> Resolve **21.1 Free trở lên đã bỏ Python** khỏi menu Scripts nên không dùng được. Hãy **tắt** tự động cập nhật
> trong *Preferences > System > General*.

### Cài đặt (một lần)
1. Cài DaVinci Resolve 21.0.x (Free) và Python 3.10+ 64-bit từ python.org.
2. Trong thư mục này, chạy:
   ```
   powershell -ExecutionPolicy Bypass -File .\install.ps1
   ```
   Script sẽ: tạo `.venv`, cài gói `mcp` (có hỏi trước), chép bridge vào thư mục Scripts của Resolve, đăng ký
   server vào Claude Desktop (**sao lưu file cấu hình trước khi sửa**), và in lệnh `claude mcp add ...` cho Claude Code.
3. **Khuyên dùng: cài Desktop Extension.** Cách này không bị mất khi Claude Desktop ghi đè file cấu hình, và
   là cách để dùng được trong Cowork:
   1. Tải `resolve-mcp-<version>.mcpb` ở [bản phát hành mới nhất](https://github.com/justhung69/resolve-mcp/releases/latest).
   2. Trong Claude Desktop, mở **Settings → Extensions → Advanced settings → Install Extension…**, chọn file vừa tải.
   3. Đặt *resolve-mcp folder* là thư mục này.

   Nếu vẫn giữ mục cấu hình ở bước 2 thì xoá một trong hai, để tool không bị hiện hai lần.
4. Khởi động lại Claude Desktop.

> **Lưu ý:** Claude Desktop ghi lại file `claude_desktop_config.json` từ bộ nhớ mỗi khi lưu cài đặt. Vì vậy mục nào
> được thêm trong lúc app đang chạy có thể bị mất. Chỉ sửa file khi đã thoát hẳn Claude Desktop, hoặc dùng extension.
> **Cowork** chạy trong máy ảo, nên có nhận extension cục bộ hay không còn tuỳ phiên bản Claude Desktop. Server vẫn
> phải chạy trên Windows, cùng máy với Resolve.

### Mỗi lần làm việc
1. Mở Resolve và mở một project.
2. Bấm **Workspace > Scripts > ClaudeBridge**. (Tắt bằng *ClaudeBridge_Stop* hoặc thoát Resolve.)
3. Nhắn Claude, ví dụ: *"Kiểm tra trạng thái Resolve và tóm tắt project cho mình."*

### Claude làm được gì
* Project, cài đặt, chuyển trang, media pool (nhập file, bin, metadata, marker, cờ, relink, proxy).
* Timeline: tạo/nối clip theo đúng frame, track, marker, playhead, title/generator, nhập/xuất EDL, FCPXML, AAF,
  OTIO, DRT…, xóa clip (kể cả ripple).
* Thuộc tính clip trên timeline (transform, crop, composite, opacity, retime/scaling), tên, màu, bật/tắt, cờ.
* Màu: LUT theo node, CDL, áp grade DRX, copy grade, version, color group, xuất LUT, still trong gallery.
* Fusion: comp trên clip, thêm tool, đặt/đọc thông số (kể cả keyframe), nối tool.
* Render: định dạng/codec, thiết lập, thêm job (có đọc lại để kiểm tra), preset. Bắt đầu render cần `confirm=true`.
* **Cách thay thế** cho những gì API không làm được: cắt (razor), trim (ripple/roll/slip/slide), transition, đổi
  tốc độ — bằng cách dựng **timeline mới** từ danh sách đã chỉnh hoặc file OTIO. Timeline gốc không bị đụng tới.
* **Vượt giới hạn API**: bánh xe màu/curves/qualifier được tính thành LUT (`color_grade_lut`); keyframe zoom/vị trí/
  xoay/độ mờ qua Fusion (`keyframe_transform`); xử lý âm thanh offline: gain, đường âm lượng, fade, normalize
  (`audio_process`); và `frame_preview` để Claude tự *nhìn* kết quả.
* **Grade thật trên trang Color, có power window** (`color_grade_drx`): bánh xe màu/saturation/contrast và cửa sổ tròn,
  tạo file .drx offline bằng bộ codec "advanced" của samuelgursky (Node, MIT, tuỳ chọn), sửa sang định dạng window của
  Resolve 21, áp dụng rồi đọc lại từ chính file Resolve xuất ra. Cần Node và repo đó nằm cạnh repo này
  (`..
esolve-mcp-advanced`, chạy `npm install` trong `resolve-advanced`) hoặc đặt biến `RESOLVE_MCP_ADVANCED`.
* **Không tự khởi động**: Resolve 21.0.4 không chạy script trong `Fusion\Scripts\Start` (đã thử), nên sau mỗi lần mở
  Resolve cần bấm menu để bật bridge.
* **Trạng thái thật**: `get_full_state` (lưu snapshot) và `diff_state` để thấy chính xác cái gì đã thay đổi.
* Bảng đầy đủ *làm được / cách thay thế / không thể* nằm trong [COVERAGE.md](COVERAGE.md).

### So sánh với các tool khác
Dựa trên lần xem các repo vào tháng 10/2026 (Tooflex chỉ dựa trên trang danh mục).

| | **resolve-mcp** (repo này) | [samuelgursky](https://github.com/samuelgursky/davinci-resolve-mcp) | [kerwilgil](https://github.com/kerwilgil/davinci-resolve-mcp) | Tooflex | Computer-use (bấm giao diện) |
|---|---|---|---|---|---|
| Chạy trên Resolve **Free** | ✅ 21.0.4, bridge trong app | ✅ có bridge cho Free | ✅ | ❌ cần Studio | ✅ mọi bản |
| Độ phủ API | Toàn bộ 381 hàm qua `api_call` + 75 tool | Rất rộng | Trung bình | Cơ bản | Mọi thứ thấy trên màn hình |
| Đọc lại sau mỗi lệnh ghi | ✅ | Một phần | ❌ | ❌ | Phải chụp màn hình |
| Kiểm tra bằng điểm ảnh | ✅ | Đọc scope khi grade | ❌ | ❌ | Nhìn ảnh chụp |
| Snapshot / diff / nhật ký | ✅ | Một phần | ❌ | ❌ | ❌ |
| An toàn (xác nhận khi xoá, backup .drp, dry-run) | ✅ | Một phần | ❌ cổng không xác thực + CORS `*` | Không rõ | Dễ bấm nhầm |
| Power window / qualifier | ✅ cửa sổ tròn đã kiểm chứng render trên 21.0.4 | ✅ viết DRX rất đầy đủ, nhưng cửa sổ không render trên 21.0.4 | ❌ | ❌ | ✅ làm tay, chậm |
| Workaround cho chỗ API thiếu | ✅ có kiểm chứng | ✅ nhiều | Ít | ❌ | ✅ chậm, dễ lỗi |
| Tốc độ | Nhanh (~2 ms/lệnh) | Nhanh | Nhanh | Nhanh | Rất chậm |

### An toàn
* Mọi lệnh ghi đều **đọc lại** giá trị và trả về `{requested, actual, ok}`. `ok:false` nghĩa là Resolve không áp
  dụng thay đổi dù báo "True".
* Xóa/ghi đè cần `confirm=true`; project được lưu và xuất `.drp` vào `%LOCALAPPDATA%\ResolveMCP\backups` trước.
  `dry_run=true` để xem trước.
* Mọi lệnh được ghi nhật ký vào `%LOCALAPPDATA%\ResolveMCP\oplog.jsonl`.
* Chỉ mở cổng trên 127.0.0.1, không gửi gì lên mạng, không telemetry.
* `python_exec` (chạy Python tùy ý trong Resolve) **tắt** mặc định.
* Phát hiện lỗ hổng bảo mật? Xem [SECURITY.md](SECURITY.md) - vui lòng báo riêng, không mở issue công khai.
* Muốn đóng góp? Xem [CONTRIBUTING.md](CONTRIBUTING.md).

### Kiểm thử
```
.venv\Scripts\python.exe tests\run_tests.py
```
Tạo project thử `ClaudeMCP_Test_<giờ>`, tự tạo media, chạy thử từng nhóm công cụ, **không** render, in bảng
PASS/FAIL (lần chạy gần nhất: **99/99 đạt** trên 21.0.4.5 Free, có kiểm tra điểm ảnh của khung đã grade/keyframe).

### Sự cố thường gặp
* *"ClaudeBridge is not running"* → bấm Workspace > Scripts > ClaudeBridge (phải đang mở project).
* *Không thấy ClaudeBridge trong menu Scripts* → chạy lại `install.ps1`; Python phải được cài cho máy (có trong PATH).
* *Lệnh bị quá thời gian* → Resolve đang mở hộp thoại hoặc đang render.
* Nhật ký bridge: `%LOCALAPPDATA%\ResolveMCP\bridge.log`.

Câu lệnh mẫu: [examples/PROMPTS.md](examples/PROMPTS.md).
