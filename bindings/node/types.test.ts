import { Laya, Request, StructuredRequest, RouterConfig, ObserverEvent } from './index';
async function example(model: string) {
  const engine = await Laya.open(model, { threads: 2 });
  const primitive: Request = { state: 'Refund please', questions: {
    refund: { type: 'noul', instructions: 'Does the customer request a refund?' }
  }};
  const structured: StructuredRequest = { state: primitive.state, schema: {
    type: 'object', properties: { refund: { type: 'boolean' },
      priority: { type: 'integer', minimum: 1, maximum: 3 },
      department: { type: 'string', enum: ['billing', 'support'] }
    }, additionalProperties: false
  }};
  const events: ObserverEvent[] = [];
  const result = await engine.decideStructured(structured, { onEvent: e => { events.push(e); } });
  const value: string | number | boolean = result.values.refund;
  await engine.decideBatch([primitive, structured]);
  await engine.close();
  const config: RouterConfig = { models: { english: model }, max_loaded: 1 };
  const router = await Laya.openRouter(config);
  await router.decide({ ...primitive, model: 'english' });
  await router.close();
  // @ts-expect-error Free text is outside the supported schema subset.
  const bad: StructuredRequest = { state: 'x', schema: { type: 'object', properties: { x: { type: 'string' } } } };
  return { value, bad };
}
void example;
