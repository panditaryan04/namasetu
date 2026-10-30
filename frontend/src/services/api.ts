// Thin fetch wrapper. All calls go to `${API_BASE}/...`; in dev and in the Docker image `/api` is proxied
// to the FastAPI server. For a split deploy (Vercel + Render) set VITE_API_BASE to the API origin.
import { tokens } from "./auth";

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "/api";

export interface ApiErrorBody {
  code: string;
  message: string;
  details?: unknown;
  request_id: string;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: ApiErrorBody | null,
  ) {
    super(body?.message ?? `Request failed with HTTP ${status}`);
    this.name = "ApiError";
  }
}

async function send(path: string, init?: RequestInit): Promise<Response> {
  const token = tokens.access();
  return fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  });
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // After a page reload the in-memory access token is gone: refresh first instead of sending a request that
  // can only come back 401 (tryRefresh is single-flight, so parallel requests share one refresh).
  if (!tokens.access() && tokens.refresh() && !path.startsWith("/auth/")) await tokens.tryRefresh();
  let resp = await send(path, init);
  // Access tokens are short-lived: refresh once, then retry the original request.
  if (resp.status === 401 && tokens.refresh() && !path.startsWith("/auth/")) {
    if (await tokens.tryRefresh()) resp = await send(path, init);
    else tokens.clear();
  }
  if (!resp.ok) {
    let body: ApiErrorBody | null = null;
    try {
      const json = (await resp.json()) as { error?: ApiErrorBody } & Partial<OperationOutcome>;
      if (json.resourceType === "OperationOutcome") {
        // FHIR endpoints report errors as OperationOutcome rather than the JSON error envelope.
        const message = (json.issue ?? []).map((i) => i.diagnostics).join("; ");
        body = {
          code: "operation_outcome",
          message,
          details: json,
          request_id: resp.headers.get("x-request-id") ?? "",
        };
      } else {
        body = json.error ?? null;
      }
    } catch {
      // non-JSON error (e.g. proxy failure) — keep body null
    }
    throw new ApiError(resp.status, body);
  }
  return (await resp.json()) as T;
}

export const apiGet = <T>(path: string, init?: RequestInit) => request<T>(path, init);

export const apiPost = <T>(path: string, body: unknown, init?: RequestInit) =>
  request<T>(path, {
    ...init,
    method: "POST",
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", ...init?.headers },
  });

export interface ComponentStatus {
  ok: boolean;
  mode: string;
  detail: string;
}

export interface Health {
  status: "ok" | "degraded";
  app: string;
  version: string;
  environment: string;
  database: ComponentStatus;
  redis: ComponentStatus;
  index: {
    loaded: boolean;
    detail: string;
    ranker?: string | null;
    semantic_method?: string | null;
    thresholds?: { high: number; low: number } | null;
  };
  modes: {
    DB_MODE: "postgres" | "sqlite-local";
    TASK_MODE: "celery-redis" | "eager-local";
    SEMANTIC_MODE: "full" | "onnx-int8" | "off";
    ICD11_MODE: "live" | "mock";
    ABHA_MODE: "live" | "mock";
    DATA_MODE: "sample" | "official";
  };
  dataset_versions: Record<string, string>;
  disclaimer: string;
}

export const getHealth = () => apiGet<Health>("/health");

// ---------------------------------------------------------------- search & mapping

export type Script = "devanagari" | "iast" | "harvard-kyoto" | "latin";
export type Band = "high" | "medium" | "low";
export type Relationship = "equivalent" | "wider" | "narrower" | "relatedto";

export interface QueryInfo {
  text: string;
  normalised: string;
  script: Script;
}

export interface Suggestion {
  match: "exact" | "prefix" | "root" | "fuzzy";
  system: string;
  code: string;
  display: string;
  matched_term: string;
  term_kind: string;
  devanagari: string | null;
  entity_type: string;
  score: number;
}

export interface CodeRef {
  system: string;
  code: string;
  display: string;
}

export interface Explanation {
  query: QueryInfo;
  namaste: CodeRef | null;
  tm2: CodeRef & { entity_type: string };
  biomedicine: {
    system: string | null;
    code: string | null;
    display: string | null;
    relationship: Relationship;
    source: "who-index" | "clinician" | "none";
    status: "approved" | "pending" | "none";
    message: string | null;
  };
  confidence: number;
  band: Band;
  relationship: Relationship;
  scores: Record<
    "exact" | "lexical" | "ngram" | "transliteration" | "semantic" | "hierarchy" | "final",
    number
  >;
  evidence: {
    matched_tokens: string[];
    matched_root_terms: string[];
    matched_terms: { source: string; target: string; via: string }[];
    shared_concepts: string[];
    hierarchy_path: string[];
    entity_type_check: string;
    transliteration_hit: boolean;
    stages_hit: string[];
    baseline: string | null;
  };
  reasoning: string;
  dataset_releases: string[];
  model_version: string;
}

export interface MapResult {
  query: QueryInfo;
  resolved: {
    code: string;
    display: string;
    matched_term: string;
    method: string;
    score: number;
    iast: string | null;
    devanagari: string | null;
  } | null;
  alternatives: Suggestion[];
  status: "suggested" | "review" | "no_match";
  message: string;
  candidates: Explanation[];
  suppressed_low: number;
  thresholds: { high: number; low: number; source: string };
  semantic_method: string;
  dataset_releases: string[];
  model_version: string;
}

export const autocomplete = (q: string, signal?: AbortSignal) =>
  apiGet<{ query: QueryInfo; items: Suggestion[] }>(
    `/search/autocomplete?q=${encodeURIComponent(q)}&limit=8`,
    {
      signal,
    },
  );

export const mapTerm = (req: { text?: string; code?: string; top_k?: number }) =>
  apiPost<MapResult>("/map", req);

// ---------------------------------------------------------------- FHIR

export interface OperationOutcome {
  resourceType: "OperationOutcome";
  issue: {
    severity: "fatal" | "error" | "warning" | "information";
    code: string;
    diagnostics?: string;
    expression?: string[];
  }[];
}

export interface ConditionBuildRequest {
  namaste_code: string;
  tm2_code?: string;
  confirm_suggestion?: boolean;
  include_biomedicine?: boolean;
  patient: { abha_number: string; display?: string; id?: string };
  encounter_id?: string;
  clinical_status?: string;
  verification_status?: string;
  category?: string;
  purpose_of_use?: string;
  note?: string;
}

export type FhirResource = { resourceType: string; id?: string } & Record<string, unknown>;

export interface ConceptMapTarget {
  code: string;
  display?: string;
  equivalence?: string;
  relationship?: string;
  comment?: string;
}

export interface ConceptMap {
  resourceType: "ConceptMap";
  id: string;
  title?: string;
  version?: string;
  group?: {
    source?: string;
    target?: string;
    element?: { code: string; display?: string; target?: ConceptMapTarget[] }[];
  }[];
}

/** Published FHIR ConceptMaps: "namaste-to-tm2" and "tm2-to-biomedicine". Only approved mappings are published. */
export const conceptMap = (id: string) => apiGet<ConceptMap>(`/fhir/ConceptMap/${encodeURIComponent(id)}`);

export const buildCondition = (req: ConditionBuildRequest) =>
  apiPost<FhirResource>("/fhir/Condition/$build", req);
export const validateResource = (resource: unknown) => apiPost<OperationOutcome>("/fhir/$validate", resource);

// ---------------------------------------------------------------- workflow

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ReviewSummary {
  id: number;
  status: "pending" | "approved" | "rejected";
  kind: string;
  source_code: string;
  source_display: string | null;
  discipline: string | null;
  band: Band;
  top_confidence: number;
  top_target: string | null;
  top_target_display: string | null;
  reason: string;
  created_by: string;
  created_at: string;
  decided_by: string | null;
  decided_at: string | null;
}

export interface ReviewDetail extends ReviewSummary {
  query_text: string | null;
  proposed_target: string | null;
  candidates: Explanation[];
  dataset_releases: string[];
  ranker_version: string | null;
  decision: { action: string; target: string | null; relationship: string | null; override: boolean } | null;
  comment: string | null;
  mapping_id: number | null;
  current_mapping: MappingOut | null;
}

export type DecisionAction = "approve" | "approve_wider" | "approve_narrower" | "choose_other" | "reject";

export interface MappingOut {
  id: number;
  source_code: string;
  source_display: string | null;
  target_code: string;
  target_display: string | null;
  relationship: Relationship;
  status: string;
  version: number;
  reviewer: string | null;
  ranker_version: string | null;
  dataset_releases: string[];
  needs_review: boolean;
  review_reason: string | null;
  created_at: string;
}

export interface MappingDetail extends MappingOut {
  history: MappingOut[];
  timeline: { ts: string; kind: "provenance" | "audit"; action: string; actor: string; detail: string }[];
}

export interface AuditEventOut {
  id: number;
  ts: string;
  actor: string;
  role: string;
  action: string;
  entity_type: string;
  entity_id: string;
  purpose_of_use: string | null;
  request_id: string;
  prev_hash: string;
  hash: string;
}

export interface ReleaseOut {
  id: number;
  tag: string;
  source: string;
  version: string;
  row_count: number;
  is_sample: boolean;
  ingested_at: string;
  ingested_by: string;
  diff_summary: Record<string, number | string | null>;
}

export const qs = (params: Record<string, string | number | undefined | null>) =>
  new URLSearchParams(
    Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== null && v !== "")
      .map(([k, v]) => [k, String(v)]),
  ).toString();

export const reviewQueue = (p: { status?: string; band?: string; limit?: number }) =>
  apiGet<Page<ReviewSummary>>(`/review/queue?${qs(p)}`);
export const reviewItem = (id: number) => apiGet<ReviewDetail>(`/review/${id}`);
export const proposeReview = (body: { namaste_code: string; tm2_code?: string; query_text?: string }) =>
  apiPost<ReviewDetail>("/review/propose", body);
export const decideReview = (
  id: number,
  body: { action: DecisionAction; tm2_code?: string; relationship?: string; comment?: string },
) => apiPost<ReviewDetail>(`/review/${id}/decision`, body);
export const mappings = (p: { source_code?: string; status?: string; limit?: number; offset?: number }) =>
  apiGet<Page<MappingOut>>(`/mappings?${qs(p)}`);
export const mappingDetail = (id: number) => apiGet<MappingDetail>(`/mappings/${id}`);
export const addBioLink = (body: { tm2_code: string; bio_code: string; comment: string }) =>
  apiPost<{ id: number }>("/mappings/bio-links", body);
export const auditEvents = (p: { action?: string; limit?: number; offset?: number }) =>
  apiGet<Page<AuditEventOut>>(`/audit?${qs(p)}`);
export const verifyChain = () =>
  apiGet<{
    ok: boolean;
    checked: number;
    first_broken_id: number | null;
    reason: string | null;
    head_hash: string | null;
  }>("/audit/verify");
export const releases = () => apiGet<Page<ReleaseOut>>("/admin/releases?limit=50");
export const getThresholds = () =>
  apiGet<{ high: number; low: number; source: string; calibrated: { high: number; low: number } }>(
    "/admin/thresholds",
  );
export const putThresholds = (body: { high: number; low: number; reason: string }) =>
  request<{ high: number; low: number }>("/admin/thresholds", {
    method: "PUT",
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
  });
export const resetThresholds = () => request<{ source: string }>("/admin/thresholds", { method: "DELETE" });
export const backfillReview = () => apiPost<{ queued: number }>("/admin/review/backfill", {});
export const adminUsers = () =>
  apiGet<
    { username: string; display_name: string; role: string; active: boolean; abha_address: string | null }[]
  >("/admin/users");
export interface TaskOut {
  task_id: string;
  state: string;
  meta: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
}
export const startIngest = (source: "namaste" | "icd11" | "synonyms", file?: File, version?: string) => {
  const form = new FormData();
  if (file) form.append("file", file);
  if (version) form.append("version", version);
  return request<TaskOut>(`/admin/ingest/${source}`, { method: "POST", body: form });
};
export const taskStatus = (id: string) => apiGet<TaskOut>(`/admin/tasks/${id}`);
export const searchTm2 = (filter: string) =>
  apiGet<{ expansion: { contains: { code: string; display: string }[] } }>(
    `/fhir/ValueSet/$expand?${qs({ url: "icd11-tm2", filter, count: 10 })}`,
  );

// ---------------------------------------------------------------- medications (reference only)

export interface MedBadge {
  code: string;
  label: string;
  severity: "info" | "caution" | "warning";
}

export interface CompositionItem {
  ingredient_id: number;
  printed_name: string;
  canonical_name: string;
  kind: "salt" | "herb" | "mineral" | "other";
  botanical_name: string | null;
  part_used: string | null;
  proportion: string | null;
  strength: string | null;
  metal_mineral: boolean;
}

export interface MedicationView {
  code: string;
  kind: "ayush_formulation" | "ayush_single" | "biomedical";
  name: string;
  name_iast: string | null;
  name_devanagari: string | null;
  discipline: string | null;
  dosage_form: string;
  route: string | null;
  therapeutic_class: string | null;
  schedule: string | null;
  nlem: boolean | null;
  composition: CompositionItem[];
  badges: MedBadge[];
  brands: string[];
  source: { title: string; edition: string | null; page: string | null };
  caution_note: string | null;
  is_sample: boolean;
  sample_label: string | null;
  active: boolean;
  disclaimer: string;
}

export interface LinkInfo {
  id: number;
  status: "approved" | "pending" | "rejected";
  status_label: string;
  reviewer: string | null;
  source: { title: string; edition: string | null; page: string | null };
  note: string | null;
}

export interface LinkedMedication {
  medication: MedicationView;
  link: LinkInfo;
}

export interface ByCondition {
  condition: { system: string; code: string; display: string | null };
  ayush: LinkedMedication[];
  biomedical: LinkedMedication[];
  disclaimer: string;
}

export interface MedSearch {
  query: string;
  brand: {
    found: boolean;
    brand: string | null;
    medication_code: string | null;
    match: string | null;
    strength_note: string | null;
    message: string;
  };
  hits: {
    type: "brand" | "medication" | "ingredient";
    ref: string;
    name: string;
    matched: string;
    kind: string;
    score: number;
  }[];
  disclaimer: string;
}

export interface IngredientDetail {
  id: number;
  kind: string;
  canonical_name: string;
  botanical_name: string | null;
  iast: string | null;
  devanagari: string | null;
  synonyms: { term: string; kind: string }[];
  metal_mineral: boolean;
  medications: MedicationView[];
  conditions: { system: string; code: string; display: string | null }[];
  disclaimer: string;
}

export interface CheckResult {
  medications: MedicationView[];
  unknown_codes: string[];
  duplicates: {
    kind: string;
    severity: "warning" | "info";
    ingredient_id: number;
    ingredient: string;
    medications: { code: string; name: string; strength: string | null }[];
    message: string;
  }[];
  prescription_only: string[];
  interaction_check: string;
  disclaimer: string;
}

export const medicationsByCondition = (system: string, code: string) =>
  apiGet<ByCondition>(`/medications/by-condition?${qs({ system, code })}`);
export const medicationSearch = (q: string) => apiGet<MedSearch>(`/medications/search?${qs({ q })}`);
export const medication = (code: string) =>
  apiGet<MedicationView>(`/medications/${encodeURIComponent(code)}`);
export const ingredient = (id: number) => apiGet<IngredientDetail>(`/salts/${id}`);
export const checkMedications = (codes: string[]) =>
  apiPost<CheckResult>("/medications/check", { medication_codes: codes });
export const medicationLinks = (status: string) =>
  apiGet<
    {
      link: LinkInfo;
      condition: { system: string; code: string; display: string | null };
      medication: MedicationView;
    }[]
  >(`/medications/links?${qs({ status })}`);
export const decideMedicationLink = (id: number, action: "approve" | "reject", comment?: string) =>
  apiPost<LinkInfo>(`/medications/links/${id}/decision`, { action, comment });

// ---------------------------------------------------------------- patients (SYNTHETIC), history, interactions

export interface PatientSummary {
  id: string;
  display: string;
  abha_number: string;
  is_synthetic: boolean;
  medications: number;
  active: number;
}

export interface Alert {
  id: number | null;
  kind: "duplicate" | "herb-drug" | "drug-drug";
  severity: "major" | "moderate" | "minor";
  evidence_level: string | null;
  agents: string[];
  path: string;
  mechanism: string;
  management_note: string | null;
  citation: string;
  dataset: string | null;
  candidate_code: string | null;
  other_medication: string;
  other_status: string | null;
  other_last_active: string | null;
  status: "open" | "acknowledged" | "overridden";
  decided_by: string | null;
  decision_reason: string | null;
}

export interface CheckOut {
  patient_id: string | null;
  as_of: string;
  candidates: string[];
  active_medications: string[];
  alerts: Alert[];
  message: string;
  dataset: string | null;
}

export interface TimelineItem {
  id: number;
  text: string;
  status: "active" | "stopped" | "unknown";
  active_now: boolean;
  period_start: string | null;
  period_end: string | null;
  encounter_id: string | null;
  source_system: string;
  resolution: {
    kind: string;
    medication_code: string | null;
    ingredient_ids: number[];
    label: string;
    note: string | null;
  };
  agents: { type: string; name: string; path: string }[];
}

export interface Timeline {
  patient: PatientSummary;
  as_of: string;
  items: TimelineItem[];
  existing_alerts: Alert[];
  dataset: string | null;
}

export interface ImportResult {
  patient_id: string;
  imported: number;
  skipped_duplicates: number;
  unresolved: number;
  items: { id: number; text: string; status: string; resolution: TimelineItem["resolution"] }[];
  issues: string[];
}

export const patients = () => apiGet<PatientSummary[]>("/patients");
export const patientTimeline = (id: string) =>
  apiGet<Timeline>(`/patients/${encodeURIComponent(id)}/medications/timeline`);
export const importHistoryText = (id: string, text: string, start_date?: string) =>
  apiPost<ImportResult>(`/patients/${encodeURIComponent(id)}/medications/import`, {
    format: "text",
    text,
    start_date,
  });
export const patientInteractions = (id: string, candidates: string[]) =>
  apiGet<CheckOut>(
    `/patients/${encodeURIComponent(id)}/interactions?${candidates.map((c) => `candidate=${encodeURIComponent(c)}`).join("&")}`,
  );
export const acknowledgeAlert = (id: number, action: "acknowledge" | "override", reason: string) =>
  apiPost<Alert>(`/interactions/${id}/acknowledge`, { action, reason });

// ------------------------------------------------------------------ evaluation, dashboard, exports

export interface RankMetrics {
  queries: number;
  top1?: number;
  top1_ci95?: [number, number] | null;
  top5?: number;
  mrr?: number;
  coverage?: number;
  high_band_precision?: number | null;
  high_band_rate?: number;
  review_queue_rate?: number;
  no_match_rate?: number;
  top1_shown?: number;
  relationship_accuracy?: number;
  resolved_to_correct_namaste?: number;
  removed?: string;
  failures?: EvalFailure[];
}

export interface EvalFailure {
  query: string;
  gold: string;
  rank: number | null;
  top: string[];
}

export interface ReliabilityBin {
  lower: number;
  upper: number;
  count: number;
  mean_predicted: number;
  observed: number;
}

export interface EvalSummary {
  test_queries: number;
  hybrid_top1: number;
  hybrid_top5: number;
  hybrid_mrr: number;
  high_band_precision: number | null;
  review_queue_rate: number | null;
  exact_top1: number;
  ece: number;
  safety_failures: number;
  fhir_resources: number;
  fhir_errors: number;
  brand_typo_accuracy: number | null;
  brand_false_resolutions: number;
  line_resolution_accuracy: number | null;
  interaction_precision: number | null;
  interaction_recall: number | null;
  semantic_method: string;
  is_sample?: boolean;
}

export interface CountsByKind {
  true_positives: number;
  false_positives: number;
  false_negatives: number;
  precision: number | null;
  recall: number | null;
}

export interface EvalResults {
  split: { test: number; train: number; note: string };
  intercomparison: { semantic_method: string; systems: Record<string, RankMetrics> };
  ablation: Record<string, RankMetrics>;
  robustness: Record<string, RankMetrics>;
  robustness_all_gold: Record<string, RankMetrics>;
  calibration: {
    thresholds: { high: number; low: number; source?: string };
    threshold_method: string;
    test_reliability: ReliabilityBin[];
    test_pairs: number;
    test_expected_calibration_error: number;
    brier_score: number | null;
  };
  safety: {
    checks: Record<string, number>;
    ok: boolean;
    examples: Record<string, string[]>;
    scope: Record<string, number>;
    fhir_conformance: Record<string, { resources: number; errors: number; warnings: number }>;
  };
  medication: {
    brands: Record<string, { cases: number; accuracy?: number | null; false_resolutions?: number }>;
    ingredient_names: {
      groups: Record<string, { cases: number; accuracy: number | null }>;
      shared_names: string[];
    };
    line_resolution: { cases: number; correct: number; accuracy: number | null };
    interactions: { cases: number; by_kind: Record<string, CountsByKind> };
  };
  timings_s: Record<string, number>;
}

export interface EvalRun {
  id: number;
  created_at: string;
  created_by: string;
  dataset_releases: string[];
  model_version: string;
  duration_s: number;
  safety_ok: boolean;
  summary: EvalSummary;
  results?: EvalResults;
}

export interface EvalAccepted {
  task_id: string;
  state: string;
  eval_run_id: number | null;
}

export const latestEvalRun = () => apiGet<EvalRun>("/eval/runs/latest");
export const evalRuns = () => apiGet<EvalRun[]>("/eval/runs");
export const startEvalRun = () => apiPost<EvalAccepted>("/eval/run", {});
export const evalTask = (taskId: string) => apiGet<EvalAccepted>(`/eval/tasks/${encodeURIComponent(taskId)}`);

export interface Dashboard {
  is_sample: boolean;
  dataset_releases: string[];
  kpis: {
    namaste_codes: number;
    tm2_codes: number;
    biomedicine_codes: number;
    mapped_approved: number;
    pending_review: number;
    no_match: number;
    approved_biomedical_links: number;
    pending_biomedical_links: number;
  };
  band_distribution: { high: number; medium: number; low: number };
  band_note: string;
  queue: {
    by_status: Record<string, number>;
    median_hours_to_decision: number | null;
    throughput: { date: string; queued: number; decided: number }[];
  };
  mapping_gap: { label: string; source: string; bars: { key: string; label: string; value: number }[] };
  latest_eval: EvalRun | null;
}

export const dashboard = () => apiGet<Dashboard>("/dashboard");

export type ExportKind = "mappings" | "decisions" | "audit";
export type ExportFormat = "json" | "csv" | "pdf";

export interface ExportJob {
  id: number;
  kind: ExportKind;
  format: ExportFormat;
  status: "queued" | "done" | "failed";
  created_at: string;
  filename: string | null;
  rows: number;
  size: number;
  sha256: string | null;
  error: string | null;
  download_url: string | null;
}

export const createExportJob = (kind: ExportKind, format: ExportFormat) =>
  apiPost<ExportJob>("/export/jobs", { kind, format });
export const exportJobs = () => apiGet<ExportJob[]>("/export/jobs");

/** Download an authenticated file (export) and hand it to the browser as a file save. */
export async function downloadFile(path: string, fallbackName: string): Promise<void> {
  const resp = await send(path);
  if (!resp.ok) throw new ApiError(resp.status, null);
  const name = /filename="([^"]+)"/.exec(resp.headers.get("content-disposition") ?? "")?.[1] ?? fallbackName;
  const url = URL.createObjectURL(await resp.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}
