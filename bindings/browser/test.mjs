// Run: node --experimental-vm-modules --test bindings/browser/test.mjs
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const source = await readFile(new URL('./index.mjs', import.meta.url), 'utf8');
const workerSource = await readFile(new URL('./worker.mjs', import.meta.url), 'utf8');
async function harness({ silentInit = false } = {}) {
  const workers = [];
  const context = vm.createContext({ URL, TextEncoder, DOMException, performance, setTimeout, clearTimeout,
    location: { href: 'https://example.test/' },
    Worker: class {
      constructor() { this.sent = []; workers.push(this); }
      postMessage(message) {
        this.sent.push(message);
        if (message.kind === 'init' && !silentInit) queueMicrotask(() => this.onmessage({ data: { id: message.id, result: { backend: 'wasm' } } }));
      }
      terminate() {}
    },
  });
  const module = new vm.SourceTextModule(source, { context,
    initializeImportMeta(meta) { meta.url = 'https://example.test/index.mjs'; },
  });
  await module.link(() => {}); await module.evaluate();
  return { Engine: module.namespace.Engine, workers };
}

test('invalid Unicode is rejected instead of replaced', async () => {
  const { Engine, workers } = await harness();
  const lone = 'a\ud800b';
  await assert.rejects(Engine.open('model.gguf', { config: { temperatures: { [lone]: 1 } } }), e => e.status === 1 && /not valid Unicode/.test(e.message));
  await assert.rejects(Engine.openRouter({ models: { [lone]: 'a.gguf' } }), e => e.status === 1 && /not valid Unicode/.test(e.message));
  await assert.rejects(Engine.openRouter({ models: { a: lone } }), e => e.status === 1 && /not valid Unicode/.test(e.message));
  assert.equal(workers.length, 0);
  const engine = await Engine.open('model.gguf');
  await assert.rejects(engine.execute({ state: lone, questions: {} }), e => e.status === 1 && /request is not valid Unicode/.test(e.message));
  await assert.rejects(engine.execute(`{"state":"${lone}"}`), e => e.status === 1 && /request is not valid Unicode/.test(e.message));
  for (const value of [NaN, Infinity])
    await assert.rejects(engine.execute({ state: { amount: value }, questions: {} }), e => e.status === 1 && /non-finite number/.test(e.message));
});

test('open takes a model and options; a router takes models, max_loaded and auto; null options are the defaults', async () => {
  const { Engine, workers } = await harness();
  await Engine.open({ url: 'model.gguf', sha256: 'a'.repeat(64) }, null);
  await Engine.openRouter({ models: { a: 'a.gguf', b: { url: 'b.gguf', config: { context_tokens: 64 } } }, max_loaded: 2, auto: { english: 'a', default: 'b' } }, { config: null });
  const [single, routed] = workers.map(worker => worker.sent.find(message => message.kind === 'init').options);
  assert.equal(single.assets.model.url, 'https://example.test/model.gguf');
  assert.equal(single.assets.model.sha256, 'a'.repeat(64));
  assert.equal(single.routed, false);
  assert.equal(routed.routed, true); assert.equal(routed.maxLoaded, 2);
  assert.equal(JSON.stringify(routed.assets.b.config), '{"context_tokens":64}');
  assert.equal(JSON.stringify(routed.auto), '{"english":"a","default":"b"}');
  // Binding-only conversions; config values themselves belong to the core.
  for (const model of [undefined, null, '', {}, { url: 5 }]) await assert.rejects(Engine.open(model), { name: 'TypeError', status: 1 });
  await assert.rejects(Engine.open('m.gguf', { config: { threads: 2 } }), { name: 'RangeError', status: 1 });
  await assert.rejects(Engine.openRouter({ models: { a: { url: 'a.gguf', config: { threads: 0 } } } }), { name: 'RangeError', status: 1 });
  await assert.rejects(Engine.openRouter({ models: { a: 'a.gguf' }, other: 1 }), { name: 'TypeError', status: 1 });
  await assert.rejects(Engine.open('m.gguf', { config: [] }), { name: 'TypeError', status: 1 });
  await assert.rejects(Engine.open({ url: 'm.gguf', sha256: 'zz' }), { name: 'TypeError', status: 1 });
  await assert.rejects(Engine.open('m.gguf', { backend: 'cuda' }), { name: 'TypeError', status: 1 });
  // An invalid config value is not the page's to judge: it reaches the worker.
  await Engine.open('m.gguf', { config: { context_tokens: 0, surprise: true } });
  assert.equal(JSON.stringify(workers.at(-1).sent.find(message => message.kind === 'init').options.config), '{"context_tokens":0,"surprise":true}');
});

test('error order: closed, then argument errors, then cancellation, then admission', async () => {
  const { Engine, workers } = await harness();
  const aborted = AbortSignal.abort();
  const engine = await Engine.open('model.gguf', { maxPending: 1 });
  const sentCount = () => workers[0].sent.length;
  await assert.rejects(engine.execute('{}', { signal: aborted, timeoutMs: -1 }), { name: 'RangeError', status: 1 });
  const before = sentCount();
  await assert.rejects(engine.execute('{}', { signal: aborted }), { status: 5 });
  assert.equal(sentCount(), before, 'an already-cancelled call sends nothing');
  const first = engine.execute('{}');
  await assert.rejects(engine.execute('{}', { signal: aborted }), { status: 5 });
  await assert.rejects(engine.execute('{}'), { code: 'ERR_LAYA_QUEUE_FULL' });
  const closing = engine.close({ cancelPending: true });
  await assert.rejects(first, { status: 5 });
  workers[0].onmessage({ data: { id: workers[0].sent.find(message => message.kind === 'close').id, result: null } });
  await closing;
  for (const options of [{ timeoutMs: -1, signal: aborted }, { signal: aborted }, 5]) {
    await assert.rejects(engine.execute('{}', options), e => e.name === 'EngineClosedError' && e.code === 'ERR_LAYA_CLOSED' && e.status === 1);
  }
});

test('no onEvent delivery after the call settled', async () => {
  const { Engine, workers } = await harness();
  const engine = await Engine.open('model.gguf');
  const events = [];
  const controller = new AbortController();
  const call = engine.execute({ state: {}, questions: {} }, { signal: controller.signal, onEvent: event => { events.push(event); } });
  const sent = workers[0].sent.find(message => message.kind === 'execute');
  workers[0].onmessage({ data: { id: sent.id, event: { event: 'start' } } });
  controller.abort();
  await assert.rejects(call, e => e.status === 5);
  workers[0].onmessage({ data: { id: sent.id, event: { event: 'end' } } });
  assert.deepEqual(events, [{ event: 'start' }]);
});

test('JSON serialization failures are argument errors with status', async () => {
  const { Engine, workers } = await harness();
  const circular = {}; circular.self = circular;
  await assert.rejects(Engine.open('model.gguf', { config: { temperatures: circular } }), e => e.status === 1);
  await assert.rejects(Engine.openRouter({ models: { a: 'a.gguf' }, auto: { english: 1n } }), e => e.name === 'TypeError' && e.status === 1);
  assert.equal(workers.length, 0);
  const engine = await Engine.open('model.gguf');
  await assert.rejects(engine.execute({ state: circular, questions: {} }), e => e.name === 'TypeError' && e.status === 1);
  await assert.rejects(engine.execute({ state: 1n, questions: {} }), e => e.name === 'TypeError' && e.status === 1);
});

test('initialization timeout carries the deadline status', async () => {
  const { Engine } = await harness({ silentInit: true });
  await assert.rejects(Engine.open('model.gguf', { initializationTimeoutMs: 5 }),
    e => e.status === 6 && /timed out/.test(e.message));
});

test('page deadline is wall-clock so the worker realm can measure it', async () => {
  const { Engine, workers } = await harness();
  const engine = await Engine.open('model.gguf');
  const before = Date.now();
  const call = engine.execute({ state: {}, questions: {} }, { timeoutMs: 60000 });
  const sent = workers[0].sent.find(message => message.kind === 'execute');
  assert.ok(sent.deadline >= before + 60000 && sent.deadline <= Date.now() + 60000);
  workers[0].onmessage({ data: { id: sent.id, result: '{}' } });
  await call;
});

async function workerHarness(abi = 2, { create, fetch: fetchAsset, openStatus = 0, validateStatus = 0 } = {}) {
  let top = 64, validated = false;
  const calls = [], strings = new Map(), heap = new Uint32Array(4096);
  const runtime = {
    HEAPU32: heap, _dtl_abi_version: () => abi, _dtl_default_call_options(pointer) { heap[pointer >>> 2] = 40; },
    lengthBytesUTF8: text => Buffer.byteLength(text), stringToUTF8: (text, pointer) => { strings.set(pointer, text); },
    _malloc: size => { const pointer = top; top += (size + 7) & ~7; return pointer; }, _free() {}, _dtl_free() {},
    UTF8ToString: () => JSON.stringify({ backend: 'wasm-cpu' }), addFunction: () => 11, removeFunction() {},
    FS: { files: new Map(), writeFile(path, bytes) { this.files.set(path, bytes); }, unlink(path) { this.files.delete(path); } },
    async ccall(name, type, types, args) {
      calls.push({ name, args: [...args], words: name === 'dtl_execute' ? [...heap.slice(args[2] >>> 2, (args[2] >>> 2) + 10)] : undefined });
      if (name === 'dtl_open' || name === 'dtl_open_router') {
        // The first router open is the validation over placeholder files; the next one is the real open.
        const status = name === 'dtl_open_router' && !validated ? (validated = true, validateStatus) : openStatus;
        heap[args[2] >>> 2] = status ? 0 : 1000; return status;
      }
      return 0;
    },
  };
  const context = vm.createContext({ onmessage: undefined, URL, performance, postMessage() {}, crypto,
    fetch: fetchAsset ?? (async () => ({ ok: true, headers: { get: () => null }, body: { getReader: () => { let done = false; return { read: async () => done ? { done: true } : (done = true, { done: false, value: new Uint8Array(4) }), cancel: async () => {} }; } } })) });
  const module = new vm.SyntheticModule(['default'], function() { this.setExport('default', create ?? (async () => runtime)); }, { context });
  await module.link(() => {}); await module.evaluate();
  const worker = new vm.SourceTextModule(workerSource + '\nexport { init, call };', { context, importModuleDynamically: async () => module });
  await worker.link(() => {}); await worker.evaluate();
  return { calls, runtime, namespace: worker.namespace };
}
// A fetch response whose body arrives in the given chunks, with an optional Content-Length.
const response = (chunks, length, status = 200, encoding = null, tracker = {}) => async () => ({ ok: status === 200, status,
  headers: { get: name => name === 'content-length' && length !== undefined ? String(length) : name === 'content-encoding' ? encoding : null },
  body: { cancel: async () => { tracker.cancelled = true; }, getReader: () => { let next = 0; return { read: async () => next < chunks.length ? { done: false, value: chunks[next++] } : { done: true }, cancel: async () => { tracker.cancelled = true; } }; } } });
const single = (extra = {}, sha256) => ({ backend: 'wasm', moduleUrl: 'https://example.test/laya.mjs', maxModelBytes: 1024, routed: false,
  assets: { model: { url: 'https://example.test/m.gguf', sha256 } }, ...extra });
const wasm = single();

test('worker forwards the auto routes into the router config', async () => {
  const { calls, namespace } = await workerHarness();
  await namespace.init(single({ routed: true, auto: { english: 'a', default: 'b' }, maxLoaded: 2, config: { context_tokens: 64 },
    assets: { a: { url: 'https://example.test/a.gguf' }, b: { url: 'https://example.test/b.gguf', config: { overflow: 'scan' } } } }));
  const opens = calls.filter(call => call.name === 'dtl_open_router');
  assert.equal(opens.length, 2, 'a validation open, then the real one');
  for (const open of opens) {
    const router = JSON.parse(open.args[0]);
    assert.deepEqual(router.auto, { english: 'a', default: 'b' });
    assert.equal(router.max_loaded, 2);
    assert.deepEqual(router.models.b.config, { overflow: 'scan' });
    assert.deepEqual(JSON.parse(open.args[1]), { context_tokens: 64, threads: 1 });
  }
  assert.match(JSON.parse(opens[0].args[0]).models.a, /^\/validate-/);
  assert.match(JSON.parse(opens[1].args[0]).models.a, /^\/model-/);
});

test('worker converts the absolute wall-clock deadline to remaining milliseconds', async () => {
  const { calls, namespace } = await workerHarness();
  await namespace.init(wasm);
  await namespace.call('dtl_execute', '{}', { id: 1, observe: false, deadline: Date.now() + 5000 });
  const timeout = calls.find(call => call.name === 'dtl_execute').words[4];
  assert.ok(timeout > 3000 && timeout <= 5000, `remaining ${timeout}`);
});

test('an incompatible runtime ABI fails with status 7 and the unsupported code', async () => {
  const { namespace } = await workerHarness(3);
  await assert.rejects(namespace.init(wasm),
    { status: 7, message: /Incompatible Laya WASM ABI/ });
});

test('the page maps status 7 worker errors to ERR_LAYA_UNSUPPORTED', async () => {
  const workers = [];
  globalThis.Worker = class { constructor() { workers.push(this); } postMessage(message) { this.sent = message; } terminate() {} };
  const { Engine } = await import('./index.mjs?unsupported');
  const opening = Engine.open('https://example.test/m.gguf', { moduleUrl: 'https://example.test/laya.mjs' });
  await new Promise(resolve => setTimeout(resolve, 0));
  workers[0].onmessage({ data: { id: workers[0].sent.id, error: 'Incompatible Laya WASM ABI', status: 7 } });
  await assert.rejects(opening, { name: 'LayaLoadError', status: 7, code: 'ERR_LAYA_UNSUPPORTED' });
});

test('model assets are read into one buffer whatever Content-Length says', async () => {
  const chunks = [new Uint8Array([1, 2, 3]), new Uint8Array([4, 5])];
  for (const length of [5, undefined, 3, 9]) {
    const { runtime, namespace } = await workerHarness(2, { fetch: response(chunks, length) });
    await namespace.init(wasm);
    const written = runtime.FS.files.get('/model-0.gguf');
    assert.deepEqual([...written], [1, 2, 3, 4, 5], `Content-Length ${length}`);
    assert.equal(written.buffer.byteLength, 5, `the written buffer holds only the model (Content-Length ${length})`);
  }
});

test('model asset failures carry their status and are final for backend auto', async () => {
  const cases = [
    [response([], undefined, 404), 2, /HTTP 404/],
    [async () => { throw new TypeError('network down'); }, 2, /Model fetch failed: network down/],
    [response([new Uint8Array(1000), new Uint8Array(100)], 1100), 1, /exceed maxModelBytes/],
    [response([new Uint8Array(4)], 4), 2, /SHA-256 mismatch/],
  ];
  for (const [fetchAsset, status, message] of cases) {
    const { namespace } = await workerHarness(2, { fetch: fetchAsset });
    await assert.rejects(namespace.init(single({}, 'a'.repeat(64))), error => error.status === status && message.test(error.message) && error.final === true);
  }
});

test('a Content-Length above maxModelBytes is rejected up front, and an allocation failure is final status 4', async () => {
  let read = false, cancelled = false;
  const declared = async () => ({ ok: true, headers: { get: name => name === 'content-length' ? '2048' : null },
    body: { getReader: () => ({ read: async () => { read = true; return { done: true }; }, cancel: async () => {} }), cancel: async () => { cancelled = true; } } });
  const { namespace } = await workerHarness(2, { fetch: declared });
  await assert.rejects(namespace.init(wasm), error => error.status === 1 && error.final === true && /exceed maxModelBytes/.test(error.message));
  assert.ok(!read && cancelled, 'no byte is read and the body is cancelled');
  const huge = await workerHarness(2, { fetch: response([new Uint8Array(4)], 2 ** 40) });
  await assert.rejects(huge.namespace.init({ ...wasm, maxModelBytes: 2 ** 41 }), error => error.status === 4 && error.final === true);
});

test('with a Content-Encoding the declared length is no bound: the stream decides, and a failed first allocation cancels the body', async () => {
  // Compressed length above the budget, decoded size within it (and the converse is caught while streaming).
  const ok = await workerHarness(2, { fetch: response([new Uint8Array([1, 2, 3])], 2048, 200, 'gzip') });
  await ok.namespace.init(wasm);
  const tracker = {};
  const over = await workerHarness(2, { fetch: response([new Uint8Array(1000), new Uint8Array(100)], 10, 200, 'br', tracker) });
  await assert.rejects(over.namespace.init(wasm), error => error.status === 1 && error.final === true && /exceed maxModelBytes/.test(error.message));
  assert.ok(tracker.cancelled, 'the body is cancelled when the stream exceeds the budget');
  // The first allocation sits inside the try: a RangeError there cancels the body.
  const failed = {};
  const huge = await workerHarness(2, { fetch: response([new Uint8Array(4)], 2 ** 40, 200, null, failed) });
  await assert.rejects(huge.namespace.init({ ...wasm, maxModelBytes: 2 ** 41 }), error => error.status === 4 && error.final === true);
  assert.ok(failed.cancelled, 'the body is cancelled after an allocation failure');
});

test('an invalid config fails with status 1 before any asset fetch and is final for backend auto; a model load failure is not', async () => {
  let fetches = 0;
  const counted = (...args) => { fetches++; return response([new Uint8Array(4)], 4)(...args); };
  const invalid = await workerHarness(2, { validateStatus: 1, fetch: counted });
  await assert.rejects(invalid.namespace.init(wasm), error => error.status === 1 && error.final === true);
  assert.equal(fetches, 0, 'no asset was fetched');
  assert.equal(invalid.runtime.FS.files.size, 0, 'the placeholders are removed');
  assert.ok(invalid.calls.some(call => call.name === 'dtl_open_router'));
  const failed = await workerHarness(2, { openStatus: 2 });
  await assert.rejects(failed.namespace.init(wasm), error => error.status === 2 && error.final !== true);
});

test('WebAssembly memory exhaustion at instantiation is status 4, other instantiation failures status 7', async () => {
  const cases = [
    [new RangeError('WebAssembly.Memory(): could not allocate memory'), 4],
    [new WebAssembly.RuntimeError('Aborted(RangeError: WebAssembly.instantiate(): Out of memory: Cannot allocate Wasm memory for new instance)'), 4],
    [new TypeError('Failed to fetch dynamically imported module'), 7],
    [new WebAssembly.CompileError('WebAssembly.instantiate(): expected magic word'), 7],
  ];
  for (const [error, status] of cases) {
    const { namespace } = await workerHarness(2, { create: async () => { throw error; } });
    await assert.rejects(namespace.init(wasm), e => e.status === status && e.message.includes(error.message) && !e.final);
  }
});
