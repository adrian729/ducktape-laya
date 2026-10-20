import { Laya, Status, Request, StructuredRequest, Result, LayaError, Answer, Overflow, Routing } from 'ducktape-laya';

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
  const overflow: Overflow = 'scan';
  const windowed = await engine.execute({ ...request, overflow, max_windows: 4, lang: 'en', questions: {
    any: { type: 'noul', instructions: 'Any refund?', aggregate: 'any' }, all: { type: 'noul', instructions: 'All refund?', aggregate: 'all' } } });
  const first: Answer = windowed.answers.any;
  const confidence: number = first.answer_confidence + first.confidence;
  const index: number | undefined = first.window?.index;
  const windows: number | undefined = windowed.usage.windows;
  const routing: Routing | undefined = windowed.usage.routing;
  const reason: string | undefined = routing?.reason;
  const policies: string[] = (await engine.info()).overflow_policies;
  void [confidence, index, windows, reason, policies];
  await engine.close({ cancelPending: true });
  const router = await Laya.openRouter({ models: { english: '/model.gguf' }, max_loaded: 1 });
  await router.execute([{ ...request, model: 'english' }]);
  const auto = await Laya.openRouter({ models: { english: '/model.gguf', other: '/model.gguf' }, auto: { english: 'english', default: 'other' } }, { overflow: 'truncate_end' });
  const routedResult: Result = await auto.execute({ ...request, model: 'auto', lang: 'de' });
  const routedModel: string | undefined = routedResult.model;
  void routedModel;
  await auto.close();
  const routed = await Laya.openRouter({ models: { english: { path: '/model.gguf', config: { context_tokens: 2048, temperatures: { choice: 1.5 } } } } });
  await routed.close();
  const contextTokens: number = (await engine.info()).model?.context_tokens ?? 0;
  void contextTokens;
  await router.close();
  // @ts-expect-error The lenient decide family was removed.
  engine.decide(request);
  // @ts-expect-error Unknown overflow policies are rejected.
  await Laya.open('/model.gguf', { overflow: 'drop' });
  // @ts-expect-error Auto routes need both entries.
  await Laya.openRouter({ models: { english: '/model.gguf' }, auto: { english: 'english' } });
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
