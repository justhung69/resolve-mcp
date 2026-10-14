"""Pack extension/ into dist/resolve-mcp-<version>.mcpb (a zip with manifest.json at the root)."""
import json
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "extension")
version = json.load(open(os.path.join(SRC, "manifest.json"), encoding="utf-8"))["version"]
os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
out = os.path.join(ROOT, "dist", "resolve-mcp-%s.mcpb" % version)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for base, _, files in os.walk(SRC):
        for f in files:
            if f.endswith(".pyc"):
                continue
            p = os.path.join(base, f)
            z.write(p, os.path.relpath(p, SRC).replace(os.sep, "/"))
print(out, os.path.getsize(out), "bytes")
