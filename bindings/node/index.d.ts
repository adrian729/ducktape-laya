export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type Question =
  | { type: 'choice'; instructions: Json; criteria: Record<string, Json> | string[] }
  | { type: 'score'; instructions: Json; criteria: Json[] }
  | { type: 'noul'; instructions: Json; criteria?: { false?: Json; true?: Json }; aggregate?: 'any' | 'all' };
export type Overflow = 'reject' | 'truncate_end' | 'truncate_start' | 'scan';
export interface Request { state: Json; questions: Record<string, Question>; model?: string; lang?: string; max_tokens?: number; temperatures?: Record<string, number>; overflow?: Overflow; max_windows?: number }
export type Answer =
  | { type: 'choice'; choice: string; probabilities: Record<string, number>; confidence: number; answer_confidence: number; window?: Window }
  | { type: 'score'; score: number; legend: Record<string, Json>; probabilities: Record<string, number>; confidence: number; answer_confidence: number; window?: Window }
  | { type: 'noul'; noul: number; confidence: number; answer_confidence: number; window?: Window };
export interface Window { index: number; count: number; token_start: number; token_end: number }
/** `windows`: the most state windows any question used (1 when the state fitted), reported with `state_tokens`, `state_tokens_dropped` and `truncated_questions` whenever the effective overflow policy is not `reject`. */
export interface Usage { input_tokens: number; output_tokens: 0; state_tokens?: number; state_tokens_dropped?: number; windows?: number; truncated_questions?: string[]; routing?: Routing }
/** Why `"model": "auto"` chose its route. */
export interface Routing { reason: 'lang' | 'english_text' | 'no_letters' | 'non_latin_script' | 'too_short' | 'other_language' | 'non_english_letters' | 'no_english_words'; lang?: string; script?: string; language?: string }
export interface Result { answers: Record<string, Answer>; usage: Usage; model?: string }
export class Laya {
  private constructor();
  static runtimeInfo(options?: { addonPath?: string }): RuntimeInfo;
  info(): Promise<RuntimeInfo>;
  static open(modelPath: string, options?: OpenOptions): Promise<Laya>;
  static openRouter(config: RouterConfig | string, options?: OpenOptions): Promise<Laya>;
  /** The only call: one request (object or JSON string) or an array of requests; arrays return arrays in order. */
  execute(request: Request, options?: ExecuteOptions): Promise<Result>;
  execute(request: StructuredRequest, options?: ExecuteOptions): Promise<StructuredResult>;
  execute(request: (Request | StructuredRequest)[], options?: ExecuteOptions): Promise<(Result | StructuredResult)[]>;
  execute(request: string, options?: ExecuteOptions): Promise<Result | StructuredResult | (Result | StructuredResult)[]>;
  /** Required: the worker keeps the process alive until closed. Idempotent; resolves even if the worker already exited. */
  close(options?: { cancelPending?: boolean }): Promise<void>;
}

export type Property =
  | { type: 'string'; enum: string[]; description?: string; title?: string }
  | { type: 'boolean'; description?: string; title?: string }
  | { type: 'integer'; minimum: number; maximum: number; description?: string; title?: string };
export interface DecisionSchema {
  type: 'object'; properties: Record<string, Property>; required?: string[];
  additionalProperties?: false; description?: string; title?: string;
}
export interface StructuredRequest { state: Json; schema: DecisionSchema; boolean_threshold?: number; model?: string; lang?: string; max_tokens?: number; temperatures?: Record<string, number>; overflow?: Overflow; max_windows?: number }
export interface StructuredResult extends Result { values: Record<string, string | number | boolean> }
export interface ObserverEvent { event: 'start' | 'end' | 'error'; request_id: number; status: number; elapsed_ms: number }
export interface CallOptions { signal?: AbortSignal; timeoutMs?: number; onEvent?: (event: ObserverEvent) => void | Promise<void> }
/** `threads` (0 automatic: at most 4 and at most the CPUs the process may use, or 1..256), `contextTokens`, `overflow` and `temperatures` become the native open config; `contextTokens` is the prompt capacity (default 512, max the model's trained length). */
export interface OpenOptions { threads?: number; contextTokens?: number; overflow?: Overflow; temperatures?: Record<string, number>; maxPending?: number; addonPath?: string }
/** `requestId` (non-negative safe integer) is reported as `request_id` in events; defaults to a generated id. */
export interface ExecuteOptions extends CallOptions { requestId?: number }
export interface RouterRoute { path: string; config?: { threads?: number; context_tokens?: number; overflow?: Overflow; temperatures?: Record<string, number> } }
export interface RouterConfig { models: Record<string, string | RouterRoute>; max_loaded?: number; auto?: AutoRoutes }
/** Routes for `"model": "auto"`: confidently English text goes to `english`, everything else to `default`. */
export interface AutoRoutes { english: string; default: string }

export interface RuntimeInfo {
  schema_version: 1; package_version: string; abi_version: number; llama_cpp_commit: string;
  runtime_patches: { path: string; sha256: string; purpose: string }[];
  backend: 'native-cpu' | 'wasm-cpu' | 'webgpu'; compiler: string;
  numerical_profile: { id: string; cross_profile_equivalence: false };
  required_cpu_features: string[]; missing_cpu_features: string[]; cpu_supported: boolean;
  decision_types: string[]; features: string[]; overflow_policies: string[]; limits: Record<string, number>;
  model?: { architecture: string; name: string; decision_type: string; max_head_tokens: number; context_tokens: number; trained_context_tokens: number; temperatures: Record<string, number>; metadata_temperatures: Record<string, number> };
}

/** Native status codes carried on `error.status`. Mirrors the C ABI `dtl_status`. */
export declare const Status: Readonly<{
  OK: 0; INVALID_ARGUMENT: 1; MODEL_ERROR: 2; INFERENCE_ERROR: 3;
  OUT_OF_MEMORY: 4; CANCELLED: 5; DEADLINE_EXCEEDED: 6; UNSUPPORTED_PLATFORM: 7;
}>;
/** Shape of errors thrown by this package; `status` is a `Status` value, `code` a stable string. */
export interface LayaError extends Error {
  status: number;
  code?: 'ERR_LAYA_CLOSED' | 'ERR_LAYA_QUEUE_FULL' | 'ERR_LAYA_WORKER_EXITED' | 'ERR_LAYA_UNSUPPORTED';
}
