"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import {
  ArrowDownToLine, ArrowLeft, ArrowRight, ArrowUpRight, Building2, CalendarDays,
  Check, ChevronDown, ChevronLeft, ChevronRight, CircleCheck, Clock3, FileCheck2,
  FileSearch, FileStack, FileText, FolderOpen, Layers3, LoaderCircle, MessageSquare,
  Paperclip, Plus, RefreshCw, ShieldCheck, Sparkles, Upload, X,
} from "lucide-react";
import previewData from "./preview-records.json";
import styles from "./workspace.module.css";

// Presentation fixtures copied from the approved wireframe. These are deliberately
// separate from lib/api.ts, lib/types.ts, and the backend's live contract.
type FindingKey = keyof typeof previewData.findings;
type RecordKey = keyof typeof previewData.docs;
type RecordPage = { title: string; paragraphs: string[]; highlight?: number[] };
type ReviewState = { comment: string; reviewed: boolean; selected: boolean; dismissed: boolean };
type SavedState = { auditRan: boolean; reviews: Record<FindingKey, ReviewState> };
type ViewerState = { ids: RecordKey[]; index: number; page: number };
const findingKeys: FindingKey[] = ["circuits", "fixtures", "weekend"];
const recordKeys = Object.keys(previewData.docs) as RecordKey[];
const storageKey = "scopeguard.design-preview.v1";
const money = (amount: number) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2, minimumFractionDigits: 2 }).format(amount);
const groups: Record<FindingKey, RecordKey[]> = {
  circuits: ["scope", "co001", "completion", "inv014", "inv015"],
  fixtures: ["scope", "si009", "quote"],
  weekend: ["scope", "si008", "timesheet", "exhibit"],
};
const evidenceLinks: Record<FindingKey, { ids: RecordKey[]; page: number }[]> = {
  circuits: [{ ids: ["scope"], page: 2 }, { ids: ["co001", "completion"], page: 1 }, { ids: ["inv014", "inv015"], page: 1 }],
  fixtures: [{ ids: ["scope"], page: 2 }, { ids: ["si009"], page: 1 }, { ids: ["quote"], page: 1 }],
  weekend: [{ ids: ["scope"], page: 3 }, { ids: ["si008"], page: 1 }, { ids: ["timesheet", "exhibit"], page: 1 }],
};
const stages = ["Reading Box records", "Connecting evidence", "Checking billing", "Findings ready"];
const emptyReview = (): ReviewState => ({ comment: "", reviewed: false, selected: false, dismissed: false });
const initialState = (): SavedState => ({ auditRan: false, reviews: { circuits: emptyReview(), fixtures: emptyReview(), weekend: emptyReview() } });

function Modal({ open, title, children, onClose, className = "" }: { open: boolean; title: string; children: ReactNode; onClose: () => void; className?: string }) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (open && dialog && !dialog.open) {
      dialog.showModal();
      const previous = document.documentElement.style.overflow;
      document.documentElement.style.overflow = "hidden";
      return () => { if (dialog.open) dialog.close(); document.documentElement.style.overflow = previous; };
    }
    if (!open && dialog?.open) dialog.close();
  }, [open]);
  return <dialog ref={ref} className={`${styles.modal} ${className}`} aria-labelledby={titleId} onCancel={onClose} onClose={onClose}
    onClick={(event) => { if (event.target !== event.currentTarget) return; const r = event.currentTarget.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) onClose(); }}>
    <div className={`${styles.modalHeader} ${styles.noPrint}`}><span className={styles.eyebrow} id={titleId}>{title}</span><button className={styles.iconButton} onClick={onClose} aria-label={`Close ${title}`}><X size={19} /></button></div>
    {children}
  </dialog>;
}

export default function ScopeGuardWorkspace() {
  const [projectOpen, setProjectOpen] = useState(false);
  const [state, setState] = useState<SavedState>(initialState);
  const [ready, setReady] = useState(false);
  const [auditStage, setAuditStage] = useState(-1);
  const [auditing, setAuditing] = useState(false);
  const [auditEvent, setAuditEvent] = useState("Your agreement and supporting records are ready.");
  const [auditSource, setAuditSource] = useState<RecordKey | null>(null);
  const [tab, setTab] = useState<"findings" | "records">("findings");
  const [category, setCategory] = useState("all");
  const [activeFinding, setActiveFinding] = useState<FindingKey | null>(null);
  const [comment, setComment] = useState("");
  const [reviewFeedback, setReviewFeedback] = useState("");
  const [viewer, setViewer] = useState<ViewerState | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [files, setFiles] = useState<string[]>([]);
  const [invoice, setInvoice] = useState<FindingKey[] | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const auditRun = useRef(0);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw) {
        const saved = JSON.parse(raw) as Partial<SavedState>;
        const next = initialState();
        next.auditRan = saved.auditRan === true;
        findingKeys.forEach((key) => {
          const review = saved.reviews?.[key];
          if (review) next.reviews[key] = { comment: typeof review.comment === "string" ? review.comment : "", reviewed: review.reviewed === true, selected: review.selected === true, dismissed: review.dismissed === true };
        });
        setState(next);
        if (next.auditRan) { setAuditStage(3); setAuditEvent("Three source-backed findings. Ready for your review."); }
      }
    } catch { /* Browser storage is optional for the visual preview. */ }
    setReady(true);
    return () => { auditRun.current += 1; if (noticeTimer.current) clearTimeout(noticeTimer.current); };
  }, []);
  useEffect(() => { if (ready) { try { localStorage.setItem(storageKey, JSON.stringify(state)); } catch { /* Keep usable without storage. */ } } }, [state, ready]);

  function toast(message: string) {
    setNotice(message);
    if (noticeTimer.current) clearTimeout(noticeTimer.current);
    noticeTimer.current = setTimeout(() => setNotice(null), 5000);
  }
  function navigateProject(open: boolean) {
    setProjectOpen(open);
    window.scrollTo({ top: 0, behavior: "instant" });
    requestAnimationFrame(() => heading.current?.focus({ preventScroll: true }));
  }
  function updateReview(key: FindingKey, patch: Partial<ReviewState>) {
    setState((previous) => ({ ...previous, reviews: { ...previous.reviews, [key]: { ...previous.reviews[key], ...patch } } }));
    setInvoice(null);
  }
  function review(key: FindingKey) { setActiveFinding(key); setComment(state.reviews[key].comment); setReviewFeedback(""); }
  function view(ids: RecordKey[], page = 1) { setViewer({ ids, index: 0, page }); }
  async function runAudit() {
    if (auditing) return;
    const token = ++auditRun.current;
    setAuditing(true); setTab("findings"); setUploadOpen(false); setAuditStage(0);
    const steps: { stage: number; message: string; source: RecordKey | null; delay: number }[] = [
      { stage: 0, message: "Reading the signed agreement and 9 supporting records from Box.", source: "scope", delay: 1400 },
      { stage: 0, message: "10 records read. Keeping each document and page reference.", source: "co001", delay: 900 },
      { stage: 1, message: "Connecting completed conference-room work to signed CO-001.", source: "completion", delay: 1400 },
      { stage: 1, message: "Comparing 26 requested fixtures with 24 in the original scope.", source: "si009", delay: 1100 },
      { stage: 2, message: "Looking for CO-001 in the supplied invoices.", source: "inv014", delay: 1200 },
      { stage: 2, message: "Checking quoted fixture pricing and recorded labor costs.", source: "quote", delay: 1000 },
    ];
    for (const step of steps) {
      if (auditRun.current !== token) return;
      setAuditStage(step.stage); setAuditEvent(step.message); setAuditSource(step.source);
      await new Promise((resolve) => setTimeout(resolve, step.delay));
    }
    if (auditRun.current !== token) return;
    setAuditStage(3); setAuditSource(null); setAuditEvent("3 discrepancies ready. Every finding links to supporting records.");
    setAuditing(false); setState((previous) => ({ ...previous, auditRan: true }));
  }

  const selected = findingKeys.filter((key) => state.reviews[key].selected);
  const total = selected.reduce((sum, key) => sum + previewData.findings[key].amount, 0);
  const toReview = findingKeys.filter((key) => !state.reviews[key].reviewed && !state.reviews[key].dismissed).length;
  const categories = [...new Set(recordKeys.map((key) => previewData.docs[key].category))];
  const shownRecords = recordKeys.filter((key) => category === "all" || previewData.docs[key].category === category);
  const currentRecord = viewer ? previewData.docs[viewer.ids[viewer.index]] : null;
  const currentPage: RecordPage | null = currentRecord && viewer ? currentRecord.pages[viewer.page - 1] : null;
  const currentFinding = activeFinding ? previewData.findings[activeFinding] : null;

  return <div className={styles.application}>
    <header className={styles.appHeader}>
      <button className={styles.brand} onClick={() => navigateProject(false)} aria-label="ScopeGuard projects"><span className={styles.brandMark}><ShieldCheck size={21} strokeWidth={1.6} /></span>ScopeGuard<span className={styles.brandDot}>.</span></button>
      <div className={styles.headerDivider} />
      <button className={styles.headerProjectLink} onClick={() => navigateProject(false)}>Projects{projectOpen && <><ChevronRight size={13} /><span>Riverside Office</span></>}</button>
      <div className={styles.headerRight}><span className={styles.previewBadge}>Design preview</span><span className={styles.companyName}>Summit Electrical</span><span className={styles.companyAvatar} aria-label="Summit Electrical">SE</span></div>
    </header>

    <main className={styles.main}>
      {!projectOpen ? <div className={styles.screen}>
        <div className={styles.pageHeading}><div><div className={styles.eyebrow}>YOUR WORKSPACE</div><h1 ref={heading} tabIndex={-1}>Projects</h1><p>Scope, changes, and the evidence behind every invoice.</p></div><button className={styles.primaryButton} disabled title="Add project is a visual placeholder"><Plus size={16} />Add project</button></div>
        <div className={styles.projectSectionLabel}><span>All projects <span className={styles.quietCount}>3</span></span><span><span className={styles.smallDot} />Summit Electrical</span></div>
        <div className={styles.projectGrid}>
          {[
            { id: "SG-024", name: "Riverside Office Renovation", customer: "Northline Builders", dates: "Oct 5 – Nov 20, 2026", status: "Active", count: "2 change logs to review", interactive: true },
            { id: "SG-025", name: "Harbor View Apartments", customer: "Beacon Construction", dates: "Sep 14 – Dec 18, 2026", status: "Active", count: "1 change log to review", interactive: false },
            { id: "SG-019", name: "Cedar Street Retail Fit-Out", customer: "Alder General Contractors", dates: "Jul 6 – Sep 25, 2026", status: "Completed", count: "All reviewed", interactive: false },
          ].map((project) => <article className={`${styles.projectCard} ${project.interactive ? styles.projectInteractive : ""}`} key={project.id}>
            <div className={styles.projectCardTop}><span className={styles.projectCode}>{project.id}</span><span className={styles.projectStatus}><span className={project.status === "Active" ? styles.smallDot : styles.mutedDot} />{project.status}</span></div>
            <div className={styles.projectCardBody}><span className={styles.projectSymbol}><Building2 size={20} strokeWidth={1.4} /></span><h2>{project.name}</h2><p>{project.customer}</p><div className={styles.projectDates}><CalendarDays size={13} />{project.dates}</div></div>
            <div className={styles.projectCardFooter}><span className={project.status === "Completed" ? styles.completeText : ""}>{project.status === "Completed" ? <CircleCheck size={14} /> : <FileStack size={14} />}{project.count}</span>{project.interactive ? <button onClick={() => navigateProject(true)} className={styles.openProject} aria-label={`Open ${project.name}`}><ArrowUpRight size={18} /></button> : <span className={styles.placeholderArrow} title="Layout preview"><ArrowUpRight size={17} /></span>}</div>
          </article>)}
        </div>
        <p className={styles.workspaceNote}><ShieldCheck size={14} />Fictional project data. Open Riverside to explore the complete review flow.</p>
        <div className={styles.workspaceFootnote}><span>One place for your project history.</span><span>Built with <span className={styles.boxWord}>box</span> & AWS</span></div>
      </div> : <div className={styles.screen}>
        <button className={styles.backLink} onClick={() => navigateProject(false)}><ArrowLeft size={14} />All projects</button>
        <div className={styles.pageHeading}><div><div className={styles.projectTitleMeta}><span className={styles.projectCode}>SG-024</span><span className={styles.projectStatus}><span className={styles.smallDot} />Active project</span></div><h1 ref={heading} tabIndex={-1}>Riverside Office Renovation</h1><p className={styles.projectSubline}><span><Building2 size={14} />Northline Builders</span><span className={styles.inlineSeparator} /><span><CalendarDays size={14} />Oct 5 – Nov 20, 2026</span></p></div><button className={styles.secondaryButton} onClick={() => setUploadOpen(true)}><Plus size={16} />Add project records</button></div>

        <section className={styles.agreement} aria-labelledby="agreement-heading">
          <div className={styles.agreementMain}><div className={styles.sectionEyebrow}><span className={styles.documentIcon}><FileCheck2 size={17} /></span><h2 id="agreement-heading">Original agreement</h2><span className={styles.signedTag}><Check size={11} />Signed Sep 28, 2026</span></div><p>Electrical fit-out: 48 outlets, 24 LED fixtures, six branch circuits, testing and labeling under drawing E-101 Rev A.</p><div className={styles.agreementTerms}><span>Weekend installation excluded</span><span>Changes require customer review</span></div></div>
          <div className={styles.agreementAside}><span className={styles.metaLabel}>Original contract value</span><strong>{money(125000)}</strong><button className={styles.textButton} onClick={() => view(["scope"])}>View original scope<FileText size={13} /><ArrowUpRight size={13} /></button></div>
        </section>

        <section className={styles.auditPanel} aria-labelledby="audit-heading" aria-busy={auditing}>
          <div className={styles.auditTop}><div className={styles.auditLabel}><span className={`${styles.auditIcon} ${auditing ? styles.auditIconActive : ""}`}><Sparkles size={19} /></span><div><h2 id="audit-heading">Project audit</h2><p>{auditing ? "Connecting your project history" : state.auditRan ? "Last audited just now · 10 project records" : "10 project records · No audit run yet"}</p></div></div><button className={styles.primaryButton} disabled={auditing} onClick={() => void runAudit()}>{auditing ? <LoaderCircle className={styles.spin} size={15} /> : state.auditRan ? <RefreshCw size={14} /> : <Sparkles size={15} />}{auditing ? "Auditing records…" : state.auditRan ? "Refresh audit" : "Run audit"}</button></div>
          <ol className={styles.auditStages}>{stages.map((stage, index) => <li key={stage} className={index === auditStage ? styles.stageActive : index < auditStage ? styles.stageDone : ""}><span className={styles.stageDot}>{index < auditStage || (index === 3 && state.auditRan && !auditing) ? <Check size={11} /> : index === auditStage && auditing ? <span className={styles.stagePulse} /> : index + 1}</span><span>{stage}</span>{index < stages.length - 1 && <span className={styles.stageConnector} />}</li>)}</ol>
          <div className={styles.auditEvent} aria-live="polite"><span className={styles.auditEventText}>{auditing ? <LoaderCircle size={12} className={styles.spin} /> : state.auditRan ? <CircleCheck size={13} /> : <Layers3 size={13} />}{auditEvent}</span>{auditSource && <button className={styles.auditSource} onClick={() => view([auditSource])}><FileText size={12} />View record<ArrowUpRight size={12} /></button>}<span className={styles.previewLabel}>Simulated preview</span></div>
        </section>

        <div className={styles.tabs} role="tablist" aria-label="Project workspace"><button id="findings-tab" role="tab" aria-selected={tab === "findings"} aria-controls="findings-panel" onClick={() => setTab("findings")}><FileSearch size={15} />Flagged discrepancies{state.auditRan && <span>{findingKeys.length}</span>}</button><button id="records-tab" role="tab" aria-selected={tab === "records"} aria-controls="records-panel" onClick={() => setTab("records")}><FolderOpen size={15} />All source records<span>10</span></button></div>
        {tab === "findings" ? <div id="findings-panel" role="tabpanel" aria-labelledby="findings-tab" className={styles.findingsLayout}>
          <section className={styles.findingsArea} aria-label="Flagged discrepancies">
            {!state.auditRan ? <div className={styles.auditEmpty}><span className={styles.emptyIcon}><FileSearch size={27} strokeWidth={1.3} /></span><h3>{auditing ? "Following the evidence" : "Ready for a closer look?"}</h3><p>{auditing ? "Reviewing your agreement, change records, and invoices. Findings will appear here." : "Run an audit to find work that may not have reached an invoice, with the records to support it."}</p><span className={styles.emptyCaption}><Paperclip size={12} />10 records ready for review</span></div> : <>
              <div className={styles.findingsHeading}><span>{toReview ? `${toReview} ${toReview === 1 ? "finding needs" : "findings need"} your review` : "All findings reviewed"}</span><span>Grounded in your project records</span></div>
              <div className={styles.findingGrid}>{findingKeys.map((key) => {
                const finding = previewData.findings[key], reviewState = state.reviews[key];
                return <article key={key} className={`${styles.findingCard} ${!reviewState.reviewed && !reviewState.selected && !reviewState.dismissed ? styles.findingUnreviewed : ""} ${reviewState.selected ? styles.findingSelected : ""} ${reviewState.dismissed ? styles.findingDismissed : ""}`}>
                  <div className={styles.findingCardMeta}><span className={styles.projectCode}>{finding.id}</span><span className={reviewState.selected ? styles.selectedStatus : styles.reviewStatus}>{reviewState.selected ? <><Check size={11} />In invoice</> : reviewState.dismissed ? "Dismissed" : reviewState.reviewed ? "Reviewed" : "Needs review"}</span></div>
                  <h3>{finding.title}</h3><p>{key === "circuits" ? "Approved and completed work. No matching charge found in the supplied invoices." : key === "fixtures" ? "The revised installation adds two fixtures beyond the signed scope." : "Saturday work was recorded, but weekend installation was excluded from the agreement."}</p>
                  <div className={styles.findingAmountRow}><strong>{money(finding.amount)}</strong><span className={key === "circuits" ? styles.approvedLabel : styles.proposedLabel}>{key === "circuits" ? "Approved change" : "Proposed addition"}</span></div>
                  <div className={styles.findingCardFooter}><button className={styles.sourceCount} onClick={() => view(groups[key])}><Paperclip size={13} />{groups[key].length} source records</button><button className={styles.reviewButton} onClick={() => review(key)}>Review<ArrowRight size={13} /></button></div>
                </article>;
              })}</div>
              <details className={styles.existingBalances}><summary><Clock3 size={14} />Existing changes & previously invoiced balances<ChevronDown size={14} /></summary><p>CO-002: $1,500 lighting upgrade already invoiced on INV-015; payment status requires confirmation.</p><p>INV-014: $40,000 remains after the deposit credit and recorded partial payment. Existing balances are excluded from the draft subtotal.</p></details>
            </>}
          </section>
          <aside className={styles.invoiceSidebar} aria-label="Invoice draft"><div className={styles.invoiceHeading}><span className={styles.invoiceIcon}><FileText size={18} /></span><h2>Invoice draft</h2><span className={styles.quietCount}>{selected.length}</span></div><p className={styles.invoiceHelp}>Your reviewed findings, together.</p>{selected.length ? <div className={styles.selectedItems}>{selected.map((key) => <div className={styles.selectedItem} key={key}><span>{previewData.findings[key].title}</span><div><strong>{money(previewData.findings[key].amount)}</strong><button aria-label={`Remove ${previewData.findings[key].title} from invoice`} onClick={() => updateReview(key, { selected: false })}><X size={13} /></button></div></div>)}</div> : <div className={styles.invoiceEmpty}><span className={styles.invoiceEmptyLines}><i /><i /><i /></span><span>No items selected yet</span><p>Review a discrepancy and add it to your invoice.</p></div>}<div className={styles.invoiceTotal}><span>Selected subtotal</span><strong>{money(total)}</strong></div><button className={styles.primaryButton} disabled={!selected.length} onClick={() => setInvoice([...selected])}>Generate for customer sign-off<ArrowRight size={14} /></button><p className={styles.invoiceNote}><ShieldCheck size={13} />Supporting evidence is included with every selected item.</p><div className={styles.invoiceStatus}><span className={styles.mutedDot} />Draft only. Nothing is sent.</div></aside>
        </div> : <section id="records-panel" role="tabpanel" aria-labelledby="records-tab" className={styles.recordsPanel}><div className={styles.recordsHeading}><div><h2>Project source records</h2><p>Original files, organized into AI-suggested categories.</p></div><label className={styles.categorySelect}><span className={styles.srOnly}>Filter source records by category</span><FolderOpen size={14} /><select value={category} onChange={(event) => setCategory(event.target.value)}><option value="all">All categories</option>{categories.map((name) => <option key={name}>{name}</option>)}</select><ChevronDown size={13} /></label></div><div className={styles.recordColumnHead}><span>Document</span><span>Category</span><span>Date added</span><span /></div>{shownRecords.map((key) => { const record = previewData.docs[key]; return <button className={styles.recordRow} key={key} onClick={() => view([key])}><span className={styles.recordName}><span className={styles.recordFileIcon}><FileText size={17} /></span><span>{record.name}<small>{record.pages.length} {record.pages.length === 1 ? "page" : "pages"} · PDF</small></span></span><span className={styles.recordCategory}>{record.category}</span><span className={styles.recordDate}>{record.date}</span><ArrowUpRight size={15} /></button>; })}<div className={styles.recordsFooter}>{shownRecords.length} of 10 records<span>Sample AI classifications · Preview data</span></div></section>}
        <footer className={styles.projectFooter}><span>Summit Electrical<span>·</span>Project workspace</span><span>Fictional records. AI analysis, uploads, and Box actions are simulated.</span></footer>
      </div>}
    </main>

    <Modal open={activeFinding !== null} title="Review discrepancy" onClose={() => setActiveFinding(null)} className={styles.reviewModal}>
      {activeFinding && currentFinding && <><div className={styles.reviewIntro}><div className={styles.projectCode}>{currentFinding.id} · RIVERSIDE OFFICE RENOVATION</div><h2>{currentFinding.title}</h2><div className={styles.reviewAmount}><strong>{money(currentFinding.amount)}</strong><span className={activeFinding === "circuits" ? styles.approvedLabel : styles.proposedLabel}>{activeFinding === "circuits" ? "Previously approved · CO-001" : "Proposed · Customer sign-off required"}</span></div></div><div className={styles.reviewBody}><div className={styles.evidenceHeading}><h3>The supporting evidence</h3><span><ShieldCheck size={13} />Source-grounded</span></div><div className={styles.evidenceList}>{currentFinding.evidence.map(([label, source, quote], index) => <div className={styles.evidenceBlock} key={label}><span className={styles.evidenceStep}>{index + 1}</span><div><span className={styles.metaLabel}>{label}</span><button className={styles.evidenceSource} onClick={() => view(evidenceLinks[activeFinding][index].ids, evidenceLinks[activeFinding][index].page)}>{source}<ArrowUpRight size={13} /></button><blockquote>{quote}</blockquote></div></div>)}</div><div className={styles.reasonBlock}><span className={styles.reasonIcon}><Sparkles size={15} /></span><div><h3>Why this was flagged</h3><p>{currentFinding.reason}</p><span className={styles.approvalDetail}>{currentFinding.approval}</span></div></div><label className={styles.commentLabel} htmlFor="review-comment">Your review comments<span>Internal only</span></label><textarea className={styles.commentInput} id="review-comment" value={comment} onChange={(event) => setComment(event.target.value)} placeholder="Add context, explain a correction, or note what you confirmed…" /><p className={styles.commentHelp} role="status"><MessageSquare size={12} />{reviewFeedback || "Comments stay with your review and are excluded from the customer invoice."}</p></div><div className={styles.reviewActions}><button className={styles.textButton} onClick={() => { updateReview(activeFinding, { dismissed: true, selected: false, reviewed: true, comment }); setActiveFinding(null); toast("Finding dismissed and excluded from the invoice draft."); }}>Dismiss flag</button><div><button className={styles.secondaryButton} onClick={() => { updateReview(activeFinding, { comment, reviewed: true }); setReviewFeedback("Internal comment saved in this browser."); }}><Check size={14} />Save comment</button><button className={styles.primaryButton} onClick={() => { updateReview(activeFinding, { comment, reviewed: true, selected: true, dismissed: false }); setActiveFinding(null); toast("Added to the invoice draft with supporting evidence."); }}>{state.reviews[activeFinding].selected ? <Check size={14} /> : <Plus size={14} />}{state.reviews[activeFinding].selected ? "Update invoice item" : "Add to invoice"}</button></div></div></>}
    </Modal>

    <Modal open={viewer !== null} title="Source record" onClose={() => setViewer(null)} className={styles.sourceModal}>
      {viewer && currentRecord && currentPage && <><div className={styles.sourceToolbar}><div><span className={styles.sourceToolbarIcon}><FileText size={20} /></span><div><h2>{currentRecord.name}</h2><p>{currentRecord.category} · {currentRecord.date}</p></div></div><span className={styles.sourceBoxBadge}>Source in <span className={styles.boxWord}>box</span></span></div>{viewer.ids.length > 1 && <div className={styles.sourceSwitch}><label htmlFor="source-record-picker">Related record</label><select id="source-record-picker" value={viewer.index} onChange={(event) => setViewer({ ...viewer, index: Number(event.target.value), page: 1 })}>{viewer.ids.map((id, index) => <option key={id} value={index}>{previewData.docs[id].name}</option>)}</select></div>}<div className={styles.documentCanvas}><article className={styles.documentPage}><div className={styles.documentLetterhead}><span>SUMMIT ELECTRICAL</span><span>PROJECT RECORD</span></div><h3>{currentPage.title}</h3>{currentPage.paragraphs.map((paragraph, index) => <p key={paragraph}>{currentPage.highlight?.includes(index) ? <mark>{paragraph}</mark> : paragraph}</p>)}<div className={styles.documentPageNumber}>Riverside Office Renovation<span>{viewer.page}</span></div></article></div><div className={styles.sourcePagination}><span>Fictional source preview · Relevant passages highlighted</span><div><button className={styles.iconButton} disabled={viewer.page <= 1} onClick={() => setViewer({ ...viewer, page: viewer.page - 1 })} aria-label="Previous source page"><ChevronLeft size={16} /></button><span>Page {viewer.page} of {currentRecord.pages.length}</span><button className={styles.iconButton} disabled={viewer.page >= currentRecord.pages.length} onClick={() => setViewer({ ...viewer, page: viewer.page + 1 })} aria-label="Next source page"><ChevronRight size={16} /></button></div></div></>}
    </Modal>

    <Modal open={uploadOpen} title="Add project records" onClose={() => setUploadOpen(false)} className={styles.uploadModal}>
      <div className={styles.uploadBody}><h2>Add records to Riverside</h2><p>Add change instructions, work reports, approvals, or invoices to this project.</p><label className={styles.uploadDropzone}><span className={styles.uploadIcon}><Upload size={23} strokeWidth={1.4} /></span><strong>Choose project records</strong><span>PDF, TXT, or Markdown</span><input type="file" accept=".pdf,.txt,.md" multiple onChange={(event) => setFiles(Array.from(event.target.files ?? []).map((file) => file.name))} /><span className={styles.secondaryButton}>Browse files<Plus size={14} /></span></label>{files.length > 0 && <div className={styles.uploadFiles}>{files.map((name, index) => <div key={`${name}-${index}`}><FileText size={14} /><span>{name}</span><Check size={13} /></div>)}</div>}<p className={styles.previewNotice}>Design preview: selected files are not uploaded. Analysis uses the existing fictional project records.</p></div><div className={styles.uploadActions}><button className={styles.secondaryButton} onClick={() => setUploadOpen(false)}>Cancel</button><button className={styles.primaryButton} onClick={() => void runAudit()}><Sparkles size={15} />Analyze new documents</button></div>
    </Modal>

    <Modal open={invoice !== null} title="Customer invoice preview" onClose={() => setInvoice(null)} className={styles.invoicePreview}>
      {invoice && <><article className={styles.invoiceDocument}><div className={styles.invoiceDocumentTop}><span className={styles.invoiceCompany}>SUMMIT ELECTRICAL</span><span className={styles.draftLabel}>DRAFT · NOT SENT</span></div><div className={styles.invoiceDocumentTitle}><div><span className={styles.eyebrow}>SUPPLEMENTAL INVOICE</span><h2>For customer review<br />and sign-off.</h2></div><FileCheck2 size={39} strokeWidth={1} /></div><div className={styles.invoiceRecipient}><div><span className={styles.metaLabel}>Prepared for</span><strong>Northline Builders</strong></div><div><span className={styles.metaLabel}>Project</span><strong>Riverside Office Renovation</strong></div></div><div className={styles.invoiceLineHead}><span>Work description</span><span>Amount</span></div>{invoice.map((key, index) => <div className={styles.invoiceLine} key={key}><div><strong>{previewData.findings[key].title}</strong><p>{previewData.findings[key].approval}</p><span>Evidence appendix {index + 1}</span></div><strong>{money(previewData.findings[key].amount)}</strong></div>)}<div className={styles.invoiceDocumentTotal}><span>Draft subtotal</span><strong>{money(invoice.reduce((sum, key) => sum + previewData.findings[key].amount, 0))}</strong></div><p className={styles.invoiceTerms}>For review only. Confirm payment terms and taxes before issue. Proposed changes remain subject to customer acceptance.</p><section className={styles.invoiceAppendix}><div className={styles.sectionEyebrow}><Paperclip size={15} /><h3>Evidence appendix</h3></div>{invoice.map((key, index) => <div key={key} className={styles.appendixItem}><h4>{index + 1}. {previewData.findings[key].title}</h4>{previewData.findings[key].evidence.map(([label, source, quote]) => <p key={label}><strong>{label} · {source}</strong><span>{quote}</span></p>)}</div>)}</section><div className={styles.invoiceAcceptance}><div>Customer acceptance<span /></div><div>Date<span /></div></div></article><div className={`${styles.invoicePreviewActions} ${styles.noPrint}`}><p role="status"><ShieldCheck size={13} />{notice || "Evidence included. Internal comments excluded."}</p><div><button className={styles.secondaryButton} onClick={() => toast("Design preview only. No invoice was uploaded to Box or sent to the customer.")}><FolderOpen size={14} />Save invoice pack to Box</button><button className={styles.primaryButton} onClick={() => window.print()}><ArrowDownToLine size={14} />Print / save PDF</button></div></div></>}
    </Modal>

    <div className={`${styles.toast} ${notice ? styles.toastVisible : ""}`} role="status" aria-live="polite" aria-hidden={!notice}><CircleCheck size={16} /><span>{notice}</span><button aria-label="Dismiss notification" onClick={() => setNotice(null)}><X size={14} /></button></div>
  </div>;
}
