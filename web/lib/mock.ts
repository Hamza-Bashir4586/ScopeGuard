// In-browser mock of the ScopeGuard API, enabled with NEXT_PUBLIC_USE_MOCK=1.
// All data below is fictional. State is kept in localStorage (when available)
// so a page refresh resumes the same scan. Simple decimal math is fine here
// because this is mock-only; the real server recomputes with Decimal.

import type { ScopeGuardApi } from "./api";
import { ApiError } from "./errors";
import type {
  CostItem,
  CostItemInput,
  Evidence,
  Finding,
  FindingPatch,
  Packet,
  Question,
  Review,
  Scan,
  ScanSummary,
  SourceFile,
} from "./types";

const STORAGE_KEY = "scopeguard.mock.v1";
const SCAN_DURATION_MS = 6000;
const LATENCY_MS = 250;
const MOCK_FAIL = process.env.NEXT_PUBLIC_MOCK_FAIL === "1";

// ---------- storage ----------

interface StoredFinding {
  // AI fields (as written by the "worker")
  base: Omit<Finding, "review" | "draft" | "cost_items" | "cost_total" | "packets">;
  review: Review;
  packets: Packet[];
}

interface StoredScan {
  scan_id: string;
  started_at: string;
  files: SourceFile[];
  findings: StoredFinding[];
}

interface MockDb {
  scans: Record<string, StoredScan>;
}

let memoryDb: MockDb | null = null;

function loadDb(): MockDb {
  if (memoryDb) return memoryDb;
  let db: MockDb = { scans: {} };
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw) db = JSON.parse(raw) as MockDb;
  } catch {
    // storage unavailable — keep in memory only
  }
  memoryDb = db;
  return db;
}

function saveDb(db: MockDb) {
  memoryDb = db;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(db));
  } catch {
    // ignore
  }
}

function delay<T>(value: T): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), LATENCY_MS));
}

function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T;
}

function rid(prefix: string): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

// ---------- money helpers (mock only) ----------

function money(n: number): string {
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function computeItem(input: CostItemInput & { item_id: string }): CostItem {
  const qty = input.qty === null || input.qty === "" ? null : input.qty;
  const rate = input.rate === null || input.rate === "" ? null : input.rate;
  let amount: string | null = null;
  let formula: string;
  if (qty !== null && rate !== null && Number.isFinite(Number(qty)) && Number.isFinite(Number(rate))) {
    const a = Math.round(Number(qty) * Number(rate) * 100) / 100;
    amount = a.toFixed(2);
    formula = `${Number(qty)} ${input.unit} × ${money(Number(rate))}/${input.unit} = ${money(a)}`;
  } else {
    const missing = [qty === null ? "quantity" : null, rate === null ? "rate" : null]
      .filter(Boolean)
      .join(" and ");
    formula = `Missing ${missing} — amount not computed`;
  }
  return {
    item_id: input.item_id,
    category: input.category,
    description: input.description,
    qty,
    unit: input.unit,
    rate,
    amount,
    formula,
    source_evidence_id: input.source_evidence_id ?? null,
  };
}

function total(items: CostItem[]): string {
  const cents = items.reduce((s, i) => s + (i.amount ? Math.round(Number(i.amount) * 100) : 0), 0);
  return (cents / 100).toFixed(2);
}

function toFinding(sf: StoredFinding): Finding {
  const cost_items = sf.review.cost_items ?? sf.base.ai_cost_items;
  const questions: Question[] = sf.base.questions.map((q) => ({
    ...q,
    resolution: sf.review.question_resolutions[q.question_id] ?? null,
  }));
  return clone({
    ...sf.base,
    questions,
    review: sf.review,
    draft: sf.review.draft ?? sf.base.ai_draft,
    cost_items,
    cost_total: total(cost_items),
    packets: sf.packets,
  });
}

// ---------- fictional seed data ----------

const BOX = "https://app.box.com/file/";

function seedFiles(): SourceFile[] {
  const f = (id: string, name: string, doc_type: string, page_count: number): SourceFile => ({
    box_file_id: id,
    box_version_id: `v${id}`,
    name,
    doc_type,
    page_count,
    box_url: `${BOX}${id}`,
  });
  return [
    f("1402220001", "Keystone Interiors – Subcontract Agreement.pdf", "subcontract", 18),
    f("1402220002", "Exhibit B – Drywall & Framing Scope of Work.pdf", "scope", 6),
    f("1402220003", "Spec 09 21 16 Gypsum Board Assemblies – Rev 2.pdf", "spec_revision", 12),
    f("1402220004", "FD-07 Field Directive – Elevator Lobby Shaft Wall.pdf", "instruction", 2),
    f("1402220005", "Daily Reports – March 2026.pdf", "daily_report", 22),
    f("1402220006", "Timesheets – Weeks 10-12.xlsx", "timesheet", 3),
    f("1402220007", "Receipt – Allied Building Supply #88431.pdf", "receipt", 1),
    f("1402220008", "Keystone 2026 T&M Rate Sheet.pdf", "rate_sheet", 2),
    f("1402220009", "Change Order Log – Riverside MOB.xlsx", "change_order_log", 2),
  ];
}

function ev(
  id: string,
  file: SourceFile,
  page: number,
  role: Evidence["role"],
  quote: string,
  verified = true,
): Evidence {
  return {
    evidence_id: id,
    box_file_id: file.box_file_id,
    box_version_id: file.box_version_id,
    file_name: file.name,
    page,
    quote,
    role,
    verified,
    box_url: file.box_url,
  };
}

function emptyReview(): Review {
  return { status: "unreviewed", draft: null, cost_items: null, question_resolutions: {}, updated_at: null };
}

function seedFindings(scan_id: string, files: SourceFile[]): StoredFinding[] {
  const [sub, scope, spec, fd07, daily, timesheet, receipt, rates, coLog] = files;
  void sub;

  // ---- Finding 1: new, overlooked change ----
  const f1: StoredFinding["base"] = {
    finding_id: "fnd_shaftwall",
    scan_id,
    title: "Added 2-hr shaft wall at Level 2 elevator lobby",
    summary:
      "Field Directive FD-07 instructed Keystone to build a 2-hour rated shaft wall at the L2 elevator lobby. Exhibit B excludes shaft wall assemblies, the work is recorded in daily reports and timesheets on Mar 12–13, and there is no matching entry in the change-order log.",
    finding_status: "new",
    co_match: null,
    scope_baseline:
      "Exhibit B §2.4: shaft wall assemblies at elevator and mechanical shafts are excluded and by others (GC self-perform).",
    requested_work:
      "FD-07 directed Keystone to frame and board approx. 64 LF of 2-hr cavity shaft wall (CH studs, 1\" liner panel, two layers 5/8\" Type X) at the L2 elevator lobby.",
    evidence: [
      ev("ev_1a", fd07, 1, "instruction",
        "Keystone to furnish and install 2-hour rated shaft wall at Level 2 elevator lobby per detail A5/A-501. Proceed immediately to maintain elevator install schedule."),
      ev("ev_1b", scope, 3, "scope_baseline",
        "2.4 Exclusions: Shaft wall assemblies at elevator and mechanical shafts (by GC)."),
      ev("ev_1c", spec, 7, "scope_baseline",
        "Rev 2 adds Section 2.6 Cavity Shaft Wall Assemblies, 2-hour rating, at Elevator 2 lobby."),
      ev("ev_1d", daily, 9, "work_record",
        "03/12 – 3 carpenters on L2 elevator lobby shaft wall per FD-07. CH studs and liner panel installed approx. 40 LF."),
      ev("ev_1e", daily, 10, "work_record",
        "03/13 – Shaft wall complete at L2 lobby, 2nd layer Type X hung. Ready for GC inspection."),
      ev("ev_1f", timesheet, 2, "cost_support",
        "Cost code 09-SW (FD-07): M. Ortiz 6.5h, D. Kim 6.5h, R. Patel 6.5h"),
      ev("ev_1g", receipt, 1, "cost_support",
        "2-1/2\" CH stud 25ga (40 pcs), J-track, 1\" shaftliner panel — Subtotal $612.40"),
      ev("ev_1h", rates, 1, "cost_support", "Journeyman carpenter – straight time: $82.00/h"),
    ],
    timeline: [
      { date: "2026-03-10", event: "Spec 09 21 16 Rev 2 issued, adding shaft wall at Elevator 2 lobby", evidence_id: "ev_1c" },
      { date: "2026-03-11", event: "GC issues Field Directive FD-07 to Keystone", evidence_id: "ev_1a" },
      { date: "2026-03-12", event: "Crew of 3 frames shaft wall, ~40 LF installed", evidence_id: "ev_1d" },
      { date: "2026-03-12", event: "Shaft wall materials purchased from Allied Building Supply", evidence_id: "ev_1g" },
      { date: "2026-03-13", event: "Shaft wall completed and ready for inspection", evidence_id: "ev_1e" },
    ],
    questions: [],
    ai_draft: `CHANGE REQUEST – Added 2-Hour Shaft Wall, Level 2 Elevator Lobby

Project: Riverside Medical Office Building – Level 2 Fit-Out
Subcontractor: Keystone Interiors (Drywall & Framing)

Description of change
On March 11, 2026 the General Contractor issued Field Directive FD-07 directing Keystone to furnish and install a 2-hour rated cavity shaft wall at the Level 2 elevator lobby per detail A5/A-501. Shaft wall assemblies at elevator shafts are expressly excluded from our scope under Exhibit B §2.4.

Work performed
Keystone performed the work on March 12–13, 2026 with a crew of three carpenters, as recorded in the daily reports and timesheets (cost code 09-SW).

Cost
Labor: 19.5 h at $82.00/h = $1,599.00
Material: shaft wall studs, track and liner panel (Allied receipt #88431) = $612.40
Material: 5/8" Type X gypsum board, 24 sheets at $18.75 = $450.00
Total requested: $2,661.40

We request that a change order be issued for the above amount. Supporting documents are attached.`,
    ai_cost_items: [
      computeItem({ item_id: "ci_1a", category: "labor", description: "Carpenters – shaft wall framing & board (3 workers × 6.5 h)", qty: "19.5", unit: "h", rate: "82.00", source_evidence_id: "ev_1f" }),
      computeItem({ item_id: "ci_1b", category: "material", description: "CH studs, J-track, 1\" shaftliner (Allied #88431)", qty: "1", unit: "lot", rate: "612.40", source_evidence_id: "ev_1g" }),
      computeItem({ item_id: "ci_1c", category: "material", description: "5/8\" Type X gypsum board 4×10", qty: "24", unit: "ea", rate: "18.75", source_evidence_id: null }),
    ],
  };

  // ---- Finding 2: already recorded ----
  const f2: StoredFinding["base"] = {
    finding_id: "fnd_exam214",
    scan_id,
    title: "Relocated partition at Exam Room 214",
    summary:
      "ASI-011 moved the east partition of Exam 214 by 2'-0\". Daily reports show demolition and re-framing on Mar 4–5. This work matches CO-012 already in the change-order log.",
    finding_status: "already_recorded",
    co_match: { co_number: "CO-012", description: "Relocate Exam 214 east partition per ASI-011", amount: "3240.00" },
    scope_baseline: "Contract drawings A-201 Rev 0 show Exam 214 east partition on grid line 6.",
    requested_work: "ASI-011 relocated the partition 2'-0\" east; existing framing removed and rebuilt (approx. 18 LF, full height).",
    evidence: [
      ev("ev_2a", daily, 4, "work_record", "03/04 – Demo existing framing Exam 214 east wall per ASI-011. Re-layout new wall line."),
      ev("ev_2b", daily, 5, "work_record", "03/05 – Re-frame and board Exam 214 east partition, 18 LF."),
      ev("ev_2c", coLog, 1, "change_order", "CO-012 | Relocate Exam 214 east partition per ASI-011 | $3,240.00 | Approved 03/09"),
    ],
    timeline: [
      { date: "2026-03-04", event: "Existing partition demolished", evidence_id: "ev_2a" },
      { date: "2026-03-05", event: "Partition re-framed and boarded", evidence_id: "ev_2b" },
      { date: "2026-03-09", event: "CO-012 approved", evidence_id: "ev_2c" },
    ],
    questions: [],
    ai_draft:
      "This work appears to be already covered by CO-012 (approved 03/09, $3,240.00). No new change request is recommended unless the reviewer identifies costs beyond CO-012.",
    ai_cost_items: [
      computeItem({ item_id: "ci_2a", category: "labor", description: "Demo and re-frame partition", qty: "32", unit: "h", rate: "82.00", source_evidence_id: "ev_2b" }),
      computeItem({ item_id: "ci_2b", category: "material", description: "Studs, track, board and finish materials", qty: "1", unit: "lot", rate: "616.00", source_evidence_id: null }),
    ],
  };

  // ---- Finding 3: needs info ----
  const f3: StoredFinding["base"] = {
    finding_id: "fnd_soffits",
    scan_id,
    title: "Additional soffit framing at corridor ceiling transitions",
    summary:
      "Daily reports show Keystone framing soffits at corridor ceiling height transitions that are not shown in the scope drawings. No written authorization was found and the installed quantity is not recorded.",
    finding_status: "needs_info",
    co_match: null,
    scope_baseline: "Exhibit B §1.2 includes ceiling framing per reflected ceiling plan A-301; no soffits are shown at corridor transitions.",
    requested_work: "Framing and boarding soffits at corridor ceiling height changes (C-201 to C-204), reportedly at the GC superintendent's verbal request.",
    evidence: [
      ev("ev_3a", daily, 15, "work_record", "03/18 – 2 carpenters framing soffits at corridor ceiling transitions C-201 thru C-204 per GC super."),
      ev("ev_3b", daily, 15, "instruction", "Super said OK to proceed with soffits, will paper it later.", false),
      ev("ev_3c", scope, 2, "scope_baseline", "1.2 Ceiling framing per reflected ceiling plan A-301."),
      ev("ev_3d", rates, 1, "cost_support", "Journeyman carpenter – straight time: $82.00/h"),
    ],
    timeline: [
      { date: "2026-03-18", event: "Soffit framing started at corridor transitions", evidence_id: "ev_3a" },
    ],
    questions: [
      { question_id: "q_3a", kind: "missing_authorization", text: "Is there a written directive, RFI response or email authorizing the corridor soffits? The only reference is a verbal approval noted in the 03/18 daily report.", resolution: null },
      { question_id: "q_3b", kind: "missing_quantity", text: "How many labor hours and linear feet of soffit were installed? Timesheets for 03/18–03/20 do not use a separate cost code.", resolution: null },
    ],
    ai_draft: `CHANGE REQUEST – Corridor Soffit Framing (DRAFT – information needed)

Keystone Interiors framed soffits at corridor ceiling height transitions (C-201 through C-204) beginning March 18, 2026. These soffits are not shown on reflected ceiling plan A-301, which defines our ceiling framing scope under Exhibit B §1.2.

[Authorization reference needed]
[Quantities and hours needed]

Cost: to be determined once quantities are confirmed.`,
    ai_cost_items: [
      computeItem({ item_id: "ci_3a", category: "labor", description: "Carpenters – soffit framing and board", qty: null, unit: "h", rate: "82.00", source_evidence_id: "ev_3d" }),
      computeItem({ item_id: "ci_3b", category: "material", description: "Soffit framing and board materials", qty: null, unit: "LF", rate: "4.10", source_evidence_id: null }),
    ],
  };

  return [f1, f2, f3].map((base) => ({ base, review: emptyReview(), packets: [] }));
}

// ---------- progress simulation ----------

const STAGES: Array<[number, string]> = [
  [0, "Queued"],
  [5, "Listing Box folder"],
  [15, "Extracting text (3/9)"],
  [30, "Extracting text (7/9)"],
  [45, "Extracting facts (4/9)"],
  [60, "Matching against change-order log"],
  [75, "Verifying quotes against sources"],
  [88, "Drafting change-request packets"],
];

function scanView(s: StoredScan): Scan {
  const elapsed = Date.now() - new Date(s.started_at).getTime();
  const base = { scan_id: s.scan_id, started_at: s.started_at };
  if (elapsed < SCAN_DURATION_MS) {
    const progress = Math.min(99, Math.floor((elapsed / SCAN_DURATION_MS) * 100));
    if (MOCK_FAIL && progress >= 60) {
      return {
        ...base,
        status: "failed",
        stage: "Matching against change-order log",
        progress,
        error: "Mock failure: could not read 'Change Order Log – Riverside MOB.xlsx' (Box returned 403).",
        finished_at: new Date().toISOString(),
        files: [],
        findings: [],
      };
    }
    const stage = [...STAGES].reverse().find(([p]) => progress >= p)?.[1] ?? "Queued";
    return {
      ...base,
      status: progress < 5 ? "queued" : "running",
      stage,
      progress,
      error: null,
      finished_at: null,
      files: [],
      findings: [],
    };
  }
  if (MOCK_FAIL) {
    return {
      ...base,
      status: "failed",
      stage: "Matching against change-order log",
      progress: 60,
      error: "Mock failure: could not read 'Change Order Log – Riverside MOB.xlsx' (Box returned 403).",
      finished_at: new Date(new Date(s.started_at).getTime() + SCAN_DURATION_MS).toISOString(),
      files: [],
      findings: [],
    };
  }
  return {
    ...base,
    status: "completed",
    stage: "Completed",
    progress: 100,
    error: null,
    finished_at: new Date(new Date(s.started_at).getTime() + SCAN_DURATION_MS).toISOString(),
    files: clone(s.files),
    findings: s.findings.map(toFinding),
  };
}

function findStored(db: MockDb, scanId: string, findingId: string): StoredFinding {
  const scan = db.scans[scanId];
  if (!scan) throw new ApiError(`Scan ${scanId} not found`, 404);
  const f = scan.findings.find((x) => x.base.finding_id === findingId);
  if (!f) throw new ApiError(`Finding ${findingId} not found`, 404);
  return f;
}

// ---------- endpoints ----------

export const mockApi: ScopeGuardApi = {
  async createScan() {
    const db = loadDb();
    const scan_id = rid("scan");
    const files = seedFiles();
    db.scans[scan_id] = {
      scan_id,
      started_at: new Date().toISOString(),
      files,
      findings: seedFindings(scan_id, files),
    };
    saveDb(db);
    return delay({ scan_id, status: "queued" as const });
  },

  async listScans(limit = 20) {
    const db = loadDb();
    const scans = Object.values(db.scans)
      .sort((a, b) => b.started_at.localeCompare(a.started_at))
      .slice(0, limit)
      .map((s): ScanSummary => {
        const v = scanView(s);
        const count = (status: string) => v.findings.filter((f) => f.finding_status === status).length;
        return {
          scan_id: v.scan_id,
          status: v.status,
          stage: v.stage,
          progress: v.progress,
          error: v.error,
          started_at: v.started_at,
          finished_at: v.finished_at,
          box_folder_id: "mock-folder",
          summary:
            v.status === "completed"
              ? {
                  file_count: v.files.length,
                  finding_count: v.findings.length,
                  new_count: count("new"),
                  needs_info_count: count("needs_info"),
                  already_recorded_count: count("already_recorded"),
                  potential_total: total(v.findings.filter((f) => f.finding_status === "new").flatMap((f) => f.cost_items)),
                }
              : null,
        };
      });
    return delay({ scans });
  },

  async getScan(scanId) {
    const db = loadDb();
    const s = db.scans[scanId];
    if (!s) {
      await delay(null);
      throw new ApiError(`Scan ${scanId} not found`, 404);
    }
    return delay(scanView(s));
  },

  async patchFinding(findingId, patch: FindingPatch) {
    await delay(null);
    if (!patch.scan_id) throw new ApiError("scan_id is required", 400);
    const db = loadDb();
    const f = findStored(db, patch.scan_id, findingId);
    if (patch.status !== undefined) {
      if (!["unreviewed", "confirmed", "dismissed"].includes(patch.status)) {
        throw new ApiError(`Invalid status: ${patch.status}`, 400);
      }
      f.review.status = patch.status;
    }
    if (patch.draft !== undefined) f.review.draft = patch.draft;
    if (patch.cost_items === null) f.review.cost_items = null;
    else if (patch.cost_items !== undefined) {
      for (const ci of patch.cost_items) {
        for (const [k, v] of [["qty", ci.qty], ["rate", ci.rate]] as const) {
          if (v !== null && v !== "" && !/^-?\d+(\.\d+)?$/.test(v)) {
            throw new ApiError(`Invalid ${k} "${v}" — must be a decimal number`, 400);
          }
        }
      }
      f.review.cost_items = patch.cost_items.map((ci) =>
        computeItem({ ...ci, item_id: ci.item_id || rid("ci") }),
      );
    }
    if (patch.question_resolutions !== undefined) {
      f.review.question_resolutions = {
        ...f.review.question_resolutions,
        ...patch.question_resolutions,
      };
      for (const [k, v] of Object.entries(patch.question_resolutions)) {
        if (v === "") delete f.review.question_resolutions[k];
      }
    }
    f.review.updated_at = new Date().toISOString();
    saveDb(db);
    return toFinding(f);
  },

  async createPacket(req) {
    await delay(null);
    const db = loadDb();
    const f = findStored(db, req.scan_id, req.finding_id);
    if (f.review.status !== "confirmed") {
      throw new ApiError("Finding must be confirmed before a packet can be generated", 409);
    }
    const id = String(1500000000 + Math.floor(Math.random() * 99999999));
    const packet: Packet = { box_file_id: id, box_url: `${BOX}${id}`, created_at: new Date().toISOString() };
    f.packets.push(packet);
    saveDb(db);
    return clone(packet);
  },
};
