// Bridge to the samuelgursky "advanced" DRX codec (MIT): node drx_gen.mjs <advancedRepo> <generate|parse> <drxPath> [gradeParamsJSON]
// Offline only: reads/writes a local .drx file, prints the tool result as JSON on stdout.
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import fs from 'node:fs';

const [repo, action, drxPath, json] = process.argv.slice(2);
const mod = pathToFileURL(path.join(repo, 'resolve-advanced', 'server', 'tools', 'drx.mjs')).href;
const { drxTool } = await import(mod);
let res;
if (action === 'generate') {
  res = await drxTool.handler({ action: 'generate', args: { gradeParams: JSON.parse(json), outputPath: drxPath } });
} else if (action === 'parse') {
  res = await drxTool.handler({ action: 'parse', args: { content: fs.readFileSync(drxPath, 'utf8') } });
  res = { nodes: (res.nodes || []).map((n) => ({ label: n.label, params: n.params, qualifier: n.qualifier ?? null })) };
} else {
  throw new Error('unknown action ' + action);
}
process.stdout.write(JSON.stringify(res));
