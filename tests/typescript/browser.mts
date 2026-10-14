import { Laya, Request, StructuredRequest } from 'ducktape-laya-browser';

const request: Request = { state: 'Refund please', questions: {
  refund: { type: 'noul', instructions: 'Is a refund requested?' },
} };
async function consumer() {
  const engine = await Laya.open({ modelUrl: '/model.gguf', backend: 'auto',
    maxPending: 4, initializationTimeoutMs: 10000, signal: new AbortController().signal });
  await engine.decide(request, { onEvent: event => { const elapsed: number = event.elapsed_ms; void elapsed; } });
  const backend: 'wasm' | 'webgpu' = engine.capabilities.backend;
  const structured: StructuredRequest = { state: 'Refund', schema: {
    type: 'object', properties: { refund: { type: 'boolean' } },
  } };
  await engine.decideStructured(structured);
  await engine.decideBatch([request, structured]);
  await engine.close();
  const router = await Laya.openRouter({ english: { url: '/model.gguf' } }, { backend: 'wasm' });
  await router.execute({ ...request, model: 'english' });
  await router.close({ cancelPending: true });
  // @ts-expect-error Native paths are not browser model assets.
  await Laya.open('/model.gguf');
  // @ts-expect-error Only declared backends are accepted.
  await Laya.open({ modelUrl: '/model.gguf', backend: 'cuda' });
  void backend;
}
void consumer;
