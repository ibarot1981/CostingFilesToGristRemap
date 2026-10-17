import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ArrowLeft, ChevronRight, LoaderCircle, RefreshCw, Search } from "lucide-react";
import { api } from "./api";
import { PartBaselinePanel } from "./PartBaselinePanel";
import type { Group, Part } from "./PartMappingView";
import "./partBaselineWorkspace.css";

type MappingDetail = { parts: Part[]; groups: Group[]; sourceHash: string; associationKey: string; associationVersion: number; version: number };
type WorkspaceRequest = { groupKey: string; part: Part; lineKey?: string; lineFingerprint?: string; context: Record<string, unknown> };
type PreviewSource = { fileId: string; workbookPath: string; sourceHash: string; associationKey: string; associationVersion: number; mappingVersion: number; mappingPolicyVersion: string; sourceSheets: Record<string, { status: string }> };
type PendingSourceRow = { groupKey: string; sheet: string; family: string; row: number | null; description: string; lineKey: string; lineFingerprint: string; evidenceFingerprint: string; status: string; reason: string; affectedPartIds: string[] };
type WorkbookPart = { id: string; partNumber?: string | null; name?: string; description?: string; snapshotKey: string; status: string; blockers: string[]; mappedLineCount: number; requirementCount: number; requirementsByFamily: Record<string, { mappedLines: number; pendingLines: number; requirements: number; applicabilityStatus: string; completenessConfirmed: boolean }>; existingContent: { processRequirements: number; components: number; purchaseSpecifications: number; requiresAppendConfirmation: boolean } };
type WorkbookPreview = { source: PreviewSource; contextKey: string; previewKey: string; parts: WorkbookPart[]; pendingRows: PendingSourceRow[]; counts: { mappedParts: number; readyParts: number; alreadyEstablishedParts: number; blockedParts: number; pendingRows: number }; canConfirm: boolean; workbookComplete: boolean };
type BulkAttempt = { key: string; payload: Record<string, unknown> };
type BulkResult = { status: string; totalSuccess: boolean; results: { partId: string; partNumber?: string; name?: string; status: string; message?: string; requirementCount?: number; idempotent?: boolean; retryable?: boolean }[]; pendingRows: PendingSourceRow[]; workbookComplete: boolean };

function baselineLabel(status?: string) {
  const value = String(status || "").replaceAll("_", " ").trim();
  return value || "Not reviewed";
}

function errorDisposition(cause: unknown) {
  if (!cause || typeof cause !== "object" || !("retryDisposition" in cause)) return "retry_same_request";
  const disposition = String((cause as { retryDisposition: unknown }).retryDisposition);
  return ["safe_to_edit", "refresh_required"].includes(disposition) ? disposition : "retry_same_request";
}

const bulkAttemptKey = (path: string) => `workbook-baseline-confirm-attempt:${path}`;

export function PartBaselineWorkspace({ path, initialPartId = "", initialPart = null, returnLabel, onBack, onUseDifferentPart, onReviewPendingRow }: {
  path: string;
  initialPartId?: string;
  initialPart?: Part | null;
  returnLabel: string;
  onBack: () => void;
  onUseDifferentPart: (request: WorkspaceRequest) => void;
  onReviewPendingRow: (row: PendingSourceRow, source: PreviewSource) => void;
}) {
  const [detail, setDetail] = useState<MappingDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [selectedPartId, setSelectedPartId] = useState(initialPartId);
  const [statuses, setStatuses] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<WorkbookPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(true);
  const [previewError, setPreviewError] = useState("");
  const [appendChoices, setAppendChoices] = useState<Record<string, boolean>>({});
  const [bulkAttempt, setBulkAttempt] = useState<BulkAttempt | null>(null);
  const [bulkResult, setBulkResult] = useState<BulkResult | null>(null);
  const [bulkError, setBulkError] = useState("");
  const [bulkBusy, setBulkBusy] = useState(false);
  const [reviewEpoch, setReviewEpoch] = useState(0);
  const generation = useRef(0);
  const bulkInFlight = useRef(false);

  async function refreshData() {
    const request = ++generation.current;
    setLoading(true);
    setPreviewLoading(true);
    setError("");
    setPreviewError("");
    try {
      const mappingResult = await api.partMappings(path);
      if (request !== generation.current) return;
      const next = mappingResult as MappingDetail;
      setDetail(next);
      setSelectedPartId(current => initialPartId || ((next.parts || []).some(part => part.id === current) ? current : next.parts?.[0]?.id || ""));
      setLoading(false);
      try {
        const workbook = await api.partBaselineWorkbookPreview(path) as WorkbookPreview;
        if (request !== generation.current) return;
        setPreview(workbook);
        setStatuses(Object.fromEntries((workbook.parts || []).map(part => [part.id,
          part.status === "already_established" ? "established" : part.status === "ready" ? "not_established" : part.status])));
        setAppendChoices(old => Object.fromEntries((workbook.parts || []).map(part => [part.id, Boolean(old[part.id])] )));
      } catch (cause) {
        if (request === generation.current) {
          setPreview(null);
          setPreviewError(String(cause));
        }
      }
    } catch (cause) {
      if (request === generation.current) {
        setError(String(cause));
      }
    } finally {
      if (request === generation.current) {
        setLoading(false);
        setPreviewLoading(false);
      }
    }
  }

  useEffect(() => {
    void refreshData();
    return () => { generation.current += 1; };
  }, [path, initialPartId]);

  useEffect(() => {
    try {
      const restored = JSON.parse(localStorage.getItem(bulkAttemptKey(path)) || "null") as BulkAttempt | null;
      if (restored?.key && restored.payload && typeof restored.payload === "object") setBulkAttempt(restored);
      else setBulkAttempt(null);
    } catch {
      setBulkAttempt(null);
    }
    setBulkResult(null);
    setBulkError("");
  }, [path]);

  const parts = useMemo(() => {
    const available = [...(detail?.parts || [])];
    if (initialPart && !available.some(part => part.id === initialPart.id)) available.unshift(initialPart);
    return [...new Map(available.filter(part => !part.legacy && part.status !== "retired" && part.selectable !== false)
      .map(part => [part.id, part])).values()];
  }, [detail, initialPart]);
  const visibleParts = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return parts.filter(part => !needle || [part.partNumber, part.name, part.description, part.variant]
      .some(value => String(value || "").toLocaleLowerCase().includes(needle)));
  }, [parts, query]);
  const selected = parts.find(part => part.id === selectedPartId) || null;
  const readyParts = (preview?.parts || []).filter(part => part.status === "ready");
  const appendChoicesReady = readyParts.every(part => !part.existingContent?.requiresAppendConfirmation || appendChoices[part.id]);

  function useDifferentPart(groupKey: string, part: Part, lineKey?: string, lineFingerprint?: string) {
    if (!detail || !lineKey || !lineFingerprint) {
      setError("This comparison does not identify one current source line. Return to Part Mapping and select the current line manually.");
      return;
    }
    const group = detail.groups.find(item => item.key === groupKey);
    const matches = group?.rows.filter(row => String(row.lineKey || "") === lineKey
      && String(row.semanticEvidence?.fingerprint || "") === lineFingerprint) || [];
    if (!group || matches.length !== 1) {
      setError("The source line changed or is ambiguous. Return to Part Mapping and review the current line before changing its draft assignment.");
      return;
    }
    const selectedLineEvidenceFingerprint = JSON.stringify([{ lineKey, fingerprint: lineFingerprint }]);
    onUseDifferentPart({ groupKey, part, lineKey, lineFingerprint, context: {
      sourceHash: detail.sourceHash,
      associationKey: detail.associationKey,
      associationVersion: detail.associationVersion,
      evidenceFingerprint: group.evidenceFingerprint,
      lineKeys: [lineKey],
      selectedLineEvidenceFingerprint,
    } });
  }

  function selectNextPart() {
    if (!parts.length) return;
    const index = parts.findIndex(part => part.id === selectedPartId);
    setSelectedPartId(parts[(index + 1) % parts.length].id);
  }

  async function submitBulk(attempt: BulkAttempt) {
    if (bulkInFlight.current) return;
    bulkInFlight.current = true;
    setBulkBusy(true);
    setBulkError("");
    try {
      const result = await api.confirmWorkbookPartBaselines(attempt.payload, attempt.key) as BulkResult;
      setBulkResult(result);
      const outcomes = Array.isArray(result.results) ? result.results : [];
      const hasUncertainOutcome = outcomes.length === 0 || outcomes.some(item => {
        if (["established", "already_established"].includes(item.status)) return false;
        if (["failed", "blocked"].includes(item.status)) return item.retryable !== false;
        return true;
      });
      if (!hasUncertainOutcome) {
        localStorage.removeItem(bulkAttemptKey(path));
        setBulkAttempt(null);
      }
      setReviewEpoch(value => value + 1);
      await refreshData();
    } catch (cause) {
      const disposition = errorDisposition(cause);
      if (disposition === "safe_to_edit" || disposition === "refresh_required") {
        localStorage.removeItem(bulkAttemptKey(path));
        setBulkAttempt(null);
        setBulkResult(null);
        if (disposition === "refresh_required") {
          setBulkError(`The workbook or saved Part mappings changed since this preview. The preview is being refreshed before you can confirm again. ${String(cause)}`);
          await refreshData();
        } else {
          setBulkError(String(cause));
        }
      } else {
        setBulkError(`The confirmation response was not received. Its original request is saved; retry the same confirmation to recover each Part safely. ${String(cause)}`);
      }
    } finally {
      bulkInFlight.current = false;
      setBulkBusy(false);
    }
  }

  function confirmAllReadyParts() {
    if (!preview || !readyParts.length || !appendChoicesReady || bulkBusy || bulkAttempt) return;
    const attempt: BulkAttempt = { key: crypto.randomUUID(), payload: {
      path,
      expectedContextKey: preview.contextKey,
      previewKey: preview.previewKey,
      reason: "Establish initial workbook manufacturing baseline",
      parts: readyParts.map(part => ({ partId: part.id, snapshotKey: part.snapshotKey,
        appendExisting: Boolean(appendChoices[part.id]) })),
    } };
    localStorage.setItem(bulkAttemptKey(path), JSON.stringify(attempt));
    setBulkAttempt(attempt);
    void submitBulk(attempt);
  }

  return <main className="part-baseline-workspace" aria-label="Manufacturing baseline review workspace">
    <header className="baseline-workspace-heading">
      <div><span className="eyebrow">Safari Manufacturing · Part review</span><h1>Review manufacturing baselines</h1>
        <p title={path}>Workbook · {path || "No workbook selected"}</p></div>
      <button type="button" className="button" onClick={onBack}><ArrowLeft size={14}/>{returnLabel}</button>
    </header>
    <div className="baseline-workspace-context" role="status">Preview all saved Part assignments from the selected workbook, then confirm every ready initial baseline in one action. Unsaved mapping drafts remain available when you return. Pending source rows stay visible for review and are excluded from baseline requirements.</div>
    {(error || previewError) && <div className="error-banner" role="alert">{previewError || error}</div>}
    <section className="baseline-bulk-panel" aria-label="Workbook baseline confirmation">
      <div className="baseline-bulk-heading"><div><span className="eyebrow">Workbook-wide action · MCL, Tool Shop and CNC</span><h2>Confirm manufacturing baselines</h2>
        <p>One confirmation publishes the saved mapped requirements for every ready Part. Each Part’s revision, source evidence and current mapping are checked again before writing.</p></div>
        <button type="button" className="button" disabled={previewLoading || bulkBusy} onClick={() => { setBulkResult(null); setBulkError(""); void refreshData(); }}><RefreshCw size={14}/>Refresh preview</button>
      </div>
      {previewLoading && <p className="baseline-bulk-loading" role="status"><LoaderCircle className="spin" size={15}/> Building a fresh workbook preview…</p>}
      {!previewLoading && preview && <>
        <div className="baseline-bulk-counts" aria-label="Workbook baseline preview counts">
          <span><strong>{preview.counts.readyParts}</strong>ready</span>
          <span><strong>{preview.counts.alreadyEstablishedParts}</strong>already established</span>
          <span><strong>{preview.counts.blockedParts}</strong>blocked</span>
          <span><strong>{preview.counts.pendingRows}</strong>rows to review</span>
        </div>
        <div className="baseline-bulk-part-list">
          {preview.parts.map(part => <article className={`baseline-bulk-part baseline-bulk-${part.status}`} key={part.id}>
            <div className="baseline-bulk-part-title"><div><strong>{part.partNumber || "Part number unavailable"} · {part.name || "Part"}</strong>
              <small>{part.mappedLineCount} mapped requirement row{part.mappedLineCount === 1 ? "" : "s"} · {baselineLabel(part.status)}</small></div>
              <button type="button" className="button quiet" onClick={() => { setSelectedPartId(part.id); document.querySelector(".baseline-workspace-main")?.scrollIntoView({block:"start",behavior:"smooth"}); }}>Review Part</button>
            </div>
            {part.status === "ready" && part.existingContent?.requiresAppendConfirmation && <label className="baseline-bulk-append"><input type="checkbox" checked={Boolean(appendChoices[part.id])} disabled={bulkBusy || Boolean(bulkAttempt)} onChange={event => setAppendChoices(old => ({...old,[part.id]:event.target.checked}))}/>
              <span>Append these workbook rows while preserving existing Rev A content: {part.existingContent.processRequirements} requirement(s), {part.existingContent.components} component(s), {part.existingContent.purchaseSpecifications} purchase specification(s).</span></label>}
            {part.blockers?.length > 0 && <ul className="baseline-bulk-blockers">{part.blockers.map((blocker,index) => <li key={`${part.id}:${index}`}>{blocker}</li>)}</ul>}
            {part.status === "already_established" && <small className="baseline-bulk-hint">This baseline is preserved. Open Part review to compare changed workbook evidence.</small>}
          </article>)}
          {!preview.parts.length && <p className="baseline-workspace-empty">No saved mapped Parts are available for this workbook. Save a Part mapping first.</p>}
        </div>
        {preview.pendingRows.length > 0 && <section className="baseline-bulk-pending" aria-label="Pending source rows for review">
          <div><strong><AlertTriangle size={15}/>Pending ODS rows · {preview.pendingRows.length}</strong><span>These active rows are excluded from baseline requirements. Review each assignment; this preview does not mark the workbook complete.</span></div>
          <ul>{preview.pendingRows.map((row,index) => <li key={`${row.groupKey}:${row.lineKey || row.row}:${index}`}>
            <span><strong>{row.sheet} · row {row.row ?? "unknown"} · {row.description || "Source row"}</strong><small>{row.status.replaceAll("_", " ")} · {row.reason}</small></span>
            <button type="button" className="button" disabled={bulkBusy} onClick={() => onReviewPendingRow(row, preview.source)}>Review in Part Mapping</button>
          </li>)}</ul>
        </section>}
        {!preview.workbookComplete && !preview.pendingRows.length && <p className="baseline-bulk-hint">Some Parts are blocked. Resolve their listed evidence issues before confirming them.</p>}
        {bulkError && <p className="baseline-bulk-error" role="alert">{bulkError}</p>}
        {bulkResult && <div className={`baseline-bulk-results ${bulkResult.totalSuccess ? "is-success" : "is-partial"}`} role="status" aria-live="polite">
          <strong>{bulkResult.totalSuccess ? "Workbook baseline confirmation complete" : "Workbook baseline confirmation needs review"}</strong>
          <span>{bulkResult.results.filter(item => item.status === "established").length} Part baseline(s) established; {bulkResult.results.filter(item => item.status === "already_established").length} already established (retained); {bulkResult.results.filter(item => item.status === "blocked").length} blocked; {bulkResult.results.filter(item => item.status === "failed").length} failed; {bulkResult.pendingRows?.length || 0} source row(s) remain for review.</span>
          <ul>{bulkResult.results.map((item,index) => <li key={`${item.partId}:${index}`}><strong>{item.partNumber || item.name || item.partId}</strong> · {baselineLabel(item.status)}{item.message ? ` · ${item.message}` : ""}</li>)}</ul>
          {!bulkResult.workbookComplete && <small>The mapped Parts can be established individually, but pending rows or blocked results mean the workbook is not fully reviewed.</small>}
        </div>}
        <div className="baseline-bulk-actions">
          {bulkAttempt ? <><span>The original workbook confirmation is retained for safe retry.</span><button type="button" className="button primary" disabled={bulkBusy} onClick={() => void submitBulk(bulkAttempt)}>{bulkBusy ? <><LoaderCircle className="spin" size={14}/>Confirming Parts…</> : "Retry same confirmation"}</button></>
            : <><span>{readyParts.length} ready Part{readyParts.length === 1 ? "" : "s"} in this fresh preview.</span><button type="button" className="button primary" disabled={!preview.canConfirm || !appendChoicesReady || bulkBusy} onClick={confirmAllReadyParts}>{bulkBusy ? "Confirming Parts…" : `Confirm ${readyParts.length} ready baseline${readyParts.length === 1 ? "" : "s"}`}</button></>}
        </div>
      </>}
    </section>
    {loading && <p className="baseline-workspace-loading" role="status"><LoaderCircle className="spin" size={15}/> Loading saved mapped Parts and workbook evidence…</p>}
    {!loading && <div className="baseline-workspace-layout">
      <aside className="baseline-workspace-nav" aria-label="Mapped Part navigation">
        <div className="baseline-workspace-nav-heading"><strong>Mapped Parts</strong><span>{parts.length}</span></div>
        <label className="baseline-workspace-search"><Search size={14}/><input aria-label="Search mapped Parts" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search number or name"/></label>
        {!parts.length && <p className="baseline-workspace-empty">No saved mapped Parts are available for this workbook. Open this review from a Part’s empty baseline entry or save a Part mapping first.</p>}
        {!!parts.length && !visibleParts.length && <p className="baseline-workspace-empty">No Parts match this search.</p>}
        <div className="baseline-workspace-part-list">{visibleParts.map(part => {
          const status = statuses[part.id] || part.manufacturingBaselineStatus || "not_established";
          return <button type="button" key={part.id} className={`baseline-workspace-part ${part.id === selectedPartId ? "is-selected" : ""}`} aria-current={part.id === selectedPartId ? "true" : undefined} onClick={() => { setError(""); setSelectedPartId(part.id); }}>
            <span><strong>{part.partNumber || "Number unavailable"} · {part.name}</strong><small>{part.description || part.variant || "No additional description"}</small><small className="baseline-workspace-status">Baseline · {baselineLabel(status)}</small></span><ChevronRight size={15}/>
          </button>;
        })}</div>
        {parts.length > 1 && <button type="button" className="button baseline-workspace-next" onClick={selectNextPart}><ChevronRight size={14}/>Next Part</button>}
      </aside>
      <section className="baseline-workspace-main" aria-label={selected ? `Baseline review for ${selected.partNumber || selected.name}` : "Baseline review details"}>
        {selected && detail ? <>
          <div className="baseline-workspace-part-heading"><div><span className="eyebrow">{selected.partNumber || "Part number unavailable"} · Rev {selected.engineeringRevision || "A"}</span><h2>{selected.name}</h2><p>{selected.description || "No Part description"}</p></div><span className="baseline-workspace-status-pill">{baselineLabel(statuses[selected.id] || selected.manufacturingBaselineStatus)}</span></div>
          <PartBaselinePanel key={`${path}:${selected.id}:${reviewEpoch}`} path={path} parts={[...detail.parts, ...(initialPart ? [initialPart] : [])]} groups={detail.groups}
            initialPartId={selected.id} autoOpen autoLoadReview showPartSelector={false}
            onStatusChange={(partId, status) => setStatuses(old => ({ ...old, [partId]: status }))}
            onUseDifferentPart={useDifferentPart}/>
        </> : <div className="baseline-workspace-empty-state"><strong>Select a mapped Part</strong><span>Search or choose a Part on the left to review its baseline evidence.</span></div>}
      </section>
    </div>}
  </main>;
}
