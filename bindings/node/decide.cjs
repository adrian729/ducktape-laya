const fs = require('node:fs');
const { Engine } = require('./index.cjs');
(async () => {
  const engine = await Engine.open(process.argv[2], { config: { threads: 2 } });
  try { console.log(JSON.stringify(await engine.execute(fs.readFileSync(0, 'utf8')))); }
  finally { await engine.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
