import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const workerSource = await readFile(new URL('../bindings/browser/worker.mjs', import.meta.url), 'utf8');
async function open(requested, actual) {
  let allocation = 32, fetched = false;
  const runtime = {
    HEAPU32: new Uint32Array(1024), _dtl_abi_version: () => 2, _dtl_default_call_options(pointer) { this.HEAPU32[pointer >>> 2] = 40; },
    _malloc: () => { allocation += 32; return allocation; }, _free() {}, _dtl_free() {},
    UTF8ToString: () => JSON.stringify({ backend: actual }), addFunction: () => 1,
    ccall(name, type, types, args) {
      assert.equal(name, 'dtl_info');
      this.HEAPU32[args[1] >>> 2] = 512;
      return 0;
    },
  };
  const context = vm.createContext({
    onmessage: undefined, URL, performance,
    navigator: { gpu: { requestAdapter: async () => ({}) } },
    GPUAdapter: class { requestDevice() {} },
    fetch: async () => { fetched = true; throw new Error('model-fetch-reached'); },
  });
  const module = new vm.SyntheticModule(['default'], function() {
    this.setExport('default', async () => runtime);
  }, { context });
  await module.link(() => {});
  await module.evaluate();
  const worker = new vm.SourceTextModule(workerSource + '\nexport { init };', {
    context, importModuleDynamically: async () => module,
  });
  await worker.link(() => {});
  await worker.evaluate();
  try {
    await worker.namespace.init({ backend: requested, moduleUrl: 'https://example.test/laya.mjs', modelUrl: 'model' });
  } catch (error) { return { error, fetched }; }
  assert.fail('Expected a mismatch or intercepted model fetch');
}
for (const [requested, actual] of [['wasm', 'webgpu'], ['webgpu', 'wasm-cpu']]) {
  test(`reject ${actual} in ${requested} slot before model download`, async () => {
    const { error, fetched } = await open(requested, actual);
    assert.match(error.message, /Laya backend mismatch/);
    assert.equal(fetched, false);
  });
}
for (const [requested, actual] of [['wasm', 'wasm-cpu'], ['webgpu', 'webgpu']]) {
  test(`accept matching ${requested} runtime`, async () => {
    const { error, fetched } = await open(requested, actual);
    assert.match(error.message, /^Model fetch failed: model-fetch-reached$/);
    assert.equal(error.status, 2);
    assert.equal(error.final, true, 'a model fetch failure is not retried with another backend');
    assert.equal(fetched, true);
  });
}

test('worker passes ABI 2 open config and the wasm32 call-options layout', async () => {
  let top = 64;
  const calls = [], posted = [], strings = new Map();
  const heap = new Uint32Array(4096);
  const runtime = {
    HEAPU32: heap, _dtl_abi_version: () => 2,
    lengthBytesUTF8: text => Buffer.byteLength(text), stringToUTF8: (text, pointer) => { strings.set(pointer, text); },
    _dtl_default_call_options(pointer) { heap[pointer >>> 2] = 40; },
    _malloc: size => { const pointer = top; top += (size + 7) & ~7; return pointer; }, _free() {}, _dtl_free() {},
    UTF8ToString: () => JSON.stringify({ backend: 'wasm-cpu' }), addFunction: () => 11, removeFunction() {},
    FS: { writeFile() {}, unlink() {} },
    async ccall(name, type, types, args) {
      calls.push({ name, types, args: [...args], request: name === 'dtl_execute' ? strings.get(args[1]) : undefined,
                   words: name === 'dtl_execute' ? [...heap.slice(args[2] >>> 2, (args[2] >>> 2) + 10)] : undefined });
      if (name === 'dtl_open' || name === 'dtl_open_router') heap[args[2] >>> 2] = 1000;
      return 0;
    },
  };
  const context = vm.createContext({
    onmessage: undefined, URL, performance, postMessage: message => posted.push(message),
    fetch: async () => ({ ok: true, headers: { get: () => null }, body: { getReader: () => { let done = false; return { read: async () => done ? { done: true } : (done = true, { done: false, value: new Uint8Array(4) }), cancel: async () => {} }; } } }),
  });
  const module = new vm.SyntheticModule(['default'], function() { this.setExport('default', async () => runtime); }, { context });
  await module.link(() => {}); await module.evaluate();
  const worker = new vm.SourceTextModule(workerSource + '\nexport { init, call };', { context, importModuleDynamically: async () => module });
  await worker.link(() => {}); await worker.evaluate();
  const base = { backend: 'wasm', moduleUrl: 'https://example.test/laya.mjs', maxModelBytes: 1024 };
  await worker.namespace.init({ ...base, modelUrl: 'https://example.test/m.gguf', contextTokens: 256, temperatures: { 'choice.3_5': 1.7 } });
  const open = calls.find(call => call.name === 'dtl_open');
  assert.deepEqual(JSON.parse(open.args[1]), { threads: 1, context_tokens: 256, temperatures: { 'choice.3_5': 1.7 } });
  const info = JSON.parse(JSON.stringify(calls)).filter(call => call.name === 'dtl_info');
  assert.equal(info.length, 1);
  await worker.namespace.call('dtl_execute', '{}', { id: 3, requestId: 2 ** 32 + 5, observe: true, deadline: 0 });
  const execute = calls.find(call => call.name === 'dtl_execute');
  // The request is passed as a heap pointer, not on the WASM stack.
  assert.equal(execute.args[0], 1000); assert.equal(execute.types[1], 'number'); assert.equal(execute.request, '{}');
  // size, reserved, request_id (lo, hi), timeout (lo, hi), on_event, event_user_data, should_cancel, cancel_user_data
  assert.deepEqual(execute.words.slice(0, 6), [40, 0, 5, 1, 0, 0]);
  assert.equal(execute.words[6], 11); assert.equal(execute.words[7], 0); assert.equal(execute.words[9], 0);
  await worker.namespace.call('dtl_execute', '{}', { id: 4, observe: false, deadline: 0 });
  const second = calls.filter(call => call.name === 'dtl_execute')[1];
  assert.deepEqual(second.words.slice(2, 4), [4, 0]); assert.equal(second.words[6], 0);
  await worker.namespace.init({ ...base, modelUrl: 'https://example.test/m.gguf', overflow: 'scan' });
  assert.deepEqual(JSON.parse(calls.filter(call => call.name === 'dtl_open')[1].args[1]), { threads: 1, overflow: 'scan' });
});

test('worker serializes per-route router config', async () => {
  let top = 64;
  const calls = [];
  const heap = new Uint32Array(4096);
  const runtime = {
    HEAPU32: heap, _dtl_abi_version: () => 2, _dtl_default_call_options(pointer) { heap[pointer >>> 2] = 40; },
    _malloc: size => { const pointer = top; top += (size + 7) & ~7; return pointer; }, _free() {}, _dtl_free() {},
    UTF8ToString: () => JSON.stringify({ backend: 'wasm-cpu' }), addFunction: () => 11, removeFunction() {},
    FS: { writeFile() {}, unlink() {} },
    async ccall(name, type, types, args) { calls.push({ name, args: [...args] }); if (name === 'dtl_open_router') heap[args[2] >>> 2] = 1000; return 0; },
  };
  const context = vm.createContext({ onmessage: undefined, URL, performance, postMessage() {},
    fetch: async () => ({ ok: true, headers: { get: () => null }, body: { getReader: () => { let done = false; return { read: async () => done ? { done: true } : (done = true, { done: false, value: new Uint8Array(4) }), cancel: async () => {} }; } } }) });
  const module = new vm.SyntheticModule(['default'], function() { this.setExport('default', async () => runtime); }, { context });
  await module.link(() => {}); await module.evaluate();
  const worker = new vm.SourceTextModule(workerSource + '\nexport { init };', { context, importModuleDynamically: async () => module });
  await worker.link(() => {}); await worker.evaluate();
  await worker.namespace.init({ backend: 'wasm', moduleUrl: 'https://example.test/laya.mjs', maxModelBytes: 1024, contextTokens: 128,
    models: { a: { url: 'https://example.test/a.gguf', contextTokens: 1024 }, b: { url: 'https://example.test/b.gguf', temperatures: { noul: 2 } } } });
  const open = calls.find(call => call.name === 'dtl_open_router');
  assert.deepEqual(JSON.parse(open.args[0]), { models: { a: { path: '/model-0.gguf', config: { context_tokens: 1024 } },
    b: { path: '/model-1.gguf', config: { temperatures: { noul: 2 } } } }, max_loaded: 1 });
  assert.deepEqual(JSON.parse(open.args[1]), { threads: 1, context_tokens: 128 });
});
