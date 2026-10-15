#!/usr/bin/env node
// Real-browser gate runner. Uses an installed Chrome, never downloads browsers/models.
import { chromium } from 'playwright';
import { parseArgs } from 'node:util';
import { spawn, execFileSync } from 'node:child_process';
import { createInterface } from 'node:readline';
import { readFile, writeFile, mkdir, stat, readdir } from 'node:fs/promises';
import { createReadStream } from 'node:fs';
import { createHash } from 'node:crypto';
import { once } from 'node:events';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const { values: args } = parseArgs({ options: {
  model: { type: 'string' }, output: { type: 'string' }, native: { type: 'string' },
  'package-dir': { type: 'string' }, backend: { type: 'string', default: 'both' },
  headless: { type: 'boolean', default: false }, isolated: { type: 'boolean', default: false },
  'skip-reference': { type: 'boolean', default: false },
  'skip-controls': { type: 'boolean', default: false },
} });
if (!args.model || !args.output || !['wasm', 'webgpu', 'both'].includes(args.backend)) {
  throw new Error('Usage: node tests/browser-automation/run.mjs --model GGUF --output NEW_DIR [--native REPORT] [--package-dir EXTRACTED_PACKAGE] [--backend wasm|webgpu|both] [--isolated] [--headless]');
}
const out = resolve(args.output), model = resolve(args.model);
await mkdir(out); // Existing output must never be overwritten.
async function hash(path) {
  const digest = createHash('sha256');
  for await (const chunk of createReadStream(path)) digest.update(chunk);
  return digest.digest('hex');
}
const manifest = JSON.parse(await readFile(join(root, 'models/manifest.json')));
if ((await stat(model)).size !== manifest.size_bytes || await hash(model) !== manifest.sha256) {
  throw new Error('Model does not match the approved manifest');
}
const backends = args.backend === 'both' ? ['wasm', 'webgpu'] : [args.backend];
const report = { schema_version: 1, source_commit: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(),
  source_tracked_dirty: !!execFileSync('git', ['diff', 'HEAD', '--name-only'], { cwd: root, encoding: 'utf8' }).trim(),
  model_sha256: manifest.sha256, fixture_hashes: {}, asset_hashes: {},
  headless: args.headless, isolated: args.isolated, launch_flags: [], chromium_sandbox: true,
  backends: {}, release_ready: false };
for (const name of (await readdir(join(root, 'tests/fixtures'))).filter(x => x.endsWith('.json')).sort()) {
  report.fixture_hashes[name] = await hash(join(root, 'tests/fixtures', name));
}
for (const backend of backends) {
  const base = args['package-dir'] ? join(resolve(args['package-dir']), backend) : join(root, 'build-browser-' + backend);
  for (const name of ['laya.mjs', 'laya.wasm', 'laya.build.json']) report.asset_hashes[backend + '/' + name] = await hash(join(base, name));
  if (!args['skip-reference']) {
    const reference = join(root, 'build-browser-' + backend);
    for (const name of ['laya-reference.mjs', 'laya-reference.wasm']) report.asset_hashes[backend + '/' + name] = await hash(join(reference, name));
  }
}
const binding = args['package-dir'] ? resolve(args['package-dir']) : join(root, 'bindings/browser');
for (const name of ['index.mjs', 'worker.mjs']) report.asset_hashes['binding/' + name] = await hash(join(binding, name));
if (args.native) report.native_report_sha256 = await hash(resolve(args.native));
const save = () => writeFile(join(out, 'report.json'), JSON.stringify(report, null, 2) + '\n');
const serverArgs = [join(root, 'tests/browser-server.py'), model];
if (args['package-dir']) serverArgs.push('--package-dir', resolve(args['package-dir']));
if (args.isolated) serverArgs.push('--isolated');
let browser;
const server = spawn('python3', serverArgs, { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
let serverError = '';
server.stderr.on('data', chunk => { serverError = (serverError + chunk).slice(-8192); });
let failed = false;
try {
  const url = await new Promise((accept, reject) => {
    const lines = createInterface({ input: server.stdout });
    const timer = setTimeout(() => reject(new Error('Test server startup timed out')), 30000);
    lines.once('line', line => { clearTimeout(timer); lines.close(); accept(line); });
    server.once('error', error => { clearTimeout(timer); reject(error); });
    server.once('exit', code => { clearTimeout(timer); reject(new Error(`Test server exited ${code}: ${serverError}`)); });
  });
  if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(url)) throw new Error('Unexpected test server URL');
  browser = await chromium.launch({ channel: 'chrome', headless: args.headless, chromiumSandbox: true });
  report.browser_version = browser.version();
  for (const backend of backends) {
    const context = await browser.newContext();
    const page = await context.newPage();
    const gates = report.backends[backend] = {};
    try {
      await page.goto(url);
      report.environment = await page.evaluate(async () => {
        const adapter = await navigator.gpu?.requestAdapter();
        return { userAgent: navigator.userAgent, crossOriginIsolated,
          gpu: adapter ? { vendor: adapter.info.vendor, architecture: adapter.info.architecture,
            fallback: adapter.info.isFallbackAdapter } : null };
      });
      if (report.environment.crossOriginIsolated !== args.isolated) throw new Error('Isolation headers not applied');
      const stages = [['execution', 'start', 'state'],
        ...(!args['skip-reference'] ? [['reference', 'startReference', 'reference']] : []),
        ...(!args['skip-controls'] ? [['controls', 'startControls', 'controls'], ['assets', 'startAssets', 'assets'],
          ...(backend === 'webgpu' ? [['device_loss', 'startDeviceLoss', 'loss']] : [])] : [])];
      gates.reference = { status: 'not_run' };
      gates.controls = gates.assets = { status: 'not_run' };
      if (backend === 'webgpu') gates.device_loss = { status: 'not_run' };
      for (const [name, method, state] of stages) {
        console.log(`${backend}/${name}: running`);
        const fixtures = Object.keys(report.fixture_hashes).map(name => name.slice(0, -'.json'.length));
        await page.evaluate(([method, backend, fixtures]) => { void window[method](backend, fixtures); }, [method, backend, fixtures]);
        await page.waitForFunction(state => ['passed', 'failed'].includes(window[state]?.status), state, { timeout: 900000 });
        const result = await page.evaluate(state => window[state], state);
        const file = backend + '-' + name + '.json';
        await writeFile(join(out, file), JSON.stringify(result, null, 2) + '\n');
        gates[name] = { status: result.status, report: file, sha256: await hash(join(out, file)) };
        if (result.status !== 'passed') failed = true;
        console.log(`${backend}/${name}: ${result.status}`);
        await save();
      }
      for (const [name, script, inputs] of [
        ['upstream_numeric', 'compare-browser-reference.py', args['skip-reference'] ? null : [join(out, backend + '-execution.json'), join(out, backend + '-reference.json')]],
        ['cross_profile_drift', 'compare-browser.py', args.native ? [join(out, backend + '-execution.json'), '--native', resolve(args.native)] : null],
      ]) {
        gates[name] = { status: 'not_run' };
        if (inputs) {
          try {
            const output = execFileSync('python3', [join(root, 'tests', script), ...inputs], { encoding: 'utf8', stdio: 'pipe' });
            gates[name] = { status: 'passed', result: JSON.parse(output) };
          } catch (error) {
            gates[name] = { status: 'failed', error: String(error.stderr || error) }; failed = true;
          }
        }
      }
    } catch (error) {
      gates.runner = { status: 'failed', error: error.stack }; failed = true;
    } finally { await context.close(); await save(); }
  }
} catch (error) {
  report.error = error.stack; failed = true;
} finally {
  try { await browser?.close(); }
  catch (error) { report.cleanup_error = String(error); failed = true; }
  if (server.exitCode === null && server.signalCode === null) {
    const exited = once(server, 'exit');
    server.kill('SIGTERM');
    await exited;
  }
  report.selected_checks = failed ? 'failed' : 'passed';
  await save();
}
console.log(JSON.stringify({ selected_checks: report.selected_checks, release_ready: false, report: join(out, 'report.json') }));
process.exitCode = failed ? 1 : 0;
