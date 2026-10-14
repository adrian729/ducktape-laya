import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const workerSource = await readFile(new URL('../bindings/browser/worker.mjs', import.meta.url), 'utf8');
async function open(requested, actual) {
  let allocation = 32, fetched = false;
  const runtime = {
    HEAPU32: new Uint32Array(1024), _dtl_abi_version: () => 1,
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
    assert.equal(error.message, 'model-fetch-reached');
    assert.equal(fetched, true);
  });
}
