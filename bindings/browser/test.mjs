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

async function workerHarness() {
  let top = 64;
  const calls = [], strings = new Map(), heap = new Uint32Array(4096);
  const runtime = {
    HEAPU32: heap, _dtl_abi_version: () => 2, _dtl_default_call_options(pointer) { heap[pointer >>> 2] = 40; },
    lengthBytesUTF8: text => Buffer.byteLength(text), stringToUTF8: (text, pointer) => { strings.set(pointer, text); },
    _malloc: size => { const pointer = top; top += (size + 7) & ~7; return pointer; }, _free() {}, _dtl_free() {},
    UTF8ToString: () => JSON.stringify({ backend: 'wasm-cpu' }), addFunction: () => 11, removeFunction() {},
    FS: { writeFile() {}, unlink() {} },
    async ccall(name, type, types, args) {
      calls.push({ name, args: [...args], words: name === 'dtl_execute' ? [...heap.slice(args[2] >>> 2, (args[2] >>> 2) + 10)] : undefined });
      if (name === 'dtl_open' || name === 'dtl_open_router') heap[args[2] >>> 2] = 1000;
      return 0;
    },
  };
  const context = vm.createContext({ onmessage: undefined, URL, performance, postMessage() {},
    fetch: async () => ({ ok: true, body: { getReader: () => { let done = false; return { read: async () => done ? { done: true } : (done = true, { done: false, value: new Uint8Array(4) }), cancel: async () => {} }; } } }) });
  const module = new vm.SyntheticModule(['default'], function() { this.setExport('default', async () => runtime); }, { context });
  await module.link(() => {}); await module.evaluate();
  const worker = new vm.SourceTextModule(workerSource + '\nexport { init, call };', { context, importModuleDynamically: async () => module });
  await worker.link(() => {}); await worker.evaluate();
  return { calls, namespace: worker.namespace };
}

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
