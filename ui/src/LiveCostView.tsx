import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type { ModelCode, Product, ProductModel } from "./types";

type Selection = { selectionIdentity: string; partId: string; partName: string; quantity: string; uom: string; sourcingRoute: string; label: string; optionGroup: string };

function message(error: unknown) { return error instanceof Error ? error.message : String(error); }
function money(value: unknown, currency: string) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "Unavailable";
  return `${currency} ${Number(value).toLocaleString("en-IN", { maximumFractionDigits: 4 })}`;
}
function newSelection(): Selection { return { selectionIdentity: crypto.randomUUID(), partId: "", partName: "", quantity: "1", uom: "each", sourcingRoute: "auto", label: "", optionGroup: "" }; }

export function LiveCostView({ products }: { products: Product[] }) {
  const [productId, setProductId] = useState("");
  const [models, setModels] = useState<ProductModel[]>([]);
  const [modelId, setModelId] = useState("");
  const [codes, setCodes] = useState<ModelCode[]>([]);
  const [codeId, setCodeId] = useState("");
  const [catalogQuery, setCatalogQuery] = useState("");
  const [catalog, setCatalog] = useState<any[]>([]);
  const [configuration, setConfiguration] = useState<any>(null);
  const [selections, setSelections] = useState<Selection[]>([]);
  const [currency, setCurrency] = useState("INR");
  const [configurationReason, setConfigurationReason] = useState("");
  const [live, setLive] = useState<any>(null);
  const [history, setHistory] = useState<any>({ items: [], total: 0, offset: 0 });
  const [policy, setPolicy] = useState<any>(null);
  const [selectedSnapshot, setSelectedSnapshot] = useState("");
  const [snapshotDetail, setSnapshotDetail] = useState<any>(null);
  const [comparison, setComparison] = useState<any>(null);
  const [snapshotLabel, setSnapshotLabel] = useState("");
  const [snapshotReason, setSnapshotReason] = useState("");
  const [saveAttempt, setSaveAttempt] = useState<any>(null);
  const [snapshotPending, setSnapshotPending] = useState<any>(null);
  const [policyScope, setPolicyScope] = useState("model_code");
  const [policyValue, setPolicyValue] = useState("inherit");
  const [timeZone, setTimeZone] = useState("Asia/Kolkata");
  const [anchorAt, setAnchorAt] = useState("");
  const [anchorDay, setAnchorDay] = useState("31");
  const [policyReason, setPolicyReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    setModels([]); setModelId(""); setCodes([]); setCodeId("");
    if (!productId) return () => { active = false; };
    api.models(productId).then(rows => { if (active) setModels(rows); }).catch(cause => { if (active) setError(message(cause)); });
    return () => { active = false; };
  }, [productId]);

  useEffect(() => {
    let active = true;
    setCodes([]); setCodeId("");
    if (!modelId) return () => { active = false; };
    api.codes(modelId).then(result => { if (active) setCodes(result.active); }).catch(cause => { if (active) setError(message(cause)); });
    return () => { active = false; };
  }, [modelId]);

  useEffect(() => {
    let active = true;
    const timer = window.setTimeout(() => {
      api.partsForConfiguration(catalogQuery).then(result => { if (active) setCatalog(result.items || []); })
        .catch(cause => { if (active) setError(message(cause)); });
    }, 180);
    return () => { active = false; window.clearTimeout(timer); };
  }, [catalogQuery]);

  const refreshCode = async (id = codeId) => {
    if (!id) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const [config, current, snapshots, due] = await Promise.all([api.costingConfiguration(id), api.liveCost(id), api.costSnapshots(id), api.costPolicy(id)]);
      setConfiguration(config); setLive(current); setHistory(snapshots); setPolicy(due);
      setCurrency(config.configuration?.currency || "INR");
      setSelections((config.configuration?.selections || []).map((item: any) => ({
        selectionIdentity: item.selectionIdentity, partId: item.partId, partName: item.partName || "",
        quantity: String(item.quantity ?? 1), uom: item.uom || "each", sourcingRoute: item.sourcingRoute || "auto",
        label: item.label || "", optionGroup: item.optionGroup || "",
      })));
      const stored = window.localStorage.getItem(`safari-cost-snapshot-pending:${id}`);
      setSnapshotPending(stored ? JSON.parse(stored) : null);
      setSnapshotDetail(null); setComparison(null);
    } catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  };

  useEffect(() => { if (codeId) void refreshCode(codeId); else { setConfiguration(null); setLive(null); setHistory({ items: [], total: 0 }); setPolicy(null); } }, [codeId]);

  const updateSelection = (identity: string, update: Partial<Selection>) => setSelections(rows => rows.map(row => row.selectionIdentity === identity ? { ...row, ...update } : row));
  const saveConfiguration = async () => {
    if (!codeId || !configurationReason.trim() || !selections.length) return;
    const payload = { currency, reason: configurationReason, selections: selections.map(row => ({
      selectionIdentity: row.selectionIdentity, partId: row.partId, quantity: Number(row.quantity), uom: row.uom,
      sourcingRoute: row.sourcingRoute, label: row.label, optionGroup: row.optionGroup,
    })) };
    setBusy(true); setError(""); setNotice("");
    try {
      await api.saveCostingConfiguration(codeId, payload, crypto.randomUUID());
      setConfigurationReason(""); setNotice("Model Code configuration revision saved in Grist.");
      await refreshCode(codeId);
    } catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  };

  const saveSnapshot = async (attempt = saveAttempt) => {
    if (!live || live.status !== "complete") return;
    const next = attempt || { key: crypto.randomUUID(), payload: { liveResult: live, label: snapshotLabel, reason: snapshotReason } };
    setSaveAttempt(next); setSnapshotPending(next); window.localStorage.setItem(`safari-cost-snapshot-pending:${codeId}`, JSON.stringify(next));
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await api.saveCostSnapshot(codeId, next.payload, next.key);
      window.localStorage.removeItem(`safari-cost-snapshot-pending:${codeId}`); setSaveAttempt(null); setSnapshotPending(null);
      setSnapshotLabel(""); setSnapshotReason(""); setNotice("Cost Snapshot is complete and its normalized Grist rows have been verified.");
      await refreshCode(codeId);
      setSnapshotDetail(result);
    } catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  };

  const savePolicy = async () => {
    if (!policyReason.trim()) return;
    const scopeId = policyScope === "system" ? null : policyScope === "product_model" ? Number(modelId) : Number(codeId);
    setBusy(true); setError("");
    try {
      await api.saveCostPolicy(policyScope, { scopeId, policy: policyValue, timeZone, scheduleAnchorAt: anchorAt ? new Date(anchorAt).toISOString() : undefined,
        anchorDay: Number(anchorDay), reason: policyReason }, crypto.randomUUID());
      setPolicyReason(""); await refreshCode(codeId); setNotice("Snapshot reminder policy saved. This policy never saves a snapshot automatically.");
    } catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  };

  const openSnapshot = async (key: string, offset = 0) => {
    setBusy(true); setError("");
    try { setSelectedSnapshot(key); setSnapshotDetail(await api.costSnapshot(key, offset, 100)); }
    catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  };

  const runComparison = async (mode: string) => {
    if (!codeId) return;
    setBusy(true); setError("");
    try {
      const body = mode === "live_last" ? { mode, liveResult: live }
        : mode === "live_snapshot" ? { mode, liveResult: live, snapshotKey: selectedSnapshot }
        : { mode, leftSnapshotKey: selectedSnapshot, rightSnapshotKey: secondSnapshot };
      setComparison(await api.compareCosts(codeId, body));
    } catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  };

  const [secondSnapshot, setSecondSnapshot] = useState("");
  const selectedPartOptions = useMemo(() => new Map(catalog.map((item: any) => [item.id, item])), [catalog]);

  return <main className="live-cost-view">
    <div className="live-cost-header"><div><div className="eyebrow">Disposable calculation · explicit history</div><h1>Live Cost &amp; Snapshots</h1>
      <p>Live Cost reads the current Grist configuration and rates. It creates no costing history. Only Save Cost Snapshot freezes a reviewed result.</p></div>
      {policy && <span className={`live-policy-badge ${policy.due ? "due" : ""}`}>{policy.policy} · {policy.due ? "Snapshot due" : "not due"}</span>}
    </div>
    <div className="live-code-selectors">
      <label>Product<select value={productId} onChange={event => { setProductId(event.target.value); setModelId(""); setCodeId(""); }}><option value="">Select Product</option>{products.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
      <label>Product Model<select value={modelId} disabled={!productId} onChange={event => { setModelId(event.target.value); setCodeId(""); }}><option value="">Select Model</option>{models.map(item => <option value={item.id} key={item.id}>{item.model_number} {item.name}</option>)}</select></label>
      <label>Model Code<select value={codeId} disabled={!modelId} onChange={event => setCodeId(event.target.value)}><option value="">Select Code</option>{codes.map(item => <option value={item.id} key={item.id}>{item.code} {item.description}</option>)}</select></label>
      <button className="button" disabled={!codeId || busy} onClick={() => void refreshCode()}>Refresh Live Cost</button>
    </div>
    {error && <div role="alert" className="live-error">{error}</div>}{notice && <div role="status" className="live-notice">{notice}</div>}
    {!codeId && <section className="live-panel"><h2>Choose a Model Code</h2><p>Each code needs its own explicit configuration. File ownership, source mappings and shared workbook summaries are not copied into this configuration.</p></section>}
    {codeId && <>
      <section className="live-panel">
        <div className="live-section-title"><div><h2>Explicit Part configuration</h2><p>{configuration?.status === "configured" ? `Current revision ${configuration.configuration.version} · ${configuration.configuration.currency}` : "No configuration saved"}</p></div><span>Changes create a new Grist configuration revision</span></div>
        {!catalog.length && <p className="live-muted">No canonical Parts found. Create or map a Part from the Parts screen first.</p>}
        {selections.map(row => <div className="live-selection" key={row.selectionIdentity}>
          <label className="live-selection-part">Canonical Part<select value={row.partId} onChange={event => { const selected = selectedPartOptions.get(event.target.value) as any; updateSelection(row.selectionIdentity, { partId: event.target.value, partName: selected?.name || row.partName }); }}>
            <option value="">Choose canonical Part</option>{row.partId && !selectedPartOptions.has(row.partId) && <option value={row.partId}>{row.partName || row.partId}</option>}{catalog.map((part: any) => <option key={part.id} value={part.id}>{part.partNumber} · {part.name} · Rev {part.engineeringRevision}</option>)}
          </select></label>
          <label>Quantity<input type="number" min="0.000001" step="any" value={row.quantity} onChange={event => updateSelection(row.selectionIdentity, { quantity: event.target.value })}/></label>
          <label>Unit<input value={row.uom} onChange={event => updateSelection(row.selectionIdentity, { uom: event.target.value })}/></label>
          <label>Sourcing route<select value={row.sourcingRoute} onChange={event => updateSelection(row.selectionIdentity, { sourcingRoute: event.target.value })}><option value="auto">Choose from Part</option><option value="make">Manufacture this occurrence</option><option value="buy">Buy this occurrence</option></select></label>
          <label>Occurrence label<input value={row.label} onChange={event => updateSelection(row.selectionIdentity, { label: event.target.value })}/></label>
          <label>Option group<input value={row.optionGroup} onChange={event => updateSelection(row.selectionIdentity, { optionGroup: event.target.value })}/></label>
          <button className="button ghost" aria-label="Remove Part occurrence" onClick={() => setSelections(items => items.filter(item => item.selectionIdentity !== row.selectionIdentity))}>Remove</button>
        </div>)}
        <div className="live-configuration-footer"><button className="button" onClick={() => setSelections(items => [...items, newSelection()])}>Add Part occurrence</button>
          <label>Currency<input value={currency} onChange={event => setCurrency(event.target.value.toUpperCase())}/></label>
          <label>Configuration reason<input value={configurationReason} onChange={event => setConfigurationReason(event.target.value)} placeholder="Why is this the current Model Code configuration?"/></label>
          <button className="button primary" disabled={busy || !selections.length || selections.some(row => !row.partId || Number(row.quantity) <= 0 || !row.uom.trim()) || !configurationReason.trim()} onClick={() => void saveConfiguration()}>Save configuration revision</button>
        </div>
        <label className="live-catalog-search">Find Part<input value={catalogQuery} onChange={event => setCatalogQuery(event.target.value)} placeholder="Search Part number or name"/></label>
      </section>
      <section className="live-panel live-result-panel">
        <div className="live-section-title"><div><h2>Current Live Cost</h2><p>Input fingerprint <code>{live?.inputFingerprint || "—"}</code></p></div>
          <strong className={live?.status === "complete" ? "live-total" : "live-total incomplete"}>{money(live?.totalCost, live?.currency || currency)}</strong></div>
        {live?.status === "complete" ? <p className="live-complete">Complete current calculation · {live.lineCount} cost lines · {live.availableLineCount} rate-backed</p>
          : live && <p className="live-incomplete">Incomplete: known subtotal {money(live.knownSubtotal, live.currency || currency)} is not a final total.</p>}
        {live?.warnings?.length > 0 && <div className="live-warning-list">{live.warnings.map((item: any, index: number) => <div key={`${item.code}:${index}`}><strong>{item.code.replaceAll("_", " ")}</strong><span>{item.message}</span></div>)}</div>}
        {live?.lines?.length > 0 && <div className="live-table-wrap"><table><thead><tr><th>Occurrence / Part</th><th>Line</th><th>Quantity</th><th>Rate</th><th>Cost</th><th>Evidence</th></tr></thead><tbody>{live.lines.map((line: any) => {
          const part = live.parts.find((item: any) => item.occurrencePath === line.partOccurrencePath);
          return <tr key={line.lineKey}><td>{part?.partNumber} · {part?.name}<small>{line.stableOccurrencePath}</small></td><td>{line.description}<small>{line.costCategory} · {line.sourceKey}</small></td><td>{line.quantity} {line.quantityUOM}</td><td>{line.status === "available" ? `${money(line.rate, line.currency)} / ${line.rateUOM}` : line.reason || "Unavailable"}</td><td>{money(line.netCost, line.currency || currency)}</td><td>{live.rateEvidence.find((item: any) => item.lineKey === line.lineKey)?.evidenceReference || line.costPolicy || "—"}</td></tr>;
        })}</tbody></table></div>}
        {live?.lines?.filter((line: any) => line.sourceType === "process" && line.status !== "available").map((line: any) => <ProcessRateCapture key={line.lineKey} line={line} currency={live.currency || currency} onSaved={() => refreshCode()}/>)}
        <div className="live-snapshot-form"><label>Snapshot label<input value={snapshotLabel} onChange={event => setSnapshotLabel(event.target.value)} placeholder="Optional review label"/></label>
          <label>Save reason<input value={snapshotReason} onChange={event => setSnapshotReason(event.target.value)} placeholder="Why save this exact cost?"/></label>
          <button className="button primary" disabled={busy || live?.status !== "complete" || !snapshotReason.trim()} onClick={() => void saveSnapshot()}>Save Cost Snapshot Now</button>
          {snapshotPending && <button className="button" disabled={busy} onClick={() => void saveSnapshot(snapshotPending)}>Resume pending snapshot</button>}
        </div>
        <p className="live-readonly-note">Opening and refreshing this result does not save history or rate evidence. No reminder can save a snapshot automatically.</p>
      </section>

      <section className="live-panel">
        <div className="live-section-title"><div><h2>Snapshot policy</h2><p>Resolved from System → Product Model → Model Code</p></div><strong>{policy?.policy || "manual"}{policy?.due ? " · due" : ""}</strong></div>
        <p>{policy?.source || "system default"} · next due {policy?.nextDueAt ? new Date(policy.nextDueAt).toLocaleString() : "No scheduled reminder"} · Snapshot Now is available · automatic saves are off.</p>
        <div className="live-policy-editor"><label>Policy scope<select value={policyScope} onChange={event => setPolicyScope(event.target.value)}><option value="system">System default</option><option value="product_model">Product Model</option><option value="model_code">Model Code</option></select></label>
          <label>Frequency<select value={policyValue} onChange={event => setPolicyValue(event.target.value)}><option value="inherit">Inherit</option><option value="weekly">Weekly reminder</option><option value="monthly">Monthly reminder</option><option value="manual">Manual</option></select></label>
          <label>Timezone<input value={timeZone} onChange={event => setTimeZone(event.target.value)}/></label>
          <label>Schedule anchor<input type="datetime-local" value={anchorAt} onChange={event => setAnchorAt(event.target.value)}/></label>
          {policyValue === "monthly" && <label>Monthly anchor day<input type="number" min="1" max="31" value={anchorDay} onChange={event => setAnchorDay(event.target.value)}/></label>}
          <label>Reason<input value={policyReason} onChange={event => setPolicyReason(event.target.value)}/></label>
          <button className="button" disabled={busy || !policyReason.trim() || (policyScope === "product_model" && !modelId)} onClick={() => void savePolicy()}>Save reminder policy</button>
        </div>
      </section>

      <section className="live-panel">
        <div className="live-section-title"><div><h2>Completed snapshots</h2><p>Historical details read frozen Grist rows and evidence.</p></div><span>{history.total || 0} saved</span></div>
        <div className="live-table-wrap"><table><thead><tr><th>Captured</th><th>Label / reason</th><th>Total</th><th>By</th><th>Rows</th><th></th></tr></thead><tbody>{history.items?.map((item: any) => <tr key={item.snapshotKey}><td>{item.capturedAt ? new Date(item.capturedAt).toLocaleString() : "—"}</td><td>{item.label || "Cost snapshot"}<small>{item.notes}</small></td><td>{money(item.totalCost, item.currency)}</td><td>{item.createdBy}</td><td>{item.partRowCount} Parts · {item.lineRowCount} lines</td><td><button className="button ghost" onClick={() => void openSnapshot(item.snapshotKey)}>Open frozen detail</button></td></tr>)}</tbody></table></div>
        <div className="live-history-actions"><button className="button" disabled={!history.offset} onClick={() => { const next = Math.max(0, history.offset - 25); api.costSnapshots(codeId, next).then(setHistory); }}>Previous</button><span>{history.total ? history.offset + 1 : 0}–{Math.min((history.offset || 0) + 25, history.total)} of {history.total || 0}</span><button className="button" disabled={(history.offset || 0) + 25 >= (history.total || 0)} onClick={() => api.costSnapshots(codeId, (history.offset || 0) + 25).then(setHistory)}>Next</button></div>
      </section>

      <section className="live-panel">
        <div className="live-section-title"><div><h2>Compare costs</h2><p>Matched by canonical occurrence path and stable line/configuration identity.</p></div></div>
        <div className="live-comparison-controls"><button className="button" disabled={busy || live?.status !== "complete" || !history.total} onClick={() => void runComparison("live_last")}>Live vs last completed</button>
          <label>Historical snapshot<select value={selectedSnapshot} onChange={event => setSelectedSnapshot(event.target.value)}><option value="">Select saved snapshot</option>{history.items?.map((item: any) => <option value={item.snapshotKey} key={item.snapshotKey}>{item.label || item.snapshotKey} · {money(item.totalCost, item.currency)}</option>)}</select></label>
          <button className="button" disabled={busy || live?.status !== "complete" || !selectedSnapshot} onClick={() => void runComparison("live_snapshot")}>Live vs selected snapshot</button>
          <label>Compare with<select value={secondSnapshot} onChange={event => setSecondSnapshot(event.target.value)}><option value="">Select second snapshot</option>{history.items?.map((item: any) => <option value={item.snapshotKey} key={item.snapshotKey}>{item.label || item.snapshotKey}</option>)}</select></label>
          <button className="button" disabled={busy || !selectedSnapshot || !secondSnapshot || selectedSnapshot === secondSnapshot} onClick={() => void runComparison("snapshot_snapshot")}>Snapshot vs snapshot</button>
        </div>
        {comparison?.status === "no_snapshot" && <p>{comparison.message}</p>}
        {comparison?.status === "incomparable" && <p className="live-incomplete">Incomparable: {comparison.reason}</p>}
        {comparison?.status === "comparable" && <>
          <div className="live-comparison-summary"><strong>{comparison.left} → {comparison.right}</strong><span>{money(comparison.leftTotal, comparison.currency)} → {money(comparison.rightTotal, comparison.currency)}</span><span>Change {money(comparison.difference, comparison.currency)} · {comparison.percentDifference === null ? "percentage undefined from zero baseline" : `${comparison.percentDifference.toFixed(2)}%`}</span>
            <small>Quantity/configuration {money(comparison.quantityImpact, comparison.currency)} · Rate {money(comparison.rateImpact, comparison.currency)} · Structure {money(comparison.structuralImpact, comparison.currency)} · Residual {money(comparison.reconciliationResidual, comparison.currency)}</small></div>
          <div className="live-table-wrap"><table><thead><tr><th>Occurrence / line</th><th>Cause</th><th>Quantity effect</th><th>Rate effect</th><th>Structural</th><th>Net change</th></tr></thead><tbody>{comparison.lines.map((row: any) => <tr key={row.matchKey}><td>{row.partName || row.matchKey}</td><td>{row.classification.join(" · ")}</td><td>{row.quantityImpact === null ? "—" : money(row.quantityImpact, comparison.currency)}</td><td>{row.rateImpact === null ? "—" : money(row.rateImpact, comparison.currency)}</td><td>{money(row.structuralImpact, comparison.currency)}</td><td>{money(row.netDelta, comparison.currency)}</td></tr>)}</tbody></table></div>
        </>}
      </section>
      {snapshotDetail?.snapshot?.PublicationStatus === "complete" && <section className="live-panel live-frozen-detail"><div className="live-section-title"><div><h2>Frozen snapshot detail</h2><p>{snapshotDetail.snapshot.SnapshotKey} · {snapshotDetail.snapshot.CreatedBy} · {snapshotDetail.snapshot.Notes}</p></div><strong>{money(snapshotDetail.snapshot.TotalCost, snapshotDetail.snapshot.Currency)}</strong></div>
        <p>This view uses only the saved Part, line and rate-evidence records. It does not look up current rates.</p>
        <div className="live-table-wrap"><table><thead><tr><th>Frozen Part occurrence</th><th>Quantity</th><th>Revision / metadata</th></tr></thead><tbody>{snapshotDetail.parts.map((part: any) => <tr key={part.PartOccurrenceKey}><td>{part.PartNumber} · {part.FrozenName}<small>{part.StableOccurrencePath}</small></td><td>{part.EffectiveQuantity} {part.QuantityUOM}</td><td>Rev {part.EngineeringRevision} · metadata v{part.MetadataVersionNumber}</td></tr>)}</tbody></table></div>
        <div className="live-table-wrap"><table><thead><tr><th>Frozen line</th><th>Quantity</th><th>Rate</th><th>Cost</th><th>Frozen source evidence</th></tr></thead><tbody>{snapshotDetail.lines.map((line: any) => <tr key={line.SnapshotLineKey}><td>{line.Description}<small>{line.SourceType} · {line.SourceKey}</small></td><td>{line.Quantity} {line.QuantityUOM}</td><td>{money(line.Rate, line.Currency)} / {line.RateUOM}</td><td>{money(line.NetCost, line.Currency)}</td><td>{snapshotDetail.rateEvidence.filter((item: any) => item.SnapshotLine === line.id).map((item: any) => `${item.SourceTable}/${item.SourceRecordId} · ${item.EvidenceReference}`).join("; ") || "No rate evidence"}</td></tr>)}</tbody></table></div>
        {snapshotDetail.lineCount > snapshotDetail.lineOffset + snapshotDetail.lineLimit && <button className="button" onClick={() => void openSnapshot(snapshotDetail.snapshot.SnapshotKey, snapshotDetail.lineOffset + snapshotDetail.lineLimit)}>More lines</button>}
      </section>}
    </>}
  </main>;
}

function ProcessRateCapture({ line, currency, onSaved }: { line: any; currency: string; onSaved: () => Promise<void> }) {
  const [open, setOpen] = useState(false); const [rate, setRate] = useState(""); const [uom, setUom] = useState(line.quantityUOM || "each");
  const [source, setSource] = useState(""); const [reason, setReason] = useState(""); const [attempt, setAttempt] = useState<any>(null); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  async function save() {
    const payload = { rate: Number(rate), uom, currency, sourceReference: source, reason };
    const next = attempt || { key: crypto.randomUUID(), payload }; setAttempt(next); setBusy(true); setError("");
    try { await api.recordProcessRate(Number(line.lineMasterId), next.payload, next.key); setAttempt(null); setOpen(false); await onSaved(); }
    catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  }
  return <div className="live-rate-capture"><button className="button ghost" onClick={() => setOpen(value => !value)}>Record current {line.process} rate for this line</button>
    {open && <div className="live-rate-form"><p>The rate is stored as a dated, sourced CostingProcessRate in Safari Grist.</p><label>Rate<input type="number" min="0" step="any" value={rate} onChange={event => setRate(event.target.value)}/></label><label>Rate unit<input value={uom} onChange={event => setUom(event.target.value)}/></label><label>Source reference<input value={source} onChange={event => setSource(event.target.value)}/></label><label>Reason<input value={reason} onChange={event => setReason(event.target.value)}/></label><button className="button primary" disabled={busy || !rate || !uom.trim() || !source.trim() || !reason.trim()} onClick={() => void save()}>{attempt ? "Retry same rate" : "Save sourced process rate"}</button>{error && <p role="alert">{error}</p>}</div>}
  </div>;
}
