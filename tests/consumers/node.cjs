// Usage: node node.cjs model.gguf [config.json] [--requests FILE... [--fresh-engine]] < request.json
// With --requests: one engine for all files (one per file with --fresh-engine), one result line per file.
const fs = require('node:fs');
const { Laya } = require('../../bindings/node/index.cjs');
(async () => {
  const args = process.argv.slice(2);
  let at = 1, files = null, fresh = false, usage = args.length < 1;
  // Optional second argument: open config as JSON (Node option names mapped from the native keys).
  const text = args.length > 1 && !args[1].startsWith('--') ? args[at++] : '{"threads":4}';
  for (; at < args.length; at++) {
    if (args[at] === '--fresh-engine') fresh = true;
    else if (args[at] === '--requests' && !files) {
      files = [];
      while (at + 1 < args.length && !args[at + 1].startsWith('--')) files.push(args[++at]);
      if (!files.length) usage = true;
    } else usage = true;
  }
  if (usage || (fresh && !files)) {
    console.error('Usage: node node.cjs model.gguf [config.json] [--requests FILE... [--fresh-engine]] < request.json');
    process.exitCode = 1;
    return;
  }
  const config = JSON.parse(text);
  const options = { threads: config.threads, contextTokens: config.context_tokens, overflow: config.overflow, temperatures: config.temperatures };
  if (!files) {
    const engine = await Laya.open(args[0], options);
    try { console.log(JSON.stringify(await engine.execute(fs.readFileSync(0, 'utf8')))); }
    finally { await engine.close(); }
    return;
  }
  let engine = null;
  try {
    for (const file of files) {
      const request = fs.readFileSync(file, 'utf8');
      engine ??= await Laya.open(args[0], options);
      console.log(JSON.stringify(await engine.execute(request)));
      if (fresh) { const closing = engine; engine = null; await closing.close(); }
    }
  } finally { if (engine) await engine.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
