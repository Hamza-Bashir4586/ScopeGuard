// Types mirrored exactly from docs/api-contract.md. Keep in sync with the backend.
// All money and quantities are decimal strings, never floats.

export type ScanStatus = "queued" | "running" | "completed" | "failed";

export interface Scan {
  scan_id: string;
  status: ScanStatus;
  stage: string; // human-readable, e.g. "Extracting facts (3/8)"
  progress: number; // 0–100
  error: string | null;
  started_at: string;
  finished_at: string | null;
  files: SourceFile[];
  findings: Finding[];
}

export interface ScanSummaryCounts {
  file_count: number;
  finding_count: number;
  new_count: number;
  needs_info_count: number;
  already_recorded_count: number;
  potential_total: string; // sum of cost_total over "new" findings at scan time
}

// One saved scan in GET /scans (no findings; open it with GET /scans/{scan_id}).
export interface ScanSummary {
  scan_id: string;
  status: ScanStatus;
  stage: string;
  progress: number;
  error: string | null;
  started_at: string;
  finished_at: string | null;
  box_folder_id: string | null;
  summary: ScanSummaryCounts | null; // null until the scan completes
}

export interface ListScansResponse {
  scans: ScanSummary[]; // newest first
}

export interface SourceFile {
  box_file_id: string;
  box_version_id: string;
  name: string;
  doc_type: string; // subcontract | scope | spec_revision | instruction | daily_report | timesheet | receipt | rate_sheet | change_order_log | other
  page_count: number;
  box_url: string; // https://app.box.com/file/<id>
}

export type FindingStatus = "new" | "already_recorded" | "needs_info";
export type ReviewStatus = "unreviewed" | "confirmed" | "dismissed";

export interface CoMatch {
  co_number: string;
  description: string;
  amount: string | null;
}

export interface Finding {
  finding_id: string;
  scan_id: string;
  title: string;
  summary: string;
  finding_status: FindingStatus;
  co_match: CoMatch | null;
  scope_baseline: string; // what the original scope says (short)
  requested_work: string; // what was asked / performed beyond it (short)
  evidence: Evidence[];
  timeline: TimelineEntry[];
  questions: Question[];
  ai_draft: string; // model-written narrative (plain text / markdown)
  ai_cost_items: CostItem[];
  review: Review;
  // Effective values = reviewer override if present, else AI value. Computed by the server.
  draft: string;
  cost_items: CostItem[];
  cost_total: string;
  packets: Packet[];
}

export type EvidenceRole =
  | "instruction"
  | "scope_baseline"
  | "work_record"
  | "cost_support"
  | "change_order"
  | "contradiction";

export interface Evidence {
  evidence_id: string;
  box_file_id: string;
  box_version_id: string;
  file_name: string;
  page: number; // 1-based
  quote: string;
  role: EvidenceRole;
  verified: boolean; // quote found in source text by Python check
  box_url: string;
}

export interface TimelineEntry {
  date: string;
  event: string;
  evidence_id: string | null;
}

export type QuestionKind =
  | "missing_authorization"
  | "unclear_scope"
  | "conflicting_records"
  | "missing_rate"
  | "missing_quantity";

export interface Question {
  question_id: string;
  kind: QuestionKind;
  text: string;
  resolution: string | null; // reviewer's answer (from review.question_resolutions)
}

export type CostCategory = "labor" | "material" | "equipment" | "markup" | "other";

export interface CostItem {
  item_id: string;
  category: CostCategory;
  description: string;
  qty: string | null; // null = unknown (raises a question)
  unit: string; // "h", "ea", "LF", "%"...
  rate: string | null; // null = unknown (raises a question)
  amount: string | null; // qty × rate, computed server-side; null if qty or rate missing
  formula: string; // server-generated
  source_evidence_id: string | null;
}

export interface Review {
  status: ReviewStatus;
  draft: string | null; // null = use ai_draft
  cost_items: CostItem[] | null; // null = use ai_cost_items
  question_resolutions: Record<string, string>; // question_id -> answer
  updated_at: string | null;
}

export type CostItemInput = Omit<CostItem, "amount" | "formula" | "item_id"> & {
  item_id?: string;
};

export interface FindingPatch {
  scan_id: string; // required (storage key)
  status?: ReviewStatus;
  draft?: string | null; // null = reset to ai_draft
  cost_items?: CostItemInput[] | null; // null = reset to ai_cost_items
  question_resolutions?: Record<string, string>;
}

export interface Packet {
  box_file_id: string;
  box_url: string;
  created_at: string;
}

export interface CreateScanResponse {
  scan_id: string;
  status: "queued";
}

export interface CreatePacketRequest {
  scan_id: string;
  finding_id: string;
}
