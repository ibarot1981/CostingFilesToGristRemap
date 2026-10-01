import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Check, Download, RefreshCw, ShieldCheck } from "lucide-react";
import { api } from "./api";
import type { DirectoryProductMapping, Product, ProductModel, ReconciliationIssue } from "./types";

type RevisionPlan = Record<string, any>;
type CleanupPlan = Record<string, any>;

export function ReconciliationView(props: { products: Product[]; onOpenFile: (path: string) => void }) {
  const [issues, setIssues] = useState<ReconciliationIssue[]>([]);
  const [mappings, setMappings] = useState<DirectoryProductMapping[]>([]);
  const [models, setModels] = useState<ProductModel[]>([]);
  const [selected, setSelected] = useState<ReconciliationIssue | null>(null);
  const [detail, setDetail] = useState<Record<string, any> | null>(null);
  const [filters, setFilters] = useState({ status: "", issue_type: "", severity: "", owner: "", product_id: "", model_id: "", query: "" });
  const [owner, setOwner] = useState("");
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [revision, setRevision] = useState<RevisionPlan | null>(null);
  const [cleanup, setCleanup] = useState<CleanupPlan[]>([]);
  const [mappingPath, setMappingPath] = useState("");
  const [mappingProduct, setMappingProduct] = useState("");
  const [mappingReason, setMappingReason] = useState("");
  const [mappingInherit, setMappingInherit] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const detailHeadingRef = useRef<HTMLHeadingElement>(null);
  const requestKeysRef = useRef(new Map<string, string>());
  const requestKeyFor = (fingerprint: string) => {
    let key = requestKeysRef.current.get(fingerprint);
    if (!key) { key = crypto.randomUUID(); requestKeysRef.current.set(fingerprint, key); }
    return key;
  };
  const clearRequestKey = (fingerprint: string) => requestKeysRef.current.delete(fingerprint);

  const load = async () => {
    setBusy(true); setError("");
    try {
      const [issueResult, mappingResult] = await Promise.all([api.reconciliationIssues(filters), api.directoryMappings()]);
      setIssues(issueResult.items); setMappings(mappingResult.items);
      if (selected) {
        const next = issueResult.items.find((item) => item.id === selected.id) || selected;
        setSelected(next);
        setDetail(await api.reconciliationIssue(next.id));
      }
    } catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  };
  useEffect(() => { void load(); }, [filters.status, filters.issue_type, filters.severity, filters.owner, filters.product_id, filters.model_id, filters.query]);
  useEffect(() => { if (!filters.product_id) { setModels([]); setFilters((old) => ({ ...old, model_id: "" })); return; } void api.models(filters.product_id).then(setModels).catch((cause) => setError(message(cause))); }, [filters.product_id]);
  useEffect(() => { if (selected) detailHeadingRef.current?.focus(); }, [selected?.id]);

  const exportQuery = useMemo(() => new URLSearchParams(Object.entries(filters).filter(([, value]) => Boolean(value))).toString(), [filters]);
  const selectIssue = async (issue: ReconciliationIssue) => {
    setSelected(issue); setDetail(null); setRevision(null); setCleanup([]); setReason(""); setConfirmed(false); setError("");
    try { setDetail(await api.reconciliationIssue(issue.id)); } catch (cause) { setError(message(cause)); }
  };
  const mutateIssue = async (action: string, requiresReason = true) => {
    if (!selected) return;
    if (requiresReason && !reason.trim()) { setError("Enter a reason before changing issue status."); return; }
    const fingerprint = JSON.stringify(["issue", selected.id, action, (detail?.issue as ReconciliationIssue | undefined)?.version ?? selected.version, reason, owner]);
    setBusy(true); setError("");
    try {
      await api.issueAction(selected.id, action, { expectedVersion: (detail?.issue as ReconciliationIssue | undefined)?.version ?? selected.version, reason, owner }, requestKeyFor(fingerprint));
      clearRequestKey(fingerprint);
      setNotice(`Issue ${action.replace("-", " ")} action recorded.`); setReason(""); await load();
    } catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  };
  const previewRevision = async () => {
    if (!selected) return;
    setBusy(true); setError(""); setRevision(null);
    try { setRevision(await api.sourceRevisionPreview(selected.id)); setConfirmed(false); setReason(""); }
    catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  };
  const applyRevision = async () => {
    if (!selected || !revision || !confirmed || !reason.trim()) return;
    const fingerprint = JSON.stringify(["revision", selected.id, revision.issueVersion, revision.stored?.sha256, revision.current?.sha256, reason]);
    setBusy(true); setError("");
    try {
      await api.sourceRevisionApply(selected.id, { expectedIssueVersion: revision.issueVersion, expectedStoredHash: revision.stored?.sha256, expectedCurrentHash: revision.current?.sha256, reason }, requestKeyFor(fingerprint));
      clearRequestKey(fingerprint);
      setNotice("Source revision accepted and a new immutable observation was recorded."); setRevision(null); setConfirmed(false); setReason(""); await load();
    } catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  };
  const previewCleanup = async () => {
    setBusy(true); setError("");
    try { const result = await api.identityCleanupPreview(); setCleanup(result.items); setConfirmed(false); setReason(""); }
    catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  };
  const applyCleanup = async (plan: CleanupPlan) => {
    if (!confirmed || !reason.trim() || !plan.issue) return;
    const fingerprint = JSON.stringify(["cleanup", plan.model.id, plan.canonicalReplacement.id, plan.issue.version, reason]);
    setBusy(true); setError("");
    try {
      await api.identityCleanupApply({ modelId: plan.model.id, canonicalModelId: plan.canonicalReplacement.id, issueId: plan.issue.id, expectedIssueVersion: plan.issue.version, reason }, requestKeyFor(fingerprint));
      clearRequestKey(fingerprint);
      setNotice(`Model ${plan.model.model_number} was superseded with an audit event.`); setCleanup([]); setConfirmed(false); setReason(""); await load();
    } catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  };
  const proposeMapping = async () => {
    if (!mappingPath.trim() || !mappingProduct) { setError("Choose a relative directory and Product for the proposal."); return; }
    const fingerprint = JSON.stringify(["directory-propose", mappingPath, mappingProduct, mappingInherit, mappingReason]);
    setBusy(true); setError("");
    try { await api.proposeDirectoryMapping({ relativePath: mappingPath, productId: mappingProduct, inherit: mappingInherit, reason: mappingReason }, requestKeyFor(fingerprint)); clearRequestKey(fingerprint); setMappingReason(""); setNotice("Directory mapping saved as a proposal. Files remain unassigned until a separate file association is saved."); await load(); }
    catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  };
  const mappingAction = async (mapping: DirectoryProductMapping, action: string) => {
    if (!mappingReason.trim()) { setError("Enter a reason for this directory mapping decision."); return; }
    const fingerprint = JSON.stringify(["directory-action", mapping.id, action, mapping.version, mappingReason]);
    setBusy(true); setError("");
    try { await api.directoryMappingAction(mapping.id, action, { expectedVersion: mapping.version, reason: mappingReason }, requestKeyFor(fingerprint)); clearRequestKey(fingerprint); setMappingReason(""); setNotice(`Directory mapping ${action} recorded.`); await load(); }
    catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  };

  return <main className="reconciliation-view">
    <header className="reconciliation-header"><div><div className="eyebrow">Governed review</div><h1>Reconciliation</h1><p>Derived warnings become durable issues only when a scan is materialized. A disappearing condition never closes an issue.</p></div><div className="reconciliation-header-actions"><a className="button" href={`/api/reconciliation/export?${exportQuery}&format=csv`}><Download size={14}/> CSV</a><a className="button" href={`/api/reconciliation/export?${exportQuery}&format=json`}><Download size={14}/> JSON</a><button className="button" onClick={() => void load()} disabled={busy}><RefreshCw size={14}/> Refresh</button></div></header>
    {error && <div className="reconciliation-alert error" role="alert"><AlertTriangle size={15}/>{error}</div>}{notice && <div className="reconciliation-alert success" role="status"><Check size={15}/>{notice}</div>}
    <div className="reconciliation-scan"><div><strong>Bounded reconciliation scan</strong><span>Scans up to 50 registered ODS files. It computes hashes only when run.</span></div><button className="button" disabled={busy} onClick={async () => { setBusy(true); setError(""); try { const result = await api.reconciliationScan(true, 50); setNotice(`${String(result.detected ?? 0)} issue candidates found. Dry-run left repository data unchanged.`); } catch (cause) { setError(message(cause)); } finally { setBusy(false); } }}>Preview scan</button><button className="button primary" disabled={busy} onClick={async () => { setBusy(true); setError(""); try { const result = await api.reconciliationScan(false, 50); setNotice(`${String(result.materialized ?? 0)} issues materialized; existing issues were updated by fingerprint.`); await load(); } catch (cause) { setError(message(cause)); } finally { setBusy(false); } }}>Materialize issues</button></div>
    <section className="reconciliation-filters" aria-label="Reconciliation filters"><label>Search<input aria-label="Search reconciliation issues" value={filters.query} onChange={(event) => setFilters({ ...filters, query: event.target.value })} placeholder="Facts, path, message"/></label><label>Status<select value={filters.status} onChange={(event) => setFilters({ ...filters, status: event.target.value })}><option value="">All statuses</option>{["open", "in_review", "deferred", "resolved", "reopened"].map((value) => <option key={value}>{value}</option>)}</select></label><label>Type<input aria-label="Filter by issue type" value={filters.issue_type} onChange={(event) => setFilters({ ...filters, issue_type: event.target.value })} placeholder="Any issue type"/></label><label>Severity<select value={filters.severity} onChange={(event) => setFilters({ ...filters, severity: event.target.value })}><option value="">All severities</option>{["high", "error", "warning", "info"].map((value) => <option key={value}>{value}</option>)}</select></label><label>Owner<input aria-label="Filter by assigned owner" value={filters.owner} onChange={(event) => setFilters({ ...filters, owner: event.target.value })} placeholder="Owner"/></label><label>Product<select value={filters.product_id} onChange={(event) => setFilters({ ...filters, product_id: event.target.value })}><option value="">All Products</option>{props.products.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>Model<select value={filters.model_id} onChange={(event) => setFilters({ ...filters, model_id: event.target.value })} disabled={!filters.product_id}><option value="">All Models</option>{models.map((item) => <option key={item.id} value={item.id}>{item.model_number}</option>)}</select></label></section>
    <section className="reconciliation-layout"><div className="reconciliation-queue" aria-label="Reconciliation issue queue"><div className="reconciliation-list-heading"><strong>Issue queue</strong><span>{issues.length} shown</span></div>{busy && !issues.length ? <div className="empty">Loading issues…</div> : issues.length ? issues.map((issue) => <button className={`issue-row${selected?.id === issue.id ? " selected" : ""}`} key={issue.id} onClick={() => void selectIssue(issue)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); void selectIssue(issue); } }}><span className="issue-type">{issue.issue_type.replaceAll("_", " ")} · {issue.severity}</span><strong>{issue.message}</strong><small>{issue.source_path || issue.source_file || issue.entity_id} · {issue.status} · v{issue.version}</small><span className="issue-owner">Owner: {issue.assigned_owner || "Unassigned"}</span></button>) : <div className="empty">No issues match these filters.</div>}</div>
      <div className="reconciliation-detail">{selected ? <><div className="reconciliation-detail-heading"><div><span className="eyebrow">{selected.issue_type.replaceAll("_", " ")}</span><h2 ref={detailHeadingRef} tabIndex={-1}>Issue details</h2><small>{selected.id} · version {(detail?.issue as ReconciliationIssue | undefined)?.version ?? selected.version}</small></div><span className={`issue-status status-${selected.status}`}>{selected.status.replaceAll("_", " ")}</span></div><p>{selected.message}</p><div className="fact-grid"><div><strong>Source path</strong><span>{selected.source_path || selected.source_file || "—"}{selected.source_row ? ` · row ${selected.source_row}` : ""}{selected.source_cell ? ` · ${selected.source_cell}` : ""}</span></div><div><strong>First / last seen</strong><span>{selected.first_seen_at} / {selected.last_seen_at}</span></div><div><strong>Assigned owner</strong><span>{selected.assigned_owner || "Unassigned"}</span></div><div><strong>Fingerprint</strong><code>{selected.fingerprint}</code></div></div><h3>Detected facts</h3><pre>{JSON.stringify(selected.detected_facts || {}, null, 2)}</pre><h3>Proposed resolution</h3><pre>{JSON.stringify(selected.proposed_resolution || {}, null, 2)}</pre>
        <div className="issue-action-form"><label>Assign owner<input value={owner} onChange={(event) => setOwner(event.target.value)} placeholder="User or team"/></label><button className="button" disabled={busy} onClick={() => void mutateIssue(owner.trim() ? "assign" : "unassign", false)}>{owner.trim() ? "Assign" : "Unassign"}</button><label>Reason<input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Required for status changes"/></label><div className="issue-action-buttons"><button className="button" disabled={busy} onClick={() => void mutateIssue("keep-open")}>Keep open / needs investigation</button><button className="button" disabled={busy} onClick={() => void mutateIssue("defer")}>Defer</button><button className="button primary" disabled={busy} onClick={() => void mutateIssue("resolve")}>Resolve</button><button className="button" disabled={busy} onClick={() => void mutateIssue("reopen")}>Reopen</button></div></div>
        {selected.issue_type === "changed_file" && <section className="governed-action"><h3>Accept new source revision</h3><p>Preview compares stored and current path, size, modification time, SHA-256, readability, sheet count, and external references. Apply rechecks the current file hash and Model Code ownership.</p><button className="button" disabled={busy} onClick={() => void previewRevision()}>Preview revision</button>{revision && <><div className="revision-grid"><div><strong>Stored observation</strong><pre>{JSON.stringify(revision.stored, null, 2)}</pre></div><div><strong>Current source</strong><pre>{JSON.stringify(revision.current, null, 2)}</pre></div></div><div className="review-confirm"><label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)}/> I reviewed both fingerprints and the current association ownership.</label><label>Acceptance reason<input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Required audit reason"/></label><button className="button primary" disabled={busy || !revision.canApply || !confirmed || !reason.trim()} onClick={() => void applyRevision()}><ShieldCheck size={14}/> Accept revision</button>{!revision.canApply && <span className="inline-error">Apply is blocked: current code ownership changed or the file is no longer a readable new revision.</span>}</div></>}</section>}
        <section className="history-section"><h3>Association history and audit trail</h3>{detail?.associationHistory?.map((entry: any, index: number) => <div className="history-entry" key={index}><strong>{entry.association?.active ? "Active" : "Superseded"} · {entry.association?.created_at}</strong><span>{entry.association?.actor} · {entry.association?.reason}</span><span>Codes: {(entry.codes || []).map((code: any) => code.code).join(", ")}</span></div>)}{detail?.relatedFileHistories?.map((related: any) => <div className="history-entry" key={related.fileId}><strong>Code owner history · {related.relativePath || related.fileId}</strong>{(related.history || []).map((entry: any, index: number) => <div key={index}><span>{entry.association?.active ? "Current owner" : "Superseded owner"} · {entry.association?.created_at}</span><span>{entry.association?.actor} · {entry.association?.reason} · codes: {(entry.codes || []).map((code: any) => code.code).join(", ")}</span></div>)}{related.relativePath && (related.history || []).some((entry: any) => entry.association?.active) && <button className="button" onClick={() => props.onOpenFile(related.relativePath)}>Review current owner in Costing Explorer</button>}</div>)}{detail?.auditTrail?.map((entry: any) => <div className="history-entry" key={entry.id}><strong>{entry.event_type} · {entry.actor}</strong><span>{entry.occurred_at} · {entry.reason}</span><pre>{JSON.stringify(entry.payload || {}, null, 2)}</pre></div>)}{detail?.observations?.map((entry: any) => <div className="history-entry" key={entry.id}><strong>File observation · {entry.observed_at}</strong><span>{entry.relative_path} · {entry.size_bytes} bytes</span><code>{entry.file_hash}</code></div>)}{selected.issue_type === "duplicate_code_ownership" && <p className="field-hint">Guided supersede: review the active owner and its exact code/audit history above. If you save a validated replacement in the Explorer, this active history will close and remain visible as superseded.</p>}</section>
      </> : <div className="empty"><ShieldCheck size={28}/><strong>Select a durable issue</strong><span>View evidence, lifecycle state, observations, association history, and the audit trail.</span></div>}</div></section>
    <section className="cleanup-section"><div><h2>Encoding cleanup plan</h2><p>Only rows with a verified canonical model and no governed references are eligible. Apply preserves the record and records the replacement in audit history.</p></div><button className="button" onClick={() => void previewCleanup()} disabled={busy}>Preview identity cleanup</button>{cleanup.length > 0 && <><div className="review-confirm cleanup-confirm"><label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)}/> I reviewed the exact model rows and reference checks.</label><label>Reason<input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Required audit reason"/></label></div><div className="cleanup-list">{cleanup.map((plan) => <div className="cleanup-row" key={plan.model.id}><div><strong>{plan.model.model_number}</strong><span>→ {plan.canonicalReplacement?.model_number || "No canonical match"}</span><small>{plan.canApply ? "No governed references found" : `Blocked: ${JSON.stringify(plan.references)}`}</small></div><button className="button primary" disabled={busy || !plan.canApply || !confirmed || !reason.trim() || !plan.issue} onClick={() => void applyCleanup(plan)}>Supersede row</button></div>)}</div></>}
    </section>
    <section className="directory-section"><div><h2>Directory-to-Product proposals</h2><p>Approved parent mappings inherit only when enabled. A deeper approved mapping takes precedence; files still need an explicit Model and Code association.</p></div><div className="directory-proposal"><label>Relative directory<input value={mappingPath} onChange={(event) => setMappingPath(event.target.value)} placeholder="S1KHF/Local"/></label><label>Product<select value={mappingProduct} onChange={(event) => setMappingProduct(event.target.value)}><option value="">Select Product</option>{props.products.map((product) => <option key={product.id} value={product.id}>{product.name}</option>)}</select></label><label className="inline-check"><input type="checkbox" checked={mappingInherit} onChange={(event) => setMappingInherit(event.target.checked)}/> Inherit to deeper folders</label><label>Reason<input value={mappingReason} onChange={(event) => setMappingReason(event.target.value)} placeholder="Proposal reason"/></label><button className="button" onClick={() => void proposeMapping()} disabled={busy}>Propose mapping</button></div><div className="mapping-list">{mappings.length ? mappings.map((mapping) => <div className="mapping-row" key={mapping.id}><div><strong>{mapping.relative_path || "Product Costing root"}</strong><span>{props.products.find((product) => product.id === mapping.product_id)?.name || mapping.product_id} · {mapping.status}{mapping.inherit ? " · inherited" : " · exact path"}</span><small>Proposed by {mapping.proposer}{mapping.approver ? ` · approved by ${mapping.approver}` : ""} · v{mapping.version}{mapping.reason ? ` · ${mapping.reason}` : ""}</small></div>{mapping.status === "proposed" && <div className="mapping-actions"><button className="button" disabled={busy} onClick={() => void mappingAction(mapping, "approve")}>Approve</button><button className="button" disabled={busy} onClick={() => void mappingAction(mapping, "reject")}>Reject</button></div>}</div>) : <p className="empty-inline">No directory mappings yet.</p>}</div></section>
  </main>;
}

function message(cause: unknown) { return cause instanceof Error ? cause.message : String(cause); }
