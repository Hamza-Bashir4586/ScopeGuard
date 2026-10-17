"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ChevronRight, FileSearch, Play, RotateCw } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import type { Finding, FindingStatus, Scan } from "@/lib/types";
import { formatDateTime, formatMoney, sumMoney } from "@/lib/format";
import { findingHref, getRememberedScanId, rememberScanId } from "@/lib/scanMemory";
import {
  AppHeader,
  BoxLink,
  Button,
  Card,
  DocTypeBadge,
  ErrorBox,
  FindingStatusBadge,
  ReviewStatusBadge,
  Spinner,
  cx,
  findingStatusLabel,
} from "./ui";

const POLL_MS = 2000;

type Filter = "all" | FindingStatus;

const statusOrder: Record<FindingStatus, number> = { new: 0, needs_info: 1, already_recorded: 2 };

function openQuestionCount(f: Finding): number {
  return f.questions.filter((q) => !q.resolution || !q.resolution.trim()).length;
}

export default function Dashboard() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const urlScanId = searchParams.get("scan");
  const [rememberedId, setRememberedId] = useState<string | null>(() => getRememberedScanId());
  const scanId = urlScanId ?? rememberedId;

  const [scan, setScan] = useState<Scan | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");

  // Put a remembered scan id back in the URL so the address is shareable/refreshable.
  useEffect(() => {
    if (!urlScanId && rememberedId) router.replace(`/?scan=${encodeURIComponent(rememberedId)}`);
    else if (urlScanId) rememberScanId(urlScanId);
  }, [urlScanId, rememberedId, router]);

  // Fetch the scan, polling every 2 s while queued/running.
  useEffect(() => {
    if (!scanId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const s = await api.getScan(scanId);
        if (cancelled) return;
        setScan(s);
        setLoadError(null);
        if (s.status === "queued" || s.status === "running") timer = setTimeout(tick, POLL_MS);
      } catch (err) {
        if (!cancelled) setLoadError(errorMessage(err));
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [scanId, reloadKey]);

  const currentScan = scan && scan.scan_id === scanId ? scan : null;
  const isActive = currentScan?.status === "queued" || currentScan?.status === "running";

  async function runScan() {
    setStarting(true);
    setStartError(null);
    try {
      const res = await api.createScan();
      rememberScanId(res.scan_id);
      setFilter("all");
      router.push(`/?scan=${encodeURIComponent(res.scan_id)}`);
    } catch (err) {
      setStartError(errorMessage(err));
    } finally {
      setStarting(false);
    }
  }

  function clearScan() {
    rememberScanId(null);
    setRememberedId(null);
    setScan(null);
    setLoadError(null);
    router.replace("/");
  }

  return (
    <div className="min-h-screen">
      <AppHeader
        scanId={scanId}
        right={
          <Button variant="primary" onClick={runScan} busy={starting} disabled={isActive}>
            {!starting && <Play className="h-4 w-4" aria-hidden />}
            {isActive ? "Scan in progress" : "Run scan"}
          </Button>
        }
      />
      <main className="mx-auto max-w-7xl space-y-5 px-4 py-6 sm:px-6">
        {startError && <ErrorBox message={`Could not start scan: ${startError}`} />}

        {!scanId && <EmptyState onRun={runScan} starting={starting} />}

        {scanId && loadError && (
          <ErrorBox
            message={`Could not load scan ${scanId}: ${loadError}`}
            action={
              <div className="flex gap-2">
                <Button onClick={() => setReloadKey((k) => k + 1)}>
                  <RotateCw className="h-4 w-4" aria-hidden /> Retry
                </Button>
                <Button variant="ghost" onClick={clearScan}>
                  Clear
                </Button>
              </div>
            }
          />
        )}

        {scanId && !currentScan && !loadError && <Spinner label="Loading scan…" />}

        {currentScan && isActive && <ScanProgress scan={currentScan} />}

        {currentScan?.status === "failed" && (
          <ErrorBox
            message={`Scan failed${currentScan.stage ? ` during “${currentScan.stage}”` : ""}: ${
              currentScan.error ?? "Unknown error"
            }`}
            action={
              <Button onClick={runScan} busy={starting}>
                <RotateCw className="h-4 w-4" aria-hidden /> Retry scan
              </Button>
            }
          />
        )}

        {currentScan?.status === "completed" && (
          <CompletedScan scan={currentScan} filter={filter} onFilter={setFilter} />
        )}
      </main>
    </div>
  );
}

function EmptyState({ onRun, starting }: { onRun: () => void; starting: boolean }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 bg-white px-6 py-12 text-center">
      <FileSearch className="mx-auto h-10 w-10 text-slate-400" aria-hidden />
      <h1 className="mt-3 text-lg font-semibold text-slate-900">No scan yet</h1>
      <p className="mx-auto mt-2 max-w-2xl text-sm leading-6 text-slate-600">
        A scan reads the project folder in Box — subcontract and scope, revised specs, written
        instructions, daily reports, timesheets, receipts, rate sheets and the change-order log — and
        flags work that looks like it went beyond the original scope but is missing from the
        change-order log. Each finding comes with source-cited evidence, a traceable cost breakdown,
        open questions and a draft change request for you to review.
      </p>
      <p className="mx-auto mt-2 max-w-2xl text-sm text-slate-500">
        ScopeGuard only flags potential extra work. You decide what gets submitted.
      </p>
      <Button variant="primary" className="mt-5" onClick={onRun} busy={starting}>
        {!starting && <Play className="h-4 w-4" aria-hidden />} Run first scan
      </Button>
    </div>
  );
}

function ScanProgress({ scan }: { scan: Scan }) {
  const pct = Math.max(0, Math.min(100, Math.round(scan.progress)));
  return (
    <Card title="Scanning project folder">
      <div className="flex items-center justify-between text-sm">
        <span className="text-slate-700">{scan.stage || (scan.status === "queued" ? "Queued" : "Running")}</span>
        <span className="tabular-nums text-slate-500">{pct}%</span>
      </div>
      <div
        className="mt-2 h-2 w-full overflow-hidden rounded-full bg-slate-100"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Scan progress"
      >
        <div className="h-full rounded-full bg-blue-600 transition-all duration-700" style={{ width: `${pct}%` }} />
      </div>
      <p className="mt-2 text-xs text-slate-500">
        Started {formatDateTime(scan.started_at)} · Scan {scan.scan_id}. This page updates automatically.
      </p>
    </Card>
  );
}

function Tile({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className={cx("mt-1 text-2xl font-semibold tabular-nums", tone ?? "text-slate-900")}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

function CompletedScan({
  scan,
  filter,
  onFilter,
}: {
  scan: Scan;
  filter: Filter;
  onFilter: (f: Filter) => void;
}) {
  const findings = scan.findings;
  const stats = useMemo(() => {
    const newOnes = findings.filter((f) => f.finding_status === "new" && f.review.status !== "dismissed");
    return {
      newCount: newOnes.length,
      newTotal: sumMoney(newOnes.map((f) => f.cost_total)),
      recorded: findings.filter((f) => f.finding_status === "already_recorded").length,
      needsInfo: findings.filter((f) => f.finding_status === "needs_info").length,
      reviewed: findings.filter((f) => f.review.status !== "unreviewed").length,
    };
  }, [findings]);

  const counts: Record<Filter, number> = {
    all: findings.length,
    new: findings.filter((f) => f.finding_status === "new").length,
    needs_info: stats.needsInfo,
    already_recorded: stats.recorded,
  };

  const visible = findings
    .filter((f) => filter === "all" || f.finding_status === filter)
    .sort((a, b) => statusOrder[a.finding_status] - statusOrder[b.finding_status]);

  const filters: Filter[] = ["all", "new", "needs_info", "already_recorded"];

  return (
    <>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold text-slate-900">Scan results</h1>
        <p className="text-xs text-slate-500">
          Completed {formatDateTime(scan.finished_at)} · {scan.files.length} files · Scan {scan.scan_id}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile
          label="Potential new changes"
          value={String(stats.newCount)}
          sub={`${formatMoney(stats.newTotal)} total (excl. dismissed)`}
          tone="text-blue-800"
        />
        <Tile label="Already recorded" value={String(stats.recorded)} sub="Matched to change-order log" />
        <Tile label="Needs info" value={String(stats.needsInfo)} sub="Open questions to resolve" tone="text-amber-800" />
        <Tile label="Reviewed" value={`${stats.reviewed} / ${findings.length}`} sub="Confirmed or dismissed" />
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <Card
          className="lg:col-span-2"
          title="Findings"
          actions={
            <div className="flex flex-wrap gap-1" role="tablist" aria-label="Filter findings">
              {filters.map((f) => (
                <button
                  key={f}
                  type="button"
                  role="tab"
                  aria-selected={filter === f}
                  onClick={() => onFilter(f)}
                  className={cx(
                    "rounded-md px-2.5 py-1 text-xs font-medium",
                    filter === f ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100",
                  )}
                >
                  {f === "all" ? "All" : findingStatusLabel[f]} ({counts[f]})
                </button>
              ))}
            </div>
          }
        >
          {visible.length === 0 ? (
            <p className="text-sm text-slate-500">
              {findings.length === 0
                ? "No potential extra work was found in this folder."
                : "No findings in this category."}
            </p>
          ) : (
            <ul className="-m-4 divide-y divide-slate-200">
              {visible.map((f) => (
                <FindingRow key={f.finding_id} scanId={scan.scan_id} finding={f} />
              ))}
            </ul>
          )}
        </Card>

        <Card title={`Source files (${scan.files.length})`}>
          {scan.files.length === 0 ? (
            <p className="text-sm text-slate-500">No files were read.</p>
          ) : (
            <ul className="-my-2 divide-y divide-slate-100">
              {scan.files.map((file) => (
                <li key={file.box_file_id} className="py-2">
                  <div className="truncate text-sm text-slate-900" title={file.name}>
                    {file.name}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                    <DocTypeBadge docType={file.doc_type} />
                    <span>
                      {file.page_count} {file.page_count === 1 ? "page" : "pages"}
                    </span>
                    <span className="ml-auto">
                      <BoxLink href={file.box_url} />
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}

function FindingRow({ scanId, finding: f }: { scanId: string; finding: Finding }) {
  const open = openQuestionCount(f);
  return (
    <li>
      <Link
        href={findingHref(scanId, f.finding_id)}
        className={cx(
          "group flex items-start gap-4 px-4 py-3 hover:bg-slate-50",
          f.review.status === "dismissed" && "opacity-60",
        )}
      >
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-slate-900 group-hover:underline">{f.title}</span>
          </div>
          <p className="mt-1 line-clamp-2 text-sm text-slate-600">{f.summary}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <FindingStatusBadge status={f.finding_status} />
            <ReviewStatusBadge status={f.review.status} />
            {f.co_match && <span>Matches {f.co_match.co_number}</span>}
            {f.questions.length > 0 && (
              <span className={open > 0 ? "font-medium text-amber-800" : ""}>
                {open} open {open === 1 ? "question" : "questions"}
              </span>
            )}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className="text-sm font-semibold tabular-nums text-slate-900">{formatMoney(f.cost_total)}</div>
          <div className="text-xs text-slate-500">cost total</div>
        </div>
        <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-slate-400" aria-hidden />
      </Link>
    </li>
  );
}
