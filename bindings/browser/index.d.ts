import type { Request, StructuredRequest, Result, StructuredResult, CallOptions, RuntimeInfo } from './protocol.js';
export type { Json, Question, Answer, Request, Result, Property, DecisionSchema, StructuredRequest, StructuredResult, ObserverEvent, CallOptions, RuntimeInfo, LayaError } from './protocol.js';
export interface ModelAsset { url: string; sha256?: string }
export interface BrowserOptions {
  modelUrl: string; sha256?: string;
  moduleUrl?: string; wasmUrl?: string; workerUrl?: string;
  backend?: 'wasm' | 'webgpu' | 'auto'; webgpuModuleUrl?: string; webgpuWasmUrl?: string;
  onDiagnostic?: (message: string) => void;
  maxPending?: number; maxModelBytes?: number; maxLoaded?: number;
  initializationTimeoutMs?: number; signal?: AbortSignal;
}
export class Laya {
  private constructor();
  info(): Promise<RuntimeInfo>;
  readonly capabilities: { backend: 'wasm' | 'webgpu'; abi: number; threads: 1; runningCancellation: boolean; deadlines: true };
  static open(options: BrowserOptions): Promise<Laya>;
  static openRouter(models: Record<string, ModelAsset>, options: Omit<BrowserOptions, 'modelUrl' | 'sha256'>): Promise<Laya>;
  decide(request: Request | string, options?: CallOptions): Promise<Result>;
  execute(request: Request | StructuredRequest | (Request | StructuredRequest)[] | string, options?: CallOptions): Promise<Result | StructuredResult | (Result | StructuredResult)[]>;
  decideStructured(request: StructuredRequest, options?: CallOptions): Promise<StructuredResult>;
  decideBatch(requests: (Request | StructuredRequest)[], options?: CallOptions): Promise<(Result | StructuredResult)[]>;
  /** Terminates the worker and frees the model; call it when done. Idempotent; resolves even if the worker already failed. */
  close(options?: { cancelPending?: boolean }): Promise<void>;
}
/** Native status codes carried on `error.status`. Mirrors the C ABI `dtl_status`. */
export declare const Status: Readonly<{
  OK: 0; INVALID_ARGUMENT: 1; MODEL_ERROR: 2; INFERENCE_ERROR: 3;
  OUT_OF_MEMORY: 4; CANCELLED: 5; DEADLINE_EXCEEDED: 6; UNSUPPORTED_PLATFORM: 7;
}>;
