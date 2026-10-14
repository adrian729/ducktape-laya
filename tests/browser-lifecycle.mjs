import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const source = await readFile(new URL('../bindings/browser/index.mjs', import.meta.url), 'utf8');
async function harness(afterReady = () => {}) {
  const workers = [];
  const context = vm.createContext({ URL, TextEncoder, DOMException, performance, setTimeout, clearTimeout,
    location: { href: 'https://example.test/' },
    Worker: class {
      constructor() { workers.push(this); }
      postMessage(message) {
        if (message.kind === 'cancel') return;
        queueMicrotask(() => {
          this.onmessage({ data: { id: message.id, result: message.kind === 'init' ? { backend: 'wasm' } : '{}' } });
          if (message.kind === 'init') afterReady();
        });
      }
      terminate() { this.terminated = true; }
    },
  });
  const module = new vm.SourceTextModule(source, { context,
    initializeImportMeta(meta) { meta.url = 'https://example.test/index.mjs'; },
  });
  await module.link(() => {}); await module.evaluate();
  return { Laya: module.namespace.Laya, workers };
}

test('abort during option normalization rejects and terminates the worker', async () => {
  const { Laya, workers } = await harness();
  const controller = new AbortController();
  await assert.rejects(Laya.open({ signal: controller.signal, backend: 'auto',
    get modelUrl() { controller.abort(); return 'model.gguf'; },
  }), { name: 'AbortError' });
  assert.equal(workers.length, 1, 'aborted auto initialization must not fall back');
  assert.ok(workers.every(worker => worker.terminated));
});

test('abort after ready delivery but before open resumes cannot return a dead engine', async () => {
  const controller = new AbortController();
  const { Laya, workers } = await harness(() => controller.abort());
  await assert.rejects(Laya.open({ modelUrl: 'model.gguf', signal: controller.signal }), { name: 'AbortError' });
  assert.ok(workers.every(worker => worker.terminated));
});

test('invalid signals and overflowing initialization timers reject before starting workers', async () => {
  const { Laya, workers } = await harness();
  for (const signal of [null, false, 0, {}, '']) {
    await assert.rejects(Laya.open({ modelUrl: 'model.gguf', signal }), /AbortSignal/);
  }
  for (const initializationTimeoutMs of [0, -1, 0.5, Infinity, 86400001, 2 ** 32]) {
    await assert.rejects(Laya.open({ modelUrl: 'model.gguf', initializationTimeoutMs }), /initializationTimeoutMs/);
  }
  assert.equal(workers.length, 0);
});

test('successful open stays usable and close terminates its worker', async () => {
  const { Laya, workers } = await harness();
  const engine = await Laya.open({ modelUrl: 'model.gguf', signal: new AbortController().signal });
  assert.deepEqual(JSON.parse(JSON.stringify(await engine.info())), {});
  await assert.rejects(engine.execute({}, { signal: null }), /AbortSignal/);
  const close = engine.close();
  assert.equal(engine.close(), close);
  await close;
  assert.ok(workers[0].terminated);
  await assert.rejects(engine.info(), /closed/);
});
