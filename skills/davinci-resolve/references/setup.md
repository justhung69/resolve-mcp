# Setup (tell the user this if no resolve-mcp tools are available)

The skill only teaches the agent how to work. The tools come from the **resolve-mcp** MCP server, which must be
installed on the same Windows PC as DaVinci Resolve:

1. **Requirements**:
   - Windows 10/11;
   - DaVinci Resolve **21.0.4 Free**. 21.1+ Free removed Python scripting, so do not update;
   - Python 3.10+ (64-bit) from python.org.
2. Clone <https://github.com/justhung69/resolve-mcp> and run in PowerShell:
   `powershell -ExecutionPolicy Bypass -File .\install.ps1`.
   This creates `.venv`, installs the `mcp` package, and copies the ClaudeBridge scripts into Resolve.
3. **Connect the agent**:
   - **Claude Desktop**: download `resolve-mcp-<version>.mcpb` from the GitHub releases, then go to
     Settings → Extensions → Advanced settings → Install Extension…, and pick the repo folder.
   - **Claude Code**: run the `claude mcp add ...` command that `install.ps1` prints.
   - **Other MCP clients**: add a stdio server with command `<repo>\.venv\Scripts\python.exe` and argument
     `<repo>\server\mcp_server.py`.
4. **Every time Resolve starts**: open a project, then click **Workspace → Scripts → ClaudeBridge**.

Optional, for `color_grade_drx` (power windows): Node.js, plus
<https://github.com/samuelgursky/davinci-resolve-mcp> cloned next to the repo as `resolve-mcp-advanced`, with
`npm install` run in its `resolve-advanced` folder.

Everything is local. The bridge listens on 127.0.0.1 with a per-session token. There are no cloud calls and no
telemetry.
