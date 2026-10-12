"""Dev probe: apply base .drx converted to R21 with one raw window override. args: base.drx pid_hex raw"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import mcp_server, core, drx_r21
base, pid, raw = sys.argv[1], int(sys.argv[2], 16), float(sys.argv[3])
out = base[:-4] + "_cal.drx"
drx_r21.to_r21(base, out, {pid: raw})
def t(n, **a): return json.loads(asyncio.run(mcp_server.server.call_tool(n, a)).content[0].text)
it = core.get(core.roots()["timeline"], "GetItemListInTrack", "video", 1)[0]
t("color_apply", items=[it["$h"]], reset_all=True, confirm=True)
print(t("color_apply", items=[it["$h"]], drx=out)["ok"])
