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
  return { Laya: module.namespace.Laya, Status: module.namespace.Status, workers };
}

test('abort during option normalization rejects and terminates the worker', async () => {
  const { Laya, workers } = await harness();
  const controller = new AbortController();
  await assert.rejects(Laya.open({ signal: controller.signal, backend: 'auto',
    get modelUrl() { controller.abort(); return 'model.gguf'; },
  }), { status: 5, message: 'Initialization cancelled' });
  assert.equal(workers.length, 1, 'aborted auto initialization must not fall back');
  assert.ok(workers.every(worker => worker.terminated));
});

test('abort after ready delivery but before open resumes cannot return a dead engine', async () => {
  const controller = new AbortController();
  const { Laya, workers } = await harness(() => controller.abort());
  await assert.rejects(Laya.open({ modelUrl: 'model.gguf', signal: controller.signal }), { status: 5, message: 'Initialization cancelled' });
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
  await assert.rejects(engine.info(), error => error.code === 'ERR_LAYA_CLOSED');
});

test('throwing diagnostic option getters cannot leak a created worker', async () => {
  for (const throwOnRead of [1, 2, 3]) {
    const { Laya, workers } = await harness();
    let reads = 0;
    let engine;
    try {
      engine = await Laya.open({ modelUrl: 'model.gguf', get onDiagnostic() {
        if (++reads === throwOnRead) throw new Error('diagnostic getter failed');
        return () => {};
      } });
    } catch (error) { assert.equal(error.message, 'diagnostic getter failed'); }
    if (engine) await engine.close();
    assert.ok(workers.every(worker => worker.terminated));
  }
});

test('close after worker failure resolves, is idempotent and terminates; errors are typed', async () => {
  const { Laya, Status, workers } = await harness();
  assert.equal(Status.INVALID_ARGUMENT, 1); assert.ok(Object.isFrozen(Status));
  const engine = await Laya.open({ modelUrl: 'model.gguf' });
  workers[0].onerror({ message: 'boom' });
  await assert.rejects(engine.info(), { code: 'ERR_LAYA_WORKER_EXITED', status: 3 });
  const close = engine.close();
  assert.equal(engine.close(), close);
  await close;
  assert.ok(workers[0].terminated);
  await assert.rejects(engine.info(), { code: 'ERR_LAYA_CLOSED', status: 1 });
});

test('validation errors carry status 1 and queue-full carries a code', async () => {
  const { Laya } = await harness();
  await assert.rejects(Laya.open({ modelUrl: 'model.gguf', maxPending: 0 }), { name: 'RangeError', status: 1 });
  const engine = await Laya.open({ modelUrl: 'model.gguf', maxPending: 1 });
  await assert.rejects(engine.execute('\0'), { name: 'TypeError', status: 1 });
  await assert.rejects(engine.execute('{}', { timeoutMs: -1 }), { name: 'RangeError', status: 1 });
  const first = engine.execute('{}');
  await assert.rejects(engine.execute('{}'), { code: 'ERR_LAYA_QUEUE_FULL', status: 1 });
  await first;
  await engine.close();
});

test('null or non-object options are argument errors with status 1', async () => {
  const { Laya, workers } = await harness();
  await assert.rejects(Laya.open(null), { name: 'TypeError', status: 1 });
  await assert.rejects(Laya.open(5), { name: 'TypeError', status: 1 });
  await assert.rejects(Laya.openRouter({ a: { url: 'a.gguf' } }, null), { name: 'TypeError', status: 1 });
  assert.equal(workers.length, 0);
  const engine = await Laya.open({ modelUrl: 'model.gguf' });
  await assert.rejects(engine.execute('{}', null), { name: 'TypeError', status: 1 });
  await assert.rejects(engine.close(null), { name: 'TypeError', status: 1 });
  await assert.rejects(engine.close('now'), { name: 'TypeError', status: 1 });
  await engine.close();
  await assert.rejects(engine.execute('{}'), { code: 'ERR_LAYA_CLOSED', status: 1 });
});

test('backend auto retries with WASM only after a failure that is not a model asset failure', async () => {
  for (const [final, attempts] of [[true, 1], [false, 2]]) {
    const workers = [];
    const context = vm.createContext({ URL, TextEncoder, DOMException, performance, setTimeout, clearTimeout,
      location: { href: 'https://example.test/' },
      Worker: class {
        constructor() { workers.push(this); }
        postMessage(message) {
          if (message.kind === 'init') queueMicrotask(() => this.onmessage({ data: { id: message.id, error: 'Model fetch failed: HTTP 404', status: 2, final } }));
        }
        terminate() { this.terminated = true; }
      },
    });
    const module = new vm.SourceTextModule(source, { context, initializeImportMeta(meta) { meta.url = 'https://example.test/index.mjs'; } });
    await module.link(() => {}); await module.evaluate();
    await assert.rejects(module.namespace.Laya.open({ modelUrl: 'model.gguf', backend: 'auto' }), { status: 2, message: /HTTP 404/ });
    assert.equal(workers.length, attempts, `final=${final}`);
    assert.ok(workers.every(worker => worker.terminated));
  }
});

test('execute is the only call and new open/call options are validated', async () => {
  const { Laya, workers } = await harness();
  for (const bad of [0, -1, 1.5, '512', NaN]) {
    await assert.rejects(Laya.open({ modelUrl: 'model.gguf', contextTokens: bad }), { name: 'RangeError', status: 1 });
  }
  for (const bad of [null, [], 'x', { choice: 0 }, { choice: -1 }, { choice: NaN }, { choice: '1' }]) {
    await assert.rejects(Laya.open({ modelUrl: 'model.gguf', temperatures: bad }), { name: 'TypeError', status: 1 });
  }
  for (const bad of ['', 'bogus', null, 1]) {
    await assert.rejects(Laya.open({ modelUrl: 'model.gguf', overflow: bad }), { name: 'RangeError', status: 1 });
  }
  assert.equal(workers.length, 0);
  const engine = await Laya.open({ modelUrl: 'model.gguf', contextTokens: 256, temperatures: { choice: 1.5 } });
  for (const name of ['decide', 'decideStructured', 'decideBatch']) assert.equal(engine[name], undefined);
  assert.equal(typeof engine.execute, 'function');
  for (const requestId of [-1, 1.5, 2 ** 53, '1']) {
    await assert.rejects(engine.execute({}, { requestId }), { name: 'RangeError', status: 1 });
  }
  assert.equal(JSON.stringify(await engine.execute([{}], { requestId: 7 })), '{}');
  await engine.close();
});
