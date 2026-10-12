# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 1.0.x (latest release) | ✅ |
| older commits | ❌ |

The project targets **DaVinci Resolve 21.0.4 Free on Windows**. Problems that only appear on other Resolve
versions are still worth reporting, but fixes are only tested on 21.0.4.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately through GitHub: **Security → Report a vulnerability** on this repository
(<https://github.com/justhung69/resolve-mcp/security/advisories/new>).

Please include:
- what an attacker can do and under which conditions (local user, web page, another process…);
- steps to reproduce, and the Resolve and Windows versions you used;
- whether the issue is already public.

You can expect a first reply within **7 days**. If the report is confirmed, a fix or mitigation is planned
together with you, and you are credited in the advisory unless you prefer otherwise.

## Security model (what is in scope)

The MCP server lets an AI assistant drive Resolve, so the boundaries matter:

- **Bridge socket**: the in-Resolve script listens on `127.0.0.1` only and every request must carry a random
  per-session token stored in `%LOCALAPPDATA%\ResolveMCP\bridge.json`. There is no HTTP endpoint and no CORS.
  Ways to reach the bridge without the token, from another machine, or from a web page are **in scope**.
- **Arbitrary code**: `python_exec` (raw Python inside Resolve) is disabled unless the user sets
  `"allow_exec": true` in `%LOCALAPPDATA%\ResolveMCP\config.json`. Any way to run code in Resolve while it is
  disabled is **in scope**.
- **Destructive actions**: deleting or overwriting projects/media requires `confirm=true` and makes a `.drp`
  backup first. Ways to bypass this are **in scope**.
- **Network**: the server makes no cloud calls and sends no telemetry. Any unexpected network traffic is
  **in scope**.

Out of scope:
- anything that requires the attacker to already run code as the same Windows user (they can read the token
  and control Resolve directly anyway);
- the AI assistant following instructions the user gave it, or content in media/project files the user opened;
- vulnerabilities in DaVinci Resolve itself (report those to Blackmagic Design), in the MCP SDK, or in the
  optional [samuelgursky/davinci-resolve-mcp](https://github.com/samuelgursky/davinci-resolve-mcp) codec
  (report to that project).

---

### Tiếng Việt (tóm tắt)
- **Đừng mở issue công khai** cho lỗi bảo mật. Hãy báo riêng qua **Security → Report a vulnerability** của repo.
- Gửi kèm: kẻ tấn công làm được gì, cách tái hiện, phiên bản Resolve/Windows.
- Phản hồi đầu tiên trong vòng **7 ngày**.
- Trong phạm vi:
  - vượt qua token hoặc cổng 127.0.0.1;
  - chạy code khi `allow_exec` đang tắt;
  - xoá hoặc ghi đè mà không cần `confirm`;
  - có kết nối mạng ngoài ý muốn.
