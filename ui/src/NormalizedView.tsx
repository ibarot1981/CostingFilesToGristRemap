import { Fragment, useEffect, useState } from "react";
import { api } from "./api";

type Row = { mapping: { status: string; master_key: string }; observation: { sheet: string; row: number; status: string; part_display_name: string; cells: Record<string, unknown>; cached_cost: string | null }; master: { process_type: string; key: string } | null; revision: { key: string; previous_key: string | null } | null; detail: { material_display_name: string; quantity: string | null; cost_cached: string | null; activity_kind: string | null } | null; audit: unknown[] };

export function NormalizedView({ selectedPath, onReconcile }: { selectedPath: string; onReconcile?: () => void }) {
  const [filters, setFilters] = useState({ sheet: "", process: "", master: "", part: "", material: "", status: "" });
  const [data, setData] = useState<any>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => {
    if (!selectedPath) return;
    let active = true;
    api.normalized(selectedPath, { ...filters, offset: String(offset), limit: "100" }).then((next) => { if (active) { setData(next); setError(""); } }).catch((cause) => { if (active) setError(String(cause)); });
    return () => { active = false; };
  }, [selectedPath, filters, offset]);
  if (!selectedPath) return <main className="normalized"><h1>Normalized process lines</h1><p>Select the mapped S1KHF workbook in Costing Explorer.</p></main>;
  return <main className="normalized">
    <h1>Workbook process lines</h1><p className="normalized-path">{selectedPath}</p>{onReconcile && <button className="button primary" onClick={onReconcile}>Open source reconciliation</button>}
    {error && <p role="alert">{error}</p>}
    {data && <><p>{data.baseline.accepted ? "Accepted Safari snapshot" : "Local source projection, awaiting Safari baseline"}: {data.baseline.snapshotKey} · {data.persistence} · {data.baseline.accepted ? (data.baseline.sourceMatchesBaseline ? "source hash matches" : data.baseline.costDriftAssessment?.cost_only_confirmed ? "current cost drift reconciles to workbook C7" : "source changed since acceptance; review required") : "unverified against Safari"}</p>
      <p>{data.total} matching rows · {data.reconciliation.unresolved_mappings} ambiguous mappings · {data.reconciliation.differences.length} source count differences{data.reconciliation.persisted_count_differences ? ` · ${data.reconciliation.persisted_count_differences.length} persisted count differences` : ""}</p>
      <div className="normalized-filters">{Object.entries(filters).map(([name, value]) => <label key={name}>{name}<input value={value} onChange={(event) => { setOffset(0); setFilters((old) => ({ ...old, [name]: event.target.value })); }} /></label>)}</div>
      <table><thead><tr><th>Sheet / row</th><th>Part evidence</th><th>Material</th><th>Process</th><th>Status</th><th>Cached cost</th><th>Mapping</th></tr></thead><tbody>{data.items.map((item: Row) => <Fragment key={item.observation.sheet + item.observation.row}><tr><td><button onClick={() => setOpen(open === item.mapping.master_key ? null : item.mapping.master_key)} aria-expanded={open === item.mapping.master_key}>{item.observation.sheet} #{item.observation.row}</button></td><td>{item.observation.part_display_name || "Unallocated"}</td><td>{item.detail?.material_display_name}</td><td>{item.master?.process_type}{item.detail?.activity_kind ? ` / ${item.detail.activity_kind}` : ""}</td><td>{item.observation.status}</td><td>{item.observation.cached_cost}</td><td>{item.mapping.status}</td></tr>{open === item.mapping.master_key && <tr><td colSpan={7}><strong>Master</strong> {item.master?.key}<br/><strong>Revision</strong> {item.revision?.key} · predecessor {item.revision?.previous_key || "none"}<br/><strong>Source cells and formulas</strong><pre>{JSON.stringify(item.observation.cells, null, 2)}</pre><strong>Audit</strong><pre>{JSON.stringify(item.audit, null, 2)}</pre></td></tr>}</Fragment>)}</tbody></table>
      <div className="normalized-pages"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>Previous</button><span>{offset + 1}–{Math.min(offset + 100, data.total)} of {data.total}</span><button disabled={offset + 100 >= data.total} onClick={() => setOffset(offset + 100)}>Next</button></div>
      {data.exceptions.length > 0 && <details><summary>{data.exceptions.length} mapping exceptions</summary><pre>{JSON.stringify(data.exceptions, null, 2)}</pre></details>}
    </>}
  </main>;
}
