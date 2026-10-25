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
  return { Laya: module.namespace.Laya, workers };
}

test('invalid Unicode is rejected instead of replaced', async () => {
  const { Laya, workers } = await harness();
  const lone = 'a\ud800b';
  await assert.rejects(Laya.open({ modelUrl: 'model.gguf', temperatures: { [lone]: 1 } }), e => e.status === 1 && /not valid Unicode/.test(e.message));
  await assert.rejects(Laya.openRouter({ [lone]: { url: 'a.gguf' } }), e => e.status === 1 && /not valid Unicode/.test(e.message));
  await assert.rejects(Laya.openRouter({ a: { url: lone } }), e => e.status === 1 && /not valid Unicode/.test(e.message));
  assert.equal(workers.length, 0);
  const engine = await Laya.open({ modelUrl: 'model.gguf' });
  await assert.rejects(engine.execute({ state: lone, questions: {} }), e => e.status === 1 && /request is not valid Unicode/.test(e.message));
  await assert.rejects(engine.execute(`{"state":"${lone}"}`), e => e.status === 1 && /request is not valid Unicode/.test(e.message));
});

test('per-route overflow is validated', async () => {
  const { Laya, workers } = await harness();
  await assert.rejects(Laya.openRouter({ a: { url: 'a.gguf', overflow: 'nope' } }), /overflow/);
  assert.equal(workers.length, 0);
});

test('no onEvent delivery after the call settled', async () => {
  const { Laya, workers } = await harness();
  const engine = await Laya.open({ modelUrl: 'model.gguf' });
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
  const { Laya, workers } = await harness();
  const circular = {}; circular.self = circular;
  await assert.rejects(Laya.open({ modelUrl: 'model.gguf', temperatures: circular }), e => e.status === 1);
  await assert.rejects(Laya.open({ modelUrl: 'model.gguf', auto: { english: 1n } }), e => e.name === 'TypeError' && e.status === 1);
  assert.equal(workers.length, 0);
  const engine = await Laya.open({ modelUrl: 'model.gguf' });
  await assert.rejects(engine.execute({ state: circular, questions: {} }), e => e.name === 'TypeError' && e.status === 1);
  await assert.rejects(engine.execute({ state: 1n, questions: {} }), e => e.name === 'TypeError' && e.status === 1);
});

test('initialization timeout carries the deadline status', async () => {
  const { Laya } = await harness({ silentInit: true });
  await assert.rejects(Laya.open({ modelUrl: 'model.gguf', initializationTimeoutMs: 5 }),
    e => e.status === 6 && /timed out/.test(e.message));
});

test('page deadline is wall-clock so the worker realm can measure it', async () => {
  const { Laya, workers } = await harness();
  const engine = await Laya.open({ modelUrl: 'model.gguf' });
  const before = Date.now();
  const call = engine.execute({ state: {}, questions: {} }, { timeoutMs: 60000 });
  const sent = workers[0].sent.find(message => message.kind === 'execute');
  assert.ok(sent.deadline >= before + 60000 && sent.deadline <= Date.now() + 60000);
  workers[0].onmessage({ data: { id: sent.id, result: '{}' } });
  await call;
});

async function workerHarness(abi = 2, { create, fetch: fetchAsset } = {}) {
  let top = 64;
  const calls = [], strings = new Map(), heap = new Uint32Array(4096);
  const runtime = {
    HEAPU32: heap, _dtl_abi_version: () => abi, _dtl_default_call_options(pointer) { heap[pointer >>> 2] = 40; },
    lengthBytesUTF8: text => Buffer.byteLength(text), stringToUTF8: (text, pointer) => { strings.set(pointer, text); },
    _malloc: size => { const pointer = top; top += (size + 7) & ~7; return pointer; }, _free() {}, _dtl_free() {},
    UTF8ToString: () => JSON.stringify({ backend: 'wasm-cpu' }), addFunction: () => 11, removeFunction() {},
    FS: { files: new Map(), writeFile(path, bytes) { this.files.set(path, bytes); }, unlink() {} },
    async ccall(name, type, types, args) {
      calls.push({ name, args: [...args], words: name === 'dtl_execute' ? [...heap.slice(args[2] >>> 2, (args[2] >>> 2) + 10)] : undefined });
      if (name === 'dtl_open' || name === 'dtl_open_router') heap[args[2] >>> 2] = 1000;
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
const wasm = { backend: 'wasm', moduleUrl: 'https://example.test/laya.mjs', maxModelBytes: 1024, modelUrl: 'https://example.test/m.gguf' };

test('worker forwards the auto routes into the router config', async () => {
  const { calls, namespace } = await workerHarness();
  await namespace.init({ backend: 'wasm', moduleUrl: 'https://example.test/laya.mjs', maxModelBytes: 1024,
    auto: { english: 'a', default: 'b' },
    models: { a: { url: 'https://example.test/a.gguf' }, b: { url: 'https://example.test/b.gguf' } } });
  const open = calls.find(call => call.name === 'dtl_open_router');
  assert.deepEqual(JSON.parse(open.args[0]).auto, { english: 'a', default: 'b' });
});

test('worker converts the absolute wall-clock deadline to remaining milliseconds', async () => {
  const { calls, namespace } = await workerHarness();
  await namespace.init({ backend: 'wasm', moduleUrl: 'https://example.test/laya.mjs', maxModelBytes: 1024, modelUrl: 'https://example.test/m.gguf' });
  await namespace.call('dtl_execute', '{}', { id: 1, observe: false, deadline: Date.now() + 5000 });
  const timeout = calls.find(call => call.name === 'dtl_execute').words[4];
  assert.ok(timeout > 3000 && timeout <= 5000, `remaining ${timeout}`);
});

test('an incompatible runtime ABI fails with status 7 and the unsupported code', async () => {
  const { namespace } = await workerHarness(3);
  await assert.rejects(namespace.init({ backend: 'wasm', moduleUrl: 'https://example.test/laya.mjs', maxModelBytes: 1024, modelUrl: 'https://example.test/m.gguf' }),
    { status: 7, message: /Incompatible Laya WASM ABI/ });
});

test('the page maps status 7 worker errors to ERR_LAYA_UNSUPPORTED', async () => {
  const workers = [];
  globalThis.Worker = class { constructor() { workers.push(this); } postMessage(message) { this.sent = message; } terminate() {} };
  const { Laya } = await import('./index.mjs?unsupported');
  const opening = Laya.open({ modelUrl: 'https://example.test/m.gguf', moduleUrl: 'https://example.test/laya.mjs' });
  await new Promise(resolve => setTimeout(resolve, 0));
  workers[0].onmessage({ data: { id: workers[0].sent.id, error: 'Incompatible Laya WASM ABI', status: 7 } });
  await assert.rejects(opening, { status: 7, code: 'ERR_LAYA_UNSUPPORTED' });
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
    await assert.rejects(namespace.init({ ...wasm, sha256: 'a'.repeat(64) }), error => error.status === status && message.test(error.message) && error.final === true);
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
