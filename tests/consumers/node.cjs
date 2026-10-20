const fs = require('node:fs');
const { Laya } = require('../../bindings/node/index.cjs');
(async () => {
  // Optional second argument: open config as JSON (Node option names mapped from the native keys).
  const config = JSON.parse(process.argv[3] ?? '{"threads":4}');
  const options = { threads: config.threads, contextTokens: config.context_tokens, overflow: config.overflow, temperatures: config.temperatures };
  const engine = await Laya.open(process.argv[2], options);
  try { console.log(JSON.stringify(await engine.execute(fs.readFileSync(0, 'utf8')))); }
  finally { await engine.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
