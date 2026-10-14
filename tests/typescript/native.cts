import { Laya, Request, StructuredRequest, Result } from 'ducktape-laya';

const request: Request = { state: 'Refund please', questions: {
  refund: { type: 'noul', instructions: 'Is a refund requested?' },
} };
const structured: StructuredRequest = { state: 'Refund please', schema: {
  type: 'object', properties: { refund: { type: 'boolean' } },
} };
async function consumer() {
  const engine = await Laya.open('/model.gguf', { threads: 2, maxPending: 4 });
  const answer: Result = await engine.decide(request);
  await engine.execute([request, structured], { timeoutMs: 100, signal: new AbortController().signal,
    onEvent: event => { const status: number = event.status; void status; } });
  const values: Record<string, string | number | boolean> = (await engine.decideStructured(structured)).values;
  const runtime: string = (await engine.info()).llama_cpp_commit;
  const profile: { id: string; cross_profile_equivalence: false } = (await engine.info()).numerical_profile;
  void profile;
  await engine.close({ cancelPending: true });
  const router = await Laya.openRouter({ models: { english: '/model.gguf' }, max_loaded: 1 });
  await router.decideBatch([{ ...request, model: 'english' }]);
  await router.close();
  // @ts-expect-error Thread count is numeric.
  await Laya.open('/model.gguf', { threads: '2' });
  // @ts-expect-error Structured results must preserve their projected value types.
  const invalid: boolean[] = values;
  void [answer, runtime, invalid];
}
void consumer;
