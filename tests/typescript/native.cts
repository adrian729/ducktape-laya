import { Laya, Status, Request, StructuredRequest, Result, LayaError } from 'ducktape-laya';

const request: Request = { state: 'Refund please', questions: {
  refund: { type: 'noul', instructions: 'Is a refund requested?' },
} };
const structured: StructuredRequest = { state: 'Refund please', schema: {
  type: 'object', properties: { refund: { type: 'boolean' } },
} };
async function consumer() {
  const engine = await Laya.open('/model.gguf', { threads: 2, maxPending: 4, contextTokens: 1024, temperatures: { choice: 1.5, 'noul.2': 2 } });
  const answer: Result = await engine.execute({ ...request, max_tokens: 256, temperatures: { noul: 1.2 } }, { requestId: 7 });
  await engine.execute([request, structured], { timeoutMs: 100, signal: new AbortController().signal,
    onEvent: event => { const status: number = event.status; void status; } });
  const values: Record<string, string | number | boolean> = (await engine.execute(structured)).values;
  const runtime: string = (await engine.info()).llama_cpp_commit;
  const profile: { id: string; cross_profile_equivalence: false } = (await engine.info()).numerical_profile;
  void profile;
  await engine.close({ cancelPending: true });
  const router = await Laya.openRouter({ models: { english: '/model.gguf' }, max_loaded: 1 });
  await router.execute([{ ...request, model: 'english' }]);
  const routed = await Laya.openRouter({ models: { english: { path: '/model.gguf', config: { context_tokens: 2048, temperatures: { choice: 1.5 } } } } });
  await routed.close();
  const contextTokens: number = (await engine.info()).model?.context_tokens ?? 0;
  void contextTokens;
  await router.close();
  // @ts-expect-error The lenient decide family was removed.
  engine.decide(request);
  // @ts-expect-error Thread count is numeric.
  await Laya.open('/model.gguf', { threads: '2' });
  // @ts-expect-error Structured results must preserve their projected value types.
  const invalid: boolean[] = values;
  void [answer, runtime, invalid];
}
void consumer;
const cancelled: 5 = Status.CANCELLED;
const typed = (e: LayaError) => e.code === 'ERR_LAYA_QUEUE_FULL' || e.status === Status.INVALID_ARGUMENT;
void cancelled; void typed;
