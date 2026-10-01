import { useState } from "react";
import { AlertTriangle, Calculator, LoaderCircle, RefreshCw, ShieldCheck, Save } from "lucide-react";
import { api } from "./api";
import type { CostingReview, CostingReviewLine } from "./types";
import "./costingReview.css";

type Props = { selectedPath: string; selectedName: string };

const currency = (value: unknown) => {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return Number.isFinite(number)
    ? new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(number)
    : String(value);
};

const numberText = (value: unknown, digits = 3) => {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return Number.isFinite(number) ? new Intl.NumberFormat("en-IN", { maximumFractionDigits: digits }).format(number) : String(value);
};

const valueText = (value: unknown) => value === null || value === undefined || value === "" ? "—" : String(value);
const totalFields = [
  ["weight_kg", "Weight (kg)"],
  ["material_cost", "Material cost"],
  ["transport", "Transport"],
  ["unloading", "Unloading"],
  ["fabrication", "Fabrication"],
  ["grand_total", "Grand total"],
] as const;

export function CostingReviewView({ selectedPath, selectedName }: Props) {
  const [review, setReview] = useState<CostingReview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [acceptanceReason, setAcceptanceReason] = useState("");
  const [ambiguityDecisions, setAmbiguityDecisions] = useState<Record<string, string>>({});
  const [acceptanceResult, setAcceptanceResult] = useState<Record<string, unknown> | null>(null);

  const calculate = async () => {
    if (!selectedPath) return;
    setBusy(true);
    setError("");
    setReview(null);
    setAcceptanceResult(null);
    setAmbiguityDecisions({});
    try {
      setReview(await api.costingReview(selectedPath));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const accept = async () => {
    if (!review || !selectedPath || !acceptanceReason.trim()) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.acceptCostingReview({
        path: selectedPath,
        reason: acceptanceReason.trim(),
        semanticHash: review.semantic_snapshot.semantic_hash,
        sourceHashes: review.semantic_snapshot.source_hashes,
        acceptedSnapshotKey: review.accepted_baseline?.snapshot_key ?? null,
        ambiguityDecisions,
      }, globalThis.crypto?.randomUUID?.() || `costing-${Date.now()}-${Math.random().toString(16).slice(2)}`);
      setAcceptanceResult(result);
      setReview(await api.costingReview(selectedPath));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  return <main className="costing-review-view">
    <header className="costing-review-header">
      <div><div className="eyebrow">Milestone 2 · governed reconciliation</div><h1>Costing Review</h1><p>Refresh the selected workbook, calculate its costing, and compare semantic line and rate state with Safari’s last accepted snapshot.</p></div>
      <span className="mode-pill"><ShieldCheck size={13}/> Refresh and calculate are read-only</span>
    </header>

    <section className="costing-review-selection" aria-label="Selected costing workbook">
      <div><span className="review-label">Selected workbook</span><strong>{selectedName || "No workbook selected"}</strong><code>{selectedPath || "Choose a product costing workbook in Costing Explorer first."}</code></div>
      <button className="button primary" type="button" disabled={!selectedPath || busy} onClick={() => void calculate()}>
        {busy ? <LoaderCircle className="spin" size={15}/> : review ? <RefreshCw size={15}/> : <Calculator size={15}/>}
        {busy ? "Refreshing and calculating…" : review ? "Refresh and recalculate" : "Refresh and calculate"}
      </button>
    </section>
    <p className="costing-review-help">Each review initiates LibreOffice external-link refresh on a disposable sibling copy. The current RawSteel and SteelRateLog ODS snapshots are read from <code>Template DB</code>; other scenario workbooks do not override this selection.</p>

    {!selectedPath && <div className="costing-review-empty"><Calculator size={26}/><strong>Select a costing workbook</strong><span>Choose the workbook assigned to the Product Model in Costing Explorer, then open this review.</span></div>}
    {busy && <div className="costing-review-empty" role="status"><LoaderCircle className="spin" size={24}/><strong>Refreshing current costing inputs</strong><span>LibreOffice is updating local external links and recalculating a temporary workbook copy.</span></div>}
    {error && <div className="costing-review-error" role="alert"><AlertTriangle size={16}/><span>{error}</span></div>}
    {review && <ReviewResult review={review} acceptanceReason={acceptanceReason} setAcceptanceReason={setAcceptanceReason} ambiguityDecisions={ambiguityDecisions} setAmbiguityDecisions={setAmbiguityDecisions} acceptanceResult={acceptanceResult} busy={busy} onAccept={() => void accept()}/>}
  </main>;
}

type ReviewResultProps = {
  review: CostingReview;
  acceptanceReason: string;
  setAcceptanceReason: (value: string) => void;
  ambiguityDecisions: Record<string, string>;
  setAmbiguityDecisions: (value: Record<string, string>) => void;
  acceptanceResult: Record<string, unknown> | null;
  busy: boolean;
  onAccept: () => void;
};

function ReviewResult({ review, acceptanceReason, setAcceptanceReason, ambiguityDecisions, setAmbiguityDecisions, acceptanceResult, busy, onAccept }: ReviewResultProps) {
  const totals = review.summary.active_totals;
  const statusBlocked = review.status !== "ready_for_owner_review";
  const ambiguities = review.semantic_comparison.ambiguities || [];
  const ambiguityChoicesComplete = ambiguities.every((item) => item.change_type === "ambiguous_line_match" && Boolean(ambiguityDecisions[String(item.candidate_pair_key)]));
  const canAccept = !busy && Boolean(review.costing_file_id) && !statusBlocked && Boolean(acceptanceReason.trim())
    && (review.can_accept || (ambiguities.length > 0 && ambiguityChoicesComplete));
  return <div className="costing-review-results">
    <section className={`costing-review-status ${statusBlocked ? "blocked" : "ready"}`} role={statusBlocked ? "alert" : "status"}>
      {statusBlocked ? <AlertTriangle size={17}/> : <ShieldCheck size={17}/>}
      <div><strong>{statusBlocked ? "Current costing is blocked for reconciliation" : ambiguities.length ? "Owner input is needed to resolve line identity" : "Current calculation is ready to reconcile"}</strong>
        <span>{review.summary.blocked_active_line_count || review.summary.unclassified_line_count ? `${review.summary.blocked_active_line_count} active line(s) have a blocked rate or missing calculation input; ${review.summary.unclassified_line_count} line(s) have an unrecognized In Use value. Their costs are withheld, and the overall total is withheld.` : ambiguities.length ? "Only these uncertain matches need an owner decision. Clear rate and structure changes can be recorded together by the reconciliation action." : "Refresh completed and active lines were calculated. The reconciliation action records semantic differences; routine workbook revisions alone do not create change items."}</span>
      </div>
    </section>

    <section className="costing-review-baseline" aria-label="Accepted costing baseline">
      <div><span className="review-label">Last accepted Safari baseline</span><strong>{review.accepted_baseline?.snapshot_key || "None yet"}</strong>
        <small>{review.accepted_baseline ? `Accepted ${new Date(review.accepted_baseline.accepted_at).toLocaleString("en-IN")} by ${review.accepted_baseline.accepted_by}` : "Accept this reviewed run to establish the first semantic baseline."}</small>
      </div>
      <div><span className="review-label">Persistence target</span><strong>{review.persistence_adapter || "Unavailable"}</strong><small>{review.persistence_adapter === "grist-safari" ? "Safari Manufacturing" : "Local development state; not durable across restarts"}</small></div>
    </section>

    <section className="costing-review-source-card">
      <div className="costing-review-source-heading"><div><h2>Source snapshot</h2><p>Observed {new Date(review.refresh.checked_at || review.observed_at).toLocaleString("en-IN")}</p></div><span className="review-badge mapped">Refresh verified</span></div>
      <div className="costing-review-source-grid">
        {Object.entries(review.sources).map(([key, source]) => <div key={key}>
          <span>{sourceLabel(key)}</span><strong title={source.path}>{source.path.split(/[\\/]/).pop() || source.path}</strong>
          <code title={source.sha256}>SHA-256 {source.sha256.slice(0, 16)}…</code><small>Modified {new Date(source.modified_at).toLocaleString("en-IN")}</small>
        </div>)}
      </div>
      {!!review.refresh.linked_sources?.length && <p className="costing-review-linked">Refreshed local dependencies: {review.refresh.linked_sources.map((item) => item.path).join(", ")}</p>}
      <p className="costing-review-linked" role="status">Verified: source workbook {review.refresh.source_workbook_unchanged ? "unchanged" : "changed"}; linked sources {review.refresh.linked_sources_unchanged ? "unchanged" : "changed"}. The temporary copy is removed after this review.</p>
    </section>

    <section className="costing-review-metrics" aria-label="Costing totals">
      <Metric label="Active MCL lines" value={numberText(review.summary.active_line_count, 0)}/>
      <Metric label="Historical lines excluded" value={numberText(review.summary.historical_line_count, 0)}/>
      <Metric label="Rate changes since saved snapshot" value={numberText(review.rate_changes_since_saved_snapshot.length, 0)}/>
      <Metric label="Current active grand total" value={totals ? currency(totals.grand_total) : "Withheld"} emphasized={!statusBlocked}/>
      <Metric label="Rounded up per active line" value={totals ? currency(totals.grand_total_rounded_up_per_line) : "Withheld"}/>
    </section>

    <SemanticChanges review={review} decisions={ambiguityDecisions} setDecisions={setAmbiguityDecisions}/>

    <section className="costing-review-section">
      <div className="costing-review-section-heading"><div><h2>Active totals and saved summary</h2><p>The saved summary cache is historical evidence and can include In Use = No rows.</p></div><span className="review-badge warning">Tolerance ±₹100 at grand-total level</span></div>
      <div className="costing-review-table-wrap"><table className="costing-review-table summary-table"><thead><tr><th scope="col">Measure</th><th scope="col">Current active calculation</th><th scope="col">Refreshed workbook cache</th><th scope="col">Saved workbook cache</th><th scope="col">Saved historical contribution</th></tr></thead><tbody>
        {totalFields.map(([field, label]) => <tr key={field}><th scope="row">{label}</th><td>{totals ? field === "weight_kg" ? numberText(totals[field]) : currency(totals[field]) : "Withheld"}</td><td>{field === "weight_kg" ? numberText(review.summary.refreshed_workbook_summary_cache[field]) : currency(review.summary.refreshed_workbook_summary_cache[field])}</td><td>{field === "weight_kg" ? numberText(review.summary.saved_workbook_summary_cache[field]) : currency(review.summary.saved_workbook_summary_cache[field])}</td><td>{field === "weight_kg" ? numberText(review.summary.historical_cached_contribution[field]) : currency(review.summary.historical_cached_contribution[field])}</td></tr>)}
      </tbody></table></div>
      <p className="costing-review-caveat">The current-versus-saved grand-total difference is {review.summary.current_total_vs_saved_summary === null ? "unavailable while blocked" : currency(review.summary.current_total_vs_saved_summary)}. It is diagnostic only because it compares current active-only costing with a saved summary that may include historical lines and older rates.</p>
    </section>

    <section className="costing-review-section">
      <div className="costing-review-section-heading"><div><h2>Rate changes since saved workbook snapshot</h2><p>Exact material matching; each change lists affected active MCL source rows.</p></div></div>
      {review.rate_changes_since_saved_snapshot.length ? <div className="costing-review-table-wrap"><table className="costing-review-table"><thead><tr><th scope="col">Material</th><th scope="col">Saved rate</th><th scope="col">Current rate</th><th scope="col">Saved/current log row</th><th scope="col">Current date</th><th scope="col">Affected MCL rows</th><th scope="col">Estimated material-cost delta</th></tr></thead><tbody>
        {review.rate_changes_since_saved_snapshot.map((change, index) => <tr key={`${String(change.material)}-${index}`}><th scope="row">{String(change.material)}</th><td>{currency(change.previous_rate_per_kg)} / kg</td><td>{change.display_status === "available" ? `${currency(change.current_rate_per_kg)} / kg` : "Blocked"}</td><td>{valueText(change.previous_source_row)} → {valueText(change.current_source_row)}</td><td>{valueText(change.current_rate_date)}</td><td>{Array.isArray(change.affected_mcl_source_rows) ? change.affected_mcl_source_rows.join(", ") : "—"}</td><td>{change.display_status === "available" ? currency(change.estimated_active_material_cost_delta) : "Withheld"}</td></tr>)}
      </tbody></table></div> : <p className="costing-review-none">No active material’s resolved rate changed between the saved workbook snapshot and this refreshed source snapshot.</p>}
    </section>

    <section className="costing-review-section">
      <div className="costing-review-section-heading"><div><h2>Material list changes after refresh</h2><p>Rows are matched by the five-field composite key, never MCL ID or row position.</p></div></div>
      {review.material_list_changes_after_refresh.length ? <div className="costing-review-change-list">{review.material_list_changes_after_refresh.map((change, index) => <article key={`${String(change.code)}-${index}`}><strong>{String(change.change || change.code).replaceAll("_", " ")}</strong><span>Key: {Array.isArray(change.identity_key) ? change.identity_key.map(valueText).join(" · ") : "ambiguous duplicate key"}</span><small>Saved row {valueText(change.previous_source_row)} → refreshed row {valueText(change.current_source_row)} · {valueText(change.previous_status)} {change.current_status ? `→ ${String(change.current_status)}` : ""}</small>{Boolean(change.fields) && <pre>{JSON.stringify(change.fields, null, 2)}</pre>}</article>)}</div> : <p className="costing-review-none">No composition or review-context changes were introduced by external refresh.</p>}
    </section>

    <section className="costing-review-section">
      <div className="costing-review-section-heading"><div><h2>Material Cut List line review</h2><p>Exact calculation, refreshed formula cache, and saved cache are shown separately. Historical rows do not contribute to current totals.</p></div></div>
      <div className="costing-review-table-wrap line-review-wrap"><table className="costing-review-table line-review-table"><thead><tr>
        <th scope="col">Row / status</th><th scope="col">Piece</th><th scope="col">Material</th><th scope="col">Dimension × qty</th><th scope="col">Effective rate</th><th scope="col">Weight (kg)</th><th scope="col">Material</th><th scope="col">Transport</th><th scope="col">Unloading</th><th scope="col">Fabrication</th><th scope="col">Current exact total</th><th scope="col">Rounded up</th><th scope="col">Refreshed cache</th><th scope="col">Cache − calculation</th><th scope="col">Formula / saved cache</th>
      </tr></thead><tbody>{review.lines.map((line) => <LineRow line={line} key={`${line.source_row}-${line.identity_key.join("|")}`}/>)}</tbody></table></div>
    </section>

    <section className="costing-review-section">
      <div className="costing-review-section-heading"><div><h2>Parity findings</h2><p>{review.issues.length} source validation or calculation finding(s); they are review evidence and do not write to Safari Manufacturing.</p></div></div>
      {review.issues.length ? <div className="costing-review-issues">{review.issues.map((issue, index) => <details key={`${String(issue.code)}-${index}`}><summary><strong>{String(issue.code || "Review finding").replaceAll("_", " ")}</strong><span>{String(issue.material || issue.source_file || "Source evidence")}{issue.source_row ? ` · row ${String(issue.source_row)}` : ""}{issue.source_cell ? ` · ${String(issue.source_cell)}` : ""}</span></summary><pre>{JSON.stringify(issue, null, 2)}</pre></details>)}</div> : <p className="costing-review-none">No source validation or line-calculation differences were reported.</p>}
    </section>

    <section className="costing-review-reconcile" aria-label="Reconcile costing changes">
      <div>
        <span className="review-label">Governed reconciliation</span>
        <h2>{review.accepted_baseline ? "Record this ODS state in Safari" : "Establish the first accepted baseline"}</h2>
        <p>Clear rate and design changes are recorded with before/after evidence, source rows, CR references when present, and cost impact where calculable. Only uncertain line identity needs owner resolution.</p>
      </div>
      {ambiguities.length > 0 && <p className="costing-review-ambiguity-note" role="alert">Resolve the identity ambiguities above before this snapshot can be accepted.</p>}
      <label>Reconciliation note <input value={acceptanceReason} onChange={(event) => setAcceptanceReason(event.target.value)} placeholder="e.g. Process refreshed S1KHF ODS changes" /></label>
      <button className="button primary" type="button" disabled={!canAccept} onClick={onAccept}>
        {busy ? <LoaderCircle className="spin" size={15}/> : <Save size={15}/>}
        {busy ? "Reconciling…" : review.accepted_baseline ? "Reconcile and accept baseline" : "Establish baseline"}
      </button>
      {acceptanceResult && <p className="costing-review-accept-result" role="status">Safari recorded this reconciliation. Snapshot: {String((acceptanceResult.snapshot as Record<string, unknown> | undefined)?.snapshot_key || "saved")}. The accepted baseline is being reloaded.</p>}
    </section>

    <p className="costing-review-footer">{review.snapshot_comparison_basis} Refresh and calculate are read-only. The separate reconciliation action persists the accepted snapshot and its change items to Safari Manufacturing.</p>
  </div>;
}

function SemanticChanges({ review, decisions, setDecisions }: {
  review: CostingReview;
  decisions: Record<string, string>;
  setDecisions: (value: Record<string, string>) => void;
}) {
  const comparison = review.semantic_comparison;
  const changes = comparison.changes || [];
  const ambiguities = comparison.ambiguities || [];
  return <section className="costing-review-section semantic-change-section" aria-label="Changes since accepted snapshot">
    <div className="costing-review-section-heading"><div><h2>Semantic changes since accepted snapshot</h2><p>Rates and product structure are compared independently. Workbook row position, cell address, and MCL ID are provenance only; they never decide line identity.</p></div><span className={`review-badge ${ambiguities.length ? "warning" : "mapped"}`}>{ambiguities.length ? `${ambiguities.length} needs owner resolution` : `${changes.length} change(s)`}</span></div>
    {!comparison.baseline_exists && <p className="costing-review-none">No accepted baseline exists yet. Establishing one records the current refreshed costing as the starting point.</p>}
    {comparison.baseline_exists && !changes.length && !ambiguities.length && <p className="costing-review-none">No semantic rate or costing-structure changes were found since the accepted Safari baseline. File hash or timestamp changes alone do not create a change item.</p>}
    {!!changes.length && <div className="semantic-change-list">{changes.map((change, index) => {
      const previous = change.previous_state as Record<string, any> | null;
      const current = change.current_state as Record<string, any> | null;
      const material = change.material || previous?.fields?.material_to_cut || current?.fields?.material_to_cut;
      return <details className={`semantic-change ${change.classification === "rate_source_change" ? "rate-change" : "structure-change"}`} key={String(change.change_key || index)}>
        <summary><strong>{String(change.change_type || "change").replaceAll("_", " ")}</strong><span>{material ? String(material) : String(change.process_list || "Costing structure")}</span><b>{change.cost_impact === null || change.cost_impact === undefined ? "Impact unavailable" : `Impact ${currency(change.cost_impact)}`}</b></summary>
        <div className="semantic-change-detail"><p>{String(change.reason || "No reason was recorded in the ODS source.")}</p>
          {change.cr_reference && <p>CR reference: {typeof change.cr_reference === "object" ? JSON.stringify(change.cr_reference) : String(change.cr_reference)}</p>}
          {!!change.field_changes && <div><strong>Changed values</strong><pre>{JSON.stringify(change.field_changes, null, 2)}</pre></div>}
          <div className="semantic-state-grid"><div><strong>Previous state</strong><pre>{JSON.stringify(previous, null, 2)}</pre></div><div><strong>Current state</strong><pre>{JSON.stringify(current, null, 2)}</pre></div></div>
          {!!change.affected_lines?.length && <div><strong>Affected costing line(s)</strong><pre>{JSON.stringify(change.affected_lines, null, 2)}</pre></div>}
          {!!change.source_evidence && <div><strong>Rate source evidence</strong><pre>{JSON.stringify(change.source_evidence, null, 2)}</pre></div>}
        </div>
      </details>;
    })}</div>}
    {!!ambiguities.length && <div className="semantic-ambiguities"><h3>Owner resolution required</h3><p>The system will not infer a match from row position or MCL ID. Resolve each probable pair, or correct duplicate composite keys in the ODS and refresh.</p>
      {ambiguities.map((item, index) => {
        const key = String(item.candidate_pair_key || `unresolvable-${index}`);
        const canChoose = item.change_type === "ambiguous_line_match";
        const label = (state: Record<string, any> | null | undefined) => state ? `${state.process_list || ""} · ${Object.entries(state.identity_values || {}).map(([name, value]) => `${name}: ${valueText(value)}`).join(" · ")}` : "No candidate";
        return <article key={key}>
          <strong>{String(item.change_type || "ambiguous identity").replaceAll("_", " ")}</strong>
          <span>{label(item.previous_candidate)} → {label(item.current_candidate)}</span>
          <small>{String(item.reason || "The available source fields do not establish a unique match.")}</small>
          {canChoose ? <label>Owner decision <select aria-label={`Owner decision for ambiguity ${index + 1}`} value={decisions[key] || ""} onChange={(event) => setDecisions({ ...decisions, [key]: event.target.value })}><option value="">Choose…</option><option value="pair">These are the same design line</option><option value="separate">These are separate lines</option></select></label> : <small>Correct the duplicate or unclear source rows in the selected ODS, then refresh the comparison.</small>}
        </article>;
      })}
    </div>}
  </section>;
}

function LineRow({ line }: { line: CostingReviewLine }) {
  const calculation = line.current_calculation;
  const active = line.status === "active";
  const blockedRows = Array.isArray(line.rate_warning?.source_rows) ? line.rate_warning.source_rows.join(", ") : "";
  const blockedCells = Array.isArray(line.rate_warning?.source_cells) ? line.rate_warning.source_cells.join(", ") : "";
  return <tr className={`${line.calculation_status === "blocked" ? "line-blocked" : ""} ${line.calculation_status === "invalid_usage_flag" ? "line-invalid" : ""} ${!active ? "line-historical" : ""}`}>
    <th scope="row">{line.source_row}<small>{line.status === "historical" ? "Historical · excluded" : line.calculation_status === "invalid_usage_flag" ? "Invalid In Use value" : line.calculation_status === "blocked" ? "Blocked" : "Active"}</small></th>
    <td>{valueText(line.machine_piece_description)}<small>{line.source_cells.machine_piece_description?.cell}</small></td>
    <td>{line.material_to_cut || "—"}<small>{line.source_cells.material_to_cut?.cell}{line.rate_warning ? ` · invalid rate log row(s) ${blockedRows || "unknown"}${blockedCells ? ` · ${blockedCells}` : ""}` : ""}</small></td>
    <td>{valueText(line.dimension_mm)} mm × {valueText(line.quantity)}<small>{valueText(line.optional_item_group_1)}</small></td>
    <td>{!active || line.rate_status !== "available" ? line.rate_status === "blocked_user_resolution_required" ? "Blocked" : "—" : `${currency(line.rate?.per_kg)} / kg`}<small>{line.rate?.source_row ? `Rate log row ${line.rate.source_row}` : line.rate?.source === "default_master_rate" ? `RawSteel default · row ${line.rate.raw_steel_source_row}` : ""}{line.rate?.date ? ` · ${line.rate.date}` : ""}</small></td>
    <td>{calculation ? numberText(Number(calculation.total_grams) / 1000) : "—"}</td>
    <td>{currency(calculation?.material_cost)}</td><td>{currency(calculation?.transport)}</td><td>{currency(calculation?.unloading)}</td><td>{currency(calculation?.fabrication)}</td>
    <td>{currency(calculation?.grand_total)}</td><td>{currency(calculation?.rounded_grand_total_up)}</td>
    <td>{currency(line.refreshed_workbook_cached_values.grand_total)}<small>{line.source_cells.grand_total?.cell}</small></td><td>{currency(line.cached_grand_total_difference)}</td>
    <td><details className="line-formula-details"><summary>Evidence</summary><pre>{JSON.stringify({ savedWorkbookCache: line.saved_workbook_cached_values, refreshedWorkbookCache: line.refreshed_workbook_cached_values, formulae: line.cached_formulae, sourceCells: line.source_cells }, null, 2)}</pre></details></td>
  </tr>;
}

function Metric({ label, value, emphasized = false }: { label: string; value: string; emphasized?: boolean }) {
  return <div className={`costing-review-metric${emphasized ? " emphasized" : ""}`}><span>{label}</span><strong>{value}</strong></div>;
}

function sourceLabel(key: string) {
  return ({
    selected_workbook_saved: "Selected workbook · saved cache",
    selected_workbook_refreshed_copy: "Selected workbook · refreshed copy",
    raw_steel: "RawSteel source",
    rate_log_dump: "SteelRateLog source",
  } as Record<string, string>)[key] || key.replaceAll("_", " ");
}
