"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { WorkspaceApiError, workspaceApi, workspaceError } from "@/lib/workspace-api";
import type { Finding, FindingPatch, InvoiceDraft, InvoiceGeneration, ProjectCard, ProjectDetail, RecordCategory, Scan, SourceRecord } from "@/lib/workspace-types";

export function useProjectWorkspace(projectId: string | null) {
  const [projects, setProjects] = useState<ProjectCard[]>([]);
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [records, setRecords] = useState<SourceRecord[]>([]);
  const [categories, setCategories] = useState<RecordCategory[]>([]);
  const [scan, setScan] = useState<Scan | null>(null);
  const [activeScan, setActiveScan] = useState<Scan | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [draft, setDraft] = useState<InvoiceDraft | null>(null);
  const [generation, setGeneration] = useState<InvoiceGeneration | null>(null);
  const [loading, setLoading] = useState(false);
  const [cardsLoading, setCardsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cardsError, setCardsError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [pollRetry, setPollRetry] = useState(0);
  const currentProject = useRef(projectId); currentProject.current = projectId;
  const loadTicket = useRef(0);
  const generationRequest = useRef<{ revision: number; key: string } | null>(null);
  const scanRequest = useRef<{ projectId: string; cutoff: string; key: string } | null>(null);

  const loadCards = useCallback(async () => {
    try { setProjects((await workspaceApi.projects()).projects); setCardsError(null); }
    catch (err) { setCardsError(workspaceError(err)); }
    finally { setCardsLoading(false); }
  }, []);
  useEffect(() => { void loadCards(); }, [loadCards]);

  const refresh = useCallback(async (): Promise<boolean> => {
    if (!projectId) return false;
    const id = projectId, ticket = ++loadTicket.current;
    try {
      const outcomes = await Promise.allSettled([workspaceApi.project(id), workspaceApi.records(id), workspaceApi.draft(id)]);
      if (currentProject.current !== id || ticket !== loadTicket.current) return false;
      const [detail, sourceRecords, invoice] = outcomes;
      if (detail.status === "fulfilled") setProject(detail.value);
      if (sourceRecords.status === "fulfilled") { setRecords(sourceRecords.value.records); setCategories(sourceRecords.value.categories); }
      if (invoice.status === "fulfilled") setDraft(invoice.value);
      const failures = outcomes.filter((item): item is PromiseRejectedResult => item.status === "rejected");
      if (failures.length) { setError(failures.map((item) => workspaceError(item.reason)).filter((value, i, all) => all.indexOf(value) === i).join(" ")); return false; }
      if (detail.status !== "fulfilled") return false;
      setActiveId(detail.value.active_scan?.scan_id || null);
      if (detail.value.latest_scan) {
        const latest = await workspaceApi.scan(detail.value.latest_scan.scan_id);
        if (currentProject.current !== id || ticket !== loadTicket.current) return false;
        setScan(latest);
      } else setScan(null);
      setError(null);
      return true;
    } catch (err) { if (currentProject.current === id) setError(workspaceError(err)); return false; }
  }, [projectId]);

  useEffect(() => {
    loadTicket.current += 1;
    setProject(null); setRecords([]); setCategories([]); setScan(null); setActiveScan(null); setActiveId(null); setDraft(null); setGeneration(null); setActionError(null);
    if (!projectId) { setLoading(false); void loadCards(); return; }
    setLoading(true);
    void refresh().finally(() => { if (currentProject.current === projectId) setLoading(false); });
    return () => { loadTicket.current += 1; };
  }, [projectId, refresh, loadCards]);

  useEffect(() => {
    if (!activeId || !projectId) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const value = await workspaceApi.scan(activeId, controller.signal);
        if (controller.signal.aborted) return;
        setActiveScan(value);
        if (value.status === "queued" || value.status === "running") timer = setTimeout(() => void tick(), 2000);
        else {
          setActiveId(null);
          if (value.status === "completed") setScan(value);
          else setActionError(value.error || "The audit failed. The previous completed audit is still available.");
          await refresh(); void loadCards();
        }
      } catch (err) { if (!controller.signal.aborted) setActionError(`${workspaceError(err)} Use Retry status to reconnect to this audit.`); }
    };
    void tick(); return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [activeId, projectId, refresh, loadCards, pollRetry]);

  async function startAudit(cutoff: string): Promise<boolean> {
    if (!projectId || busy || activeId) return false;
    const id = projectId; setBusy("scan"); setActionError(null);
    const previous = scanRequest.current;
    const request = previous?.projectId === id && previous.cutoff === cutoff ? previous : { projectId: id, cutoff, key: crypto.randomUUID() };
    scanRequest.current = request;
    try {
      const result = await workspaceApi.startScan({ project_id: id, cutoff_date: cutoff || undefined, request_id: request.key });
      if (currentProject.current !== id) return false;
      setActiveScan(null); setActiveId(result.scan_id); scanRequest.current = null; return true;
    } catch (err) {
      if (err instanceof WorkspaceApiError && err.code === "scan_active" && typeof err.details?.scan_id === "string") { setActiveId(err.details.scan_id); scanRequest.current = null; return true; }
      setActionError(workspaceError(err)); return false;
    } finally { setBusy(null); }
  }

  async function recover(err: unknown) {
    const conflict = err instanceof WorkspaceApiError && (err.status === 409 || err.status === 422);
    if (conflict) await refresh();
    setActionError(`${workspaceError(err)}${conflict ? " The latest saved state has been loaded. Review it before trying again." : ""}`);
  }

  async function saveReview(finding: Finding, value: Omit<FindingPatch, "scan_id" | "expected_revision">, addToInvoice: boolean): Promise<boolean> {
    if (!projectId || busy) return false;
    const id = projectId; setBusy("review"); setActionError(null);
    try {
      const saved = await workspaceApi.review(finding.finding_id, { ...value, scan_id: finding.scan_id, expected_revision: finding.review.revision });
      if (currentProject.current !== id) return false;
      setScan((previous) => previous ? { ...previous, findings: previous.findings.map((item) => item.work_item_id === saved.work_item_id ? saved : item) } : previous);
      if (addToInvoice) {
        const latest = await workspaceApi.draft(id);
        const ids = latest.lines.map((item) => item.work_item_id);
        if (!ids.includes(saved.work_item_id)) ids.push(saved.work_item_id);
        setDraft(await workspaceApi.updateDraft(id, { expected_revision: latest.revision, scan_id: finding.scan_id, selected_work_item_ids: ids }));
      }
      setGeneration(null); await refresh(); void loadCards(); return true;
    } catch (err) { await recover(err); return false; }
    finally { setBusy(null); }
  }

  async function removeLine(workItemId: string): Promise<boolean> {
    if (!projectId || !scan || busy) return false;
    const id = projectId; setBusy(`remove:${workItemId}`); setActionError(null);
    try {
      const latest = await workspaceApi.draft(id);
      setDraft(await workspaceApi.updateDraft(id, { expected_revision: latest.revision, scan_id: scan.scan_id, selected_work_item_ids: latest.lines.filter((item) => item.work_item_id !== workItemId).map((item) => item.work_item_id) }));
      setGeneration(null); await refresh(); void loadCards(); return true;
    } catch (err) { await recover(err); return false; }
    finally { setBusy(null); }
  }

  async function generate(): Promise<boolean> {
    if (!projectId || !draft?.can_generate || busy) return false;
    setBusy("generate"); setActionError(null);
    const request = generationRequest.current?.revision === draft.revision ? generationRequest.current : { revision: draft.revision, key: crypto.randomUUID() };
    generationRequest.current = request;
    try { setGeneration(await workspaceApi.generate(projectId, { expected_revision: draft.revision, request_id: request.key })); return true; }
    catch (err) { await recover(err); return false; }
    finally { setBusy(null); }
  }

  async function saveToBox(): Promise<boolean> {
    if (!projectId || !generation || busy) return false;
    setBusy("box"); setActionError(null);
    try { setGeneration(await workspaceApi.saveToBox(projectId, generation.generation_id)); return true; }
    catch (err) { setActionError(workspaceError(err)); return false; }
    finally { setBusy(null); }
  }

  async function resetDemo(): Promise<boolean> {
    if (!projectId || busy || activeId) return false;
    setBusy("reset"); setActionError(null);
    try {
      await workspaceApi.resetDemo(projectId, { request_id: crypto.randomUUID(), expected_revision: draft?.revision || 0 });
      setScan(null); setActiveScan(null); setActiveId(null); setGeneration(null);
      scanRequest.current = null; generationRequest.current = null;
      await refresh(); await loadCards(); return true;
    } catch (err) { await recover(err); return false; }
    finally { setBusy(null); }
  }

  return { projects, project, records, categories, scan, activeScan, activeId, draft, generation, loading, cardsLoading, error: projectId ? error : cardsError, actionError, busy,
    setActionError, setBusy, setGeneration, refresh, loadCards, startAudit, saveReview, removeLine, generate, saveToBox, resetDemo,
    retryStatus: () => { setActionError(null); setPollRetry((value) => value + 1); } };
}
