# ScopeGuard API contract v2 (project audit + invoice draft)

Binding contract between `backend/` and `web/` for the approved prototype flow
(<https://scopeguard-team-prototype.elvenspice11.chatgpt.site>) and `docs/backend-requirements.md`.
Requirement IDs in brackets (e.g. [AUD-04]) point to that document. v1 (`docs/api-contract.md`) is superseded
except `POST /packets`, which stays for compatibility.

## Conventions

- Base URL `NEXT_PUBLIC_API_URL`. No auth headers. CORS on every response, `OPTIONS` → 204.
- Money/quantities: decimal strings (`"8500.00"`). `null` means **unknown**, never `"0.00"` [FIN-10, §7].
- Timestamps ISO 8601 UTC; business dates `YYYY-MM-DD`.
- Errors: `{"error": "message", "code"?: "snake_code", "details"?: {...}}`. Status codes: 400 malformed,
  404 unknown scoped resource, 409 active scan / revision conflict / stale selection, 413 too large,
  415 unsupported media, 422 generation/selection blockers, 5xx safe upstream error (no raw SDK text).
- Binary responses (PDF/text content) are returned with the right `Content-Type`; JSON otherwise.

## Shared types

```ts
type Money = string | null;              // decimal string; null = unknown

interface SourceRef {                    // a pinned place in a known record [PRJ-03, DOC-06]
  file_id: string;
  version_id: string;
  file_name: string;
  page: number | null;                   // 1-based
  quote: string | null;                  // verified excerpt when available
  available: boolean;                    // false = record/version can no longer be opened [VIEW-07]
}

type ProjectStatus = "active" | "completed";

interface ProjectCard {
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

interface AgreementTerm { text: string; source: SourceRef | null; }

interface Agreement {                    // [PRJ-02..04] available before the first audit
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

interface InvoiceDraftSummary { revision: number; item_count: number; subtotal: Money; }

interface ProjectDetail extends ProjectCard {
  currency: "USD";
  agreement: Agreement;
  audit_action: "run" | "refresh";       // button label [AUD-01]
  latest_scan: ScanSummary | null;       // latest COMPLETED scan [AUD-09]
  active_scan: ScanSummary | null;       // queued/running scan, if any [AUD-04]
  records_count: number;
  new_input_available: boolean;          // records uploaded/changed since latest completed scan [DOC-04]
  invoice_draft: InvoiceDraftSummary;
}
```

### Records

```ts
type IngestionStatus = "ingested" | "pending" | "failed" | "unsupported" | "excluded";

interface RecordCategory { category_id: string; label: string; count: number; }

interface SourceRecord {                 // [DOC-02, DOC-07, DOC-08]
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

interface RecordPage {                   // source modal [VIEW-01..04]
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
```

### Scans (audits)

```ts
type ScanStatus = "queued" | "running" | "completed" | "failed";
type StageId = "queued" | "reading_box_records" | "connecting_evidence" | "checking_billing" | "findings_ready";

interface CurrentEvent {                 // real backend work, never simulated [AUD-07]
  message: string;                       // "Comparing CO-001 with INV-014 and INV-015"
  source: SourceRef | null;              // enables "View source ↗"
}

interface Coverage { complete: boolean; warnings: string[]; }   // separate from status [AUD-05]

interface ScanSummaryCounts {
  file_count: number;
  finding_count: number;
  to_review_count: number;               // findings not dismissed and not selected
  eligible_total: Money;                 // sum of priced, eligible finding amounts (not a recovery promise)
  by_type: Record<FindingType, number>;
}

interface ScanSummary {
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

interface Scan extends ScanSummary {
  files_processed: number;
  files_total: number;
  current_event: CurrentEvent | null;
  coverage: Coverage;
  files: SourceRecord[];                 // input snapshot with per-file outcomes [AUD-03, DOC-02]
  findings: Finding[];                   // ordered D-001, D-002, ...
  existing_items: ExistingItem[];        // "Existing changes & previously invoiced balances"
}

interface ExistingItem {                 // tracked, NEVER a new charge [FIN-02, FIN-06..08]
  item_id: string;
  kind: "approved_billed" | "invoice_balance" | "retainage" | "credit" | "reconciled";
  reference: string;                     // "CO-002", "INV-015"
  label: string;                         // "CO-002 lighting upgrade already invoiced"
  amount: Money;                         // e.g. balance "500.00", retainage "6250.00"
  explanation: string;                   // "$1,500.00 billed − $1,000.00 allocated payment = $500.00 unresolved"
  sources: SourceRef[];
}
```

### Findings and reviews

```ts
type FindingType = "approved_unbilled" | "scope_change_proposed" | "authorization_unresolved"
                 | "invoice_balance_unresolved" | "needs_information";
type AuthorizationStatus = "approved" | "pending" | "not_found" | "unclear";
type ReviewStatus = "unreviewed" | "reviewed" | "dismissed" | "needs_revalidation";

interface Evidence {
  evidence_id: string;
  file_id: string;
  version_id: string;
  file_name: string;
  page: number;
  quote: string;
  verified: boolean;                     // Python found the quote in the source text [AI-05]
}

interface EvidenceGroup {                // matches prototype review blocks
  label: string;                         // "Original agreement" | "Approved change and completed work" | "Billing comparison" | ...
  source_label: string;                  // "Scope exhibit, p. 2" | "CO-001 + completion record, p. 1"
  summary_quote: string;                 // short line shown in the block
  evidence: Evidence[];                  // the records opened by the source link (viewer record selector)
}

interface CostItem {
  description: string;
  qty: Money; unit: string; rate: Money; amount: Money;
  formula: string;                       // "2 × $800.00 = $1,600.00"
  source: SourceRef | null;
}

interface FinancialState {               // separate dimensions [FIN-01, FIN-06]
  approved_amount: Money;
  invoiced_amount: Money;
  credited_amount: Money;
  paid_amount: Money;
  retained_amount: Money;
  unresolved_balance: Money;
}

interface Question { question_id: string; kind: string; text: string; }

interface Review {                       // project-level, keyed by work_item_id [REV-06]
  work_item_id: string;
  status: ReviewStatus;
  internal_comment: string;              // never in customer documents [REV-04]
  customer_description: string | null;   // optional customer-facing wording
  authorization_acknowledged: boolean;   // required before an authorization_unresolved item can be invoiced
  revision: number;                      // for 409 conflict detection [REV-09]
  updated_at: string | null;
}

interface Finding {
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

interface FindingPatch {                 // PATCH /findings/{finding_id}
  scan_id: string;
  expected_revision?: number;            // 409 if the stored review revision differs
  status?: "unreviewed" | "reviewed" | "dismissed";   // dismiss also removes it from the draft
  internal_comment?: string;
  customer_description?: string | null;
  acknowledge_authorization?: boolean;
  confirm_revalidation?: boolean;        // clears needs_revalidation after the user re-checks changed evidence
}
```

### Invoice draft and generations

```ts
interface InvoiceLine {
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

interface InvoiceDraft {                 // one per project [INV-01, INV-02]
  project_id: string;
  revision: number;
  scan_id: string | null;
  lines: InvoiceLine[];
  subtotal: Money;                       // server-calculated
  blockers: string[];                    // draft-level, e.g. "Select at least one item"
  can_generate: boolean;
  updated_at: string | null;
}

interface InvoiceDraftPatch {            // PATCH /projects/{id}/invoice-draft
  expected_revision: number;             // 409 on mismatch
  scan_id: string;
  selected_work_item_ids: string[];      // full ordered list; adding an ineligible item → 422
}

interface AppendixEntry { label: string; file_name: string; version_id: string; page: number; quote: string; explanation: string; }

interface InvoiceGeneration {            // immutable snapshot [INV-08]
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
```

## Endpoints

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/projects` | – | `200 {"projects": ProjectCard[]}` (Riverside first; 2 preview-only cards) |
| GET | `/projects/{project_id}` | – | `200 ProjectDetail`; preview-only project → 404 |
| GET | `/projects/{project_id}/records?category_id=` | – | `200 {"records": SourceRecord[], "categories": RecordCategory[], "total": number}` (categories always list all; `total` = unfiltered count) |
| POST | `/projects/{project_id}/records` | `multipart/form-data`, field `file` (one file) | `201 SourceRecord` (`ingestion_status: "pending"`); 413 > 4 MiB, 415 unsupported, 400 empty/malformed [DOC-04/05] |
| GET | `/projects/{project_id}/records/{file_id}/content?version_id=` | – | raw bytes: `application/pdf` (inline) or `text/plain; charset=utf-8`; 404 unknown record/version |
| GET | `/projects/{project_id}/records/{file_id}/pages/{page}?version_id=&scan_id=&finding_id=` | – | `200 RecordPage`; highlights = verified quotes cited by that finding (or by the agreement baseline when no finding) on that page |
| POST | `/scans` | `{"project_id"?: "riverside", "cutoff_date"?: "YYYY-MM-DD", "request_id"?: string}` (`{}` = configured demo project) | `202 {"scan_id", "project_id", "status": "queued"}`; same `request_id` → same scan; active scan → `409 {"error", "code": "scan_active", "details": {"scan_id"}}` |
| GET | `/scans?project_id=&limit=20` | – | `200 {"scans": ScanSummary[]}` newest first |
| GET | `/scans/{scan_id}` | – | `200 Scan` |
| PATCH | `/findings/{finding_id}` | `FindingPatch` | `200 Finding`; 409 `code: "revision_conflict"` |
| GET | `/projects/{project_id}/invoice-draft` | – | `200 InvoiceDraft` |
| PATCH | `/projects/{project_id}/invoice-draft` | `InvoiceDraftPatch` | `200 InvoiceDraft`; 409 conflict; 422 ineligible item (`details.work_item_id`) |
| POST | `/projects/{project_id}/invoice-generations` | `{"request_id": string, "expected_revision": number}` | `201 InvoiceGeneration` (same request_id + revision → same generation); 422 blockers (`details.blockers`) |
| GET | `/projects/{project_id}/invoice-generations/{generation_id}` | – | `200 InvoiceGeneration` |
| GET | `/projects/{project_id}/invoice-generations/{generation_id}/content` | – | `application/pdf` attachment |
| POST | `/projects/{project_id}/invoice-generations/{generation_id}/save-to-box` | `{}` | `200 InvoiceGeneration` with `box` set; repeat → same Box file [INV-10, INV-12] |
| POST | `/packets` | v1 | kept for compatibility |

## Behavior the UI relies on

- **Run vs Refresh:** `audit_action` from `GET /projects/{id}`. While a refresh runs, the UI keeps showing the
  latest completed scan's findings and polls the active scan every 2 s [AUD-08]. On 409 `scan_active`, resume
  polling `details.scan_id`.
- **Stages:** `stage_id` drives the 4-step strip: reading_box_records → connecting_evidence → checking_billing →
  findings_ready (completed). `current_event` is the one-line event with optional "View source".
- **Card status label (UI-derived):** selected → "Added to invoice"; review.status dismissed → "Dismissed";
  needs_revalidation → "Evidence changed"; reviewed → "Reviewed"; else "Needs review".
- **Review panel actions:** Save comment = PATCH `internal_comment` (+ `status: "reviewed"` if unreviewed);
  Add to invoice = PATCH finding (comment, `status: "reviewed"`, and `acknowledge_authorization: true` when the
  user ticked the acknowledgment for authorization_unresolved) then PATCH draft with the work item appended;
  Dismiss flag = PATCH `status: "dismissed"` (backend removes it from the draft and bumps the draft revision).
- **Refresh preservation:** reviews, comments and selections live at project level by `work_item_id`, so they
  survive page reloads and new scans. Changed evidence → `review.status = "needs_revalidation"`,
  `evidence_changed = true`, and the item blocks generation until `confirm_revalidation` [REV-08].
- **Invoice:** sidebar = `InvoiceDraft`. "Generate for customer sign-off" → POST generation, show the preview
  (lines, subtotal, appendix) from the response, "Download invoice + evidence" → GET content,
  "Save invoice pack to Box" → POST save-to-box and show the Box link. Generated PDFs are labeled
  "Draft — for customer review and sign-off" / "Not sent", list proposal disclosures, include an evidence
  appendix and an acceptance section, and omit internal comments [INV-04..07].

## Backend implementation notes (v2 backend, 2026-10-17)

Clarifications of the contract as implemented in `backend/`; no field was renamed or removed.

- **Error codes** (all optional per the conventions): `scan_active`, `revision_conflict`, `stale_selection` (409,
  `details.latest_scan_id` - draft PATCH with a `scan_id` that is not the latest completed audit),
  `ineligible_item` / `duplicate_selection` / `unknown_work_item` (422, `details.work_item_id`; `ineligible_item` also
  has `details.blockers`), `generation_blocked` (422, `details.blockers`), `project_not_found`, `record_not_found`,
  `page_not_found`, `scan_not_found`, `finding_not_found`, `generation_not_found`, `source_unavailable` (404 content of a
  version that can no longer be opened), `file_too_large` (413), `unsupported_media_type` (415), `empty_file` /
  `malformed_file` / `malformed_upload` / `invalid_filename` / `malformed_json` / `bad_request` (400),
  `box_unavailable` / `worker_start_failed` (502), `generation_expired` (410, only if the temporary S3 PDF expired,
  a deterministic re-render does not match the stored hash and no Box copy exists).
- **Scan `error_code`** values: `baseline_missing`, `baseline_unreadable`, `model_failure`, `worker_timeout` (stale run
  past its deadline), `worker_start_failed`, `internal_error`.
- **Draft PATCH eligibility** is enforced for work items being *added*; items already in the draft that later become
  ineligible (e.g. evidence changed) stay listed with `blockers` so the user can remove or revalidate them, and
  `can_generate` is false until resolved. Adding an `unreviewed` item marks its review `reviewed` (bumps that review's
  revision).
- **`eligible_total`** = sum of priced amounts of findings of types `approved_unbilled`, `scope_change_proposed`,
  `authorization_unresolved` that are not dismissed (acknowledgment/revalidation state is shown per finding via
  `invoice_eligible`/`eligibility_blockers`, not subtracted here).
- **`needs_revalidation`** is derived: a review stores the evidence fingerprint it confirmed; when a later audit cites a
  different file version/quote set for the same `work_item_id`, a reviewed or selected item reads
  `needs_revalidation` until `confirm_revalidation: true`. `status: "reviewed"` alone does not clear it.
- **Evidence** objects additionally carry `fact_type` and `approved` (additive, informational).
- **Scan `coverage.complete`** is `false` while queued/running and for failed scans; unsupported or over-limit files
  also make it `false` (with a warning). Findings are blocked from invoicing only when a supported record failed to be
  read/analyzed in that audit.
- **RecordPage** for a pinned version that can no longer be opened returns `available: false` with the text captured
  during the audit (if any) and a `warning`.
- **Upload response** `warnings` may include "No extractable text found (scanned PDF?)". Uploading a file whose name
  already exists creates a new version of that record (same `file_id`, new `version_id`).

## Demo reset extension

When `demo_reset_enabled` is true on ProjectDetail, `POST /projects/{project_id}/demo-reset` accepts
`{request_id, expected_revision}` and returns `{project_id, reset_id, reset_at, reviews_cleared,
records_preserved, message}`. It archives current reviews/draft/project pointer, replaces reviews with
unreviewed empty states, empties the draft, and clears the current completed-audit pointer. Historical
scan/generation objects and Box records are retained. Disabled deployments return 404; active scans or
revision conflicts return 409. The endpoint uses the existing project scan lease during reset.

For a needs-information finding, `cost_items` may contain a verified quotation for review while the finding's
`amount` remains null. Display that as quoted/excluded; never add it to the invoice subtotal.
