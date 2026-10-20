import type { Overflow, Request, StructuredRequest, Result, StructuredResult, ExecuteOptions, RuntimeInfo } from './protocol.js';
export type { Json, Question, Answer, Window, Overflow, Usage, Request, Result, Routing, Property, DecisionSchema, StructuredRequest, StructuredResult, ObserverEvent, CallOptions, ExecuteOptions, RuntimeInfo, LayaError } from './protocol.js';
/** contextTokens/overflow/temperatures override the open options for this route only. */
export interface ModelAsset { url: string; sha256?: string; contextTokens?: number; overflow?: Overflow; temperatures?: Record<string, number> }
export interface BrowserOptions {
  modelUrl: string; sha256?: string;
  moduleUrl?: string; wasmUrl?: string; workerUrl?: string;
  backend?: 'wasm' | 'webgpu' | 'auto'; webgpuModuleUrl?: string; webgpuWasmUrl?: string;
  onDiagnostic?: (message: string) => void;
  /** Native open config: prompt capacity (default 512, max the model's trained length) and calibration overrides. */
  contextTokens?: number; overflow?: Overflow; temperatures?: Record<string, number>;
  maxPending?: number; maxModelBytes?: number; maxLoaded?: number;
  /** Router only: routes for `"model": "auto"` (confidently English text to `english`, everything else to `default`). */
  auto?: { english: string; default: string };
  initializationTimeoutMs?: number; signal?: AbortSignal;
}
export class Laya {
  private constructor();
  info(): Promise<RuntimeInfo>;
  readonly capabilities: { backend: 'wasm' | 'webgpu'; abi: number; threads: 1; runningCancellation: boolean; deadlines: true };
  static open(options: BrowserOptions): Promise<Laya>;
  static openRouter(models: Record<string, ModelAsset>, options?: Omit<BrowserOptions, 'modelUrl' | 'sha256'>): Promise<Laya>;
  /** The only call: one request (object or JSON string) or an array of requests; arrays return arrays in order. */
  execute(request: Request, options?: ExecuteOptions): Promise<Result>;
  execute(request: StructuredRequest, options?: ExecuteOptions): Promise<StructuredResult>;
  execute(request: (Request | StructuredRequest)[], options?: ExecuteOptions): Promise<(Result | StructuredResult)[]>;
  execute(request: string, options?: ExecuteOptions): Promise<Result | StructuredResult | (Result | StructuredResult)[]>;
  /** Terminates the worker and frees the model; call it when done. Idempotent; resolves even if the worker already failed. */
  close(options?: { cancelPending?: boolean }): Promise<void>;
}
/** Native status codes carried on `error.status`. Mirrors the C ABI `dtl_status`. */
export declare const Status: Readonly<{
  OK: 0; INVALID_ARGUMENT: 1; MODEL_ERROR: 2; INFERENCE_ERROR: 3;
  OUT_OF_MEMORY: 4; CANCELLED: 5; DEADLINE_EXCEEDED: 6; UNSUPPORTED_PLATFORM: 7;
}>;
