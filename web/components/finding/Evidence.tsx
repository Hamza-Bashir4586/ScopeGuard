"use client";

import { CheckCircle2, CircleAlert } from "lucide-react";
import type { Evidence, EvidenceRole, TimelineEntry } from "@/lib/types";
import { formatDate } from "@/lib/format";
import { BoxLink, Card, cx } from "../ui";

const roleOrder: EvidenceRole[] = [
  "instruction",
  "scope_baseline",
  "work_record",
  "cost_support",
  "change_order",
  "contradiction",
];

const roleLabel: Record<EvidenceRole, string> = {
  instruction: "Instructions",
  scope_baseline: "Original scope",
  work_record: "Work records",
  cost_support: "Cost support",
  change_order: "Change orders",
  contradiction: "Contradicting records",
};

export function evidenceAnchor(id: string): string {
  return `ev-${id}`;
}

export function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  const verifiedCount = evidence.filter((e) => e.verified).length;
  const groups = roleOrder
    .map((role) => ({ role, items: evidence.filter((e) => e.role === role) }))
    .filter((g) => g.items.length > 0);
  // Any role values outside the contract still get shown.
  const other = evidence.filter((e) => !roleOrder.includes(e.role));

  return (
    <Card
      title={`Evidence (${evidence.length})`}
      actions={
        <span className="text-xs text-slate-500">
          {verifiedCount} of {evidence.length} quotes verified in source
        </span>
      }
    >
      {evidence.length === 0 ? (
        <p className="text-sm text-slate-500">No evidence cited.</p>
      ) : (
        <div className="space-y-5">
          {groups.map((g) => (
            <EvidenceGroup key={g.role} label={roleLabel[g.role]} items={g.items} />
          ))}
          {other.length > 0 && <EvidenceGroup label="Other" items={other} />}
        </div>
      )}
    </Card>
  );
}

function EvidenceGroup({ label, items }: { label: string; items: Evidence[] }) {
  return (
    <div>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {label} ({items.length})
      </h3>
      <ul className="space-y-2">
        {items.map((e) => (
          <EvidenceCard key={e.evidence_id} e={e} />
        ))}
      </ul>
    </div>
  );
}

function EvidenceCard({ e }: { e: Evidence }) {
  return (
    <li
      id={evidenceAnchor(e.evidence_id)}
      className={cx(
        "evidence-card scroll-mt-20 rounded-md border px-3 py-2.5",
        e.verified ? "border-slate-200 bg-white" : "border-dashed border-slate-300 bg-slate-50 text-slate-500",
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0 text-sm">
          <span className={cx("font-medium", e.verified ? "text-slate-900" : "text-slate-500")}>{e.file_name}</span>
          <span className="text-slate-500"> · p. {e.page}</span>
        </div>
        <div className="flex items-center gap-3">
          {e.verified ? (
            <span className="inline-flex items-center gap-1 text-xs text-emerald-700">
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> Verified
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-xs text-slate-500">
              <CircleAlert className="h-3.5 w-3.5" aria-hidden /> Not verified
            </span>
          )}
          <BoxLink href={e.box_url} />
        </div>
      </div>
      <blockquote
        className={cx(
          "mt-2 border-l-2 pl-3 text-sm leading-6",
          e.verified ? "border-slate-300 text-slate-800" : "border-slate-200 italic text-slate-500",
        )}
      >
        “{e.quote}”
      </blockquote>
      {!e.verified && (
        <p className="mt-1.5 text-xs text-slate-500">Quote not found in source — excluded from packet.</p>
      )}
    </li>
  );
}

export function Timeline({ entries, evidence }: { entries: TimelineEntry[]; evidence: Evidence[] }) {
  const sorted = [...entries].sort((a, b) => a.date.localeCompare(b.date));
  const byId = new Map(evidence.map((e) => [e.evidence_id, e]));
  return (
    <Card title="Timeline">
      {sorted.length === 0 ? (
        <p className="text-sm text-slate-500">No dated events.</p>
      ) : (
        <ol className="relative ml-1.5 border-l border-slate-200">
          {sorted.map((t, i) => {
            const ev = t.evidence_id ? byId.get(t.evidence_id) : undefined;
            return (
              <li key={`${t.date}-${i}`} className="mb-4 ml-4 last:mb-0">
                <span className="absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full border-2 border-white bg-slate-400" />
                <div className="text-xs font-medium tabular-nums text-slate-500">{formatDate(t.date)}</div>
                <div className="text-sm text-slate-800">{t.event}</div>
                {t.evidence_id && (
                  <a
                    href={`#${evidenceAnchor(t.evidence_id)}`}
                    className="text-xs text-blue-700 hover:underline"
                  >
                    {ev ? `${ev.file_name}, p. ${ev.page}` : "View evidence"}
                  </a>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </Card>
  );
}
