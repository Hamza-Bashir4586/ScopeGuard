"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  AlertCircle, ArrowDownToLine, ArrowLeft, ArrowRight, ArrowUpRight, Building2, CalendarDays,
  Check, ChevronDown, ChevronRight, CircleCheck, Clock3, FileCheck2, FileSearch, FileStack,
  FileText, FolderOpen, LoaderCircle, MessageSquare, Paperclip, Plus, RefreshCw, ShieldCheck,
  Sparkles, Upload, X,
} from "lucide-react";
import { LOCAL_BACKEND_DEMO, workspaceApi, workspaceError } from "@/lib/workspace-api";
import type { Evidence, Finding, SourceRecord, SourceRef } from "@/lib/workspace-types";
import { useProjectWorkspace } from "./useProjectWorkspace";
import SourceRecordViewer, { type SourceViewerRequest } from "./SourceRecordViewer";
import Modal from "./WorkspaceModal";
import styles from "./workspace.module.css";

function money(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  // Formatting only: all amounts and totals come from Python, never client calculations.
  const numeric = Number(value);
  return Number.isFinite(numeric) ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(numeric) : "—";
}
function date(value: string | null | undefined, year = true): string {
  if (!value) return "Date not supplied";
  const parsed = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  return Number.isNaN(parsed.getTime()) ? "Date not supplied" : new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", ...(year ? { year: "numeric" as const } : {}) }).format(parsed);
}
function today(): string { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
function sourceRef(record: SourceRecord): SourceRef { return { file_id: record.file_id, version_id: record.version_id, file_name: record.name, page: 1, quote: null, available: record.ingestion_status !== "failed" }; }
function evidenceSources(evidence: Evidence[]): SourceRef[] {
  return evidence.filter((item, index, all) => all.findIndex((other) => other.file_id === item.file_id && other.version_id === item.version_id) === index)
    .map((item) => ({ file_id: item.file_id, version_id: item.version_id, file_name: item.file_name, page: item.page, quote: item.verified ? item.quote : null, available: true }));
}
const stages = ["Reading Box records", "Connecting evidence", "Checking billing", "Findings ready"];
const stageIds = ["reading_box_records", "connecting_evidence", "checking_billing", "findings_ready"];

export default function ConnectedWorkspace() {
  const router = useRouter();
  const params = useSearchParams();
  const projectId = params.get("project");
  const data = useProjectWorkspace(projectId);
  const { project, scan, draft, generation } = data;
  const [tab, setTab] = useState<"findings" | "records">("findings");
  const [category, setCategory] = useState("all");
  const [viewer, setViewer] = useState<SourceViewerRequest | null>(null);
  const [activeWorkItem, setActiveWorkItem] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const [acknowledge, setAcknowledge] = useState(false);
  const [revalidate, setRevalidate] = useState(false);
  const [reviewFeedback, setReviewFeedback] = useState("");
  const [invoiceOpen, setInvoiceOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [uploaded, setUploaded] = useState<string[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [cutoff, setCutoff] = useState(today);
  const heading = useRef<HTMLHeadingElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => { setTab("findings"); setCategory("all"); setViewer(null); setActiveWorkItem(null); setInvoiceOpen(false); setUploadOpen(false); setFiles([]); setUploaded([]); setUploadError(null); }, [projectId]);
  useEffect(() => { if (project?.latest_scan?.cutoff_date) setCutoff(project.latest_scan.cutoff_date); }, [project?.latest_scan?.cutoff_date]);

  function navigate(id?: string) { router.push(id ? `/?project=${encodeURIComponent(id)}` : "/"); window.scrollTo({ top: 0, behavior: "instant" }); requestAnimationFrame(() => heading.current?.focus({ preventScroll: true })); }
  function view(sources: SourceRef[], finding?: Finding) {
    if (!sources.length) return;
    setViewer({ sources, scanId: finding?.scan_id, findingId: finding?.finding_id });
  }
  function review(finding: Finding) {
    setComment(finding.review.internal_comment); setAcknowledge(finding.review.authorization_acknowledged); setRevalidate(false); setReviewFeedback("");
    data.setActionError(null); setActiveWorkItem(finding.work_item_id);
  }
  const activeFinding = scan?.findings.find((finding) => finding.work_item_id === activeWorkItem) || null;
  async function saveReview(intent: "save" | "add" | "dismiss") {
    if (!activeFinding) return;
    const saved = await data.saveReview(activeFinding, {
      internal_comment: comment,
      status: intent === "dismiss" ? "dismissed" : "reviewed",
      ...(acknowledge ? { acknowledge_authorization: true } : {}),
      ...(revalidate ? { confirm_revalidation: true } : {}),
    }, intent === "add");
    if (saved) { if (intent === "save") setReviewFeedback("Review and internal comment saved to the project."); else setActiveWorkItem(null); }
  }
  async function generate() { if (await data.generate()) setInvoiceOpen(true); }
  async function downloadInvoice() {
    if (!projectId || !generation || data.busy) return;
    data.setBusy("download"); data.setActionError(null);
    let url: string | undefined;
    try {
      const pdf = await workspaceApi.generationContent(projectId, generation.generation_id);
      url = URL.createObjectURL(pdf);
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = generation.file_name; document.body.append(anchor); anchor.click(); anchor.remove();
      const downloadUrl = url; setTimeout(() => URL.revokeObjectURL(downloadUrl), 60000);
    } catch (error) { if (url) URL.revokeObjectURL(url); data.setActionError(workspaceError(error)); }
    finally { data.setBusy(null); }
  }
  async function uploadRecords() {
    if (!projectId || !files.length || data.busy) return;
    data.setBusy("upload"); setUploadError(null);
    const pending = [...files];
    try {
      for (const file of pending) {
        if (file.size > 4 * 1024 * 1024) throw new Error(`${file.name} exceeds the 4 MiB upload limit.`);
        if (!/\.(pdf|txt|md)$/i.test(file.name)) throw new Error(`${file.name} is not a supported PDF, TXT, or Markdown record.`);
        const saved = await workspaceApi.upload(projectId, file);
        setUploaded((previous) => [...previous, saved.name]);
        setFiles((previous) => previous.filter((item) => item !== file));
      }
      await data.refresh();
      if (fileInput.current) fileInput.current.value = "";
    } catch (error) { setUploadError(workspaceError(error)); }
    finally { data.setBusy(null); }
  }
  async function analyzeUploaded() { if (await data.startAudit(cutoff)) { setUploadOpen(false); setTab("findings"); } }

  const currentAudit = data.activeScan || scan;
  const isAuditing = !!data.activeId || data.busy === "scan";
  const stageIndex = data.activeId && !data.activeScan ? 0 : currentAudit ? stageIds.indexOf(currentAudit.stage_id) : -1;
  const findings = scan?.findings || [];
  const toReview = findings.filter((finding) => finding.review.status === "unreviewed" || finding.review.status === "needs_revalidation").length;
  const shownRecords = data.records.filter((record) => category === "all" || record.category_id === category);
  const company = project?.contractor || data.projects.find((item) => !item.preview_only)?.contractor || "Project workspace";
  const initials = company.split(/\s+/).map((word) => word[0]).join("").slice(0, 2);
  const canAdd = activeFinding && activeFinding.amount !== null && (!activeFinding.evidence_changed || revalidate) && (activeFinding.finding_type !== "authorization_unresolved" || acknowledge);

  return <div className={styles.application}>
    <header className={styles.appHeader}>
      <button className={styles.brand} onClick={() => navigate()} aria-label="ScopeGuard projects"><span className={styles.brandMark}><ShieldCheck size={21} strokeWidth={1.6} /></span>ScopeGuard<span className={styles.brandDot}>.</span></button><div className={styles.headerDivider} />
      <button className={styles.headerProjectLink} onClick={() => navigate()}>Projects{project && <><ChevronRight size={13} /><span>{project.name}</span></>}</button>
      <div className={styles.headerRight}><span className={styles.previewBadge}>{LOCAL_BACKEND_DEMO ? "Local backend demo" : "Connected workspace"}</span><span className={styles.companyName}>{company}</span><span className={styles.companyAvatar} aria-label={company}>{initials}</span></div>
    </header>

    <main className={styles.main}>
      {data.error && <div className={styles.errorBanner} role="alert"><AlertCircle size={16} /><span>{data.error}</span><button onClick={() => void (projectId ? data.refresh() : data.loadCards())}>Retry</button></div>}
      {!projectId ? <div className={styles.screen}>
        <div className={styles.pageHeading}><div><div className={styles.eyebrow}>YOUR WORKSPACE</div><h1 ref={heading} tabIndex={-1}>Projects</h1><p>Scope, changes, and the evidence behind every invoice.</p></div><button className={styles.primaryButton} disabled title="Add project is a visual placeholder"><Plus size={16} />Add project</button></div>
        <div className={styles.projectSectionLabel}><span>All projects<span className={styles.quietCount}>{data.projects.length}</span></span><span><span className={styles.smallDot} />{company}</span></div>
        {data.cardsLoading && <p className={styles.loadingState}><LoaderCircle size={16} className={styles.spin} />Loading projects…</p>}
        {!data.cardsLoading && !data.error && !data.projects.length && <p className={styles.loadingState}>No projects are configured yet.</p>}
        <div className={styles.projectGrid}>{data.projects.map((item) => <article key={item.project_id} className={`${styles.projectCard} ${!item.preview_only ? styles.projectInteractive : ""}`}>
          <div className={styles.projectCardTop}><span className={styles.projectCode}>{item.code}</span><span className={styles.projectStatus}><span className={item.status === "active" ? styles.smallDot : styles.mutedDot} />{item.status === "active" ? "Active" : "Completed"}</span></div>
          <div className={styles.projectCardBody}><span className={styles.projectSymbol}><Building2 size={20} strokeWidth={1.4} /></span><h2>{item.name}</h2><p>{item.customer}</p><div className={styles.projectDates}><CalendarDays size={13} />{date(item.start_date, false)} – {date(item.end_date)}</div></div>
          <div className={styles.projectCardFooter}><span className={item.to_review_count === 0 ? styles.completeText : ""}>{item.to_review_count === 0 ? <CircleCheck size={14} /> : <FileStack size={14} />}{item.review_label}</span>{!item.preview_only ? <button className={styles.openProject} aria-label={`Open ${item.name}`} onClick={() => navigate(item.project_id)}><ArrowUpRight size={18} /></button> : <span className={styles.placeholderArrow} title="Layout preview"><ArrowUpRight size={17} /></span>}</div>
        </article>)}</div>
        {LOCAL_BACKEND_DEMO && <p className={styles.workspaceNote}><ShieldCheck size={14} />Fictional project records served by the local Python backend.</p>}
        <div className={styles.workspaceFootnote}><span>One place for your project history.</span><span>Built with <span className={styles.boxWord}>box</span> & AWS</span></div>
      </div> : <div className={styles.screen}>
        <button className={styles.backLink} onClick={() => navigate()}><ArrowLeft size={14} />All projects</button>
        {data.loading && !project && <p className={styles.loadingState}><LoaderCircle size={16} className={styles.spin} />Loading project history…</p>}
        {project && <>
          <div className={styles.pageHeading}><div><div className={styles.projectTitleMeta}><span className={styles.projectCode}>{project.code}</span><span className={styles.projectStatus}><span className={styles.smallDot} />{project.status === "active" ? "Active project" : "Completed project"}</span></div><h1 ref={heading} tabIndex={-1}>{project.name}</h1><p className={styles.projectSubline}><span><Building2 size={14} />{project.customer}</span><span className={styles.inlineSeparator} /><span><CalendarDays size={14} />{date(project.start_date, false)} – {date(project.end_date)}</span></p></div><button className={styles.secondaryButton} disabled={!!data.busy} onClick={() => { setUploadOpen(true); setUploadError(null); }}><Plus size={16} />Add project records</button></div>
          <section className={styles.agreement} aria-labelledby="live-agreement-heading"><div className={styles.agreementMain}><div className={styles.sectionEyebrow}><span className={styles.documentIcon}><FileCheck2 size={17} /></span><h2 id="live-agreement-heading">Original agreement</h2>{project.agreement.signed_date && <span className={styles.signedTag}><Check size={11} />Signed {date(project.agreement.signed_date)}</span>}</div><p>{project.agreement.summary}</p><div className={styles.agreementTerms}>{project.agreement.key_terms.map((term) => <span key={term}>{term}</span>)}</div></div><div className={styles.agreementAside}><span className={styles.metaLabel}>Original contract value</span><strong>{money(project.agreement.contract_value)}</strong><button className={styles.textButton} disabled={!project.agreement.source} onClick={() => project.agreement.source && view([project.agreement.source])}>View original scope<FileText size={13} /><ArrowUpRight size={13} /></button></div></section>

          <section className={styles.auditPanel} aria-labelledby="live-audit-heading" aria-busy={isAuditing}>
            <div className={styles.auditTop}><div className={styles.auditLabel}><span className={`${styles.auditIcon} ${isAuditing ? styles.auditIconActive : ""}`}><Sparkles size={19} /></span><div><h2 id="live-audit-heading">Project audit</h2><p>{isAuditing ? (data.activeScan?.stage || "Audit queued") : project.latest_scan ? `Last audited ${date(project.latest_scan.finished_at)} · ${project.records_count} project records` : `${project.records_count} project records · No audit run yet`}</p></div></div><button className={styles.primaryButton} disabled={isAuditing || !!data.busy} onClick={() => { setTab("findings"); void data.startAudit(cutoff); }}>{isAuditing ? <LoaderCircle className={styles.spin} size={15} /> : project.audit_action === "refresh" ? <RefreshCw size={14} /> : <Sparkles size={15} />}{isAuditing ? "Auditing records…" : project.audit_action === "refresh" ? "Refresh audit" : "Run audit"}</button></div>
            <ol className={styles.auditStages}>{stages.map((label, index) => <li key={label} className={index === stageIndex ? styles.stageActive : index < stageIndex ? styles.stageDone : ""}><span className={styles.stageDot}>{index < stageIndex || (index === 3 && currentAudit?.status === "completed") ? <Check size={11} /> : index === stageIndex && isAuditing ? <span className={styles.stagePulse} /> : index + 1}</span><span>{label}</span>{index < 3 && <span className={styles.stageConnector} />}</li>)}</ol>
            <div className={styles.auditEvent} aria-live="polite"><span className={styles.auditEventText}>{isAuditing ? <LoaderCircle size={12} className={styles.spin} /> : scan ? <CircleCheck size={13} /> : <FileSearch size={13} />}{data.activeScan?.current_event?.message || (isAuditing ? "Waiting for the audit worker…" : scan ? (scan.current_event?.message || `${scan.findings.length} findings ready for review.`) : "Your agreement and supporting records are ready.")}</span>{data.activeScan?.current_event?.source && <button className={styles.auditSource} onClick={() => { const source = data.activeScan?.current_event?.source; if (source) view([source]); }}><FileText size={12} />View record<ArrowUpRight size={12} /></button>}</div>
            <div className={styles.auditSettings}><label htmlFor="audit-cutoff">Audit through<input id="audit-cutoff" type="date" value={cutoff} disabled={isAuditing || !!data.busy} onChange={(event) => setCutoff(event.target.value)} /></label><span>{isAuditing && data.activeScan ? `${data.activeScan.files_processed} / ${data.activeScan.files_total} records · ${data.activeScan.progress}%` : project.new_input_available ? "New or changed records available. Refresh to include them." : scan?.cutoff_date ? `Records assessed through ${date(scan.cutoff_date)}.` : "Choose a cutoff date for the project history."}</span></div>
          </section>
          {data.actionError && !activeFinding && !invoiceOpen && <div className={styles.errorBanner} role="alert"><AlertCircle size={16} /><span>{data.actionError}</span>{data.activeId && <button onClick={data.retryStatus}>Retry status</button>}</div>}
          {scan && (!scan.coverage.complete || scan.coverage.warnings.length > 0) && <div className={styles.warningBanner} role="status"><AlertCircle size={15} /><span>{scan.coverage.complete ? "Audit notes: " : "Audit coverage is incomplete. "}{scan.coverage.warnings.join(" ")}</span></div>}
          <div className={styles.tabs} role="tablist" aria-label="Project workspace"><button id="live-findings-tab" role="tab" aria-selected={tab === "findings"} aria-controls="live-findings-panel" onClick={() => setTab("findings")}><FileSearch size={15} />Flagged discrepancies{scan && <span>{findings.length}</span>}</button><button id="live-records-tab" role="tab" aria-selected={tab === "records"} aria-controls="live-records-panel" onClick={() => setTab("records")}><FolderOpen size={15} />All source records<span>{data.records.length}</span></button></div>

          {tab === "findings" ? <div id="live-findings-panel" role="tabpanel" aria-labelledby="live-findings-tab" className={styles.findingsLayout}>
            <section className={styles.findingsArea} aria-label="Flagged discrepancies">
              {!scan || !findings.length ? <div className={styles.auditEmpty}><span className={styles.emptyIcon}><FileSearch size={27} strokeWidth={1.3} /></span><h3>{isAuditing ? "Following the evidence" : scan ? "No discrepancies found in this audit" : "Ready for a closer look?"}</h3><p>{isAuditing ? "The backend is reviewing project records. Your previous completed audit stays available during a refresh." : scan ? "Review the audit coverage and source records to understand what was included." : "Run an audit to find work that may not have reached an invoice, with the records to support it."}</p><span className={styles.emptyCaption}><Paperclip size={12} />{project.records_count} project records</span></div> : <>
                <div className={styles.findingsHeading}><span>{toReview ? `${toReview} ${toReview === 1 ? "finding needs" : "findings need"} your review` : "All findings reviewed"}</span><span>Grounded in your project records</span></div>
                <div className={styles.findingGrid}>{findings.map((finding) => {
                  const unreviewed = finding.review.status === "unreviewed" || finding.review.status === "needs_revalidation";
                  const status = finding.review.status === "needs_revalidation" ? "Evidence changed" : finding.selected ? "In invoice" : finding.review.status === "dismissed" ? "Dismissed" : finding.review.status === "reviewed" ? "Reviewed" : "Needs review";
                  return <article key={finding.work_item_id} className={`${styles.findingCard} ${unreviewed ? styles.findingUnreviewed : ""} ${finding.selected && !unreviewed ? styles.findingSelected : ""} ${finding.review.status === "dismissed" ? styles.findingDismissed : ""}`}><div className={styles.findingCardMeta}><span className={styles.projectCode}>{finding.code}</span><span className={finding.selected && !unreviewed ? styles.selectedStatus : styles.reviewStatus}>{finding.selected && <Check size={11} />}{status}</span></div><h3>{finding.title}</h3><p>{finding.reason}</p><div className={styles.findingAmountRow}><strong>{finding.amount === null ? "Unpriced" : money(finding.amount)}</strong><span className={finding.proposal ? styles.proposedLabel : styles.approvedLabel}>{finding.proposal ? "Proposed addition" : finding.authorization_status === "approved" ? "Approved change" : finding.type_label}</span></div><div className={styles.findingCardFooter}><button className={styles.sourceCount} onClick={() => view(evidenceSources(finding.evidence_groups.flatMap((group) => group.evidence)), finding)}><Paperclip size={13} />{finding.source_count} source records</button><button className={styles.reviewButton} onClick={() => review(finding)}>Review<ArrowRight size={13} /></button></div></article>;
                })}</div>
              </>}
              {!!scan?.existing_items.length && <details className={styles.existingBalances}><summary><Clock3 size={14} />Existing changes & previously invoiced balances<ChevronDown size={14} /></summary>{scan.existing_items.map((item) => <div key={item.item_id} className={styles.existingItem}><p><strong>{item.reference} · {money(item.amount)}</strong><br />{item.label}. {item.explanation}</p>{item.sources.length > 0 && <button className={styles.textButton} onClick={() => view(item.sources)}>View supporting records<ArrowUpRight size={12} /></button>}</div>)}</details>}
            </section>
            <aside className={styles.invoiceSidebar} aria-label="Invoice draft"><div className={styles.invoiceHeading}><span className={styles.invoiceIcon}><FileText size={18} /></span><h2>Invoice draft</h2><span className={styles.quietCount}>{draft?.lines.length || 0}</span></div><p className={styles.invoiceHelp}>Your reviewed findings, together.</p>{draft?.lines.length ? <div className={styles.selectedItems}>{draft.lines.map((line) => <div className={styles.selectedItem} key={line.work_item_id}><span>{line.description}</span><div><strong>{money(line.amount)}</strong><button disabled={!!data.busy} aria-label={`Remove ${line.title} from invoice`} onClick={() => void data.removeLine(line.work_item_id)}>{data.busy === `remove:${line.work_item_id}` ? <LoaderCircle size={13} className={styles.spin} /> : <X size={13} />}</button></div>{line.blockers.map((blocker) => <p className={styles.lineBlocker} key={blocker}>{blocker}</p>)}</div>)}</div> : <div className={styles.invoiceEmpty}><span className={styles.invoiceEmptyLines}><i /><i /><i /></span><span>No items selected yet</span><p>Review a discrepancy and add it to your invoice.</p></div>}<div className={styles.invoiceTotal}><span>Selected subtotal</span><strong>{money(draft?.subtotal)}</strong></div><button className={styles.primaryButton} disabled={!draft?.can_generate || !!data.busy} onClick={() => void generate()}>{data.busy === "generate" ? <LoaderCircle size={14} className={styles.spin} /> : null}{data.busy === "generate" ? "Generating invoice…" : "Generate for customer sign-off"}<ArrowRight size={14} /></button>{draft?.lines.length ? draft.blockers.map((blocker) => <p key={blocker} className={styles.lineBlocker}>{blocker}</p>) : null}<p className={styles.invoiceNote}><ShieldCheck size={13} />Supporting evidence is included with every selected item.</p><div className={styles.invoiceStatus}><span className={styles.mutedDot} />Draft only. Nothing is sent.</div>{generation && <button className={styles.textButton} onClick={() => setInvoiceOpen(true)}>View generated invoice<ArrowUpRight size={12} /></button>}</aside>
          </div> : <section id="live-records-panel" role="tabpanel" aria-labelledby="live-records-tab" className={styles.recordsPanel}><div className={styles.recordsHeading}><div><h2>Project source records</h2><p>Original files, organized by the backend&apos;s suggested categories.</p></div><label className={styles.categorySelect}><span className={styles.srOnly}>Filter source records by category</span><FolderOpen size={14} /><select value={category} onChange={(event) => setCategory(event.target.value)}><option value="all">All categories</option>{data.categories.map((item) => <option key={item.category_id} value={item.category_id}>{item.label} ({item.count})</option>)}</select><ChevronDown size={13} /></label></div><div className={styles.recordColumnHead}><span>Document</span><span>Category</span><span>Document date</span><span /></div>{shownRecords.map((record) => <button className={styles.recordRow} key={`${record.file_id}-${record.version_id}`} onClick={() => view([sourceRef(record)])}><span className={styles.recordName}><span className={styles.recordFileIcon}><FileText size={17} /></span><span>{record.name}<small>{record.page_count === null ? "Not analyzed" : `${record.page_count} ${record.page_count === 1 ? "page" : "pages"}`} · {record.extension.toUpperCase()}{record.ingestion_status !== "ingested" ? ` · ${record.ingestion_status}` : ""}{record.warnings.length ? ` · ${record.warnings.join("; ")}` : ""}</small></span></span><span className={styles.recordCategory}>{record.category_label}</span><span className={styles.recordDate}>{date(record.document_date || record.modified_at)}</span><ArrowUpRight size={15} /></button>)}{!shownRecords.length && <p className={styles.loadingState}>No records in this category.</p>}<div className={styles.recordsFooter}>{shownRecords.length} of {data.records.length} records<span>Source versions retained for evidence review</span></div></section>}
          <footer className={styles.projectFooter}><span>{project.contractor}<span>·</span>Project workspace</span><span>{LOCAL_BACKEND_DEMO ? "Local Python backend · Fictional records · Deterministic analysis" : "Source-linked findings. Invoice drafts require customer review."}</span></footer>
        </>}
      </div>}
    </main>

    <Modal open={!!activeFinding} title="Review discrepancy" onClose={() => setActiveWorkItem(null)} className={styles.reviewModal}>
      {activeFinding && <><div className={styles.reviewIntro}><div className={styles.projectCode}>{activeFinding.code} · {project?.name}</div><h2>{activeFinding.title}</h2><div className={styles.reviewAmount}><strong>{activeFinding.amount === null ? "Unpriced" : money(activeFinding.amount)}</strong><span className={activeFinding.proposal ? styles.proposedLabel : styles.approvedLabel}>{activeFinding.approval_label}</span></div></div><div className={styles.reviewBody}><div className={styles.evidenceHeading}><h3>The supporting evidence</h3><span><ShieldCheck size={13} />Source-grounded</span></div><div className={styles.evidenceList}>{activeFinding.evidence_groups.map((group, index) => <div className={styles.evidenceBlock} key={`${group.label}-${index}`}><span className={styles.evidenceStep}>{index + 1}</span><div><span className={styles.metaLabel}>{group.label}</span><button className={styles.evidenceSource} disabled={!group.evidence.length} onClick={() => view(evidenceSources(group.evidence), activeFinding)}>{group.source_label}<ArrowUpRight size={13} /></button><blockquote>{group.summary_quote}</blockquote>{group.evidence.some((item) => !item.verified) && <p className={styles.lineBlocker}>Contains unverified evidence; unverified passages are excluded from the invoice.</p>}</div></div>)}</div><div className={styles.reasonBlock}><span className={styles.reasonIcon}><Sparkles size={15} /></span><div><h3>Why this was flagged</h3><p>{activeFinding.explanation}</p>{activeFinding.calculation && <p>{activeFinding.calculation}</p>}<span className={styles.approvalDetail}>{activeFinding.approval_label}</span></div></div>
        {activeFinding.questions.length > 0 && <div className={styles.reviewQuestions}><h3>Items requiring attention</h3><ul>{activeFinding.questions.map((question) => <li key={question.question_id}>{question.text}</li>)}</ul></div>}
        {activeFinding.evidence_changed && <label className={styles.acknowledgment}><input type="checkbox" checked={revalidate} disabled={!!data.busy} onChange={(event) => setRevalidate(event.target.checked)} /><span>I have reviewed the changed source evidence and reconfirm this finding.</span></label>}
        {activeFinding.finding_type === "authorization_unresolved" && <label className={styles.acknowledgment}><input type="checkbox" checked={acknowledge} disabled={!!data.busy} onChange={(event) => setAcknowledge(event.target.checked)} /><span>I acknowledge that authorization is unresolved. Keep this item labeled as a proposal requiring customer acceptance.</span></label>}
        <label className={styles.commentLabel} htmlFor="live-review-comment">Your review comments<span>Internal only</span></label><textarea className={styles.commentInput} id="live-review-comment" maxLength={10000} disabled={!!data.busy} value={comment} onChange={(event) => { setComment(event.target.value); setReviewFeedback(""); }} placeholder="Add context, explain a correction, or note what you confirmed…" /><p className={styles.commentHelp} role="status"><MessageSquare size={12} />{reviewFeedback || "Comments are saved to the project and excluded from the customer invoice."}</p>
        {activeFinding.eligibility_blockers.length > 0 && <div className={styles.reviewQuestions}><h3>Invoice eligibility</h3><ul>{activeFinding.eligibility_blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul></div>}
        {data.actionError && <p className={styles.errorBanner} role="alert">{data.actionError}</p>}
      </div><div className={styles.reviewActions}><button className={styles.textButton} disabled={!!data.busy} onClick={() => void saveReview("dismiss")}>Dismiss flag</button><div><button className={styles.secondaryButton} disabled={!!data.busy} onClick={() => void saveReview("save")}>{data.busy === "review" ? <LoaderCircle size={14} className={styles.spin} /> : <Check size={14} />}Save comment</button><button className={styles.primaryButton} disabled={!!data.busy || !canAdd} onClick={() => void saveReview("add")}><Plus size={14} />{activeFinding.selected ? "Update invoice item" : "Add to invoice"}</button></div></div></>}
    </Modal>
    {projectId && <SourceRecordViewer projectId={projectId} request={viewer} onClose={() => setViewer(null)} />}

    <Modal open={uploadOpen} title="Add project records" onClose={() => { if (data.busy !== "upload") setUploadOpen(false); }} className={styles.uploadModal}>
      <div className={styles.uploadBody}><h2>Add records to this project</h2><p>Add change instructions, work reports, approvals, or invoices. Files are uploaded one at a time, then you can run the audit.</p><label className={styles.uploadDropzone}><span className={styles.uploadIcon}><Upload size={23} strokeWidth={1.4} /></span><strong>Choose project records</strong><span>PDF, TXT, or Markdown · Up to 4 MiB per file</span><input ref={fileInput} type="file" accept=".pdf,.txt,.md" multiple disabled={!!data.busy} onChange={(event) => { setFiles(Array.from(event.target.files || [])); setUploadError(null); }} /><span className={styles.secondaryButton}>Browse files<Plus size={14} /></span></label>{files.length > 0 && <div className={styles.uploadFiles}>{files.map((file, i) => <div key={`${file.name}-${i}`}><FileText size={14} /><span>{file.name}</span><Clock3 size={13} /></div>)}</div>}{uploaded.length > 0 && <div className={styles.uploadFiles}>{uploaded.map((name, i) => <div key={`${name}-${i}`}><Check size={14} /><span>{name} · Uploaded</span></div>)}</div>}{uploadError && <p className={styles.errorBanner} role="alert">{uploadError}</p>}{data.actionError && <p className={styles.errorBanner} role="alert">{data.actionError}</p>}</div><div className={styles.uploadActions}><button className={styles.secondaryButton} disabled={!!data.busy} onClick={() => setUploadOpen(false)}>Close</button>{files.length ? <button className={styles.primaryButton} disabled={!!data.busy} onClick={() => void uploadRecords()}>{data.busy === "upload" ? <LoaderCircle size={15} className={styles.spin} /> : <Upload size={15} />}{data.busy === "upload" ? "Uploading…" : "Upload records"}</button> : <button className={styles.primaryButton} disabled={!!data.busy || !!data.activeId || !uploaded.length} onClick={() => void analyzeUploaded()}><Sparkles size={15} />Analyze new documents</button>}</div>
    </Modal>

    <Modal open={invoiceOpen && !!generation} title="Customer invoice preview" onClose={() => setInvoiceOpen(false)} className={styles.invoicePreview}>
      {generation && project && <><article className={styles.invoiceDocument}><div className={styles.invoiceDocumentTop}><span className={styles.invoiceCompany}>{project.contractor}</span><span className={styles.draftLabel}>DRAFT · NOT SENT</span></div><div className={styles.invoiceDocumentTitle}><div><span className={styles.eyebrow}>SUPPLEMENTAL INVOICE</span><h2>For customer review<br />and sign-off.</h2></div><FileCheck2 size={39} strokeWidth={1} /></div><div className={styles.invoiceRecipient}><div><span className={styles.metaLabel}>Prepared for</span><strong>{project.customer}</strong></div><div><span className={styles.metaLabel}>Project</span><strong>{project.name}</strong></div></div><div className={styles.invoiceLineHead}><span>Work description</span><span>Amount</span></div>{generation.lines.map((line) => <div className={styles.invoiceLine} key={line.work_item_id}><div><strong>{line.description}</strong><p>{line.approval_label}</p><span>{line.code}</span></div><strong>{money(line.amount)}</strong></div>)}<div className={styles.invoiceDocumentTotal}><span>Draft subtotal</span><strong>{money(generation.subtotal)}</strong></div><p className={styles.invoiceTerms}>For customer review. Proposed changes remain subject to customer acceptance. Complete payment terms, taxes, and applicable retainage before issue. Generated {date(generation.created_at)}.</p><section className={styles.invoiceAppendix}><div className={styles.sectionEyebrow}><Paperclip size={15} /><h3>Evidence appendix</h3></div>{generation.appendix.map((entry) => <div key={entry.line_index} className={styles.appendixItem}><h4>{entry.line_index}. {entry.title}</h4>{entry.entries.map((evidence, i) => <p key={`${evidence.version_id}-${i}`}><strong>{evidence.label} · {evidence.file_name}, p. {evidence.page}</strong><span>{evidence.quote}</span><span>{evidence.explanation}</span></p>)}</div>)}</section><div className={styles.invoiceAcceptance}><div>Customer acceptance<span /></div><div>Date<span /></div></div></article><div className={`${styles.invoicePreviewActions} ${styles.noPrint}`}><p role="status"><ShieldCheck size={13} />{generation.box ? LOCAL_BACKEND_DEMO ? "Saved to the local project output folder." : "Invoice pack saved in Box." : "Evidence included. Internal comments excluded."}</p>{generation.box && !LOCAL_BACKEND_DEMO && <a className={styles.textButton} href={generation.box.url} target="_blank" rel="noopener noreferrer">Open saved invoice in Box<ArrowUpRight size={12} /></a>}{data.actionError && <p className={styles.errorBanner} role="alert">{data.actionError}</p>}<div><button className={styles.secondaryButton} disabled={!!data.busy || !!generation.box} onClick={() => void data.saveToBox()}>{data.busy === "box" ? <LoaderCircle size={14} className={styles.spin} /> : generation.box ? <Check size={14} /> : <FolderOpen size={14} />}{generation.box ? "Saved" : LOCAL_BACKEND_DEMO ? "Save to output folder" : "Save invoice pack to Box"}</button><button className={styles.primaryButton} disabled={!!data.busy} onClick={() => void downloadInvoice()}>{data.busy === "download" ? <LoaderCircle size={14} className={styles.spin} /> : <ArrowDownToLine size={14} />}Download invoice + evidence</button></div></div></>}
    </Modal>
  </div>;
}
