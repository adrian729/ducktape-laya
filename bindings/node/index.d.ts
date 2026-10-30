export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type Question =
  | { type: 'choice'; instructions: Json; criteria: Record<string, Json> | string[] }
  | { type: 'score'; instructions: Json; criteria: Json[] }
  | { type: 'noul'; instructions: Json; criteria?: { false?: Json; true?: Json }; aggregate?: 'any' | 'all'; labels?: { false: string; true: string } };
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
export class Engine {
  private constructor();
  static runtimeInfo(): RuntimeInfo;
  info(): Promise<RuntimeInfo>;
  /** `modelPath`: a string, the bytes of a path (Buffer or Uint8Array; POSIX paths need not be UTF-8) or a `file:` URL. */
  static open(modelPath: string | Uint8Array | URL, options?: OpenOptions | null): Promise<Engine>;
  /** `router`: a RouterConfig object or its JSON text. */
  static openRouter(router: RouterConfig | string, options?: OpenOptions | null): Promise<Engine>;
  /** The only call: one request (object or JSON string) or an array of requests; arrays return arrays in order. */
  /** With `raw: true`: the core's exact result text. */
  execute(request: Request | StructuredRequest | (Request | StructuredRequest)[] | string, options: CallOptions & { raw: true }): Promise<string>;
  execute(request: Request, options?: (CallOptions & { raw?: false }) | null): Promise<Result>;
  execute(request: StructuredRequest, options?: (CallOptions & { raw?: false }) | null): Promise<StructuredResult>;
  execute(request: (Request | StructuredRequest)[], options?: (CallOptions & { raw?: false }) | null): Promise<(Result | StructuredResult)[]>;
  execute(request: string, options?: (CallOptions & { raw?: false }) | null): Promise<Result | StructuredResult | (Result | StructuredResult)[]>;
  /** Call it when done: it releases the model. An idle engine does not keep the process alive. Idempotent; resolves even if the worker already exited. */
  close(options?: { cancelPending?: boolean } | null): Promise<void>;
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
/** A passive observer event: `kind` is `start`, then `end` or `error`; `status` is the call's status (0 on start and end); `elapsedMs` is 0 on start. */
export interface LayaEvent { kind: 'start' | 'end' | 'error'; requestId: number; status: StatusCode; elapsedMs: number }
/**
 * `timeoutMs`: 0 or absent for no deadline, otherwise a non-negative safe integer counted from the call (enqueue), with no upper bound.
 * `requestId`: a non-negative safe integer reported in events (default 0). `raw`: resolve to the core's exact result text instead of the parsed value.
 */
export interface CallOptions { signal?: AbortSignal; timeoutMs?: number; requestId?: number; raw?: boolean; onEvent?: (event: LayaEvent) => void | Promise<void> }
/**
 * The native open config, with the native keys; the core validates every value (status 1).
 * `threads`: 0 for automatic (at most 4 and at most the CPUs the process may use) or 1..256. `context_tokens`: the prompt capacity
 * (default 512, at most the model's trained length). `max_windows_per_call`: the forward passes one call may plan
 * (1..4096, default 256; engine level, so a router accepts it in the shared config only).
 */
export interface Config { threads?: number; context_tokens?: number; overflow?: Overflow; temperatures?: Record<string, number>; max_windows_per_call?: number }
/** `config` is the native config (a router applies it to every route); `maxPending` bounds the queued calls (default 16). */
export interface OpenOptions { config?: Config | null; maxPending?: number }
export interface RouterRoute { path: string; config?: Omit<Config, 'max_windows_per_call'> }
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

/** The native status values. */
export type StatusCode = 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7;
/** Native status codes carried on `error.status`. Mirrors the C ABI `dtl_status`. */
export declare const Status: Readonly<{
  OK: 0; INVALID_ARGUMENT: 1; MODEL_ERROR: 2; INFERENCE_ERROR: 3;
  OUT_OF_MEMORY: 4; CANCELLED: 5; DEADLINE_EXCEEDED: 6; UNSUPPORTED_PLATFORM: 7;
}>;
export type LayaErrorCode = 'ERR_LAYA_CLOSED' | 'ERR_LAYA_QUEUE_FULL' | 'ERR_LAYA_WORKER_EXITED' | 'ERR_LAYA_UNSUPPORTED';
/** Every error this package raises, except that argument errors are `TypeError` or `RangeError` objects that also carry `status` 1. */
export class LayaError extends Error {
  constructor(message: string, status: StatusCode, code?: LayaErrorCode);
  status: StatusCode;
  code?: LayaErrorCode;
}
/** The addon or the native library cannot be found or loaded: status 7, code `ERR_LAYA_UNSUPPORTED`. */
export class LayaLoadError extends LayaError {
  constructor(message: string);
  status: 7;
  code: 'ERR_LAYA_UNSUPPORTED';
}
/** A call on a closing or closed engine: status 1, code `ERR_LAYA_CLOSED`. */
export class EngineClosedError extends LayaError {
  constructor(message?: string);
  status: 1;
  code: 'ERR_LAYA_CLOSED';
}
