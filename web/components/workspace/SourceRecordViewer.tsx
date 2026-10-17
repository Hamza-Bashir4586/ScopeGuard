"use client";

import { useEffect, useState, type ReactNode } from "react";
import { ArrowDownToLine, ArrowUpRight, ChevronLeft, ChevronRight, FileText, LoaderCircle } from "lucide-react";
import { LOCAL_BACKEND_DEMO, workspaceApi, workspaceError } from "@/lib/workspace-api";
import type { RecordPage, SourceRef } from "@/lib/workspace-types";
import Modal from "./WorkspaceModal";
import styles from "./workspace.module.css";

export interface SourceViewerRequest { sources: SourceRef[]; scanId?: string; findingId?: string; }

function highlighted(text: string, quotes: string[]): ReactNode {
  const ranges = quotes.filter(Boolean).map((quote) => ({ start: text.indexOf(quote), quote })).filter((match) => match.start >= 0).sort((a, b) => a.start - b.start);
  const pieces: ReactNode[] = []; let cursor = 0;
  ranges.forEach(({ start, quote }) => { if (start < cursor) return; pieces.push(text.slice(cursor, start), <mark key={`${start}-${quote.length}`}>{quote}</mark>); cursor = start + quote.length; });
  pieces.push(text.slice(cursor)); return pieces;
}

export default function SourceRecordViewer({ projectId, request, onClose }: { projectId: string; request: SourceViewerRequest | null; onClose: () => void }) {
  const [index, setIndex] = useState(0);
  const [page, setPage] = useState(1);
  const [recordPage, setRecordPage] = useState<RecordPage | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [contentError, setContentError] = useState<string | null>(null);
  const [content, setContent] = useState<{ url: string; pdf: boolean } | null>(null);
  const [loadingPage, setLoadingPage] = useState(false);
  const [loadingContent, setLoadingContent] = useState(false);
  const [reload, setReload] = useState(0);
  const [showPdf, setShowPdf] = useState(false);
  useEffect(() => { setIndex(0); setPage(request?.sources[0]?.page || 1); setRecordPage(null); setShowPdf(false); }, [request]);
  const source = request?.sources[index];

  useEffect(() => {
    if (!source || !request) return;
    const controller = new AbortController();
    setLoadingPage(true); setPageError(null); setRecordPage(null);
    workspaceApi.recordPage(projectId, source.file_id, source.version_id, page, request.scanId, request.findingId, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setRecordPage(value); })
      .catch((error) => { if (!controller.signal.aborted) setPageError(workspaceError(error)); })
      .finally(() => { if (!controller.signal.aborted) setLoadingPage(false); });
    return () => controller.abort();
  }, [projectId, source, request, page, reload]);

  useEffect(() => {
    if (!source) { setContent(null); return; }
    const controller = new AbortController(); let objectUrl: string | undefined;
    setContent(null); setContentError(null); setLoadingContent(true);
    workspaceApi.recordContent(projectId, source.file_id, source.version_id, controller.signal)
      .then((blob) => { if (controller.signal.aborted) return; objectUrl = URL.createObjectURL(blob); setContent({ url: objectUrl, pdf: blob.type.includes("pdf") }); })
      .catch((error) => { if (!controller.signal.aborted) setContentError(workspaceError(error)); })
      .finally(() => { if (!controller.signal.aborted) setLoadingContent(false); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [projectId, source, reload]);

  return <Modal open={!!request} title="Source record" onClose={onClose} className={styles.sourceModal}>
    {source && <><div className={styles.sourceToolbar}><div><span className={styles.sourceToolbarIcon}><FileText size={20} /></span><div><h2>{recordPage?.name || source.file_name}</h2><p>{recordPage?.category_label || "Project record"}{recordPage?.document_date ? ` · ${recordPage.document_date}` : ""}</p></div></div><span className={styles.sourceBoxBadge}>{LOCAL_BACKEND_DEMO ? "Local source" : <>Source in <span className={styles.boxWord}>box</span></>}</span></div>
      {request && request.sources.length > 1 && <div className={styles.sourceSwitch}><label htmlFor="connected-source-record">Related record</label><select id="connected-source-record" value={index} onChange={(event) => { const next = Number(event.target.value); setIndex(next); setPage(request.sources[next]?.page || 1); }}>{request.sources.map((item, i) => <option value={i} key={`${item.file_id}-${item.version_id}-${i}`}>{item.file_name}</option>)}</select></div>}
      {recordPage?.warning && <p className={styles.warningBanner} role="status">{recordPage.warning}</p>}
      {(pageError || contentError) && <div className={styles.errorBanner} role="alert"><span>{pageError || contentError}</span><button onClick={() => setReload((value) => value + 1)}>Retry</button></div>}
      {content?.pdf && <div className={styles.sourceSwitch}><span>Analyzed page text with verified passages highlighted</span><button className={styles.textButton} onClick={() => setShowPdf((value) => !value)}>{showPdf ? "Show readable page" : "View original PDF"}</button></div>}
      <div className={styles.documentCanvas}>
        {(loadingPage || loadingContent) && <p className={styles.loadingState}><LoaderCircle size={15} className={styles.spin} />Loading the analyzed source version…</p>}
        {content?.pdf && showPdf && <iframe className={styles.sourcePdf} src={`${content.url}#page=${page}&view=FitH`} title={`${source.file_name} — page ${page}`} />}
        {recordPage && (!showPdf || recordPage.highlights.length > 0) && <article className={`${styles.documentPage} ${showPdf ? styles.sourceExcerpt : ""}`}><div className={styles.documentLetterhead}><span>{showPdf ? "VERIFIED SOURCE EXCERPTS" : "PROJECT SOURCE RECORD"}</span><span>PAGE {recordPage.page}</span></div>{showPdf ? recordPage.highlights.map((quote, i) => <p key={i}><mark>{quote}</mark></p>) : <div className={styles.sourcePlainText}>{recordPage.text ? highlighted(recordPage.text, recordPage.highlights) : "No readable page text is available. Download the original record for review."}</div>}</article>}
      </div>
      <div className={styles.sourcePagination}><span>{source.version_id ? `Analyzed version ${source.version_id}` : "Project source"}{content && <a className={styles.sourceDownload} href={content.url} download={source.file_name}><ArrowDownToLine size={12} />Download original<ArrowUpRight size={11} /></a>}</span><div><button className={styles.iconButton} disabled={loadingPage || page <= 1} onClick={() => setPage((value) => value - 1)} aria-label="Previous source page"><ChevronLeft size={16} /></button><span>Page {page}{recordPage ? ` of ${recordPage.page_count}` : ""}</span><button className={styles.iconButton} disabled={loadingPage || !recordPage || page >= recordPage.page_count} onClick={() => setPage((value) => value + 1)} aria-label="Next source page"><ChevronRight size={16} /></button></div></div>
    </>}
  </Modal>;
}
