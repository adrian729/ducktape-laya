const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { Laya, Status } = require('./index.cjs');
test('missing model rejects asynchronously', async () => {
  await assert.rejects(Laya.open('/nonexistent/ducktape-laya.gguf'), error => error.status === 2);
});
test('invalid options reject', async () => {
  await assert.rejects(Laya.open('x', { threads: -1 }), /threads/);
  await assert.rejects(Laya.open('x', { maxPending: 0 }), /maxPending/);
  const circular = {}; circular.self = circular;
  await assert.rejects(Laya.openRouter(circular), /circular/i);
  await assert.rejects(Laya.openRouter({ models: {} }), e => e.status === 1);
  // The addon's own argument checks carry a status too: an open config over 1 MiB.
  const temperatures = Object.fromEntries(Array.from({ length: 600 }, (_, i) => [`k${i}${'x'.repeat(1900)}`, 1]));
  await assert.rejects(Laya.openRouter({ models: { unused: __filename } }, { temperatures }),
    e => e.status === 1 && /1 MiB/.test(e.message));
});
test('serialization reentry cannot bypass queue or close boundaries', async () => {
  // Lazy routing permits validation tests without loading a model.
  const config = { models: { unused: __filename } };
  const engine = await Laya.openRouter(config, { maxPending: 1 });
  try {
    let nested;
    await assert.rejects(engine.execute({ toJSON() {
      nested = assert.rejects(engine.execute('{}'), e => e.status === 1);
      return {};
    }}), /queue is full/);
    await nested;
  } finally { await engine.close(); }
  const closing = await Laya.openRouter(config);
  try {
    await assert.rejects(closing.execute({ toJSON() {
      closing.close();
      return {};
    }}), error => error.code === 'ERR_LAYA_CLOSED');
  } finally { await closing.close(); }
});
test('inference, queue bound and drain on close', { skip: !process.env.LAYA_MODEL }, async () => {
  const engine = await Laya.open(process.env.LAYA_MODEL, { threads: 2, maxPending: 1 });
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
  const engine = await Laya.open(process.env.LAYA_MODEL, { threads: 2 });
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
  const router = await Laya.openRouter({ models: { english: process.env.LAYA_MODEL, alias: process.env.LAYA_MODEL }, max_loaded: 1 }, { threads: 2 });
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
  const engine = await Laya.open(process.env.LAYA_MODEL, { threads: 2, maxPending: 2 });
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
  const closing = await Laya.open(process.env.LAYA_MODEL, { threads: 2 });
  const a = closing.execute(request), b = closing.execute(request);
  const stopped = Promise.all([assert.rejects(a, { status: 5 }), assert.rejects(b, { status: 5 })]);
  const close = closing.close({ cancelPending: true });
  assert.equal(closing.close({ cancelPending: true }), close);
  await Promise.all([stopped, close]);
});

test('runtime and model diagnostics share native identity', { skip: !process.env.LAYA_MODEL }, async () => {
  const runtime = Laya.runtimeInfo();
  assert.equal(runtime.abi_version, 2); assert.equal(runtime.cpu_supported, true);
  assert.equal(runtime.limits.questions_per_batch, 128);
  const engine = await Laya.open(process.env.LAYA_MODEL);
  try {
    const info = await engine.info();
    assert.equal(info.model.decision_type, 'laya');
    assert.ok(info.model.temperatures.noul > 0);
    delete info.model; assert.deepEqual(info, runtime);
  } finally { await engine.close(); }
  await assert.rejects(engine.info(), { code: 'ERR_LAYA_CLOSED' });
});

test('Status is frozen and validation errors carry status and codes', async () => {
  assert.ok(Object.isFrozen(Status));
  assert.deepEqual(Status, { OK: 0, INVALID_ARGUMENT: 1, MODEL_ERROR: 2, INFERENCE_ERROR: 3, OUT_OF_MEMORY: 4, CANCELLED: 5, DEADLINE_EXCEEDED: 6, UNSUPPORTED_PLATFORM: 7 });
  await assert.rejects(Laya.open('x', { threads: -1 }), { name: 'RangeError', status: 1 });
  await assert.rejects(Laya.open('x\0'), { name: 'TypeError', status: 1 });
  const circular = {}; circular.self = circular;
  await assert.rejects(Laya.openRouter(circular), { name: 'TypeError', status: 1 });
  const engine = await Laya.openRouter({ models: { unused: __filename } }, { maxPending: 1 });
  try {
    await assert.rejects(engine.execute('\0'), { name: 'TypeError', status: 1 });
    await assert.rejects(engine.execute('{}', { timeoutMs: -1 }), { name: 'RangeError', status: 1 });
    const first = engine.execute('{}').catch(() => {});
    await assert.rejects(engine.execute('{}'), { code: 'ERR_LAYA_QUEUE_FULL', status: 1, message: 'Laya queue is full' });
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
  try { return { Laya: require('./index.cjs').Laya, workers }; }
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
  const { Laya: FreshLaya, workers } = withWorkers();
  const engine = await FreshLaya.openRouter({ models: { unused: __filename } });
  await workers[0].terminate();
  await assert.rejects(engine.info(), { code: 'ERR_LAYA_WORKER_EXITED', status: 3, message: 'Engine worker has exited' });
  const close = engine.close();
  assert.equal(engine.close(), close);
  await close;
  await assert.rejects(engine.info(), { code: 'ERR_LAYA_CLOSED', status: 1 });
});

test('execute is the only call', async () => {
  const engine = await Laya.openRouter({ models: { unused: __filename } });
  try {
    for (const name of ['decide', 'decideStructured', 'decideBatch']) assert.equal(engine[name], undefined);
    assert.equal(typeof engine.execute, 'function');
    for (const requestId of [-1, 1.5, 2 ** 53, '1']) await assert.rejects(engine.execute('{}', { requestId }), { name: 'RangeError', status: 1 });
  } finally { await engine.close(); }
});

test('open options contextTokens and temperatures are validated and serialized', async () => {
  for (const contextTokens of [0, -1, 1.5, '512', NaN]) await assert.rejects(Laya.open('x', { contextTokens }), { name: 'RangeError', status: 1 });
  for (const temperatures of [null, [], 'x', { choice: 0 }, { choice: -1 }, { choice: NaN }, { choice: '1' }]) {
    await assert.rejects(Laya.open('x', { temperatures }), { status: 1 });
  }
  const models = { m: __filename };
  // Native validation of the serialized config: unknown keys are rejected at open. Router routes load
  // lazily, so model-dependent bounds (context_tokens) are checked when a route first loads.
  await assert.rejects(Laya.openRouter({ models }, { threads: 2, temperatures: { 'bogus!': 1.5 } }), { status: 1 });
  await assert.rejects(Laya.openRouter({ models: { m: { path: __filename, config: { surprise: 1 } } } }), { status: 1 });
  await (await Laya.openRouter({ models }, { contextTokens: 256, threads: 1, temperatures: { choice: 1.5, 'noul.2': 2 } })).close();
  await (await Laya.openRouter({ models: { m: { path: __filename, config: { context_tokens: 64, temperatures: { choice: 1.2 } } } } })).close();
});

test('open option overflow is validated', async () => {
  for (const overflow of ['', 'bogus', null, 1, 'TRUNCATE_END']) await assert.rejects(Laya.open('x', { overflow }), { name: 'RangeError', status: 1 });
});

test('open option overflow reaches the model', { skip: !process.env.LAYA_MODEL }, async () => {
  const request = { state: 'refund '.repeat(800), questions: { refund: { type: 'noul', instructions: 'Is a refund requested?' } } };
  const engine = await Laya.open(process.env.LAYA_MODEL, { threads: 2, overflow: 'truncate_end' });
  // The policy reaches the engine: the default (reject) would refuse this request.
  try { assert.ok((await engine.execute(request)).answers.refund); } finally { await engine.close(); }
  await assert.rejects(Laya.open(process.env.LAYA_MODEL, { threads: 2, overflow: 'bogus' }), { status: 1 });
});

test('context tokens, temperatures and max_tokens reach the model', { skip: !process.env.LAYA_MODEL }, async () => {
  const request = { state: 'Please refund me.', questions: { refund: { type: 'noul', instructions: 'Is a refund requested?' } } };
  const engine = await Laya.open(process.env.LAYA_MODEL, { threads: 2, contextTokens: 256, temperatures: { 'noul.2': 2 } });
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
  await assert.rejects(Laya.open(process.env.LAYA_MODEL, { contextTokens: 1e9 }), { status: 1 });
  await assert.rejects(Laya.open(process.env.LAYA_MODEL, { contextTokens: 8 }), { status: 1 });
  await assert.rejects(Laya.open(process.env.LAYA_MODEL, { temperatures: { 'noul.2': 1.5, nonsense: 1 } }), { status: 1 });
});

test('router route config below the minimum is rejected', { skip: !process.env.LAYA_MODEL }, async () => {
  const request = { state: 'Please refund me.', questions: { refund: { type: 'noul', instructions: 'Is a refund requested?' } } };
  // A route below the 32-token minimum is rejected (at open, or at first load for older cores).
  const bad = await Laya.openRouter({ models: { tiny: { path: process.env.LAYA_MODEL, config: { context_tokens: 8 } }, ok: process.env.LAYA_MODEL } }, { threads: 2 }).catch(error => {
    assert.equal(error.status, 1); return undefined;
  });
  if (bad) try {
    await assert.rejects(bad.execute({ ...request, model: 'tiny' }), { status: 1 });
    await bad.execute({ ...request, model: 'ok' });
  } finally { await bad.close(); }
});
test('invalid Unicode is rejected instead of replaced', async () => {
  const lone = 'a\ud800b';
  await assert.rejects(Laya.open(lone), e => e.status === 1 && /model path is not valid Unicode/.test(e.message));
  await assert.rejects(Laya.open('x', { temperatures: { [lone]: 1 } }), e => e.status === 1 && /not valid Unicode/.test(e.message));
  await assert.rejects(Laya.openRouter({ models: { [lone]: __filename } }), e => e.status === 1 && /router config is not valid Unicode/.test(e.message));
  await assert.rejects(Laya.openRouter({ models: { a: lone } }), e => e.status === 1 && /router config is not valid Unicode/.test(e.message));
  const engine = await Laya.openRouter({ models: { unused: __filename } });
  try {
    await assert.rejects(engine.execute({ state: lone, questions: {} }), e => e.status === 1 && /request is not valid Unicode/.test(e.message));
    await assert.rejects(engine.execute({ state: {}, questions: { [lone]: {} } }), e => e.status === 1 && /request is not valid Unicode/.test(e.message));
    for (const value of [NaN, Infinity, -Infinity])
      await assert.rejects(engine.execute({ state: { amount: value }, questions: {} }), e => e.status === 1 && /non-finite number/.test(e.message));
    await assert.rejects(engine.execute(`{"state":"${lone}"}`), e => e.status === 1 && /request is not valid Unicode/.test(e.message));
  } finally { await engine.close(); }
});
test('invalid router config names the router config', async () => {
  await assert.rejects(Laya.openRouter('x\0'), /router config/);
  await assert.rejects(Laya.open('x\0'), /model path/);
});
test('no onEvent delivery after the call settled', async () => {
  const engine = await Laya.openRouter({ models: { unused: __filename } });
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
  await assert.rejects(Laya.openRouter(circular), e => e instanceof TypeError && e.status === Status.INVALID_ARGUMENT);
  await assert.rejects(Laya.openRouter({ models: { a: 1n } }), e => e.status === Status.INVALID_ARGUMENT);
  const engine = await Laya.openRouter({ models: { unused: __filename } });
  try {
    await assert.rejects(engine.execute({ state: circular, questions: {} }), e => e instanceof TypeError && e.status === Status.INVALID_ARGUMENT);
    await assert.rejects(engine.execute({ state: 1n, questions: {} }), e => e instanceof TypeError && e.status === Status.INVALID_ARGUMENT);
  } finally { await engine.close(); }
});
test('router auto config and lang are validated by the native core', async () => {
  const models = { english: __filename, other: __filename };
  const request = { state: 'hi', questions: { q: { type: 'noul', instructions: 'x' } } };
  for (const auto of [{ english: 'english' }, { english: 'english', default: 'missing' }, { english: 'english', default: 'other', extra: 1 }, 'english']) {
    await assert.rejects(Laya.openRouter({ models, auto }), e => e.status === Status.INVALID_ARGUMENT && /auto/.test(e.message), JSON.stringify(auto));
  }
  const router = await Laya.openRouter({ models });
  try {
    await assert.rejects(router.execute({ ...request, model: 'auto' }), e => e.status === 1 && /auto/.test(e.message));
    await assert.rejects(router.execute({ ...request, model: 'english', lang: 'en' }), e => e.status === 1 && /lang/.test(e.message));
  } finally { await router.close(); }
});
test('calls after close() report ERR_LAYA_CLOSED once the worker has exited', async () => {
  const { Laya: FreshLaya, workers } = withWorkers();
  const engine = await FreshLaya.openRouter({ models: { unused: __filename } });
  await engine.close();
  await exited(workers[0]);
  await assert.rejects(engine.info(), { code: 'ERR_LAYA_CLOSED', status: 1 });
  await assert.rejects(engine.execute('{}'), { code: 'ERR_LAYA_CLOSED', status: 1 });
});

test('addon load failures carry status 7 and ERR_LAYA_UNSUPPORTED', async () => {
  const addonPath = path.join(__dirname, 'does-not-exist.node');
  await assert.rejects(Laya.open('x', { addonPath }), { status: 7, code: 'ERR_LAYA_UNSUPPORTED' });
  await assert.rejects(Laya.openRouter({ models: { unused: __filename } }, { addonPath }), { status: 7, code: 'ERR_LAYA_UNSUPPORTED' });
  assert.throws(() => Laya.runtimeInfo({ addonPath }), { status: 7, code: 'ERR_LAYA_UNSUPPORTED' });
  // An addon that exists but cannot be loaded is reported as found, with the loader's reason.
  const broken = path.join(require('node:os').tmpdir(), `ducktape-laya-broken-${process.pid}.node`);
  fs.writeFileSync(broken, 'not an addon');
  try {
    const found = /^The native Laya addon was found but could not be loaded: \S.* \(it needs [^)]+\)$/s;
    assert.throws(() => Laya.runtimeInfo({ addonPath: broken }), { status: 7, code: 'ERR_LAYA_UNSUPPORTED', message: found });
    await assert.rejects(Laya.open('x', { addonPath: broken }), { status: 7, code: 'ERR_LAYA_UNSUPPORTED', message: found });
  } finally { fs.rmSync(broken, { force: true }); }
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
    assert.throws(() => resolveAddon(undefined, { platform: 'linux', arch, env: {}, musl: () => true }), { status: 7, message: musl });
  }
  // glibc: the packaged addon path (missing in a checkout); other systems are never checked for musl.
  assert.throws(() => resolveAddon(undefined, { platform: 'linux', arch: 'x64', env: {}, musl: () => false }), { status: 7, message: /is missing/ });
  for (const arch of ['arm64', 'x64']) assert.throws(() => resolveAddon(undefined, { platform: 'darwin', arch, env: {}, musl: () => true }), { status: 7, message: /Native Laya addon for darwin-\w+ is missing/ });
  assert.throws(() => resolveAddon(undefined, { platform: 'darwin', arch: 'ia32', env: {}, musl: () => false }), { status: 7, message: /macOS arm64 and x64/ });
  // An explicit addon (a musl build) is used as given.
  assert.equal(resolveAddon('/opt/musl/ducktape_laya.node', { platform: 'linux', arch: 'x64', env: {}, musl: () => true }), path.resolve('/opt/musl/ducktape_laya.node'));
  assert.equal(resolveAddon(undefined, { platform: 'linux', arch: 'x64', env: { DUCKTAPE_LAYA_ADDON: '/opt/musl/a.node' }, musl: () => true }), path.resolve('/opt/musl/a.node'));
  // Through the public API, in a child process whose resolver sees a musl system.
  const { execFileSync } = require('node:child_process');
  const script = `
    const resolver = require.resolve(${JSON.stringify(path.join(__dirname, 'resolve-addon.cjs'))});
    const real = require(resolver);
    require.cache[resolver].exports = Object.assign((o, h) => real(o, { ...h, musl: () => true, platform: 'linux', arch: 'x64' }), real);
    const { Laya } = require(${JSON.stringify(path.join(__dirname, 'index.cjs'))});
    const results = [];
    try { Laya.runtimeInfo(); } catch (e) { results.push([e.status, e.code, e.message]); }
    Laya.open('model.gguf').catch(e => results.push([e.status, e.code, e.message]))
      .then(() => Laya.openRouter({ models: { a: 'model.gguf' } })).catch(e => results.push([e.status, e.code, e.message]))
      .then(() => process.stdout.write(JSON.stringify(results)));`;
  const env = { ...process.env };
  delete env.DUCKTAPE_LAYA_ADDON;
  const results = JSON.parse(execFileSync(process.execPath, ['-e', script], { env, encoding: 'utf8' }));
  assert.equal(results.length, 3);
  for (const [status, code, message] of results) {
    assert.equal(status, 7);
    assert.equal(code, 'ERR_LAYA_UNSUPPORTED');
    assert.match(message, musl);
  }
});

test('runtimeInfo maps an addon that reports the wrong ABI to ERR_LAYA_UNSUPPORTED', () => {
  // A stand-in addon module whose info() fails like the addon's own library check.
  const fake = path.join(require('node:os').tmpdir(), `ducktape-laya-fake-${process.pid}.cjs`);
  fs.writeFileSync(fake, "module.exports = { info() { throw Object.assign(new Error('Native ducktape-laya ABI 3 is not supported (expected 2)'), { status: 7 }); } };");
  try {
    assert.throws(() => Laya.runtimeInfo({ addonPath: fake }), { status: 7, code: 'ERR_LAYA_UNSUPPORTED', message: /ABI 3/ });
  } finally { fs.rmSync(fake, { force: true }); delete require.cache[fake]; }
});

test('addonPath must be a non-empty string: an argument error, not a load failure', async () => {
  for (const addonPath of [42, '', 'a\0b', {}]) {
    assert.throws(() => Laya.runtimeInfo({ addonPath }), { name: 'TypeError', status: 1 });
    await assert.rejects(Laya.open('x', { addonPath }), { name: 'TypeError', status: 1 });
  }
});

test('null or non-object options are argument errors with status 1', async () => {
  await assert.rejects(Laya.open('x', null), { name: 'TypeError', status: 1 });
  await assert.rejects(Laya.openRouter({ models: { unused: __filename } }, null), { name: 'TypeError', status: 1 });
  assert.throws(() => Laya.runtimeInfo(null), { name: 'TypeError', status: 1 });
  const engine = await Laya.openRouter({ models: { unused: __filename } });
  try {
    await assert.rejects(engine.execute('{}', null), { name: 'TypeError', status: 1 });
    await assert.rejects(engine.close(null), { name: 'TypeError', status: 1 });
    await assert.rejects(engine.close(5), { name: 'TypeError', status: 1 });
  } finally { await engine.close(); }
});
