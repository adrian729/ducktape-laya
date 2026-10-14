import type { Request, StructuredRequest, Result, StructuredResult, CallOptions, RuntimeInfo } from './protocol.js';
export type { Json, Question, Answer, Request, Result, Property, DecisionSchema, StructuredRequest, StructuredResult, ObserverEvent, CallOptions, RuntimeInfo } from './protocol.js';
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
  close(options?: { cancelPending?: boolean }): Promise<void>;
}
