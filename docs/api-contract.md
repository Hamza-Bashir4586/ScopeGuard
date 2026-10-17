> **Superseded by [api-contract-v2.md](api-contract-v2.md)** (project audit + invoice draft). This v1 document is kept for
> history; only `POST /packets` remains implemented with a v1-style request (it now renders a v2 finding).

# ScopeGuard API contract

Shared between `backend/` (Python Lambdas) and `web/` (Next.js). Both sides must match this file.

- Base URL: `NEXT_PUBLIC_API_URL` (API Gateway HTTP API URL, or `http://localhost:8000` for local dev).
- All bodies are JSON. All money and quantities are **decimal strings** (`"1599.00"`, `"6.5"`), never floats.
- Errors: non-2xx status with `{"error": "<message>"}`.
- Timestamps: ISO 8601 UTC strings.

## Endpoints

| Method | Path | Request body | Response |
| --- | --- | --- | --- |
| POST | `/scans` | `{}` (Box folder comes from server config) | `202 {"scan_id": "...", "status": "queued"}` |
| GET | `/scans?limit=20` | – | `200 {"scans": ScanSummary[]}` newest first (saved scans; no re-ingestion) |
| GET | `/scans/{scan_id}` | – | `200 Scan` |
| PATCH | `/findings/{finding_id}` | `FindingPatch` | `200 Finding` (totals recomputed server-side) |
| POST | `/packets` | `{"scan_id": "...", "finding_id": "..."}` | `201 Packet` |

The frontend polls `GET /scans/{scan_id}` every 2 s while `status` is `queued` or `running`.

## Types

```ts
type ScanStatus = "queued" | "running" | "completed" | "failed";

interface Scan {
  scan_id: string;
  status: ScanStatus;
  stage: string;            // human-readable, e.g. "Extracting facts (3/8)"
  progress: number;         // 0–100
  error: string | null;
  started_at: string;
  finished_at: string | null;
  files: SourceFile[];
  findings: Finding[];
}

interface ScanSummary {
  scan_id: string;
  status: ScanStatus;
  stage: string;
  progress: number;
  error: string | null;
  started_at: string;
  finished_at: string | null;
  box_folder_id: string | null;
  summary: {                 // null until the scan completes
    file_count: number;
    finding_count: number;
    new_count: number;
    needs_info_count: number;
    already_recorded_count: number;
    potential_total: string; // sum of cost_total over "new" findings at scan time
  } | null;
}

interface SourceFile {
  box_file_id: string;
  box_version_id: string;
  name: string;
  doc_type: string;         // subcontract | scope | spec_revision | instruction | daily_report | timesheet | receipt | rate_sheet | change_order_log | other
  page_count: number;
  box_url: string;          // https://app.box.com/file/<id>
}

type FindingStatus = "new" | "already_recorded" | "needs_info";
type ReviewStatus = "unreviewed" | "confirmed" | "dismissed";

interface Finding {
  finding_id: string;
  scan_id: string;
  title: string;
  summary: string;
  finding_status: FindingStatus;
  co_match: { co_number: string; description: string; amount: string | null } | null;
  scope_baseline: string;   // what the original scope says (short)
  requested_work: string;   // what was asked / performed beyond it (short)
  evidence: Evidence[];
  timeline: TimelineEntry[];
  questions: Question[];
  ai_draft: string;         // model-written narrative (plain text / markdown)
  ai_cost_items: CostItem[];
  review: Review;
  // Effective values = reviewer override if present, else AI value. Computed by the server.
  draft: string;
  cost_items: CostItem[];
  cost_total: string;
  packets: Packet[];
}

type EvidenceRole = "instruction" | "scope_baseline" | "work_record" | "cost_support" | "change_order" | "contradiction";

interface Evidence {
  evidence_id: string;
  box_file_id: string;
  box_version_id: string;
  file_name: string;
  page: number;             // 1-based
  quote: string;
  role: EvidenceRole;
  verified: boolean;        // quote found in source text by Python check
  box_url: string;
}

interface TimelineEntry { date: string; event: string; evidence_id: string | null; }

type QuestionKind = "missing_authorization" | "unclear_scope" | "conflicting_records" | "missing_rate" | "missing_quantity";

interface Question {
  question_id: string;
  kind: QuestionKind;
  text: string;
  resolution: string | null;   // reviewer's answer (from review.question_resolutions)
}

type CostCategory = "labor" | "material" | "equipment" | "markup" | "other";

interface CostItem {
  item_id: string;
  category: CostCategory;
  description: string;
  qty: string | null;       // null = unknown (raises a question)
  unit: string;             // "h", "ea", "LF", "%"...
  rate: string | null;      // null = unknown (raises a question)
  amount: string | null;    // qty × rate, computed server-side with Decimal; null if qty or rate missing
  formula: string;          // e.g. "3 workers × 6.5 h × $82.00/h = $1,599.00" (server-generated)
  source_evidence_id: string | null;
}

interface Review {
  status: ReviewStatus;
  draft: string | null;                         // null = use ai_draft
  cost_items: CostItem[] | null;                // null = use ai_cost_items
  question_resolutions: Record<string, string>; // question_id -> answer
  updated_at: string | null;
}

interface FindingPatch {
  scan_id: string;                  // required (storage key)
  status?: ReviewStatus;
  draft?: string;
  cost_items?: Array<Omit<CostItem, "amount" | "formula" | "item_id"> & { item_id?: string }>;
  question_resolutions?: Record<string, string>;
}

interface Packet { box_file_id: string; box_url: string; created_at: string; }
```

## Rules

- The worker writes only AI fields; PATCH writes only `review`. A re-run scan never overwrites reviewer edits.
- `POST /packets` requires `review.status === "confirmed"`; otherwise `409 {"error": ...}`.
- Unverified evidence is returned (shown greyed out) but excluded from packets.
- PATCH `draft: null` / `cost_items: null` resets that override back to the AI value (so `FindingPatch.draft` is `string | null`, `cost_items` is `CostItem-ish[] | null`).
- PATCH `question_resolutions` is **merged** into the existing map; a key with value `""` removes that answer.
- Cost math: for every item except percentage markup, `amount = qty × rate`. A `markup` item with `unit: "%"` uses `rate` as the percentage and `amount = (sum of all non-markup amounts) × rate / 100` (qty ignored, may be null). Round half-up to cents.
- `formula` is server-generated from the fields only, e.g. `"19.5 h × $82.00/h = $1,599.00"` or `"10% × $2,450.00 = $245.00"`; crew detail belongs in `description`.
- Detail page URL is `/finding/?scan=<scan_id>&id=<finding_id>` (static export with trailing slash).
