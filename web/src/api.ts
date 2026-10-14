export type Status = {
  backend: string;
  voice_provider: string;
  model: string;
  keys: { anthropic: boolean; runpod: boolean; elevenlabs: boolean };
  serverless_endpoint: boolean;
  storage: boolean;
  webhooks: boolean;
  workflows: number;
  budget_default: number;
};

export type Recipe = {
  name: string;
  title: string;
  description: string;
  output: "video" | "image";
  axes: { key: string; label: string }[];
  requires: string[];
  qa_stages: string[];
};

export type Workflow = {
  name: string;
  kind: string;
  version: string;
  description: string;
  license: string;
  commercial_ok: boolean;
  min_vram_gb: number;
  est_seconds: number;
  verified: boolean;
  inputs: { name: string; type: string; node: string | null; default: unknown; optional: boolean; derive: string }[];
  models: { url: string; path: string }[];
  custom_nodes: { repo: string; commit: string }[];
};

export type Brand = {
  id?: number;
  name: string;
  product: string;
  audience: string;
  facts: string[];
  banned: string[];
  tone: string;
  language: string;
  colors: string[];
  cta_default: string;
  logo_asset_id: number | null;
  endcard_asset_id: number | null;
  music_asset_id: number | null;
  caption_style: string | null;
  aspects: string[];
};

export type Asset = { id: number; kind: string; name: string; path: string; url: string; created: string };

export type Scene = { visual: string; motion: string; line: string };
export type Plan = {
  angle: string;
  hooks: string[];
  bodies: string[];
  ctas: string[];
  headlines: string[];
  presenter_prompts: string[];
  scene_styles: string[];
  broll_motion: string;
  scenes: Scene[];
};

export type StageState = { status: string; cached: boolean; seconds: number; cost_usd: number; error: string };
export type QA = {
  passed?: boolean;
  score?: number | null;
  issues?: string[];
  technical?: string[];
  brand_violations?: string[];
  suspect_stage?: string;
  summary?: string;
  vision?: boolean;
  regenerating?: string;
};

export type Variant = {
  id: number;
  batch_id: number;
  code: string;
  axes: Record<string, number>;
  status: string;
  review: string;
  qa: QA;
  outputs: Record<string, string>;
  overrides: Record<string, Record<string, unknown>>;
  cost_usd: number;
  attempts: number;
  error: string;
  labels: Record<string, string>;
  stages: Record<string, StageState>;
};

export type Estimate = {
  variants: number;
  renders: Record<string, number>;
  gpu_seconds: number;
  gpu_seconds_without_cache: number;
  usd: number;
  usd_without_cache: number;
  backend: string;
  rate_per_gpu_second: number;
  warnings: string[];
};

export type BatchSummary = {
  id: number;
  name: string;
  recipe: string;
  status: string;
  cost_usd: number;
  created: string;
  variants: number;
  done: number;
  failed: number;
  approved: number;
  thumb: string | null;
  settings: Record<string, unknown>;
};

export type BatchView = {
  id: number;
  name: string;
  recipe: string;
  status: string;
  brief: { text: string; url: string | null; product_asset_id: number | null };
  plan: Plan | Record<string, never>;
  matrix: { selected?: Record<string, number[]>; mode?: string; n?: number | null };
  settings: Record<string, unknown>;
  cost_usd: number;
  error: string;
  created: string;
  brand: Brand;
  variants: Variant[];
  options: Record<string, string[]>;
  recipe_info: Recipe;
  estimate?: Estimate;
};

export type BatchEvent = {
  topic: number;
  ts: number;
  type: "batch" | "variant" | "stage" | "progress";
  status?: string;
  variant_id?: number;
  code?: string;
  stage?: string;
  progress?: number;
  message?: string;
  error?: string;
  cached?: boolean;
  cost_usd?: number;
};

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["content-type"] = "application/json";
  }
  const r = await fetch(url, init);
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const j = await r.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* not json */
    }
    throw new Error(detail);
  }
  return r.json() as Promise<T>;
}

export const api = {
  status: () => req<Status>("GET", "/api/status"),
  recipes: () => req<Recipe[]>("GET", "/api/recipes"),
  workflows: () => req<Workflow[]>("GET", "/api/workflows"),
  brands: () => req<Brand[]>("GET", "/api/brands"),
  saveBrand: (b: Brand) => (b.id ? req<Brand>("PUT", `/api/brands/${b.id}`, b) : req<Brand>("POST", "/api/brands", b)),
  assets: (kind?: string) => req<Asset[]>("GET", `/api/assets${kind ? `?kind=${kind}` : ""}`),
  upload: (kind: string, file: File) => {
    const fd = new FormData();
    fd.append("kind", kind);
    fd.append("file", file);
    return req<Asset>("POST", "/api/assets", fd);
  },
  deleteAsset: (id: number) => req("DELETE", `/api/assets/${id}`),
  batches: () => req<BatchSummary[]>("GET", "/api/batches"),
  batch: (id: number) => req<BatchView>("GET", `/api/batches/${id}`),
  createBatch: (b: Record<string, unknown>) => req<BatchView>("POST", "/api/batches", b),
  patchSettings: (id: number, s: Record<string, unknown>) => req<BatchView>("PATCH", `/api/batches/${id}/settings`, s),
  writePlan: (id: number, counts?: Record<string, number>) => req<BatchView>("POST", `/api/batches/${id}/plan`, { counts }),
  setPlan: (id: number, plan: Plan) => req<BatchView>("PUT", `/api/batches/${id}/plan`, plan),
  matrix: (id: number, selected: Record<string, number[]>, mode: string, n: number | null) =>
    req<BatchView>("POST", `/api/batches/${id}/matrix`, { selected, mode, n }),
  estimate: (id: number) => req<Estimate>("GET", `/api/batches/${id}/estimate`),
  run: (id: number) => req("POST", `/api/batches/${id}/run`),
  cancel: (id: number) => req("POST", `/api/batches/${id}/cancel`),
  exportBatch: (id: number, approved_only: boolean) =>
    req<{ files: number; download: string; url?: string }>("POST", `/api/batches/${id}/export`, { approved_only }),
  review: (id: number, decision: string) => req("POST", `/api/variants/${id}/review`, { decision }),
  regenerate: (id: number, stage: string | null, tweak: Record<string, unknown> = {}) =>
    req("POST", `/api/variants/${id}/regenerate`, { stage, tweak }),
};

export const fileUrl = (rel: string) => `/files/${rel}`;
export const money = (v: number) => (v < 0.01 && v > 0 ? "<$0.01" : `$${v.toFixed(2)}`);
export const isVideo = (p: string) => /\.(mp4|webm|mov)$/i.test(p);
