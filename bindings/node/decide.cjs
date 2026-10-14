const fs = require('node:fs');
const { Laya } = require('./index.cjs');
(async () => {
  const engine = await Laya.open(process.argv[2], { threads: 2 });
  try { console.log(JSON.stringify(await engine.execute(fs.readFileSync(0, 'utf8')))); }
  finally { await engine.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
