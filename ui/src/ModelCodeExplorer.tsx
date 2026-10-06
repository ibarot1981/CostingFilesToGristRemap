import { Fragment, useEffect, useState } from "react";
import { api } from "./api";
import { CostingReviewView } from "./CostingReviewView";
import type { ModelCode, Product, ProductModel } from "./types";

export function ModelCodeExplorer({ products, onOpenFile }: { products: Product[]; onOpenFile: (path: string) => void }) {
  const [product, setProduct] = useState("");
  const [models, setModels] = useState<ProductModel[]>([]);
  const [model, setModel] = useState("");
  const [codes, setCodes] = useState<ModelCode[]>([]);
  const [code, setCode] = useState("");
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [offset, setOffset] = useState(0);
  const [reconcile, setReconcile] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [filters, setFilters] = useState({ sheet: "", process: "", part: "", material: "", status: "" });
  useEffect(() => {
    setModels([]); setModel(""); setError("");
    if (!product) return;
    let active = true;
    api.models(product).then(next => { if (active) setModels(next); }).catch(cause => { if (active) setError(String(cause)); });
    return () => { active = false; };
  }, [product]);
  useEffect(() => {
    setCodes([]); setCode(""); setError("");
    if (!model) return;
    let active = true;
    api.codes(model).then(next => { if (active) setCodes(next.active); }).catch(cause => { if (active) setError(String(cause)); });
    return () => { active = false; };
  }, [model]);
  useEffect(() => {
    setData(null); setError(""); setOpen(null); setReconcile(false); setBusy(Boolean(code));
    if (!code) return;
    let active = true;
    api.codeRecords(code, { ...filters, offset: String(offset), limit: "100" }).then(next => { if (active) setData(next); })
      .catch(cause => { if (active) setError(String(cause)); }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [code, filters, offset]);
  const reset = () => { setCode(""); setData(null); setReconcile(false); setOffset(0); };
  return <main className="normalized model-code-explorer">
    <h1>Product Models → Model Codes</h1>
    <p>Inspect stored Safari records. Reconciliation refreshes a disposable workbook copy when requested.</p>
    <div className="normalized-filters">
      <label>Product<select value={product} onChange={event => { reset(); setModel(""); setProduct(event.target.value); }}><option value="">Select Product</option>{products.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label>Product Model<select disabled={!product} value={model} onChange={event => { reset(); setModel(event.target.value); }}><option value="">Select Model</option>{models.map(item => <option key={item.id} value={item.id}>{item.model_number} {item.name}</option>)}</select></label>
      <label>Model Code<select disabled={!model} value={code} onChange={event => { reset(); setCode(event.target.value); }}><option value="">Select Code</option>{codes.map(item => <option key={item.id} value={item.id}>{item.code}</option>)}</select></label>
    </div>
    {busy && <p role="status">Loading stored records…</p>}{error && <p role="alert">{error}</p>}
    {data && <>
      <p>{data.status === "stored" ? "Stored Grist records" : data.status.replaceAll("_", " ")} · Read {data.readAt}</p>
      <p>Shared source-file evidence for {data.code.code}. Model Code configuration requires review; costing authority is unchanged.</p>
      {data.baseline && <p>Snapshot {data.baseline.snapshotKey} · accepted {data.baseline.acceptedAt}<br/>Source SHA-256: <code>{data.baseline.sourceHash || "Unavailable"}</code> · current ODS revision has not been checked.</p>}
      {data.source && <><p>{data.source.relative_path}</p><div className="workbench-actions"><button className="button" onClick={() => onOpenFile(data.source.relative_path)}>Open source file</button><button className="button primary" onClick={() => setReconcile(true)}>Open source reconciliation</button></div></>}
      {reconcile && <CostingReviewView key={code} selectedPath={data.source.relative_path} selectedName={data.source.name} />}
      <div className="normalized-filters">{Object.entries(filters).map(([name, value]) => <label key={name}>{name}<input value={value} onChange={event => { setOffset(0); setFilters(old => ({ ...old, [name]: event.target.value })); }} /></label>)}</div>
      <p>{data.total} matching stored rows</p>
      <table><thead><tr><th>Sheet / row</th><th>Part evidence</th><th>Process</th><th>Material</th><th>Quantity</th><th>Status</th></tr></thead><tbody>{data.items.map((row: any) => {
        const key = `${row.observation.sheet}:${row.observation.row}`;
        return <Fragment key={key}><tr><td><button aria-expanded={open === key} onClick={() => setOpen(open === key ? null : key)}>{row.observation.sheet} #{row.observation.row}</button></td><td>{row.observation.part_display_name || "Unallocated"}</td><td>{row.master?.process_type}</td><td>{row.detail?.material_display_name}</td><td>{row.detail?.quantity}</td><td>{row.mapping.status}</td></tr>{open === key && <tr><td colSpan={6}><strong>Stored revision and audit</strong><p>Revision: {row.revision?.key || "No stored revision"}<br/>Previous revision: {row.revision?.previous_key || "None"}</p>{row.audit.length ? row.audit.map((event: any, index: number) => <p key={event.key || index}>{event.actor || "Unknown actor"} · {event.reason || "No reason recorded"}{event.cr_reference ? ` · CR ${event.cr_reference}` : ""}</p>) : <p>No audit entries recorded.</p>}</td></tr>}</Fragment>;
      })}</tbody></table>
      <div className="normalized-pages"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>Previous</button><span>{data.total ? offset + 1 : 0}–{Math.min(offset + 100, data.total)} of {data.total}</span><button disabled={offset + 100 >= data.total} onClick={() => setOffset(offset + 100)}>Next</button></div>
    </>}
  </main>;
}
