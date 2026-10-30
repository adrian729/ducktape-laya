import type { Overflow, Request, StructuredRequest, Result, StructuredResult, CallOptions, RuntimeInfo } from './protocol.js';
export type { Json, Question, Answer, Window, Overflow, Usage, Request, Result, Routing, Property, DecisionSchema, StructuredRequest, StructuredResult, LayaEvent, CallOptions, RuntimeInfo, StatusCode, LayaErrorCode } from './protocol.js';
export { Status, LayaError, LayaLoadError, EngineClosedError } from './protocol.js';

/**
 * The native open config, with the native keys; the core validates every value (status 1), before any model is downloaded.
 * `threads` may only be 1: the browser runs one thread. `max_windows_per_call` (1..4096, default 256) is engine level, so a router
 * accepts it in the shared config only.
 */
export interface Config { threads?: 1; context_tokens?: number; overflow?: Overflow; temperatures?: Record<string, number>; max_windows_per_call?: number }
/** A model to download: a URL, or a URL with the SHA-256 (64 hexadecimal characters) its bytes must have. */
export type Model = string | { url: string; sha256?: string };
/** A router route: a model, with a config of its own that overrides the shared `config` key by key. */
export type Route = string | { url: string; sha256?: string; config?: Omit<Config, 'max_windows_per_call'> };
export interface RouterConfig { models: Record<string, Route>; max_loaded?: number;
  /** Routes for `"model": "auto"` (confidently English text to `english`, everything else to `default`). */
  auto?: { english: string; default: string } }
export interface OpenOptions {
  /** The native open config (a router applies it to every route). */
  config?: Config | null;
  moduleUrl?: string; wasmUrl?: string; workerUrl?: string;
  backend?: 'wasm' | 'webgpu' | 'auto'; webgpuModuleUrl?: string; webgpuWasmUrl?: string;
  onDiagnostic?: (message: string) => void;
  maxPending?: number; maxModelBytes?: number;
  initializationTimeoutMs?: number; signal?: AbortSignal;
}
export class Engine {
  private constructor();
  info(): Promise<RuntimeInfo>;
  readonly capabilities: { backend: 'wasm' | 'webgpu'; abi: number; threads: 1; runningCancellation: boolean; deadlines: true };
  static open(model: Model, options?: OpenOptions | null): Promise<Engine>;
  static openRouter(router: RouterConfig, options?: OpenOptions | null): Promise<Engine>;
  /** The only call: one request (object or JSON string) or an array of requests; arrays return arrays in order. */
  /** With `raw: true`: the core's exact result text. */
  execute(request: Request | StructuredRequest | (Request | StructuredRequest)[] | string, options: CallOptions & { raw: true }): Promise<string>;
  execute(request: Request, options?: (CallOptions & { raw?: false }) | null): Promise<Result>;
  execute(request: StructuredRequest, options?: (CallOptions & { raw?: false }) | null): Promise<StructuredResult>;
  execute(request: (Request | StructuredRequest)[], options?: (CallOptions & { raw?: false }) | null): Promise<(Result | StructuredResult)[]>;
  execute(request: string, options?: (CallOptions & { raw?: false }) | null): Promise<Result | StructuredResult | (Result | StructuredResult)[]>;
  /** `raw` known only at run time: the text or the parsed value. */
  execute(request: Request | StructuredRequest | (Request | StructuredRequest)[] | string, options?: CallOptions | null): Promise<string | Result | StructuredResult | (Result | StructuredResult)[]>;
  /** Terminates the worker and frees the model; call it when done. Idempotent; resolves even if the worker already failed. */
  close(options?: { cancelPending?: boolean } | null): Promise<void>;
}
