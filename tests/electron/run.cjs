// Isolated consumer of a built npm tarball, tested in actual Electron main/worker processes.
const { mkdtempSync, mkdirSync, writeFileSync, rmSync, existsSync, readFileSync } = require('node:fs');
const { createHash } = require('node:crypto');
const { tmpdir } = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
async function main() {
  const [artifact, model, report] = process.argv.slice(2).map(p => path.resolve(p));
  if (!artifact || !model || !existsSync(artifact) || !existsSync(model)) throw new Error('Usage: node run.cjs package.tgz model.gguf [report.json]');
  const results = [];
  const directory = mkdtempSync(path.join(tmpdir(), 'ducktape-electron-'));
  const { createPackageWithOptions } = await import('@electron/asar');
  try {
    const app = path.join(directory, 'application'); mkdirSync(app);
    writeFileSync(path.join(app, 'package.json'), JSON.stringify({ name: 'ducktape-test-consumer', version: '1.0.0', main: 'main.cjs' }));
    const install = ['install', '--ignore-scripts', '--no-audit', '--no-fund', '--offline'];
    // npm on Windows is a .cmd script, which needs a shell.
    let result = process.platform === 'win32'
      ? spawnSync(`npm ${install.join(' ')} "${artifact}"`, { cwd: app, encoding: 'utf8', shell: true })
      : spawnSync('npm', [...install, artifact], { cwd: app, encoding: 'utf8' });
    if (result.status) throw new Error(result.stderr);
    writeFileSync(path.join(app, 'main.cjs'), `
const { app } = require('electron');
const { Laya } = require('ducktape-laya');
app.disableHardwareAcceleration();
app.whenReady().then(async () => {
  let engine;
  try {
    engine = await Laya.open(process.env.DTL_ELECTRON_MODEL);
    const result = await engine.execute({ state: 'Please refund me.', questions: { refund: { type: 'noul', instructions: 'Is a refund requested?' } } });
    if (!(result.answers.refund.noul >= 0 && result.answers.refund.noul <= 1)) throw new Error('Invalid probabilities');
    await engine.close();
    console.log(JSON.stringify({ electron: process.versions.electron, node: process.versions.node, result }));
    app.exit(0);
  } catch (error) { console.error(error); if (engine) await engine.close().catch(() => {}); app.exit(1); }
});
`);
    const asar = path.join(directory, 'application.asar');
    // Worker_threads cannot rely on Electron's main-thread ASAR filesystem hooks.
    await createPackageWithOptions(app, asar, { unpackDir: 'node_modules/ducktape-laya' });
    const env = { ...process.env, DTL_ELECTRON_MODEL: model };
    delete env.DUCKTAPE_LAYA_ADDON; delete env.DUCKTAPE_LAYA_LIB_DIR; delete env.ELECTRON_RUN_AS_NODE;
    for (const application of [app, asar]) {
      result = spawnSync(require('electron'), ['--ozone-platform=headless', '--disable-gpu', application], { env, encoding: 'utf8', timeout: 180000 });
      if (result.status !== 0) throw new Error(`Electron ${application}: ${result.error || result.stderr}\n${result.stdout}`);
      const line = result.stdout.split('\n').find(line => line.startsWith('{"electron"'));
      if (!line) throw new Error('No Electron inference result');
      results.push({ mode: application === asar ? 'asar' : 'directory', ...JSON.parse(line) });
      console.log(JSON.stringify(results.at(-1)));
    }
    if (report) writeFileSync(report, JSON.stringify({ status: 'passed', artifact: path.basename(artifact),
      artifact_sha256: createHash('sha256').update(readFileSync(artifact)).digest('hex'), results }, null, 2) + '\n');
  } finally { rmSync(directory, { recursive: true, force: true }); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
