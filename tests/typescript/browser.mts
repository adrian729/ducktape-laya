import { Engine, Status, StatusCode, Request, StructuredRequest, Config, LayaError, LayaLoadError, EngineClosedError, Answer, Overflow, Routing, Usage, Window } from 'ducktape-laya-browser';

const request: Request = { state: 'Refund please', questions: {
  refund: { type: 'noul', instructions: 'Is a refund requested?' },
} };
async function consumer() {
  const engine = await Engine.open('/model.gguf', { backend: 'auto', maxPending: 4,
    config: { threads: 1, context_tokens: 1024, max_windows_per_call: 64, temperatures: { choice: 1.5 } }, initializationTimeoutMs: 10000, signal: new AbortController().signal });
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
  const router = await Engine.openRouter({ models: { english: { url: '/model.gguf', config: { context_tokens: 2048, temperatures: { noul: 2 } } } } }, { backend: 'wasm' });
  await router.execute({ ...request, model: 'english' });
  const config: Config = { overflow: 'reject' };
  const auto = await Engine.openRouter({ models: { english: '/model.gguf', other: { url: '/model.gguf', sha256: 'a'.repeat(64), config: { overflow: 'scan' } } },
    max_loaded: 1, auto: { english: 'english', default: 'other' } }, { config });
  await (await Engine.open({ url: '/model.gguf' }, null)).close();
  await auto.execute({ ...request, model: 'auto' });
  await auto.close();
  await router.close({ cancelPending: true });
  // @ts-expect-error The lenient decide family was removed.
  engine.decideBatch([request]);
  // @ts-expect-error The model is the first argument, not a property of the options.
  await Engine.open({ modelUrl: '/model.gguf' });
  // @ts-expect-error Only declared backends are accepted.
  await Engine.open('/model.gguf', { backend: 'cuda' });
  // @ts-expect-error The browser runs one thread.
  await Engine.open('/model.gguf', { config: { threads: 2 } });
  // @ts-expect-error A router takes its models in a models object.
  await Engine.openRouter({ english: '/model.gguf' });
  void backend;
}
void consumer;
const cancelled: 5 = Status.CANCELLED;
const typed = (e: LayaError) => e.code === 'ERR_LAYA_WORKER_EXITED' || e.status === Status.INVALID_ARGUMENT;
const classify = (e: unknown) => e instanceof LayaLoadError ? e.status : e instanceof EngineClosedError ? e.code : e instanceof LayaError ? (e.status satisfies StatusCode) : undefined;
void cancelled; void typed; void classify;
