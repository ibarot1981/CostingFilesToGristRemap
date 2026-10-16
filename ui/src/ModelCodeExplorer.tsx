import { Fragment, useEffect, useMemo, useState, type FormEvent } from "react";
import { api } from "./api";
import { CostingReviewView } from "./CostingReviewView";
import type { ModelCode, Product, ProductModel } from "./types";

export function ModelCodeExplorer({ products, onOpenFile, writeEnabled = false, onCatalogChanged }: {
  products: Product[];
  onOpenFile: (path: string) => void;
  writeEnabled?: boolean;
  onCatalogChanged?: (code: ModelCode) => void;
}) {
  const [product, setProduct] = useState("");
  const [models, setModels] = useState<ProductModel[]>([]);
  const [model, setModel] = useState("");
  const [codes, setCodes] = useState<ModelCode[]>([]);
  const [code, setCode] = useState("");
  const [codeQuery, setCodeQuery] = useState("");
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [savingCode, setSavingCode] = useState(false);
  const [offset, setOffset] = useState(0);
  const [reconcile, setReconcile] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [filters, setFilters] = useState({ sheet: "", process: "", part: "", material: "", status: "" });
  const [newCode, setNewCode] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [editingCodeId, setEditingCodeId] = useState("");
  const [editingDescription, setEditingDescription] = useState("");
  const [managementError, setManagementError] = useState("");
  const [managementNotice, setManagementNotice] = useState("");

  useEffect(() => {
    setModels([]); setModel(""); setCodes([]); setCode(""); setData(null); setEditingCodeId(""); setManagementError("");
    if (!product) return;
    let active = true;
    api.models(product).then(next => { if (active) setModels(next); }).catch(cause => { if (active) setError(String(cause)); });
    return () => { active = false; };
  }, [product]);
  useEffect(() => {
    setCodes([]); setCode(""); setCodeQuery(""); setEditingCodeId(""); setManagementError(""); setManagementNotice("");
    if (!model) return;
    let active = true;
    api.codes(model).then(next => { if (active) setCodes([...next.active, ...next.legacy]); }).catch(cause => { if (active) setError(String(cause)); });
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

  const selectedModel = models.find(item => item.id === model);
  const visibleCodes = useMemo(() => {
    const query = codeQuery.trim().toLocaleLowerCase();
    if (!query) return codes;
    const modelContext = `${selectedModel?.model_number || ""} ${selectedModel?.name || ""}`;
    const productContext = products.find(item => item.id === product)?.name || "";
    return codes.filter(item => `${item.code} ${item.description} ${modelContext} ${productContext}`.toLocaleLowerCase().includes(query));
  }, [codeQuery, codes, product, products, selectedModel]);

  const reset = () => { setCode(""); setData(null); setReconcile(false); setOffset(0); };

  async function createCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!model || !newCode.trim() || !newDescription.trim() || !writeEnabled || savingCode) return;
    setSavingCode(true); setManagementError(""); setManagementNotice("");
    try {
      const saved = await api.createModelCode(model, { code: newCode, description: newDescription });
      setCodes((old: ModelCode[]) => [...old.filter(item => item.id !== saved.id), saved].sort((a, b) => a.code.localeCompare(b.code)));
      setCode(saved.id); setNewCode(""); setNewDescription(""); setManagementNotice(`${saved.code} was created under ${selectedModel?.model_number || "this Product Model"}. No workbook association or costing configuration was created.`);
      onCatalogChanged?.(saved);
    } catch (cause) { setManagementError(String(cause)); }
    finally { setSavingCode(false); }
  }

  async function saveDescription(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const current = codes.find(item => item.id === editingCodeId);
    if (!current || !writeEnabled || savingCode) return;
    setSavingCode(true); setManagementError(""); setManagementNotice("");
    try {
      const saved = await api.updateModelCodeDescription(current.id, { description: editingDescription, expectedDescription: current.description || "" });
      setCodes((old: ModelCode[]) => old.map(item => item.id === saved.id ? saved : item));
      setData((old: any) => old?.code?.id === saved.id ? { ...old, code: saved } : old);
      setEditingCodeId(""); setEditingDescription(""); setManagementNotice(`Description saved for ${saved.code}. The code identity, hierarchy and existing associations are unchanged.`);
      onCatalogChanged?.(saved);
    } catch (cause) { setManagementError(String(cause)); }
    finally { setSavingCode(false); }
  }

  return <main className="normalized model-code-explorer">
    <h1>Product Models → Model Codes</h1>
    <p>Inspect stored Safari records and manage code descriptions under an existing Product Model.</p>
    <div className="normalized-filters">
      <label>Product<select value={product} onChange={event => { reset(); setModel(""); setProduct(event.target.value); }}><option value="">Select Product</option>{products.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label>Product Model<select disabled={!product} value={model} onChange={event => { reset(); setModel(event.target.value); }}><option value="">Select Model</option>{models.map(item => <option key={item.id} value={item.id}>{item.model_number} {item.name}</option>)}</select></label>
    </div>

    <section className="model-code-management" aria-label="Product Model Code management">
      <header><div><h2>Manage Product Model Codes</h2><p>Descriptions are stored on the canonical code record. Creating a code does not associate a workbook or configure costing.</p></div></header>
      {!writeEnabled && <p className="model-code-management-readonly" role="status">The current repository is read-only for code management. Connect the canonical Safari Grist repository to save changes.</p>}
      {!model ? <p className="field-hint">Choose an existing Product and Product Model to manage its codes.</p> : <>
        <label className="model-code-search">Search codes or Product Model context<input value={codeQuery} onChange={event => setCodeQuery(event.target.value)} placeholder="Code, description, Product Model…" autoComplete="off" spellCheck={false}/></label>
        <div className="model-code-managed-list" aria-label="Existing Product Model Codes">
          {visibleCodes.map(item => <article key={item.id}>
            <div><strong>{item.code}</strong><span>{item.description || "No description"}</span>{item.legacy_spares_only && <small>Legacy spares code · excluded from active selectors</small>}</div>
            <button type="button" className="button" disabled={!writeEnabled || savingCode} onClick={() => { setEditingCodeId(item.id); setEditingDescription(item.description || ""); setManagementError(""); setManagementNotice(""); }}>Edit description</button>
          </article>)}
          {!visibleCodes.length && <p className="field-hint">No Product Model Codes match this search.</p>}
        </div>
        {editingCodeId && <form className="model-code-edit-form" onSubmit={event => void saveDescription(event)}>
          <h3>Edit description · {codes.find(item => item.id === editingCodeId)?.code}</h3>
          <label>Description<textarea value={editingDescription} onChange={event => setEditingDescription(event.target.value)} disabled={!writeEnabled || savingCode} rows={3}/></label>
          <div className="workbench-actions"><button type="submit" className="button primary" disabled={!writeEnabled || savingCode}>{savingCode ? "Saving…" : "Save description"}</button><button type="button" className="button" disabled={savingCode} onClick={() => { setEditingCodeId(""); setEditingDescription(""); }}>Cancel</button></div>
        </form>}
        <form className="model-code-create-form" onSubmit={event => void createCode(event)}>
          <h3>Create a Model Code under {selectedModel?.model_number || "this Product Model"}</h3>
          <label>New Model Code<input value={newCode} onChange={event => setNewCode(event.target.value)} required disabled={!writeEnabled || savingCode} autoComplete="off"/></label>
          <label>Description<input value={newDescription} onChange={event => setNewDescription(event.target.value)} required disabled={!writeEnabled || savingCode} autoComplete="off"/></label>
          <button type="submit" className="button primary" disabled={!writeEnabled || savingCode || !newCode.trim() || !newDescription.trim()}>{savingCode ? "Saving…" : "Create Model Code"}</button>
        </form>
      </>}
      {managementError && <p role="alert" className="model-code-management-error">{managementError}</p>}
      {managementNotice && <p role="status" className="model-code-management-notice">{managementNotice}</p>}
    </section>

    <section className="model-code-records" aria-label="Stored Product Model Code records">
      <h2>Stored source records</h2>
      <div className="normalized-filters">
        <label>Product Model Code<select disabled={!model} value={code} onChange={event => { reset(); setCode(event.target.value); }}><option value="">Select Code</option>{visibleCodes.map(item => <option key={item.id} value={item.id}>{item.code} · {item.description || "No description"}</option>)}</select></label>
      </div>
      {busy && <p role="status">Loading stored records…</p>}{error && <p role="alert">{error}</p>}
      {data && <>
        <p>{data.status === "stored" ? "Stored Grist records" : data.status.replaceAll("_", " ")} · Read {data.readAt}</p>
        <p>Shared source-file evidence for {data.code.code} · {data.code.description || "No description"}. Model Code configuration requires review; costing authority is unchanged.</p>
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
    </section>
  </main>;
}
