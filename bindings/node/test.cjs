const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { Engine, Status, LayaError, LayaLoadError, EngineClosedError } = require('./index.cjs');
test('missing model rejects asynchronously', async () => {
  await assert.rejects(Engine.open('/nonexistent/ducktape-laya.gguf'), error => error.status === 2);
});
test('invalid options reject', async () => {
  await assert.rejects(Engine.open('x', { maxPending: 0 }), /maxPending/);
  const circular = {}; circular.self = circular;
  await assert.rejects(Engine.openRouter(circular), /circular/i);
  await assert.rejects(Engine.openRouter({ models: {} }), e => e.status === 1);
  // The addon's own argument checks carry a status too: an open config over 1 MiB.
  const temperatures = Object.fromEntries(Array.from({ length: 600 }, (_, i) => [`k${i}${'x'.repeat(1900)}`, 1]));
  await assert.rejects(Engine.openRouter({ models: { unused: __filename } }, { config: { temperatures } }),
    e => e.status === 1 && /1 MiB/.test(e.message));
});
test('serialization reentry cannot bypass queue or close boundaries', async () => {
  // Lazy routing permits validation tests without loading a model.
  const config = { models: { unused: __filename } };
  const engine = await Engine.openRouter(config, { maxPending: 1 });
  try {
    let nested;
    await assert.rejects(engine.execute({ toJSON() {
      nested = assert.rejects(engine.execute('{}'), e => e.status === 1);
      return {};
    }}), /queue is full/);
    await nested;
  } finally { await engine.close(); }
  const closing = await Engine.openRouter(config);
  try {
    await assert.rejects(closing.execute({ toJSON() {
      closing.close();
      return {};
    }}), error => error.code === 'ERR_LAYA_CLOSED');
  } finally { await closing.close(); }
});
test('inference, queue bound and drain on close', { skip: !process.env.LAYA_MODEL }, async () => {
  const engine = await Engine.open(process.env.LAYA_MODEL, { config: { threads: 2 }, maxPending: 1 });
  const request = fs.readFileSync(path.join(__dirname, '../../tests/fixtures/primitive.json'), 'utf8');
  try {
    await assert.rejects(engine.execute('{'), error => error.status === 1);
    const first = engine.execute(request);
    await assert.rejects(engine.execute(request), /queue is full/);
    const close = engine.close();
    await assert.rejects(engine.execute(request), error => error.code === 'ERR_LAYA_CLOSED');
    const result = await first;
    assert.equal(result.answers.department.choice, 'billing');
    assert.ok(result.answers.refund_requested.noul > 0.5);
    assert.ok(result.answers.urgency.score >= 0 && result.answers.urgency.score <= 2);
    await close;
    await engine.close();
  } finally { await engine.close(); }
});

test('batch, structured projection, hooks and worker responsiveness', { skip: !process.env.LAYA_MODEL }, async () => {
  const engine = await Engine.open(process.env.LAYA_MODEL, { config: { threads: 2 } });
  const request = JSON.parse(fs.readFileSync(path.join(__dirname, '../../tests/fixtures/primitive.json'), 'utf8'));
  const structured = { state: request.state, schema: { type: 'object', properties: {
    refund: { type: 'boolean', description: 'Does the customer request a refund?' },
    priority: { type: 'integer', minimum: 1, maximum: 3, description: 'How urgent is this?' },
    team: { type: 'string', enum: ['billing', 'technical'], description: 'Which team should handle this?' }
  }, required: ['refund'], additionalProperties: false } };
  try {
    const single = await engine.execute(request);
    const tagged = { ...request, metadata: 'caller tag' };
    await assert.rejects(engine.execute(tagged), e => e.status === 1);
    const projected = await engine.execute(structured);
    assert.equal(projected.values.refund, projected.answers.refund.noul >= 0.5);
    assert.equal(projected.values.team, projected.answers.team.choice);
    assert.ok(Number.isInteger(projected.values.priority) && projected.values.priority >= 1 && projected.values.priority <= 3);
    const events = [];
    let ticks = 0;
    const timer = setInterval(() => { ticks++; }, 10);
    let batch;
    try { batch = await engine.execute([request, structured, request], { onEvent: event => { events.push(event); } }); }
    finally { clearInterval(timer); }
    assert.deepEqual(batch, [single, projected, single]);
    assert.ok(ticks > 5, 'inference must leave the host event loop running');
    assert.deepEqual(events.map(e => e.event), ['start', 'end']);
    assert.equal(events[0].request_id, events[1].request_id);
    assert.ok(events[1].elapsed_ms > 0);
    const errors = [];
    await assert.rejects(engine.execute('{}', { onEvent: e => { errors.push(e); throw Error('telemetry'); } }), e => e.status === 1);
    assert.deepEqual(errors.map(e => e.event), ['start', 'error']);
    await assert.rejects(engine.execute([]), e => e.status === 1);
    await assert.rejects(engine.execute(Array(33).fill(request)), e => e.status === 1);
    await assert.rejects(engine.execute(request, { onEvent: 3 }), /onEvent/);
    // Rejected observer promises are contained. Reentry is queued behind this call.
    let nested;
    const observed = await engine.execute(request, { onEvent: async e => {
      if (e.event === 'start') nested = engine.execute(request);
      throw Error('ignored asynchronous telemetry failure');
    }});
    assert.deepEqual(await nested, observed);
  } finally { await engine.close(); }
});

test('router shares native model selection and drains accepted batch on close', { skip: !process.env.LAYA_MODEL }, async () => {
  const router = await Engine.openRouter({ models: { english: process.env.LAYA_MODEL, alias: process.env.LAYA_MODEL }, max_loaded: 1 }, { config: { threads: 2 } });
  const request = { state: 'Please refund me.', questions: { q: { type: 'noul', instructions: 'Is a refund requested?' } } };
  try {
    await assert.rejects(router.execute(request), e => e.status === 1);
    await assert.rejects(router.execute({ ...request, model: 'missing' }), e => e.status === 1);
    const pending = router.execute([{ ...request, model: 'english' }, { ...request, model: 'alias' }]);
    const close = router.close();
    const [a, b] = await pending;
    assert.equal(a.model, 'english'); assert.equal(b.model, 'alias');
    assert.deepEqual(a.answers, b.answers);
    await close;
  } finally { await router.close(); }
});

test('cooperative cancellation, deadline, bounded cancelled queue and shutdown', { skip: !process.env.LAYA_MODEL }, async () => {
  const engine = await Engine.open(process.env.LAYA_MODEL, { config: { threads: 2 }, maxPending: 2 });
  const request = { state: 'Please refund me.', questions: { refund: { type: 'noul', instructions: 'Is a refund requested?' } } };
  const baseline = await engine.execute(request);
  await assert.rejects(engine.execute(request, { timeoutMs: 1 }), { status: 6 });
  assert.deepEqual(await engine.execute(request), baseline);
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(engine.execute(request, { signal: controller.signal }), { status: 5 });
  const queued = new AbortController();
  const first = engine.execute(request);
  const second = engine.execute(request, { signal: queued.signal });
  queued.abort();
  const cancelled = assert.rejects(second, { status: 5 });
  const full = assert.rejects(engine.execute(request), /queue is full/);
  await Promise.all([first, cancelled, full]);
  // Drain the cancelled queue entry before the next independent check.
  await engine.close();
  const closing = await Engine.open(process.env.LAYA_MODEL, { config: { threads: 2 } });
  const a = closing.execute(request), b = closing.execute(request);
  const stopped = Promise.all([assert.rejects(a, { status: 5 }), assert.rejects(b, { status: 5 })]);
  const close = closing.close({ cancelPending: true });
  assert.equal(closing.close({ cancelPending: true }), close);
  await Promise.all([stopped, close]);
});

test('runtime and model diagnostics share native identity', { skip: !process.env.LAYA_MODEL }, async () => {
  const runtime = Engine.runtimeInfo();
  assert.equal(runtime.abi_version, 2); assert.equal(runtime.cpu_supported, true);
  assert.equal(runtime.limits.questions_per_batch, 128);
  const engine = await Engine.open(process.env.LAYA_MODEL);
  try {
    const info = await engine.info();
    assert.equal(info.model.decision_type, 'laya');
    assert.ok(info.model.temperatures.noul > 0);
    delete info.model; assert.deepEqual(info, runtime);
  } finally { await engine.close(); }
  await assert.rejects(engine.info(), { code: 'ERR_LAYA_CLOSED' });
});

test('Status is frozen and argument errors carry status and codes', async () => {
  assert.ok(Object.isFrozen(Status));
  await assert.rejects(Engine.open('x', { maxPending: 0 }), { name: 'RangeError', status: 1 });
  await assert.rejects(Engine.open('x\0'), { name: 'TypeError', status: 1 });
  const circular = {}; circular.self = circular;
  await assert.rejects(Engine.openRouter(circular), { name: 'TypeError', status: 1 });
  const engine = await Engine.openRouter({ models: { unused: __filename } }, { maxPending: 1 });
  try {
    await assert.rejects(engine.execute('\0'), { name: 'TypeError', status: 1 });
    await assert.rejects(engine.execute('{}', { timeoutMs: -1 }), { name: 'RangeError', status: 1 });
    const first = engine.execute('{}').catch(() => {});
    await assert.rejects(engine.execute('{}'), e => e instanceof LayaError && e.name === 'LayaError' && e.code === 'ERR_LAYA_QUEUE_FULL' && e.status === 1 && e.message === 'Laya queue is full');
    await first;
  } finally { await engine.close(); }
});

// A fresh copy of the binding whose workers are recorded, to observe or end them.
function withWorkers() {
  const threads = require('node:worker_threads');
  const OriginalWorker = threads.Worker;
  const workers = [];
  threads.Worker = class extends OriginalWorker { constructor(...args) { super(...args); workers.push(this); } };
  const indexPath = require.resolve('./index.cjs');
  delete require.cache[indexPath];
  try { return { Engine: require('./index.cjs').Engine, workers }; }
  finally { threads.Worker = OriginalWorker; delete require.cache[indexPath]; }
}
// Resolves when the worker has exited; fails after timeoutMs.
function exited(worker, timeoutMs = 10000) {
  return new Promise((resolve, reject) => {
    if (worker.threadId === -1) { resolve(); return; }
    const timer = setTimeout(() => reject(new Error(`worker did not exit within ${timeoutMs} ms`)), timeoutMs);
    worker.once('exit', () => { clearTimeout(timer); resolve(); });
  });
}

test('close resolves after the worker has died and stays idempotent', async () => {
  const { Engine: FreshEngine, workers } = withWorkers();
  const engine = await FreshEngine.openRouter({ models: { unused: __filename } });
  await workers[0].terminate();
  await assert.rejects(engine.info(), { code: 'ERR_LAYA_WORKER_EXITED', status: 3, message: 'Engine worker has exited' });
  const close = engine.close();
  assert.equal(engine.close(), close);
  await close;
  await assert.rejects(engine.info(), { code: 'ERR_LAYA_CLOSED', status: 1 });
});

test('execute is the only call', async () => {
  const engine = await Engine.openRouter({ models: { unused: __filename } });
  try {
    for (const name of ['decide', 'decideStructured', 'decideBatch']) assert.equal(engine[name], undefined);
    assert.equal(typeof engine.execute, 'function');
    for (const requestId of [-1, 1.5, 2 ** 53, '1']) await assert.rejects(engine.execute('{}', { requestId }), { name: 'RangeError', status: 1 });
  } finally { await engine.close(); }
});

test('open config is passed to the core unvalidated and per-route config is native-keyed', async () => {
  const models = { m: __filename };
  // The core validates the serialized config: unknown keys are rejected at open. Router routes load
  // lazily, so model-dependent bounds (context_tokens) are checked when a route first loads.
  await assert.rejects(Engine.openRouter({ models }, { config: { threads: 2, temperatures: { 'bogus!': 1.5 } } }), { status: 1 });
  await assert.rejects(Engine.openRouter({ models }, { config: { surprise: 1 } }), { status: 1 });
  await assert.rejects(Engine.openRouter({ models: { m: { path: __filename, config: { surprise: 1 } } } }), { status: 1 });
  await (await Engine.openRouter({ models }, { config: { context_tokens: 256, threads: 1, temperatures: { choice: 1.5, 'noul.2': 2 } } })).close();
  await (await Engine.openRouter({ models: { m: { path: __filename, config: { context_tokens: 64, temperatures: { choice: 1.2 } } } } })).close();
  // Binding-only conversions: a config that is not an object, and values JSON cannot carry.
  for (const config of [5, 'x', []]) await assert.rejects(Engine.open('x', { config }), { name: 'TypeError', status: 1 });
  await assert.rejects(Engine.open('x', { config: { temperatures: { choice: NaN } } }), { name: 'TypeError', status: 1, message: /non-finite/ });
});

test('open option overflow reaches the model', { skip: !process.env.LAYA_MODEL }, async () => {
  const request = { state: 'refund '.repeat(800), questions: { refund: { type: 'noul', instructions: 'Is a refund requested?' } } };
  const engine = await Engine.open(process.env.LAYA_MODEL, { config: { threads: 2, overflow: 'truncate_end' } });
  // The policy reaches the engine: the default (reject) would refuse this request.
  try { assert.ok((await engine.execute(request)).answers.refund); } finally { await engine.close(); }
  await assert.rejects(Engine.open(process.env.LAYA_MODEL, { config: { threads: 2, overflow: 'bogus' } }), { status: 1 });
});

test('context tokens, temperatures and max_tokens reach the model', { skip: !process.env.LAYA_MODEL }, async () => {
  const request = { state: 'Please refund me.', questions: { refund: { type: 'noul', instructions: 'Is a refund requested?' } } };
  const engine = await Engine.open(process.env.LAYA_MODEL, { config: { threads: 2, context_tokens: 256, temperatures: { 'noul.2': 2 } } });
  try {
    const info = await engine.info();
    assert.equal(info.model.context_tokens, 256);
    assert.ok(info.model.trained_context_tokens >= 256);
    assert.equal(info.model.temperatures['noul.2'], 2);
    assert.equal(info.limits.default_context_tokens, 512);
    // Per-request max_tokens above the engine capacity and bad temperatures are rejected.
    await assert.rejects(engine.execute({ ...request, max_tokens: 257 }), { status: 1 });
    await assert.rejects(engine.execute({ ...request, max_tokens: 0 }), { status: 1 });
    await assert.rejects(engine.execute({ ...request, temperatures: { 'noul.2': -1 } }), { status: 1 });
    const ids = [];
    await engine.execute(request, { requestId: 4242, onEvent: e => { ids.push(e.request_id); } });
    assert.deepEqual(ids, [4242, 4242]);
  } finally { await engine.close(); }
  await assert.rejects(Engine.open(process.env.LAYA_MODEL, { config: { context_tokens: 1e9 } }), { status: 1 });
  await assert.rejects(Engine.open(process.env.LAYA_MODEL, { config: { context_tokens: 8 } }), { status: 1 });
  await assert.rejects(Engine.open(process.env.LAYA_MODEL, { config: { temperatures: { 'noul.2': 1.5, nonsense: 1 } } }), { status: 1 });
});

test('router route config below the minimum is rejected', { skip: !process.env.LAYA_MODEL }, async () => {
  const request = { state: 'Please refund me.', questions: { refund: { type: 'noul', instructions: 'Is a refund requested?' } } };
  // A route below the 32-token minimum is rejected (at open, or at first load for older cores).
  const bad = await Engine.openRouter({ models: { tiny: { path: process.env.LAYA_MODEL, config: { context_tokens: 8 } }, ok: process.env.LAYA_MODEL } }, { config: { threads: 2 } }).catch(error => {
    assert.equal(error.status, 1); return undefined;
  });
  if (bad) try {
    await assert.rejects(bad.execute({ ...request, model: 'tiny' }), { status: 1 });
    await bad.execute({ ...request, model: 'ok' });
  } finally { await bad.close(); }
});
test('invalid Unicode is rejected instead of replaced', async () => {
  const lone = 'a\ud800b';
  await assert.rejects(Engine.open(lone), e => e.status === 1 && /model path is not valid Unicode/.test(e.message));
  await assert.rejects(Engine.open('x', { config: { temperatures: { [lone]: 1 } } }), e => e.status === 1 && /not valid Unicode/.test(e.message));
  await assert.rejects(Engine.openRouter({ models: { [lone]: __filename } }), e => e.status === 1 && /router config is not valid Unicode/.test(e.message));
  await assert.rejects(Engine.openRouter({ models: { a: lone } }), e => e.status === 1 && /router config is not valid Unicode/.test(e.message));
  const engine = await Engine.openRouter({ models: { unused: __filename } });
  try {
    await assert.rejects(engine.execute({ state: lone, questions: {} }), e => e.status === 1 && /request is not valid Unicode/.test(e.message));
    await assert.rejects(engine.execute({ state: {}, questions: { [lone]: {} } }), e => e.status === 1 && /request is not valid Unicode/.test(e.message));
    for (const value of [NaN, Infinity, -Infinity])
      await assert.rejects(engine.execute({ state: { amount: value }, questions: {} }), e => e.status === 1 && /non-finite number/.test(e.message));
    await assert.rejects(engine.execute(`{"state":"${lone}"}`), e => e.status === 1 && /request is not valid Unicode/.test(e.message));
  } finally { await engine.close(); }
});
test('invalid router config names the router config', async () => {
  await assert.rejects(Engine.openRouter('x\0'), /router config/);
  await assert.rejects(Engine.open('x\0'), /model path/);
});
test('no onEvent delivery after the call settled', async () => {
  const engine = await Engine.openRouter({ models: { unused: __filename } });
  try {
    const events = [];
    const controller = new AbortController();
    const call = engine.execute({ state: {}, questions: { q: { type: 'bool', text: 'q' } }, model: 'unused' }, { signal: controller.signal, onEvent: event => { events.push(event); } });
    controller.abort();
    await assert.rejects(call, e => e.status === Status.CANCELLED);
    await engine.close();
    assert.deepEqual(events, []);
  } finally { await engine.close(); }
});
test('JSON serialization failures are argument errors with status', async () => {
  const circular = {}; circular.self = circular;
  await assert.rejects(Engine.openRouter(circular), e => e instanceof TypeError && e.status === Status.INVALID_ARGUMENT);
  await assert.rejects(Engine.openRouter({ models: { a: 1n } }), e => e.status === Status.INVALID_ARGUMENT);
  const engine = await Engine.openRouter({ models: { unused: __filename } });
  try {
    await assert.rejects(engine.execute({ state: circular, questions: {} }), e => e instanceof TypeError && e.status === Status.INVALID_ARGUMENT);
    await assert.rejects(engine.execute({ state: 1n, questions: {} }), e => e instanceof TypeError && e.status === Status.INVALID_ARGUMENT);
  } finally { await engine.close(); }
});
test('router auto config and lang are validated by the native core', async () => {
  const models = { english: __filename, other: __filename };
  const request = { state: 'hi', questions: { q: { type: 'noul', instructions: 'x' } } };
  for (const auto of [{ english: 'english' }, { english: 'english', default: 'missing' }, { english: 'english', default: 'other', extra: 1 }, 'english']) {
    await assert.rejects(Engine.openRouter({ models, auto }), e => e.status === Status.INVALID_ARGUMENT && /auto/.test(e.message), JSON.stringify(auto));
  }
  const router = await Engine.openRouter({ models });
  try {
    await assert.rejects(router.execute({ ...request, model: 'auto' }), e => e.status === 1 && /auto/.test(e.message));
    await assert.rejects(router.execute({ ...request, model: 'english', lang: 'en' }), e => e.status === 1 && /lang/.test(e.message));
  } finally { await router.close(); }
});
test('calls after close() report ERR_LAYA_CLOSED once the worker has exited', async () => {
  const { Engine: FreshEngine, workers } = withWorkers();
  const engine = await FreshEngine.openRouter({ models: { unused: __filename } });
  await engine.close();
  await exited(workers[0]);
  await assert.rejects(engine.info(), { code: 'ERR_LAYA_CLOSED', status: 1 });
  await assert.rejects(engine.execute('{}'), { code: 'ERR_LAYA_CLOSED', status: 1 });
});

// Runs fn with the override variables set (undefined deletes), then restores them.
async function withEnv(vars, fn) {
  const saved = Object.fromEntries(Object.keys(vars).map(name => [name, process.env[name]]));
  const set = values => { for (const [name, value] of Object.entries(values)) { if (value === undefined) delete process.env[name]; else process.env[name] = value; } };
  set(vars);
  try { return await fn(); } finally { set(saved); }
}
test('addon load failures are LayaLoadError with status 7 and ERR_LAYA_UNSUPPORTED', async () => {
  const missing = path.join(__dirname, 'does-not-exist.node');
  const load = { name: 'LayaLoadError', status: 7, code: 'ERR_LAYA_UNSUPPORTED' };
  await withEnv({ DUCKTAPE_LAYA_LIBRARY: missing, DUCKTAPE_LAYA_LIB_DIR: undefined }, async () => {
    await assert.rejects(Engine.open('x'), e => e instanceof LayaLoadError && e instanceof LayaError);
    await assert.rejects(Engine.open('x'), load);
    await assert.rejects(Engine.openRouter({ models: { unused: __filename } }), load);
    assert.throws(() => Engine.runtimeInfo(), load);
  });
  await withEnv({ DUCKTAPE_LAYA_LIBRARY: undefined, DUCKTAPE_LAYA_LIB_DIR: __dirname }, async () => {
    await assert.rejects(Engine.open('x'), load);
  });
  // An addon that exists but cannot be loaded is reported as found, with the loader's reason.
  const broken = path.join(require('node:os').tmpdir(), `ducktape-laya-broken-${process.pid}.node`);
  fs.writeFileSync(broken, 'not an addon');
  try {
    const found = /^The native Laya addon was found but could not be loaded: \S.* \(it needs [^)]+\)$/s;
    await withEnv({ DUCKTAPE_LAYA_LIBRARY: broken }, async () => {
      assert.throws(() => Engine.runtimeInfo(), { ...load, message: found });
      await assert.rejects(Engine.open('x'), { ...load, message: found });
    });
  } finally { fs.rmSync(broken, { force: true }); }
});

test('overrides must be absolute paths: a relative one is a load error', async () => {
  for (const name of ['DUCKTAPE_LAYA_LIBRARY', 'DUCKTAPE_LAYA_LIB_DIR']) {
    await withEnv({ DUCKTAPE_LAYA_LIBRARY: undefined, DUCKTAPE_LAYA_LIB_DIR: undefined, [name]: 'relative/ducktape_laya.node' }, async () => {
      await assert.rejects(Engine.open('x'), { name: 'LayaLoadError', status: 7, message: new RegExp(`${name} must be an absolute path`) });
      assert.throws(() => Engine.runtimeInfo(), { name: 'LayaLoadError', status: 7 });
    });
  }
  const resolveAddon = require('./resolve-addon.cjs');
  const env = { DUCKTAPE_LAYA_LIB_DIR: '/opt/laya' };
  assert.equal(resolveAddon({ env }), path.join('/opt/laya', 'ducktape_laya.node'));
  assert.equal(resolveAddon({ env: { ...env, DUCKTAPE_LAYA_LIBRARY: '/elsewhere/a.node' } }), '/elsewhere/a.node');
  assert.equal(resolveAddon({ env: { ...env, DUCKTAPE_LAYA_LIBRARY: '' } }), path.join('/opt/laya', 'ducktape_laya.node'));
  // An installed libdir keeps the addon in ducktape-laya/node.
  const libdir = fs.mkdtempSync(path.join(require('node:os').tmpdir(), 'ducktape-laya-libdir-'));
  try {
    const installed = path.join(libdir, 'ducktape-laya', 'node', 'ducktape_laya.node');
    fs.mkdirSync(path.dirname(installed), { recursive: true });
    fs.writeFileSync(installed, '');
    assert.equal(resolveAddon({ env: { DUCKTAPE_LAYA_LIB_DIR: libdir } }), installed);
  } finally { fs.rmSync(libdir, { recursive: true, force: true }); }
});

test('musl detection reads the dynamic loaders in the given directories', () => {
  const { isMusl } = require('./resolve-addon.cjs');
  const root = fs.mkdtempSync(path.join(require('node:os').tmpdir(), 'laya-musl-'));
  try {
    const dir = (name, ...files) => {
      const d = path.join(root, name);
      fs.mkdirSync(d);
      for (const f of files) fs.writeFileSync(path.join(d, f), '');
      return d;
    };
    const alpine = dir('alpine', 'ld-musl-x86_64.so.1', 'libc.musl-x86_64.so.1');
    const debian = dir('debian', 'ld-linux-x86-64.so.2');
    const arm = dir('arm', 'ld-linux-aarch64.so.1');
    const gcompat = dir('gcompat', 'ld-musl-aarch64.so.1', 'ld-linux-aarch64.so.1');
    const empty = dir('empty');
    assert.equal(isMusl([alpine]), true);
    assert.equal(isMusl([alpine, path.join(root, 'missing')]), true);
    assert.equal(isMusl([debian]), false);
    assert.equal(isMusl([arm]), false);
    // A glibc loader anywhere (Alpine with gcompat, or one in /lib64) means glibc can load.
    assert.equal(isMusl([gcompat]), false);
    assert.equal(isMusl([alpine, debian]), false);
    assert.equal(isMusl([empty, path.join(root, 'missing')]), false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('on musl the resolver and every entry point report that the glibc addon cannot load', async () => {
  const resolveAddon = require('./resolve-addon.cjs');
  const musl = /needs glibc, and this looks like a musl system/;
  for (const arch of ['x64', 'arm64']) {
    assert.throws(() => resolveAddon({ platform: 'linux', arch, env: {}, musl: () => true }), { status: 7, message: musl });
  }
  // glibc: the packaged addon path (missing in a checkout); other systems are never checked for musl.
  assert.throws(() => resolveAddon({ platform: 'linux', arch: 'x64', env: {}, musl: () => false }), { status: 7, message: /is missing/ });
  for (const arch of ['arm64', 'x64']) assert.throws(() => resolveAddon({ platform: 'darwin', arch, env: {}, musl: () => true }), { status: 7, message: /Native Laya addon for darwin-\w+ is missing/ });
  assert.throws(() => resolveAddon({ platform: 'darwin', arch: 'ia32', env: {}, musl: () => false }), { status: 7, message: /macOS arm64 and x64/ });
  // An explicit addon (a musl build) is used as given, and only when absolute.
  assert.equal(resolveAddon({ platform: 'linux', arch: 'x64', env: { DUCKTAPE_LAYA_LIBRARY: '/opt/musl/a.node' }, musl: () => true }), '/opt/musl/a.node');
  // Through the public API, in a child process whose resolver sees a musl system.
  const { execFileSync } = require('node:child_process');
  const script = `
    const resolver = require.resolve(${JSON.stringify(path.join(__dirname, 'resolve-addon.cjs'))});
    const real = require(resolver);
    require.cache[resolver].exports = Object.assign(h => real({ ...h, musl: () => true, platform: 'linux', arch: 'x64' }), real);
    const { Engine } = require(${JSON.stringify(path.join(__dirname, 'index.cjs'))});
    const results = [];
    try { Engine.runtimeInfo(); } catch (e) { results.push([e.status, e.code, e.message]); }
    Engine.open('model.gguf').catch(e => results.push([e.status, e.code, e.message]))
      .then(() => Engine.openRouter({ models: { a: 'model.gguf' } })).catch(e => results.push([e.status, e.code, e.message]))
      .then(() => process.stdout.write(JSON.stringify(results)));`;
  const env = { ...process.env };
  delete env.DUCKTAPE_LAYA_LIBRARY; delete env.DUCKTAPE_LAYA_LIB_DIR;
  const results = JSON.parse(execFileSync(process.execPath, ['-e', script], { env, encoding: 'utf8' }));
  assert.equal(results.length, 3);
  for (const [status, code, message] of results) {
    assert.equal(status, 7);
    assert.equal(code, 'ERR_LAYA_UNSUPPORTED');
    assert.match(message, musl);
  }
});

test('runtimeInfo maps an addon that reports the wrong ABI to LayaLoadError', async () => {
  // A stand-in addon module whose info() fails like the addon's own library check.
  const fake = path.join(require('node:os').tmpdir(), `ducktape-laya-fake-${process.pid}.cjs`);
  fs.writeFileSync(fake, "module.exports = { info() { throw Object.assign(new Error('Native ducktape-laya ABI 3 is not supported (expected 2)'), { status: 7 }); } };");
  try {
    await withEnv({ DUCKTAPE_LAYA_LIBRARY: fake }, () => assert.throws(() => Engine.runtimeInfo(), { name: 'LayaLoadError', status: 7, code: 'ERR_LAYA_UNSUPPORTED', message: /ABI 3/ }));
  } finally { fs.rmSync(fake, { force: true }); delete require.cache[fake]; }
});

test('null options mean the defaults; a non-object is an argument error with status 1', async () => {
  await (await Engine.openRouter({ models: { unused: __filename } }, null)).close();
  await (await Engine.openRouter({ models: { unused: __filename } }, { config: null })).close();
  await assert.rejects(Engine.open('x', 5), { name: 'TypeError', status: 1 });
  await assert.rejects(Engine.open('x', { config: 5 }), { name: 'TypeError', status: 1 });
  const engine = await Engine.openRouter({ models: { unused: __filename } });
  try {
    await assert.rejects(engine.execute('{}', 5), { name: 'TypeError', status: 1 });
    await assert.rejects(engine.execute('{', null), { status: 1 });
    await assert.rejects(engine.close(5), { name: 'TypeError', status: 1 });
    await engine.close(null);
    await assert.rejects(engine.execute('{}'), EngineClosedError);
  } finally { await engine.close(); }
});

test('paths: a Buffer and a file: URL reach the core, a NUL or a wrong type does not', async () => {
  const missing = path.join(__dirname, 'no', 'such', 'model.gguf');
  const { pathToFileURL } = require('node:url');
  for (const target of [Buffer.from(missing), new Uint8Array(Buffer.from(missing)), pathToFileURL(missing)]) {
    await assert.rejects(Engine.open(target), e => e.status === 2 && e.message.includes(missing));
  }
  // Bytes that are not UTF-8 are kept as they are.
  await assert.rejects(Engine.open(Buffer.from([0x2f, 0xff, 0xfe])), { status: 2 });
  await assert.rejects(Engine.open(Buffer.from('a\0b')), { name: 'TypeError', status: 1 });
  await assert.rejects(Engine.open(new URL('https://example.test/model.gguf')), { name: 'TypeError', status: 1 });
  await assert.rejects(Engine.open(5), { name: 'TypeError', status: 1 });
});

test('error order: closed, then argument errors, then cancellation, then admission', async () => {
  const { Engine: Fresh, workers } = withWorkers();
  const aborted = AbortSignal.abort();
  const engine = await Fresh.openRouter({ models: { unused: __filename } }, { maxPending: 1 });
  const messages = [];
  workers[0].on('message', message => messages.push(message));
  try {
    // Open: an argument error wins over a cancelled signal; cancellation alone fails at once with no worker message.
    await assert.rejects(engine.execute('{}', { signal: aborted, timeoutMs: -1 }), { name: 'RangeError', status: 1 });
    const before = messages.length;
    const sent = [];
    const post = workers[0].postMessage.bind(workers[0]);
    workers[0].postMessage = message => { sent.push(message); return post(message); };
    await assert.rejects(engine.execute('{}', { signal: aborted }), { status: Status.CANCELLED });
    assert.deepEqual(sent, []);
    assert.equal(messages.length, before);
    // A cancelled call fails before admission, even when the queue is full.
    const first = engine.execute('{}').catch(() => {});
    await assert.rejects(engine.execute('{}', { signal: aborted }), { status: Status.CANCELLED });
    await first;
  } finally { await engine.close(); }
  // Closed wins over a bad option and a cancelled signal.
  for (const options of [{ timeoutMs: -1, signal: aborted }, { signal: aborted }, 5]) {
    await assert.rejects(engine.execute('{}', options), e => e.name === 'EngineClosedError' && e.code === 'ERR_LAYA_CLOSED' && e.status === 1);
  }
});
