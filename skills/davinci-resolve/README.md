# davinci-resolve skill

Teaches an AI agent to drive **DaVinci Resolve 21.0.4 Free** (Windows) safely through the open-source
[resolve-mcp](https://github.com/justhung69/resolve-mcp) MCP server. The agent can edit, cut, trim, add
transitions and speed changes, color grade (including circle power windows), keyframe, fix audio and render.
It always checks the bridge, snapshots the state, checks every `ok`, and reports what actually changed.

**You need the MCP server installed.** This skill contains instructions only; see `references/setup.md`.
Without the server the agent will tell you how to install it.

Files:
- `SKILL.md`: the workflow, the tool map, safety rules, color tips, limits, and the reply format.
- `references/setup.md`: installation and per-session steps.
- `references/quirks.md`: Resolve 21.0.4 behaviours the tools work around.

Try it:
- "Cut every clip on V1 at 3 seconds and add 12-frame cross dissolves."
- "Brighten the person in the left third with a soft circle window and show me the frame."
- "What changed in my project since the last snapshot?"

License: MIT. Not affiliated with Blackmagic Design.
