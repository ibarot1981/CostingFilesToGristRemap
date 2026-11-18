import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Check, ChevronLeft, ChevronRight, Clock3, FileSpreadsheet, History, LoaderCircle, PackagePlus, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { ApiError, api } from "./api";
import "./spares.css";

type Spare = {
  recordId: number; spareKey: string; spareCode: string; description: string; category: string; unit: string;
  sourcingType: "purchased" | "in_house" | "unclassified"; status: "active" | "retired"; version: number;
  createdAt?: string; createdBy?: string; updatedAt?: string; updatedBy?: string;
  legacyAliases?: string[]; sourceCosts?: any[]; sourceEvidence?: any[]; history?: any[];
};
type Attempt = { key: string; operation: "create" | "update" | "retire"; spareKey?: string; payload: Record<string, unknown> };
type ImportAttempt = { key: string; path: string; previewFingerprint: string; sourceHashes: Record<string, string> };
type Preview = { previewFingerprint: string; sourceHashes: Record<string, string>; sourceLabels: Record<string, string>; counts: Record<string, number>; identityRows: any[]; identityOffset: number; identityLimit: number; identityTotal: number; sourceEvidenceRows?: any[]; costEvidenceRows?: any[]; compositionEvidenceRows?: any[]; evidenceOffset?: number; evidenceLimit?: number; evidenceTotals?: Record<string, number>; reviewRequired: boolean; readOnly: boolean };

const masterAttemptKey = "spares:master-write-attempt";
const importAttemptKey = "spares:import-write-attempt";
const emptyForm = { description: "", category: "", unit: "", sourcingType: "unclassified", legacyAliases: "", reason: "" };

function readAttempt<T>(key: string): T | null {
  try { return JSON.parse(sessionStorage.getItem(key) || "null") as T | null; } catch { return null; }
}
function errorMessage(error: unknown): string { return error instanceof Error ? error.message : String(error); }
function label(value: unknown): string { return String(value ?? "").trim() || "Not recorded"; }
function pretty(value: unknown): string { return typeof value === "string" ? value : JSON.stringify(value ?? null); }
function timestampLabel(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Not recorded";
  const raw = Array.isArray(value) && value[0] === "D" ? value[1] : value;
  const numeric = typeof raw === "number" || (typeof raw === "string" && /^\d+(?:\.\d+)?$/.test(raw.trim()));
  const parsed = numeric ? new Date(Number(raw) * 1000) : new Date(String(raw));
  if (!Number.isFinite(parsed.getTime())) return String(raw);
  return new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Kolkata" }).format(parsed);
}
function ageLabel(age: any, sourceDate?: string): string {
  if (!age) return "Age unknown";
  if (age.status === "future_date") return `Future source date · ${label(sourceDate || age.sourceDate)} · ${age.daysUntil} days ahead`;
  if (age.status === "unknown" || age.status === "missing") return "Age unknown · no source date";
  return `${age.ageDays} calendar days · source date ${label(sourceDate || age.sourceDate)} (Asia/Kolkata)`;
}

export function SparesMasterView({ active = true }: { active?: boolean }) {
  const [items, setItems] = useState<Spare[]>([]);
  const [total, setTotal] = useState(0);
  const [sources, setSources] = useState<any[]>([]);
  const [query, setQuery] = useState("");
  const [sourcingType, setSourcingType] = useState("all");
  const [status, setStatus] = useState("active");
  const [selectedKey, setSelectedKey] = useState("");
  const [detail, setDetail] = useState<Spare | null>(null);
  const [mode, setMode] = useState<"view" | "create" | "edit">("view");
  const [form, setForm] = useState(emptyForm);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [sourcesError, setSourcesError] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [masterAttempt, setMasterAttempt] = useState<Attempt | null>(() => readAttempt(masterAttemptKey));
  const [importAttempt, setImportAttempt] = useState<ImportAttempt | null>(() => readAttempt(importAttemptKey));
  const [importPath, setImportPath] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewOffset, setPreviewOffset] = useState(0);
  const [evidenceOffset, setEvidenceOffset] = useState(0);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
  const [importStatus, setImportStatus] = useState<any>(null);

  const refreshList = async () => {
    const result = await api.spares(query, sourcingType === "all" ? "" : sourcingType, status, 0, 500);
    setItems(result.items || []); setTotal(Number(result.total || 0));
    if (selectedKey && !(result.items || []).some((item: Spare) => item.spareKey === selectedKey) && status !== "all") setSelectedKey("");
  };
  const loadDetail = async (key: string) => {
    if (!key) { setDetail(null); return; }
    setDetailLoading(true);
    try { const result = await api.spareDetail(key); setDetail(result); }
    catch (cause) { setError(errorMessage(cause)); }
    finally { setDetailLoading(false); }
  };
  const loadSources = async () => {
    try { const result = await api.spareSources(); setSources(result.items || []); setSourcesError(""); }
    catch (cause) { setSources([]); setSourcesError(errorMessage(cause)); }
  };

  useEffect(() => {
    if (!active) return;
    let current = true;
    const timer = window.setTimeout(() => {
      setLoading(true); setError("");
      void api.spares(query, sourcingType === "all" ? "" : sourcingType, status, 0, 500)
        .then(result => { if (current) { setItems(result.items || []); setTotal(Number(result.total || 0)); } })
        .catch(cause => { if (current) setError(errorMessage(cause)); })
        .finally(() => { if (current) setLoading(false); });
    }, query ? 220 : 0);
    return () => { current = false; window.clearTimeout(timer); };
  }, [active, query, sourcingType, status]);

  useEffect(() => { if (active) void loadSources(); }, [active]);
  useEffect(() => { if (active && selectedKey && mode === "view") void loadDetail(selectedKey); else if (!selectedKey) setDetail(null); }, [active, selectedKey, mode]);
  useEffect(() => { if (sources.length && !importPath) setImportPath(sources[0].path); }, [sources, importPath]);

  const selected = useMemo(() => items.find(item => item.spareKey === selectedKey) || detail || null, [items, selectedKey, detail]);
  const startCreate = () => { setSelectedKey(""); setDetail(null); setForm(emptyForm); setMode("create"); setError(""); setNotice(""); };
  const startEdit = () => {
    if (!detail) return;
    setForm({ description: detail.description, category: detail.category, unit: detail.unit, sourcingType: detail.sourcingType, legacyAliases: (detail.legacyAliases || []).join(", "), reason: "" });
    setMode("edit"); setError(""); setNotice("");
  };
  const clearMasterAttempt = () => { sessionStorage.removeItem(masterAttemptKey); setMasterAttempt(null); };

  const runMasterAttempt = async (attempt: Attempt) => {
    setError(""); setNotice("");
    try {
      const result = attempt.operation === "create" ? await api.createSpare(attempt.payload, attempt.key)
        : attempt.operation === "retire" ? await api.retireSpare(attempt.spareKey || "", attempt.payload, attempt.key)
          : await api.updateSpare(attempt.spareKey || "", attempt.payload, attempt.key);
      clearMasterAttempt();
      const key = String(result.spareKey || attempt.spareKey || "");
      if (key) { setSelectedKey(key); setMode("view"); await loadDetail(key); }
      await refreshList();
      setNotice(attempt.operation === "create" ? `Created ${result.spareCode || "spare"}.` : attempt.operation === "retire" ? "Spare retired; its history and references are preserved." : "Spare changes saved.");
    } catch (cause) {
      if (cause instanceof ApiError && cause.retryDisposition === "safe_to_edit") clearMasterAttempt();
      setError(errorMessage(cause));
    }
  };

  const saveMaster = async () => {
    if (masterAttempt) { setError("A previous save has an uncertain outcome. Resume the same request before starting another."); return; }
    const payload: Record<string, unknown> = {
      description: form.description.trim(), category: form.category.trim(), unit: form.unit.trim(),
      sourcingType: form.sourcingType, reason: form.reason.trim(),
    };
    let attempt: Attempt;
    if (mode === "create") {
      payload.legacyAliases = form.legacyAliases.split(/[\n,;]/).map(value => value.trim()).filter(Boolean);
      attempt = { key: crypto.randomUUID(), operation: "create", payload };
    } else {
      if (!detail) return;
      payload.expectedVersion = detail.version;
      attempt = { key: crypto.randomUUID(), operation: "update", spareKey: detail.spareKey, payload };
    }
    sessionStorage.setItem(masterAttemptKey, JSON.stringify(attempt)); setMasterAttempt(attempt);
    await runMasterAttempt(attempt);
  };

  const retireSelected = async () => {
    if (!detail || masterAttempt) return;
    const reason = window.prompt(`Retire ${detail.spareCode}? Historical references are preserved. Enter a reason:`);
    if (reason === null) return;
    const attempt: Attempt = { key: crypto.randomUUID(), operation: "retire", spareKey: detail.spareKey, payload: { expectedVersion: detail.version, reason: reason.trim() } };
    sessionStorage.setItem(masterAttemptKey, JSON.stringify(attempt)); setMasterAttempt(attempt);
    await runMasterAttempt(attempt);
  };

  const clearImportAttempt = () => { sessionStorage.removeItem(importAttemptKey); setImportAttempt(null); };
  const loadPreview = async (offset = 0, nextEvidenceOffset = evidenceOffset) => {
    if (!importPath) return;
    setPreviewLoading(true); setError(""); setReviewed(false); setImportStatus(null);
    try { const result = await api.spareImportPreview(importPath, offset, 100, nextEvidenceOffset, 50); setPreview(result); setPreviewOffset(offset); setEvidenceOffset(nextEvidenceOffset); }
    catch (cause) { setPreview(null); setError(errorMessage(cause)); }
    finally { setPreviewLoading(false); }
  };
  const applyImport = async (existingAttempt = importAttempt) => {
    if (!preview || !reviewed || importBusy) return;
    if (existingAttempt && (existingAttempt.path !== importPath || JSON.stringify(existingAttempt.sourceHashes) !== JSON.stringify(preview.sourceHashes))) {
      setError("The pending request belongs to different source revisions. Reload its exact source revision before resuming it."); return;
    }
    const attempt = existingAttempt || { key: crypto.randomUUID(), path: importPath, previewFingerprint: preview.previewFingerprint, sourceHashes: preview.sourceHashes };
    if (!existingAttempt) { sessionStorage.setItem(importAttemptKey, JSON.stringify(attempt)); setImportAttempt(attempt); }
    setImportBusy(true); setError(""); setImportStatus(null);
    try {
      const result = await api.applySpareImport({ path: attempt.path, previewFingerprint: attempt.previewFingerprint }, attempt.key);
      clearImportAttempt(); setImportStatus({ status: "complete", progress: result }); setNotice("Reviewed source import completed in Safari Manufacturing Grist.");
      await refreshList(); if (selectedKey) await loadDetail(selectedKey);
    } catch (cause) {
      setError(errorMessage(cause));
      try {
        const statusResult = await api.spareImportStatus(attempt.key);
        setImportStatus(statusResult);
        if (statusResult.status === "complete") setNotice("Grist reports this import as complete. Rebuild the original preview and resume the same request to verify its imported identities before clearing recovery data.");
        else if (statusResult.status !== "applying" && statusResult.status !== "recovery_required") clearImportAttempt();
        else if (statusResult.previewFingerprint !== attempt.previewFingerprint || JSON.stringify(statusResult.sourceHashes) !== JSON.stringify(attempt.sourceHashes)) clearImportAttempt();
      } catch (statusCause) {
        if (statusCause instanceof ApiError && statusCause.status === 404) clearImportAttempt();
        // Without an authoritative Grist status, retain the exact key and payload for safe recovery.
      }
    } finally { setImportBusy(false); }
  };

  const pollImport = async () => {
    if (!importAttempt) return;
    try {
      const result = await api.spareImportStatus(importAttempt.key); setImportStatus(result);
      if (result.status === "complete") setNotice("Grist reports this import as complete. Rebuild the original preview and resume the same request to verify its imported identities before clearing recovery data.");
      else if (result.status === "recovery_required") setNotice("This import is waiting for its exact unfinished spare create request to be resolved. The original retry key has been retained.");
    } catch (cause) { setError(errorMessage(cause)); }
  };

  const countEntries = Object.entries(preview?.counts || {});
  const identityEnd = Math.min(previewOffset + (preview?.identityLimit || 100), preview?.identityTotal || 0);
  const evidenceEnd = Math.min(evidenceOffset + (preview?.evidenceLimit || 50), Math.max(0, ...Object.values(preview?.evidenceTotals || {})));
  const evidenceRows = [
    ...(preview?.sourceEvidenceRows || []).map(row => ({ ...row, evidenceType: "Source metadata" })),
    ...(preview?.costEvidenceRows || []).map(row => ({ ...row, evidenceType: `Cost · ${row.costKind || "source"}` })),
    ...(preview?.compositionEvidenceRows || []).map(row => ({ ...row, evidenceType: `Composition · ${row.sourceKind || "source"}` })),
  ];

  return <main className="spares-page">
    <header className="spares-heading"><div><div className="eyebrow">Safari Manufacturing · B1 foundation</div><h1>Spare master</h1><p>Canonical Grist records with reviewable ODS lineage and source-date evidence.</p></div><button className="button" type="button" onClick={() => { void refreshList().catch(cause => setError(errorMessage(cause))); if (selectedKey) void loadDetail(selectedKey); }}><RefreshCw size={15}/> Refresh records</button></header>
    {error && <div className="spares-alert error" role="alert"><AlertTriangle size={16}/><span>{error}</span></div>}
    {notice && <div className="spares-alert success" role="status"><Check size={16}/><span>{notice}</span></div>}
    {!active && <div className="spares-muted">Spares data loads when this workspace is opened.</div>}

    <section className="spares-workspace" aria-label="Spare master records">
      <aside className="spares-list-panel">
        <div className="spares-list-heading"><div><h2>Spare records</h2><span>{total} matching record{total === 1 ? "" : "s"}</span></div><button type="button" className="button primary" onClick={startCreate}><PackagePlus size={15}/> New spare</button></div>
        <label className="spares-search"><Search size={16}/><span className="sr-only">Search code, legacy code, description or category</span><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Code, legacy code, description…"/></label>
        <div className="spares-filters"><label>Sourcing<select aria-label="Filter by sourcing type" value={sourcingType} onChange={event => setSourcingType(event.target.value)}><option value="all">All sourcing types</option><option value="purchased">Purchased</option><option value="in_house">In-house</option><option value="unclassified">Unclassified</option></select></label><label>Status<select aria-label="Filter by status" value={status} onChange={event => setStatus(event.target.value)}><option value="active">Active</option><option value="retired">Retired</option><option value="all">All statuses</option></select></label></div>
        <div className="spares-record-list" aria-live="polite">{loading ? <div className="spares-empty"><LoaderCircle className="spin"/>Loading spare records…</div> : items.length ? items.map(item => <button type="button" key={item.spareKey} className={`spares-record ${selectedKey === item.spareKey ? "selected" : ""}`} onClick={() => { setMode("view"); setSelectedKey(item.spareKey); setNotice(""); }}><span className="spares-record-top"><strong>{item.spareCode}</strong><span className={`spares-status ${item.status}`}>{item.status}</span></span><span className="spares-record-description">{item.description || "Description required"}</span><span className="spares-record-meta">{item.sourcingType.replace("_", " ")} · {item.legacyAliases?.length || 0} legacy alias{item.legacyAliases?.length === 1 ? "" : "es"}</span></button>) : <div className="spares-empty">No spare records match these filters.</div>}</div>
      </aside>

      <section className="spares-detail-panel" aria-label="Spare detail">
        {mode === "create" || mode === "edit" ? <form className="spares-form" onSubmit={event => { event.preventDefault(); void saveMaster(); }}>
          <div className="spares-section-heading"><div><div className="eyebrow">{mode === "create" ? "New canonical identity" : detail?.spareCode}</div><h2>{mode === "create" ? "Create spare" : "Edit spare metadata"}</h2></div><button className="button" type="button" onClick={() => setMode("view")}>Cancel</button></div>
          {mode === "create" && <p className="spares-note">The permanent SM-S number is allocated by the Grist-backed registry. Sourcing class is recorded separately; legacy codes are retained as aliases.</p>}
          <label>Description<input required maxLength={240} value={form.description} onChange={event => setForm({ ...form, description: event.target.value })}/></label>
          <div className="spares-form-grid"><label>Category<input value={form.category} maxLength={120} onChange={event => setForm({ ...form, category: event.target.value })}/></label><label>Unit<input value={form.unit} maxLength={40} onChange={event => setForm({ ...form, unit: event.target.value })}/></label></div>
          <label>Sourcing type<select value={form.sourcingType} onChange={event => setForm({ ...form, sourcingType: event.target.value })}><option value="purchased">Purchased</option><option value="in_house">In-house</option><option value="unclassified">Unclassified · review needed</option></select></label>
          {mode === "create" && <label>Legacy Item Code aliases<textarea rows={3} placeholder="One per line, comma or semicolon separated" value={form.legacyAliases} onChange={event => setForm({ ...form, legacyAliases: event.target.value })}/></label>}
          <label>Reason for change<input required maxLength={500} value={form.reason} onChange={event => setForm({ ...form, reason: event.target.value })}/></label>
          <div className="spares-form-actions"><button className="button primary" type="submit" disabled={Boolean(masterAttempt) || !form.description.trim()}>{mode === "create" ? "Create in Grist" : "Save changes"}</button></div>
        </form> : detailLoading ? <div className="spares-empty"><LoaderCircle className="spin"/>Loading saved spare evidence…</div> : detail ? <SpareDetail spare={detail} onEdit={startEdit} onRetire={() => void retireSelected()} canRetire={!masterAttempt}/> : <div className="spares-empty spares-detail-empty"><PackagePlus size={30}/><strong>Select a spare record</strong><span>Create a canonical spare or select a row to inspect its source, costs and history.</span></div>}
        {masterAttempt && <div className="spares-pending" role="status"><AlertTriangle size={17}/><div><strong>Previous save outcome is uncertain.</strong><span>Retrying uses the exact same key and payload, so Grist can safely return the saved result.</span></div><button className="button" type="button" onClick={() => void runMasterAttempt(masterAttempt)}>Resume same save</button></div>}
      </section>
    </section>

    <section className="spares-import-card" aria-labelledby="spares-import-title">
      <div className="spares-section-heading"><div><div className="eyebrow">Read-only source inspection</div><h2 id="spares-import-title">Review spare workbook import</h2><p>Preview associated SparesDB, detail and summary rows plus Template Master lineage. Nothing is saved until you apply the reviewed preview.</p></div><FileSpreadsheet size={22}/></div>
      {sourcesError && <div className="spares-inline-warning"><AlertTriangle size={15}/><span>{sourcesError}</span></div>}
      <div className="spares-import-controls"><label>Associated costing workbook<select aria-label="Associated costing workbook" value={importPath} onChange={event => { setImportPath(event.target.value); setPreview(null); setReviewed(false); }} disabled={!sources.length}><option value="">{sources.length ? "Choose a workbook" : "No active associated ODS workbooks"}</option>{sources.map(source => <option value={source.path} key={source.path}>{source.fileName} · {source.product} / {source.model}</option>)}</select></label><button type="button" className="button primary" disabled={!importPath || previewLoading || importBusy} onClick={() => void loadPreview(0)}>{previewLoading ? <LoaderCircle className="spin" size={15}/> : <Search size={15}/>} Build read-only preview</button></div>
      {importAttempt && !preview && <div className="spares-pending" role="status"><Clock3 size={17}/><div><strong>A saved import attempt is available for recovery.</strong><span>Choose its original workbook and build a fresh preview; recovery will reuse the original key and source hashes.</span></div><button className="button" type="button" onClick={() => void pollImport()}>Check saved status</button></div>}
      {preview && <div className="spares-preview" aria-live="polite">
        <div className="spares-preview-banner"><ShieldCheck size={16}/><div><strong>Read-only preview · review required</strong><span>{preview.sourceLabels?.associatedWorkbook} · Template: {preview.sourceLabels?.templateMaster}</span><code>Preview {preview.previewFingerprint}</code></div></div>
        <div className="spares-count-grid">{countEntries.map(([key, value]) => <div className="spares-count" key={key}><strong>{Number(value).toLocaleString()}</strong><span>{key.replace(/([A-Z])/g, " $1")}</span></div>)}</div>
        <div className="spares-hash-grid">{Object.entries(preview.sourceHashes || {}).map(([key, value]) => <div key={key}><strong>{key}</strong><code>{value}</code></div>)}</div>
        {preview.identityRows?.length > 0 && <div className="spares-preview-table-wrap"><h3>Identity decisions · {preview.identityTotal} rows</h3><table className="spares-preview-table"><thead><tr><th>Legacy code</th><th>Proposed action</th><th>Description</th><th>Sourcing</th><th>Source cell</th></tr></thead><tbody>{preview.identityRows.map((row, index) => <tr key={`${row.normalizedLegacyCode || row.legacyCode}-${index}`}><td><code>{label(row.legacyCode)}</code></td><td><span className={`spares-action ${row.action}`}>{label(row.action)}</span><small>{label(row.resolution)}</small></td><td>{label(row.description || row.maintainedDescription)}</td><td>{label(row.sourcingTypeCandidate || "Unclassified · review before classifying")}</td><td>{label(row.sourceSheet)} · row {label(row.sourceRow)} · {label(row.sourceCell)}</td></tr>)}</tbody></table>
          <div className="spares-pager"><span>Showing {preview.identityTotal ? previewOffset + 1 : 0}–{identityEnd} of {preview.identityTotal}</span><button type="button" className="button" disabled={previewOffset <= 0 || previewLoading} onClick={() => { setReviewed(false); void loadPreview(Math.max(0, previewOffset - 100)); }}><ChevronLeft size={14}/> Previous</button><button type="button" className="button" disabled={identityEnd >= preview.identityTotal || previewLoading} onClick={() => { setReviewed(false); void loadPreview(identityEnd); }}>Next <ChevronRight size={14}/></button></div>
        </div>}
        <div className="spares-preview-table-wrap"><h3>Source, cost and composition evidence · paged read-only records</h3>{evidenceRows.length ? <div className="spares-evidence-list">{evidenceRows.map((row, index) => <article className="spares-evidence-row" key={`${row.evidenceType}-${row.key || row.sourceCell}-${index}`}><div><strong>{row.evidenceType}</strong><span>{label(row.sourceFile)} · {label(row.sheetName)} · row {label(row.sourceRow)} · {label(row.sourceCell || row.sourceCodeCell)}</span></div><span>Identity: {label(row.legacyCode || row.sourceIdentity)} · {label(row.resolutionStatus || row.rowAction)}</span>{row.formula && <code>Formula: {row.formula}</code>}<span>Cached/source value: {label(row.cachedValue ?? row.sourceDateRaw ?? row.sourceLineCode ?? row.quantity)}</span>{row.sourceDateISO !== undefined && <span>Source date: {label(row.sourceDateISO)} · {label(row.sourceDateMeaning)} · {label(row.dateBasis)}</span>}{row.manufacturingLineCode && <span>Manufacturing line: {row.manufacturingLineCode}</span>}{row.storesLineCode && <span>Stores line: {row.storesLineCode}</span>}{row.nestedLegacySpareCode && <span>Nested legacy spare: {row.nestedLegacySpareCode} · {label(row.nestedReferenceStatus)}</span>}{row.quantityOverride !== undefined && <span>Quantity: {label(row.quantity)} · override: {label(row.quantityOverride)}</span>}</article>)}</div> : <div className="spares-muted">No source evidence rows on this preview page.</div>}<div className="spares-pager"><span>Showing {evidenceEnd ? evidenceOffset + 1 : 0}–{evidenceEnd} of {Math.max(0, ...Object.values(preview.evidenceTotals || {}))} maximum evidence rows</span><button type="button" className="button" disabled={evidenceOffset <= 0 || previewLoading} onClick={() => void loadPreview(previewOffset, Math.max(0, evidenceOffset - (preview.evidenceLimit || 50)))}><ChevronLeft size={14}/> Previous evidence</button><button type="button" className="button" disabled={evidenceEnd >= Math.max(0, ...Object.values(preview.evidenceTotals || {})) || previewLoading} onClick={() => void loadPreview(previewOffset, evidenceEnd)}>Next evidence <ChevronRight size={14}/></button></div></div>
        <p className="spares-note">Source cost evidence stays labelled by its recorded date meaning. Missing dates remain unknown; future dates are flagged. Formula caches are preserved as unapproved source evidence and are never promoted to live rates.</p>
        {importAttempt && <div className="spares-pending"><Clock3 size={17}/><div><strong>Import request {importAttempt.key.slice(0, 8)} is retained in this browser session.</strong><span>{importStatus ? `Status: ${importStatus.status} · ${pretty(importStatus.progress)}` : "Resume only with the same source preview and request key."}</span></div><button className="button" type="button" onClick={() => void pollImport()}>Check saved status</button></div>}
        {importStatus?.status === "applying" && <p className="spares-inline-warning" role="status">Import is applying in Grist: {pretty(importStatus.progress)}. Retry with the same key if the connection was interrupted.</p>}
        {importStatus?.status === "recovery_required" && <p className="spares-inline-warning" role="status">Import recovery needs attention: {pretty(importStatus.progress)}. The saved request is retained and will not be cleared from this browser.</p>}
        {importStatus?.status === "complete" && importAttempt && <p className="spares-inline-warning" role="status">Grist reports completion. Rebuild the original source preview and resume this request so the server can verify every imported identity before recovery data is cleared.</p>}
        <label className="spares-review-check"><input type="checkbox" checked={reviewed} onChange={event => setReviewed(event.target.checked)} disabled={previewLoading || importBusy}/><span>I reviewed the identity decisions, source hashes and evidence, and approve this exact preview.</span></label>
        <div className="spares-form-actions"><button type="button" className="button primary" disabled={!reviewed || importBusy || previewLoading || Boolean(importAttempt && (importAttempt.path !== importPath || JSON.stringify(importAttempt.sourceHashes) !== JSON.stringify(preview.sourceHashes)))} onClick={() => void applyImport()}>{importBusy ? <><LoaderCircle size={15} className="spin"/>Applying reviewed import…</> : importAttempt ? "Resume same reviewed import" : "Apply reviewed import in Grist"}</button></div>
      </div>}
    </section>
  </main>;
}

function SpareDetail({ spare, onEdit, onRetire, canRetire }: { spare: Spare; onEdit: () => void; onRetire: () => void; canRetire: boolean }) {
  const evidence = spare.sourceEvidence || [];
  return <article className="spares-detail">
    <div className="spares-section-heading"><div><div className="eyebrow">Canonical spare · version {spare.version}</div><h2>{spare.spareCode}</h2><p>{spare.description}</p></div><div className="spares-detail-actions"><span className={`spares-status ${spare.status}`}>{spare.status}</span><button type="button" className="button" onClick={onEdit}>Edit</button>{spare.status === "active" && <button type="button" className="button" disabled={!canRetire} onClick={onRetire}>Retire</button>}</div></div>
    <dl className="spares-facts"><div><dt>Category</dt><dd>{label(spare.category)}</dd></div><div><dt>Unit</dt><dd>{label(spare.unit)}</dd></div><div><dt>Sourcing type</dt><dd>{spare.sourcingType.replace("_", " ")}</dd></div><div><dt>Created</dt><dd>{timestampLabel(spare.createdAt)} · {label(spare.createdBy)}</dd></div><div><dt>Last updated</dt><dd>{timestampLabel(spare.updatedAt)} · {label(spare.updatedBy)}</dd></div></dl>
    <section className="spares-detail-section"><h3><Search size={15}/> Legacy Item Code aliases</h3>{spare.legacyAliases?.length ? <div className="spares-aliases">{spare.legacyAliases.map(alias => <code key={alias}>{alias}</code>)}</div> : <p>No legacy aliases recorded.</p>}</section>
    <section className="spares-detail-section"><h3><Clock3 size={15}/> Source costs and aging</h3>{spare.sourceCosts?.length ? <div className="spares-cost-list">{spare.sourceCosts.map((cost, index) => <article key={`${cost.sourceReference}-${index}`}><strong>{label(cost.kind)} · {label(cost.value)} {label(cost.currency)} / {label(cost.unit)}</strong><span>{label(cost.sourceReference)}</span><span>Source date: {label(cost.sourceDate)} · {label(cost.sourceDateMeaning)}</span><span>{ageLabel(cost.age, cost.sourceDate)}</span><span>Evidence: {label(cost.dateBasis)} · {label(cost.approvalStatus)}</span>{cost.formula && <code>{cost.formula} · cached {label(cost.cachedValue)}</code>}</article>)}</div> : <p>No source-cost dates recorded. Age is unknown; a metadata update does not reset it.</p>}</section>
    <section className="spares-detail-section"><h3><FileSpreadsheet size={15}/> Source and draft composition evidence</h3>{evidence.length ? <div className="spares-cost-list">{evidence.map((item, index) => <article key={`${item.EvidenceKey || item.CompositionKey || item.SourceKind}-${index}`}><strong>{label(item.SourceKind)} · {label(item.SheetName)} row {label(item.SourceRow)} {label(item.SourceCell)}</strong><span>{label(item.SourceFile)} · {label(item.SourceHash)}</span><span>Legacy identity: {label(item.LegacyItemCode || item.SourceIdentity)} · {label(item.ResolutionStatus)}</span>{item.SourceLineCode && <span>Source line: {item.SourceLineCode} · manufacturing {label(item.ManufacturingLineCode)} · stores {label(item.StoresLineCode)}</span>}{item.NestedLegacySpareCode && <span>Nested spare: {item.NestedLegacySpareCode} · {label(item.NestedReferenceStatus)}</span>}{item.Quantity !== undefined && <span>Quantity {label(item.Quantity)} {label(item.QuantityUnit)} · override {label(item.QuantityOverride)} {label(item.OverrideUnit)}</span>}{item.Formula && <code>{item.Formula} · cached {pretty(item.CachedValue)}</code>}</article>)}</div> : <p>No imported source evidence is linked to this spare.</p>}</section>
    <section className="spares-detail-section"><h3><History size={15}/> Change history</h3>{spare.history?.length ? <ol className="spares-history">{spare.history.map((row, index) => <li key={`${row.RequestKey || row.Version}-${index}`}><strong>{label(row.EventType)} · version {label(row.Version)}</strong><span>{timestampLabel(row.OccurredAt)} · {label(row.Actor)}</span><span>{label(row.Reason)}</span></li>)}</ol> : <p>No change history has been recorded.</p>}</section>
  </article>;
}
