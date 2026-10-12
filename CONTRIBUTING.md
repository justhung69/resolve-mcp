# Contributing

Thanks for helping! Bug reports, measured Resolve quirks, workarounds and docs fixes are all welcome.

## Ground rules of this project
These are what make the tool trustworthy. Please keep them in any change:

1. **Never guess API names.** The only source of truth is the Scripting README shipped with Resolve
   (`C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\README.txt`).
   `server/catalog.py` parses it at runtime. If you need a method that is not there, it does not exist.
2. **Every write reads back.** A write tool returns `{requested, actual, ok}`. If Resolve has no getter for the
   value, say so in `notes` and use `verified: null` instead of pretending.
3. **Measure, don't assume.** New behaviour claims (quirks, scales, formats) need a test or a measurement on a
   real Resolve, ideally with pixels (`tests/pngstat.py`, `frame_preview`). Record them in `COVERAGE.md`
   with the Resolve version.
4. **Safety first.** Anything that deletes or overwrites needs `confirm=true` and goes through `core.call`,
   which makes the `.drp` backup. Batch edits should support `dry_run`. Never touch the user's original
   timeline in a workaround: build a new one.
5. **Local only.** No cloud calls, no telemetry, no new network listeners. New dependencies need a good reason.
6. **Be honest in `COVERAGE.md`.** "Workaround" and "No" are fine answers. "Partial / not verified" is better
   than an untested "Yes".

## Development setup
1. Windows 11 + DaVinci Resolve **21.0.4** Free (Resolve 21.1+ Free has no Python scripting).
2. `.\install.ps1` (creates `.venv`, installs `mcp==2.3.0`, copies the bridge scripts).
3. In Resolve: open any project, **Workspace → Scripts → ClaudeBridge**.
4. After editing `bridge/bridge_core.py`, copy it to `%LOCALAPPDATA%\ResolveMCP\` and hot-reload with the
   bridge op `reload` (no Resolve restart needed). Changes to `ClaudeBridge.py` need the script restarted.
5. Optional for `color_grade_drx`: Node.js and
   [samuelgursky/davinci-resolve-mcp](https://github.com/samuelgursky/davinci-resolve-mcp) cloned next to this
   repo as `resolve-mcp-advanced` (`npm install` in `resolve-advanced`).

## Tests
```
.venv\Scripts\python.exe tests\run_tests.py
```
The suite creates its own throwaway project `ClaudeMCP_Test_<timestamp>` with generated media, never opens or
changes your other projects, and prints a PASS/FAIL table. **All tests must pass** before a pull request.
Add a test for every new tool or workaround, using the `check(group, name, fn)` helper. Pixel-check visual
results instead of trusting the API's return value.

Regenerate the method coverage table after catalog changes:
```
.venv\Scripts\python.exe tools\gen_coverage.py
```

## Pull requests
- One topic per PR, with a short description of **what you measured** and on which Resolve version.
- Update `COVERAGE.md` (and `README.md` in both English and Vietnamese if user-facing behaviour changes).
- Match the surrounding code style: small helpers, plain Python standard library where possible.
- Do not commit local output (`tests/last_results.json`, renders, `.drp` backups, tokens).

## Security issues
Do **not** open a public issue. See [SECURITY.md](SECURITY.md).

---

### Tiếng Việt (tóm tắt)
- **Không đoán tên API**: chỉ dùng hàm có trong README Scripting của Resolve.
- **Mọi lệnh ghi phải đọc lại**: trả về `{requested, actual, ok}`.
- **Đo trên Resolve thật** trước khi khẳng định điều gì, nên kiểm tra bằng điểm ảnh. Ghi kết quả vào `COVERAGE.md`.
- **An toàn**: xoá hoặc ghi đè phải có `confirm=true` và backup `.drp`. Không sửa timeline gốc.
- **Không gửi gì lên mạng.**
- Chạy `tests\run_tests.py`, tất cả phải đạt trước khi gửi PR.
- Có thay đổi người dùng thấy được thì cập nhật README cả hai thứ tiếng.
