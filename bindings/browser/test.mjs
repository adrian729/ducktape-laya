// Run: node --experimental-vm-modules --test bindings/browser/test.mjs
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const source = await readFile(new URL('./index.mjs', import.meta.url), 'utf8');
async function harness() {
  const workers = [];
  const context = vm.createContext({ URL, TextEncoder, DOMException, performance, setTimeout, clearTimeout,
    location: { href: 'https://example.test/' },
    Worker: class {
      constructor() { this.sent = []; workers.push(this); }
      postMessage(message) {
        this.sent.push(message);
        if (message.kind === 'init') queueMicrotask(() => this.onmessage({ data: { id: message.id, result: { backend: 'wasm' } } }));
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
