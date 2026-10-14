export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type Question =
  | { type: 'choice'; instructions: Json; criteria: Record<string, Json> }
  | { type: 'score'; instructions: Json; criteria: Json[] }
  | { type: 'noul'; instructions: Json; criteria?: { false?: Json; true?: Json } };
export interface Request { state: Json; questions: Record<string, Question>; model?: string }
export type Answer =
  | { type: 'choice'; choice: string; probabilities: Record<string, number>; confidence: number }
  | { type: 'score'; score: number; legend: Record<string, Json>; probabilities: Record<string, number>; confidence: number }
  | { type: 'noul'; noul: number };
export interface Result { answers: Record<string, Answer>; usage: { input_tokens: number; output_tokens: 0 }; model?: string }
export class Laya {
  private constructor();
  static runtimeInfo(options?: { addonPath?: string }): RuntimeInfo;
  info(): Promise<RuntimeInfo>;
  static open(modelPath: string, options?: OpenOptions): Promise<Laya>;
  static openRouter(config: RouterConfig | string, options?: OpenOptions): Promise<Laya>;
  decide(request: Request | string, options?: CallOptions): Promise<Result>;
  decideStructured(request: StructuredRequest, options?: CallOptions): Promise<StructuredResult>;
  decideBatch(requests: (Request | StructuredRequest)[], options?: CallOptions): Promise<(Result | StructuredResult)[]>;
  execute(request: Request | StructuredRequest | (Request | StructuredRequest)[] | string, options?: CallOptions): Promise<Result | StructuredResult | (Result | StructuredResult)[]>;
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
export interface StructuredRequest { state: Json; schema: DecisionSchema; boolean_threshold?: number; model?: string }
export interface StructuredResult extends Result { values: Record<string, string | number | boolean> }
export interface ObserverEvent { event: 'start' | 'end' | 'error'; request_id: number; status: number; elapsed_ms: number }
export interface CallOptions { signal?: AbortSignal; timeoutMs?: number; onEvent?: (event: ObserverEvent) => void | Promise<void> }
export interface OpenOptions { threads?: number; maxPending?: number; addonPath?: string }
export interface RouterConfig { models: Record<string, string>; max_loaded?: number }

export interface RuntimeInfo {
  schema_version: 1; package_version: string; abi_version: number; llama_cpp_commit: string;
  runtime_patches: { path: string; sha256: string; purpose: string }[];
  backend: 'native-cpu' | 'wasm-cpu' | 'webgpu'; compiler: string;
  numerical_profile: { id: string; cross_profile_equivalence: false };
  required_cpu_features: string[]; missing_cpu_features: string[]; cpu_supported: boolean;
  decision_types: string[]; features: string[]; limits: Record<string, number>;
  model?: { architecture: string; name: string; decision_type: string; max_head_tokens: number; max_tokens: number; temperatures: Record<string, number> };
}
