import { Laya, Status, Request, StructuredRequest, LayaError, Answer, Overflow, Routing, Usage, Window } from 'ducktape-laya-browser';

const request: Request = { state: 'Refund please', questions: {
  refund: { type: 'noul', instructions: 'Is a refund requested?' },
} };
async function consumer() {
  const engine = await Laya.open({ modelUrl: '/model.gguf', backend: 'auto',
    maxPending: 4, contextTokens: 1024, temperatures: { choice: 1.5 }, initializationTimeoutMs: 10000, signal: new AbortController().signal });
  await engine.execute({ ...request, max_tokens: 128 }, { requestId: 3, onEvent: event => { const elapsed: number = event.elapsed_ms; void elapsed; } });
  const backend: 'wasm' | 'webgpu' = engine.capabilities.backend;
  const structured: StructuredRequest = { state: 'Refund', schema: {
    type: 'object', properties: { refund: { type: 'boolean' } },
  } };
  await engine.execute(structured);
  const overflow: Overflow = 'truncate_start';
  const windowed = await engine.execute({ ...request, overflow, max_windows: 2, lang: 'en', questions: {
    any: { type: 'noul', instructions: 'Any refund?', aggregate: 'any' } } });
  const answer: Answer = windowed.answers.any;
  const window: Window | undefined = answer.window;
  const usage: Usage = windowed.usage;
  const routing: Routing | undefined = usage.routing;
  const policies: string[] = (await engine.info()).overflow_policies;
  void [answer.answer_confidence, window, usage.windows, routing?.reason, policies];
  await engine.execute([request, structured]);
  const profile: { id: string; cross_profile_equivalence: false } = (await engine.info()).numerical_profile;
  void profile;
  await engine.close();
  const router = await Laya.openRouter({ english: { url: '/model.gguf', contextTokens: 2048, temperatures: { noul: 2 } } }, { backend: 'wasm' });
  await router.execute({ ...request, model: 'english' });
  const auto = await Laya.openRouter({ english: { url: '/model.gguf' }, other: { url: '/model.gguf', overflow: 'scan' } },
    { auto: { english: 'english', default: 'other' }, overflow: 'reject', maxLoaded: 1 });
  await auto.execute({ ...request, model: 'auto' });
  await auto.close();
  await router.close({ cancelPending: true });
  // @ts-expect-error The lenient decide family was removed.
  engine.decideBatch([request]);
  // @ts-expect-error Native paths are not browser model assets.
  await Laya.open('/model.gguf');
  // @ts-expect-error Only declared backends are accepted.
  await Laya.open({ modelUrl: '/model.gguf', backend: 'cuda' });
  void backend;
}
void consumer;
const cancelled: 5 = Status.CANCELLED;
const typed = (e: LayaError) => e.code === 'ERR_LAYA_WORKER_EXITED' || e.status === Status.INVALID_ARGUMENT;
void cancelled; void typed;
