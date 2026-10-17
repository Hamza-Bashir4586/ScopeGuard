// Shared backend v2 types copied from docs/api-contract-v2.md.
// Monetary values remain decimal strings; null means unknown.

export type Money = string | null;              // decimal string; null = unknown

export interface SourceRef {                    // a pinned place in a known record [PRJ-03, DOC-06]
  file_id: string;
  version_id: string;
  file_name: string;
  page: number | null;                   // 1-based
  quote: string | null;                  // verified excerpt when available
  available: boolean;                    // false = record/version can no longer be opened [VIEW-07]
}

export type ProjectStatus = "active" | "completed";

export interface ProjectCard {
  project_id: string;                    // "riverside"
  code: string;                          // "SG-024"
  name: string;                          // "Riverside Office Renovation"
  contractor: string;                    // "Summit Electrical" (your company)
  customer: string;                      // "Northline Builders"
  contract_label: string;                // "Electrical contract"
  start_date: string | null;             // "2026-10-05"
  end_date: string | null;               // "2026-11-20"
  status: ProjectStatus;
  preview_only: boolean;                 // true = layout placeholder card, not openable [§2 Projects]
  review_label: string;                  // "3 findings to review" | "Not audited yet" | "All reviewed"
  to_review_count: number | null;
}

export interface AgreementTerm { text: string; source: SourceRef | null; }

export interface Agreement {                    // [PRJ-02..04] available before the first audit
  summary: string;                       // one-paragraph plain-English scope summary
  signed_date: string | null;
  contract_value: Money;                 // "125000.00"
  key_terms: string[];                   // short chips: "Weekend installation excluded", "Changes require customer review"
  inclusions: AgreementTerm[];
  exclusions: AgreementTerm[];
  pricing_basis: AgreementTerm[];
  change_terms: AgreementTerm[];
  payment_terms: AgreementTerm[];        // incl. retainage when stated
  source: SourceRef | null;              // the signed agreement record (opens in the viewer)
  baseline_version: string;              // changes when the agreement record version changes [PRJ-05]
  status: "seeded" | "refreshed";        // seeded manifest vs refreshed by an audit
}

export interface InvoiceDraftSummary { revision: number; item_count: number; subtotal: Money; }

export interface ProjectDetail extends ProjectCard {
  demo_reset_enabled?: boolean;
  currency: "USD";
  agreement: Agreement;
  audit_action: "run" | "refresh";       // button label [AUD-01]
  latest_scan: ScanSummary | null;       // latest COMPLETED scan [AUD-09]
  active_scan: ScanSummary | null;       // queued/running scan, if any [AUD-04]
  records_count: number;
  new_input_available: boolean;          // records uploaded/changed since latest completed scan [DOC-04]
  invoice_draft: InvoiceDraftSummary;
}

export type IngestionStatus = "ingested" | "pending" | "failed" | "unsupported" | "excluded";

export interface RecordCategory { category_id: string; label: string; count: number; }

export interface SourceRecord {                 // [DOC-02, DOC-07, DOC-08]
  file_id: string;
  version_id: string;
  name: string;                          // original filename
  extension: string;                     // "pdf" | "txt" | "md" | ...
  mime_type: string;
  size_bytes: number;
  modified_at: string | null;
  document_date: string | null;          // date stated in the document, when known
  page_count: number | null;
  category_id: string;                   // "original_agreement", "approvals", "field_records", "billing",
                                         // "change_instructions", "pricing", "payments", "unclassified", ...
  category_label: string;                // "Original agreement"
  classification_basis: "ai" | "filename" | "seeded" | "unclassified";
  ingestion_status: IngestionStatus;     // "pending" = uploaded, not yet audited
  warnings: string[];
  box_url: string;                       // optional "Open in Box" (requires a Box account) [VIEW-05]
}

export interface RecordPage {                   // source modal [VIEW-01..04]
  file_id: string;
  version_id: string;
  name: string;
  category_label: string;
  document_date: string | null;
  page: number;
  page_count: number;
  text: string;                          // extracted page text (plain; render escaped, never as HTML)
  highlights: string[];                  // verified quotes on THIS page to highlight (may be empty)
  available: boolean;
  warning: string | null;                // e.g. "Viewing version analyzed on Oct 16; a newer version exists"
}

export type ScanStatus = "queued" | "running" | "completed" | "failed";
export type StageId = "queued" | "reading_box_records" | "connecting_evidence" | "checking_billing" | "findings_ready";

export interface CurrentEvent {                 // real backend work, never simulated [AUD-07]
  message: string;                       // "Comparing CO-001 with INV-014 and INV-015"
  source: SourceRef | null;              // enables "View source ↗"
}

export interface Coverage { complete: boolean; warnings: string[]; }   // separate from status [AUD-05]

export interface ScanSummaryCounts {
  file_count: number;
  finding_count: number;
  to_review_count: number;               // findings not dismissed and not selected
  eligible_total: Money;                 // sum of priced, eligible finding amounts (not a recovery promise)
  by_type: Record<FindingType, number>;
}

export interface ScanSummary {
  scan_id: string;
  project_id: string;
  status: ScanStatus;
  stage_id: StageId;
  stage: string;                         // human label of the current stage/step
  progress: number;                      // 0–100 estimate, not confidence
  error: string | null;
  error_code: string | null;
  cutoff_date: string | null;
  started_at: string;
  updated_at: string;
  finished_at: string | null;
  summary: ScanSummaryCounts | null;     // null until completed
}

export interface Scan extends ScanSummary {
  files_processed: number;
  files_total: number;
  current_event: CurrentEvent | null;
  coverage: Coverage;
  files: SourceRecord[];                 // input snapshot with per-file outcomes [AUD-03, DOC-02]
  findings: Finding[];                   // ordered D-001, D-002, ...
  existing_items: ExistingItem[];        // "Existing changes & previously invoiced balances"
}

export interface ExistingItem {                 // tracked, NEVER a new charge [FIN-02, FIN-06..08]
  item_id: string;
  kind: "approved_billed" | "invoice_balance" | "retainage" | "credit" | "reconciled";
  reference: string;                     // "CO-002", "INV-015"
  label: string;                         // "CO-002 lighting upgrade already invoiced"
  amount: Money;                         // e.g. balance "500.00", retainage "6250.00"
  explanation: string;                   // "$1,500.00 billed − $1,000.00 allocated payment = $500.00 unresolved"
  sources: SourceRef[];
}

export type FindingType = "approved_unbilled" | "scope_change_proposed" | "authorization_unresolved"
                 | "invoice_balance_unresolved" | "needs_information";
export type AuthorizationStatus = "approved" | "pending" | "not_found" | "unclear";
export type ReviewStatus = "unreviewed" | "reviewed" | "dismissed" | "needs_revalidation";

export interface Evidence {
  evidence_id: string;
  file_id: string;
  version_id: string;
  file_name: string;
  page: number;
  quote: string;
  verified: boolean;                     // Python found the quote in the source text [AI-05]
}

export interface EvidenceGroup {                // matches prototype review blocks
  label: string;                         // "Original agreement" | "Approved change and completed work" | "Billing comparison" | ...
  source_label: string;                  // "Scope exhibit, p. 2" | "CO-001 + completion record, p. 1"
  summary_quote: string;                 // short line shown in the block
  evidence: Evidence[];                  // the records opened by the source link (viewer record selector)
}

export interface CostItem {
  description: string;
  qty: Money; unit: string; rate: Money; amount: Money;
  formula: string;                       // "2 × $800.00 = $1,600.00"
  source: SourceRef | null;
}

export interface FinancialState {               // separate dimensions [FIN-01, FIN-06]
  approved_amount: Money;
  invoiced_amount: Money;
  credited_amount: Money;
  paid_amount: Money;
  retained_amount: Money;
  unresolved_balance: Money;
}

export interface Question { question_id: string; kind: string; text: string; }

export interface Review {                       // project-level, keyed by work_item_id [REV-06]
  work_item_id: string;
  status: ReviewStatus;
  internal_comment: string;              // never in customer documents [REV-04]
  customer_description: string | null;   // optional customer-facing wording
  authorization_acknowledged: boolean;   // required before an authorization_unresolved item can be invoiced
  revision: number;                      // for 409 conflict detection [REV-09]
  updated_at: string | null;
}

export interface Finding {
  finding_id: string;                    // scan-local
  scan_id: string;
  work_item_id: string;                  // stable across scans; no scan_id inside [REV-06]
  code: string;                          // "D-001"
  finding_type: FindingType;
  type_label: string;                    // "MISSING FROM INVOICE" | "QUANTITY CHANGE" | "EXCLUDED WORK" | ...
  title: string;
  reason: string;                        // short card text
  explanation: string;                   // "Why this was flagged" (what differs, which records, what's missing, how priced) [AI-06]
  authorization_status: AuthorizationStatus;
  approval_label: string;                // "Previously approved · CO-001" | "Proposed addition · Customer sign-off required" | ...
  proposal: boolean;                     // true = needs customer acceptance; label stays on invoice [FIN-09]
  amount: Money;                         // proposed/approved amount; null = unpriced
  amount_basis: "approved_lump_sum" | "quantity_x_rate" | "unknown";
  calculation: string | null;            // "9 h × $100.00/h = $900.00"
  cost_items: CostItem[];
  financial: FinancialState;
  evidence_groups: EvidenceGroup[];
  source_count: number;                  // distinct records cited
  questions: Question[];
  invoice_eligible: boolean;             // could enter a priced draft now
  eligibility_blockers: string[];        // human messages, e.g. "Acknowledge unresolved authorization first"
  evidence_changed: boolean;             // evidence fingerprint differs from the reviewed one [REV-08]
  review: Review;
  selected: boolean;                     // in the current invoice draft
}

export interface FindingPatch {                 // PATCH /findings/{finding_id}
  scan_id: string;
  expected_revision?: number;            // 409 if the stored review revision differs
  status?: "unreviewed" | "reviewed" | "dismissed";   // dismiss also removes it from the draft
  internal_comment?: string;
  customer_description?: string | null;
  acknowledge_authorization?: boolean;
  confirm_revalidation?: boolean;        // clears needs_revalidation after the user re-checks changed evidence
}

export interface InvoiceLine {
  work_item_id: string;
  finding_id: string;
  code: string;
  title: string;
  description: string;                   // customer_description ?? title
  amount: Money;
  approval_label: string;
  proposal: boolean;
  authorization_status: AuthorizationStatus;
  blockers: string[];
}

export interface InvoiceDraft {                 // one per project [INV-01, INV-02]
  project_id: string;
  revision: number;
  scan_id: string | null;
  lines: InvoiceLine[];
  subtotal: Money;                       // server-calculated
  blockers: string[];                    // draft-level, e.g. "Select at least one item"
  can_generate: boolean;
  updated_at: string | null;
}

export interface InvoiceDraftPatch {            // PATCH /projects/{id}/invoice-draft
  expected_revision: number;             // 409 on mismatch
  scan_id: string;
  selected_work_item_ids: string[];      // full ordered list; adding an ineligible item → 422
}

export interface AppendixEntry { label: string; file_name: string; version_id: string; page: number; quote: string; explanation: string; }

export interface InvoiceGeneration {            // immutable snapshot [INV-08]
  generation_id: string;
  project_id: string;
  scan_id: string;
  draft_revision: number;
  created_at: string;
  file_name: string;                     // sanitized
  content_hash: string;                  // sha256 of the PDF
  lines: InvoiceLine[];
  subtotal: Money;
  appendix: { line_index: number; title: string; entries: AppendixEntry[] }[];
  content_path: string;                  // relative API path for download, e.g. "/projects/riverside/invoice-generations/<id>/content"
  box: { file_id: string; version_id: string; url: string; saved_at: string } | null;
}
