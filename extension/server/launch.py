"""Desktop-extension launcher: runs the resolve-mcp server from the user's install folder.

The extension only carries this launcher. The real server, its .venv and the bridge scripts live in the
folder install.ps1 set up (chosen in the extension settings), so updating the repo needs no re-install.
"""
import os
import runpy
import sys

root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.expanduser("~"), "resolve-mcp"))
server = os.path.join(root, "server", "mcp_server.py")
if not os.path.isfile(server):
    sys.stderr.write("resolve-mcp not found at %s - run install.ps1 there or fix the folder in the extension settings\n" % root)
    sys.exit(1)
sys.argv = [server]
sys.path.insert(0, os.path.dirname(server))
runpy.run_path(server, run_name="__main__")
