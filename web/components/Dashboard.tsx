"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { Check, ChevronRight, FileSearch, History, Play, RotateCw } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import type { Finding, FindingStatus, Scan, ScanSummary } from "@/lib/types";
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
  Badge,
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
  const [scans, setScans] = useState<ScanSummary[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [listKey, setListKey] = useState(0);

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
    let sawActive = false;
    const tick = async () => {
      try {
        const s = await api.getScan(scanId);
        if (cancelled) return;
        setScan(s);
        setLoadError(null);
        if (s.status === "queued" || s.status === "running") {
          sawActive = true;
          timer = setTimeout(tick, POLL_MS);
        } else if (sawActive) {
          setListKey((k) => k + 1); // the watched scan just finished: refresh the history
        }
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

  // Saved scans (GET /scans): reopen past ingestions without re-running them.
  useEffect(() => {
    let cancelled = false;
    api
      .listScans()
      .then((res) => {
        if (cancelled) return;
        setScans(res.scans);
        setListError(null);
      })
      .catch((err) => {
        if (!cancelled) setListError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [listKey]);

  // Nothing in the URL or this browser's memory: open the most recent saved scan.
  useEffect(() => {
    if (scanId || !scans) return;
    const latest = scans.find((s) => s.status === "completed") ?? scans[0];
    if (latest) router.replace(`/?scan=${encodeURIComponent(latest.scan_id)}`);
  }, [scanId, scans, router]);

  function openScan(id: string) {
    if (id === scanId) return;
    setFilter("all");
    router.push(`/?scan=${encodeURIComponent(id)}`);
  }

  async function runScan() {
    setStarting(true);
    setStartError(null);
    try {
      const res = await api.createScan();
      rememberScanId(res.scan_id);
      setFilter("all");
      setListKey((k) => k + 1);
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

        {!scanId && listError && <ErrorBox message={`Could not load saved scans: ${listError}`} />}
        {!scanId && !scans && !listError && <Spinner label="Loading saved scans…" />}
        {!scanId && (listError || scans?.length === 0) && <EmptyState onRun={runScan} starting={starting} />}

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
          <CompletedScan
            scan={currentScan}
            filter={filter}
            onFilter={setFilter}
            history={<ScanHistory scans={scans} currentId={scanId} onOpen={openScan} />}
          />
        )}

        {scanId && currentScan?.status !== "completed" && scans && scans.length > 0 && (
          <ScanHistory scans={scans} currentId={scanId} onOpen={openScan} />
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
  history,
}: {
  scan: Scan;
  filter: Filter;
  onFilter: (f: Filter) => void;
  history: ReactNode;
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

      <div className="flex items-start gap-2 rounded-lg border border-blue-200 bg-blue-50 px-4 py-2.5 text-sm text-blue-900">
        <History className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        <p>
          Saved results from the ingestion on {formatDateTime(scan.finished_at)}, loaded without re-reading Box. Reviews
          and edits are kept. Use <span className="font-medium">Run scan</span> only when the project folder has
          changed.
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

        <div className="space-y-5">
        {history}
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
      </div>
    </>
  );
}

function ScanHistory({
  scans,
  currentId,
  onOpen,
}: {
  scans: ScanSummary[] | null;
  currentId: string | null;
  onOpen: (scanId: string) => void;
}) {
  if (!scans || scans.length === 0) return null;
  return (
    <Card title={`Scan history (${scans.length})`}>
      <ul className="-m-4 divide-y divide-slate-100">
        {scans.map((s) => {
          const current = s.scan_id === currentId;
          return (
            <li key={s.scan_id}>
              <button
                type="button"
                onClick={() => onOpen(s.scan_id)}
                aria-current={current ? "true" : undefined}
                className={cx(
                  "flex w-full items-start gap-3 px-4 py-2.5 text-left",
                  current ? "bg-blue-50" : "hover:bg-slate-50",
                )}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className={cx("font-medium", current ? "text-blue-900" : "text-slate-900")}>
                      {formatDateTime(s.finished_at ?? s.started_at)}
                    </span>
                    {s.status !== "completed" && (
                      <Badge tone={s.status === "failed" ? "red" : "amber"}>
                        {s.status === "failed" ? "Failed" : `Running ${Math.round(s.progress)}%`}
                      </Badge>
                    )}
                  </div>
                  {s.summary ? (
                    <div className="mt-0.5 text-xs text-slate-500">
                      {s.summary.file_count} files · {s.summary.new_count} new · {s.summary.needs_info_count} needs info ·{" "}
                      {s.summary.already_recorded_count} recorded
                    </div>
                  ) : (
                    <div className="mt-0.5 truncate text-xs text-slate-500" title={s.error ?? s.stage}>
                      {s.error ?? s.stage}
                    </div>
                  )}
                </div>
                <div className="shrink-0 text-right">
                  {s.summary && (
                    <div className="text-sm font-semibold tabular-nums text-slate-900">
                      {formatMoney(s.summary.potential_total)}
                    </div>
                  )}
                  {current && (
                    <div className="flex items-center justify-end gap-1 text-xs text-blue-800">
                      <Check className="h-3 w-3" aria-hidden /> Viewing
                    </div>
                  )}
                </div>
              </button>
            </li>
          );
        })}
      </ul>
    </Card>
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
