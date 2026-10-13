export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type Question =
  | { type: 'choice'; instructions: Json; criteria: Record<string, Json> }
  | { type: 'score'; instructions: Json; criteria: Json[] }
  | { type: 'noul'; instructions: Json; criteria?: { false?: Json; true?: Json } };
export interface Request { state: Json; questions: Record<string, Question> }
export type Answer =
  | { type: 'choice'; choice: string; probabilities: Record<string, number>; confidence: number }
  | { type: 'score'; score: number; legend: Record<string, Json>; probabilities: Record<string, number>; confidence: number }
  | { type: 'noul'; noul: number };
export interface Result { answers: Record<string, Answer>; usage: { input_tokens: number; output_tokens: 0 } }
export class Laya {
  private constructor();
  static open(modelPath: string, options?: { threads?: number; maxPending?: number; addonPath?: string }): Promise<Laya>;
  decide(request: Request | string): Promise<Result>;
  close(): Promise<void>;
}
