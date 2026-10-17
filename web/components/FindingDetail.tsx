"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, CheckCircle2, CircleSlash, FileCheck2, Info, RotateCw, Undo2 } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import type { Finding, FindingPatch, Packet, Scan } from "@/lib/types";
import { formatDateTime, formatMoney } from "@/lib/format";
import { rememberScanId } from "@/lib/scanMemory";
import {
  AppHeader,
  BoxLink,
  Button,
  Card,
  ErrorBox,
  FindingStatusBadge,
  ReviewStatusBadge,
  SaveIndicator,
  type SaveState,
  Spinner,
} from "./ui";
import { EvidenceList, Timeline } from "./finding/Evidence";
import { CostWorksheet } from "./finding/CostWorksheet";
import { DraftEditor } from "./finding/DraftEditor";
import { QuestionsPanel } from "./finding/Questions";

export type PatchFn = (patch: Omit<FindingPatch, "scan_id">) => Promise<Finding>;

export default function FindingDetail() {
  const params = useSearchParams();
  const scanId = params.get("scan");
  const findingId = params.get("id");

  const [scan, setScan] = useState<Scan | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!scanId) return;
    let cancelled = false;
    rememberScanId(scanId);
    api
      .getScan(scanId)
      .then((s) => {
        if (cancelled) return;
        setScan(s);
        setLoadError(null);
      })
      .catch((err) => {
        if (!cancelled) setLoadError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [scanId, reloadKey]);

  const replaceFinding = useCallback((updated: Finding) => {
    setScan((s) =>
      s ? { ...s, findings: s.findings.map((f) => (f.finding_id === updated.finding_id ? updated : f)) } : s,
    );
  }, []);

  const backHref = scanId ? `/?scan=${encodeURIComponent(scanId)}` : "/";
  const currentScan = scan && scan.scan_id === scanId ? scan : null;
  const finding = currentScan?.findings.find((f) => f.finding_id === findingId) ?? null;

  let body: React.ReactNode;
  if (!scanId || !findingId) {
    body = <ErrorBox message="This link is missing a scan or finding id." />;
  } else if (loadError) {
    body = (
      <ErrorBox
        message={`Could not load scan: ${loadError}`}
        action={
          <Button onClick={() => setReloadKey((k) => k + 1)}>
            <RotateCw className="h-4 w-4" aria-hidden /> Retry
          </Button>
        }
      />
    );
  } else if (!currentScan) {
    body = <Spinner label="Loading finding…" />;
  } else if (currentScan.status !== "completed") {
    body = (
      <ErrorBox
        message={
          currentScan.status === "failed"
            ? `This scan failed: ${currentScan.error ?? "unknown error"}`
            : "This scan is still running. Findings appear when it completes."
        }
        action={
          <Link href={backHref} className="text-sm font-medium underline">
            Back to dashboard
          </Link>
        }
      />
    );
  } else if (!finding) {
    body = <ErrorBox message={`Finding ${findingId} was not found in scan ${scanId}.`} />;
  } else {
    body = (
      <FindingView
        key={finding.finding_id}
        finding={finding}
        onUpdated={replaceFinding}
        onReload={() => setReloadKey((k) => k + 1)}
      />
    );
  }

  return (
    <div className="min-h-screen">
      <AppHeader scanId={scanId} />
      <main className="mx-auto max-w-7xl space-y-5 px-4 py-6 sm:px-6">
        <Link
          href={backHref}
          className="inline-flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900"
        >
          <ArrowLeft className="h-4 w-4" aria-hidden /> All findings
        </Link>
        {body}
      </main>
    </div>
  );
}

function FindingView({
  finding,
  onUpdated,
  onReload,
}: {
  finding: Finding;
  onUpdated: (f: Finding) => void;
  onReload: () => void;
}) {
  const patch: PatchFn = useCallback(
    async (p) => {
      const updated = await api.patchFinding(finding.finding_id, { scan_id: finding.scan_id, ...p });
      onUpdated(updated);
      return updated;
    },
    [finding.finding_id, finding.scan_id, onUpdated],
  );

  return (
    <>
      <FindingHeader finding={finding} />

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="min-w-0 space-y-5 lg:col-span-2">
          <WhatChanged finding={finding} />
          <EvidenceList evidence={finding.evidence} />
          <CostWorksheet finding={finding} patch={patch} />
          <DraftEditor finding={finding} patch={patch} />
        </div>
        <aside className="min-w-0 space-y-5">
          <ReviewPanel finding={finding} patch={patch} onUpdated={onUpdated} onReload={onReload} />
          <QuestionsPanel finding={finding} patch={patch} />
          <Timeline entries={finding.timeline} evidence={finding.evidence} />
        </aside>
      </div>
    </>
  );
}

function FindingHeader({ finding }: { finding: Finding }) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold text-slate-900">{finding.title}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <FindingStatusBadge status={finding.finding_status} />
            <ReviewStatusBadge status={finding.review.status} />
            {finding.review.updated_at && (
              <span className="text-xs text-slate-500">
                Last reviewed {formatDateTime(finding.review.updated_at)}
              </span>
            )}
          </div>
        </div>
        <div className="text-right">
          <div className="text-xs uppercase tracking-wide text-slate-500">Cost total</div>
          <div className="text-xl font-semibold tabular-nums text-slate-900">{formatMoney(finding.cost_total)}</div>
        </div>
      </div>
      <p className="max-w-4xl text-sm leading-6 text-slate-700">{finding.summary}</p>
      {finding.co_match && (
        <div className="flex items-start gap-3 rounded-md border border-slate-300 bg-slate-50 px-4 py-3 text-sm">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-slate-600" aria-hidden />
          <div>
            <div className="font-medium text-slate-900">
              Already recorded as {finding.co_match.co_number}
              {finding.co_match.amount && (
                <span className="font-normal text-slate-600"> · {formatMoney(finding.co_match.amount)}</span>
              )}
            </div>
            <div className="text-slate-700">{finding.co_match.description}</div>
          </div>
        </div>
      )}
    </div>
  );
}

function WhatChanged({ finding }: { finding: Finding }) {
  return (
    <Card title="What changed">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Original scope</div>
          <p className="mt-1.5 whitespace-pre-line text-sm leading-6 text-slate-800">
            {finding.scope_baseline || "—"}
          </p>
        </div>
        <div className="rounded-md border border-blue-200 bg-blue-50/60 p-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-blue-800">Requested work</div>
          <p className="mt-1.5 whitespace-pre-line text-sm leading-6 text-slate-800">
            {finding.requested_work || "—"}
          </p>
        </div>
      </div>
    </Card>
  );
}

function ReviewPanel({
  finding,
  patch,
  onUpdated,
  onReload,
}: {
  finding: Finding;
  patch: PatchFn;
  onUpdated: (f: Finding) => void;
  onReload: () => void;
}) {
  const [statusState, setStatusState] = useState<SaveState>({ kind: "idle" });
  const [pending, setPending] = useState<string | null>(null);
  const [packetState, setPacketState] = useState<SaveState>({ kind: "idle" });
  const [lastPacket, setLastPacket] = useState<Packet | null>(null);
  const status = finding.review.status;
  const confirmed = status === "confirmed";

  async function setStatus(next: Finding["review"]["status"]) {
    setPending(next);
    setStatusState({ kind: "saving" });
    try {
      await patch({ status: next });
      setStatusState({ kind: "saved", at: Date.now() });
    } catch (err) {
      setStatusState({ kind: "error", message: errorMessage(err) });
    } finally {
      setPending(null);
    }
  }

  async function generatePacket() {
    setPacketState({ kind: "saving" });
    setLastPacket(null);
    try {
      const packet = await api.createPacket({ scan_id: finding.scan_id, finding_id: finding.finding_id });
      setLastPacket(packet);
      setPacketState({ kind: "saved", at: Date.now() });
      if (!finding.packets.some((p) => p.box_file_id === packet.box_file_id)) {
        onUpdated({ ...finding, packets: [...finding.packets, packet] });
      }
      onReload();
    } catch (err) {
      setPacketState({ kind: "error", message: errorMessage(err) });
    }
  }

  const packets = [...finding.packets].sort((a, b) => b.created_at.localeCompare(a.created_at));

  return (
    <Card title="Review" actions={<SaveIndicator state={statusState} />}>
      <div className="space-y-4">
        <div className="flex items-center gap-2 text-sm text-slate-700">
          Status: <ReviewStatusBadge status={status} />
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant={confirmed ? "primary" : "secondary"}
            onClick={() => setStatus("confirmed")}
            disabled={confirmed || pending !== null}
            busy={pending === "confirmed"}
          >
            {pending !== "confirmed" && <CheckCircle2 className="h-4 w-4" aria-hidden />} Confirm
          </Button>
          <Button
            variant="danger"
            onClick={() => setStatus("dismissed")}
            disabled={status === "dismissed" || pending !== null}
            busy={pending === "dismissed"}
          >
            {pending !== "dismissed" && <CircleSlash className="h-4 w-4" aria-hidden />} Dismiss
          </Button>
          {status !== "unreviewed" && (
            <Button
              variant="ghost"
              onClick={() => setStatus("unreviewed")}
              disabled={pending !== null}
              busy={pending === "unreviewed"}
            >
              {pending !== "unreviewed" && <Undo2 className="h-4 w-4" aria-hidden />} Undo
            </Button>
          )}
        </div>

        <div className="border-t border-slate-200 pt-4">
          <Button
            variant="primary"
            className="w-full"
            onClick={generatePacket}
            disabled={!confirmed}
            busy={packetState.kind === "saving"}
          >
            {packetState.kind !== "saving" && <FileCheck2 className="h-4 w-4" aria-hidden />}
            Generate packet &amp; save to Box
          </Button>
          <p className="mt-2 text-xs text-slate-500">
            {confirmed
              ? "Uses the last saved draft, costs and verified evidence. Save your edits first."
              : "Confirm the finding to enable packet generation."}
          </p>
          {packetState.kind === "error" && (
            <div className="mt-2">
              <ErrorBox message={`Packet not generated: ${packetState.message}`} />
            </div>
          )}
          {lastPacket && packetState.kind === "saved" && (
            <div className="mt-2 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
              Packet saved to Box. <BoxLink href={lastPacket.box_url} label="Open packet" />
            </div>
          )}
          {packets.length > 0 && (
            <div className="mt-3">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Packets</div>
              <ul className="mt-1 divide-y divide-slate-100 text-sm">
                {packets.map((p) => (
                  <li key={p.box_file_id} className="flex items-center justify-between gap-2 py-1.5">
                    <span className="text-slate-700">{formatDateTime(p.created_at)}</span>
                    <BoxLink href={p.box_url} label="Open" />
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <p className="border-t border-slate-200 pt-3 text-xs leading-5 text-slate-500">
          ScopeGuard flags potential extra work; it does not determine payment entitlement.
        </p>
      </div>
    </Card>
  );
}
