import { useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type ReactNode } from "react";
import { Archive, ArrowLeft, Check, ChevronDown, ChevronRight, FileText, History, LoaderCircle, PackagePlus, Plus, Search, ShoppingCart, Tags, Wrench } from "lucide-react";
import { api } from "./api";
import "./parts.css";

type Scope = "global" | "product" | "product_model" | "model_code";
type Target = { id: string; label: string; parentId?: string; shortcode?: string | null };
type ScopeOption = { id: Scope; label: string; target?: Target; targets?: Target[] };
type Part = { id: string; partNumber: string | null; name: string; description: string; variant: string; scope: string; scopeTargetId: string | null; scopeTarget: string; shortcode?: string; engineeringRevision: string | null; status: string; metadataVersion: number | null; aliases: string[]; legacy: boolean; createdAt?: string; actor?: string; reason?: string; legacyRevisionValues?: string[]; partKey?: string; ambiguousName?: boolean; metadataHistory?: any[]; lifecycleHistory?: any[] };
type Preview = { name: string; available: boolean; collision: { source: string; id: string; number: string | null; status: string }[]; before?: any; after?: any; affectedSourceAssignments?: any[]; usageFingerprint?: string };
type Attempt = { key: string; payload: Record<string, unknown> };
type Register = { items: Part[]; legacyItems: Part[] };
type TreeEntry = { id: string; parentId?: string; label: string; depth: number; kind: "group" | "target" | "part"; part?: Part; count?: number; expanded?: boolean; hasChildren?: boolean };

function restoredCreateAttempt(): Attempt | null {
  try {
    const value = JSON.parse(sessionStorage.getItem("parts:create-attempt") || "null");
    return value && typeof value.key === "string" && value.payload ? value as Attempt : null;
  } catch { return null; }
}

const scopeLabels: Record<Scope, string> = { global: "Global", product: "Product", product_model: "Product Model", model_code: "Model Code" };
const emptyRegister: Register = { items: [], legacyItems: [] };

export function PartsView({ mappingReturn = null, onReturnToMapping }: { mappingReturn?: { path: string; groupKey: string; partId: string | null } | null; onReturnToMapping?: (partId: string | null) => void }) {
  const [scopes, setScopes] = useState<ScopeOption[]>([]);
  const [register, setRegister] = useState<Register>(emptyRegister);
  const [query, setQuery] = useState(() => sessionStorage.getItem("parts:search") || "");
  const [expandedIds, setExpandedIds] = useState<Record<string, boolean>>(() => {
    try { return JSON.parse(sessionStorage.getItem("parts:expanded") || "{}"); } catch { return {}; }
  });
  const [createAttempt, setCreateAttempt] = useState<Attempt | null>(() => restoredCreateAttempt());
  const [selectedId, setSelectedId] = useState<string | null>(() => {
    const route = window.location.hash.match(/^#parts\/(.+)$/);
    return route ? decodeURIComponent(route[1]) : sessionStorage.getItem("parts:selected");
  });
  const [mode, setMode] = useState<"selected" | "create" | "edit">(() => createAttempt ? "create" : "selected");
  const [fullDetails, setFullDetails] = useState(Boolean(window.location.hash.match(/^#parts\//)));
  const [details, setDetails] = useState<any>(null);
  const [scope, setScope] = useState<Scope>(() => (createAttempt?.payload.scope as Scope) || "global");
  const [targetId, setTargetId] = useState(() => String(createAttempt?.payload.targetId || "global"));
  const [description, setDescription] = useState(() => String(createAttempt?.payload.description || ""));
  const [variant, setVariant] = useState(() => String(createAttempt?.payload.variant || ""));
  const [reason, setReason] = useState(() => String(createAttempt?.payload.reason || ""));
  const [filterProductId, setFilterProductId] = useState(() => String(createAttempt?.payload.selectedProductId || ""));
  const [filterModelId, setFilterModelId] = useState(() => String(createAttempt?.payload.selectedProductModelId || ""));
  const [intendedCodeIds, setIntendedCodeIds] = useState<string[]>(() => Array.isArray(createAttempt?.payload.intendedModelCodeIds) ? (createAttempt?.payload.intendedModelCodeIds as unknown[]).map(String) : []);
  const [codeSearch, setCodeSearch] = useState("");
  const [nameAnchorTouched, setNameAnchorTouched] = useState(Boolean(createAttempt));
  const [namePreview, setNamePreview] = useState<Preview | null>(null);
  const [shortcode, setShortcode] = useState("");
  const [shortcodeReason, setShortcodeReason] = useState("");
  const [editShortcode, setEditShortcode] = useState(false);
  const [retireReason, setRetireReason] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [shortcodeAttempt, setShortcodeAttempt] = useState<Attempt | null>(null);
  const [metadataAttempt, setMetadataAttempt] = useState<Attempt | null>(null);
  const [retireAttempt, setRetireAttempt] = useState<Attempt | null>(null);
  const [previewRefresh, setPreviewRefresh] = useState(0);
  const previewGeneration = useRef(0);
  const detailsGeneration = useRef(0);

  async function refresh(search = query) {
    setLoading(true);
    setError("");
    try {
      const [targets, items] = await Promise.all([api.partScopeTargets(), api.parts(search)]);
      setScopes(targets.scopes || []);
      setRegister(items);
    } catch (cause) { setError(String(cause)); }
    finally { setLoading(false); }
  }

  useEffect(() => { void refresh(); }, []);
  useEffect(() => { sessionStorage.setItem("parts:search", query); }, [query]);
  useEffect(() => { sessionStorage.setItem("parts:expanded", JSON.stringify(expandedIds)); }, [expandedIds]);
  useEffect(() => { if (createAttempt) sessionStorage.setItem("parts:create-attempt", JSON.stringify(createAttempt)); else sessionStorage.removeItem("parts:create-attempt"); }, [createAttempt]);
  useEffect(() => { if (selectedId) sessionStorage.setItem("parts:selected", selectedId); else sessionStorage.removeItem("parts:selected"); }, [selectedId]);
  useEffect(() => {
    if (!mappingReturn?.partId) return;
    setSelectedId(mappingReturn.partId);
    setMode("selected");
    setFullDetails(true);
    history.replaceState(null, "", `#parts/${encodeURIComponent(mappingReturn.partId)}`);
  }, [mappingReturn?.path, mappingReturn?.partId]);

  const scopeOption = scopes.find(item => item.id === scope);
  const targets = scope === "global" ? (scopeOption?.target ? [scopeOption.target] : []) : (scopeOption?.targets || []);
  const target = targets.find(item => item.id === targetId);
  const shortcodeMissing = Boolean(scopeOption && target && !target.shortcode);
  const productTargets = scopes.find(item => item.id === "product")?.targets || [];
  const modelTargets = scopes.find(item => item.id === "product_model")?.targets || [];
  const codeTargets = scopes.find(item => item.id === "model_code")?.targets || [];

  useEffect(() => {
    setTargetId(current => scope === "global" ? "global" : !targets.length || targets.some(item => item.id === current) ? current : "");
    setNamePreview(null);
  }, [scope, scopes]);

  useEffect(() => {
    if (mode !== "create" && mode !== "edit") return;
    if (!targetId || !description.trim() || !target?.shortcode) { setNamePreview(null); return; }
    const generation = ++previewGeneration.current;
    const timer = window.setTimeout(async () => {
      try {
        const result = mode === "edit" && selectedId
          ? await api.partMetadataPreview(selectedId, scope, targetId, description, variant)
          : await api.partNamePreview(scope, targetId, description, variant);
        if (generation === previewGeneration.current) setNamePreview(mode === "edit" ? { ...result, name: result.after.name } : result);
      } catch (cause) {
        if (generation === previewGeneration.current) { setNamePreview(null); setError(String(cause)); }
      }
    }, 250);
    return () => window.clearTimeout(timer);
  }, [mode, scope, targetId, target?.shortcode, description, variant, selectedId, previewRefresh]);

  useEffect(() => {
    if (!selectedId || mode !== "selected") { setDetails(null); return; }
    const generation = ++detailsGeneration.current;
    setDetails(null);
    api.partDetails(selectedId).then(value => { if (generation === detailsGeneration.current) { setDetails(value); setRegister(old => ({ ...old, items: old.items.map(part => part.id === selectedId ? { ...part, ...value.part } : part) })); } })
      .catch(cause => { if (generation === detailsGeneration.current) setError(String(cause)); });
    return () => { ++detailsGeneration.current; };
  }, [selectedId, mode]);

  useEffect(() => {
    const onPopState = () => {
      const route = window.location.hash.match(/^#parts\/(.+)$/);
      setSelectedId(route ? decodeURIComponent(route[1]) : sessionStorage.getItem("parts:selected"));
      setFullDetails(Boolean(route));
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const allParts = useMemo(() => [...register.items, ...register.legacyItems], [register]);
  const tree = useMemo(() => buildPartsTree(scopes, allParts, expandedIds), [scopes, allParts, expandedIds]);
  const selected = allParts.find(part => part.id === selectedId) || null;

  function toggleTree(entry: TreeEntry) {
    if (!entry.hasChildren) return;
    setExpandedIds(old => ({ ...old, [entry.id]: !(old[entry.id] ?? Boolean(entry.expanded)) }));
  }

  function treeKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number, entry: TreeEntry) {
    const focus = (next: number) => document.querySelector<HTMLButtonElement>(`[data-parts-tree-index="${next}"]`)?.focus();
    if (event.key === "ArrowDown") { event.preventDefault(); focus(Math.min(tree.length - 1, index + 1)); }
    else if (event.key === "ArrowUp") { event.preventDefault(); focus(Math.max(0, index - 1)); }
    else if (event.key === "Home") { event.preventDefault(); focus(0); }
    else if (event.key === "End") { event.preventDefault(); focus(tree.length - 1); }
    else if (event.key === "ArrowRight" && entry.hasChildren) { event.preventDefault(); if (!entry.expanded) toggleTree(entry); else focus(Math.min(tree.length - 1, index + 1)); }
    else if (event.key === "ArrowLeft") { event.preventDefault(); if (entry.hasChildren && entry.expanded) toggleTree(entry); else if (entry.parentId) focus(Math.max(0, tree.findIndex(row => row.id === entry.parentId))); }
    else if ((event.key === "Enter" || event.key === " ") && entry.part) { event.preventDefault(); selectPart(entry.part); }
  }

  async function reloadSelectedDetails() {
    if (!selectedId) return;
    const value = await api.partDetails(selectedId);
    setDetails(value);
    setRegister(old => ({ ...old, items: old.items.map(part => part.id === selectedId ? { ...part, ...value.part } : part) }));
    await refresh();
  }

  function startCreate() {
    setError(""); setNotice(""); setSelectedId(null); setDetails(null); setMode("create"); setFullDetails(false);
    setDescription(""); setVariant(""); setReason(""); setScope("global"); setTargetId("global"); setNamePreview(null);
    setFilterProductId(""); setFilterModelId(""); setIntendedCodeIds([]); setCodeSearch(""); setNameAnchorTouched(false);
    setCreateAttempt(null); setShortcodeAttempt(null); setMetadataAttempt(null); setRetireAttempt(null); setRetireReason(""); setEditShortcode(false); setShortcodeReason(""); setShortcode("");
    if (window.location.hash.startsWith("#parts/")) history.pushState(null, "", "#parts");
  }

  function selectPart(part: Part) {
    setSelectedId(part.id); setMode("selected"); setFullDetails(false); setError(""); setNotice(""); setRetireReason(""); setRetireAttempt(null);
    if (window.location.hash.startsWith("#parts/")) history.pushState(null, "", "#parts");
  }

  function openFullDetails() {
    if (!selectedId) return;
    history.pushState(null, "", `#parts/${encodeURIComponent(selectedId)}`);
    setFullDetails(true);
  }

  function backToSummary() {
    history.pushState(null, "", "#parts");
    setFullDetails(false);
  }

  function openNestedPart(part: Part) {
    history.pushState(null, "", `#parts/${encodeURIComponent(part.id)}`);
    setSelectedId(part.id); setMode("selected"); setFullDetails(true); setError(""); setNotice("");
  }

  async function saveShortcode() {
    const attempt = shortcodeAttempt || { key: crypto.randomUUID(), payload: { scope, targetId, shortcode, reason: shortcodeReason } };
    setShortcodeAttempt(attempt); setBusy(true); setError("");
    try {
      await api.maintainPartShortcode(attempt.payload, attempt.key);
      setShortcodeAttempt(null); setShortcodeReason(""); setEditShortcode(false);
      await refresh(); setNotice("Scope shortcode saved with its own audit history.");
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }

  async function savePart() {
    if (!createAttempt && !namePreview?.available) return;
    const attempt = createAttempt || { key: crypto.randomUUID(), payload: { scope, targetId, description, variant, expectedName: namePreview?.name || "", reason,
      selectedProductId: filterProductId || null, selectedProductModelId: filterModelId || null, intendedModelCodeIds: [...intendedCodeIds] } };
    setCreateAttempt(attempt); setBusy(true); setError("");
    try {
      const result = await api.createManagedPart(attempt.payload, attempt.key);
      setCreateAttempt(null); setSelectedId(result.part.id); setMode("selected"); setFullDetails(false);
      setNotice(`Created ${result.part.partNumber} · ${result.part.name} · Rev A.`);
      await refresh(""); setQuery("");
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }

  function changeFilterProduct(value: string) {
    setFilterProductId(value); setFilterModelId(""); setCodeSearch("");
    if (!nameAnchorTouched) { setScope(value ? "product" : "global"); setTargetId(value || "global"); }
  }

  function changeFilterModel(value: string) {
    setFilterModelId(value); setCodeSearch("");
    if (!nameAnchorTouched) {
      if (value) { setScope("product_model"); setTargetId(value); }
      else if (filterProductId) { setScope("product"); setTargetId(filterProductId); }
      else { setScope("global"); setTargetId("global"); }
    }
  }

  async function openCollision(collision: Preview["collision"][number]) {
    const id = collision.source === "legacy" ? `legacy:${collision.id}` : collision.id;
    let existing = allParts.find(part => part.id === id);
    if (!existing) {
      try {
        const result = await api.parts(namePreview?.name || query);
        setRegister(result);
        existing = [...result.items, ...result.legacyItems].find((part: Part) => part.id === id);
      } catch (cause) { setError(String(cause)); return; }
    }
    if (existing) selectPart(existing);
    else setError("The matching legacy Part name is ambiguous. Review the legacy records before creating another Part with this name.");
  }

  function beginEdit() {
    if (!selected || selected.legacy) return;
    setScope(selected.scope as Scope); setTargetId(selected.scopeTargetId || ""); setDescription(selected.description); setVariant(selected.variant);
    setReason(""); setNamePreview(null); setMode("edit"); setFullDetails(false); setError("");
  }

  async function saveMetadata() {
    if (!selected || selected.legacy || !namePreview?.available) return;
    const attempt = metadataAttempt || { key: crypto.randomUUID(), payload: { scope, targetId, description, variant, expectedName: namePreview.name, expectedVersion: selected.metadataVersion, expectedUsageFingerprint: namePreview.usageFingerprint, reason } };
    setMetadataAttempt(attempt); setBusy(true); setError("");
    try {
      const result = await api.updatePartMetadata(selected.id, attempt.payload, attempt.key);
      setMetadataAttempt(null); setMode("selected"); setSelectedId(result.part.id); setNotice("Part name/scope metadata saved. Part number and Rev A were preserved.");
      await refresh();
    } catch (cause) {
      const message = String(cause); setError(message);
      if (message.includes("PART_USAGE_STALE") || message.includes("PART_METADATA_STALE") || message.includes("PART_NAME_PREVIEW_STALE")) {
        setMetadataAttempt(null); setPreviewRefresh(value => value + 1);
      }
    }
    finally { setBusy(false); }
  }

  async function retire() {
    if (!selected || selected.legacy || !retireReason.trim()) return;
    const attempt = retireAttempt || { key: crypto.randomUUID(), payload: { expectedVersion: selected.metadataVersion, reason: retireReason } };
    setRetireAttempt(attempt); setBusy(true); setError("");
    try {
      await api.retirePart(selected.id, attempt.payload, attempt.key);
      setRetireAttempt(null); setRetireReason(""); setNotice(`${selected.partNumber} retired. Its identity and number remain searchable.`);
      const updated = await api.partDetails(selected.id);
      setDetails(updated);
      setRegister(old => ({ ...old, items: old.items.map(part => part.id === selected.id ? { ...part, ...updated.part } : part) }));
      await refresh();
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }

  const formLocked = busy || Boolean(createAttempt || shortcodeAttempt || metadataAttempt);
  const form = <section className="parts-form-card">
    <div><span className="eyebrow">{mode === "edit" ? "Reviewed metadata change" : "Guided creation"}</span><h2>{mode === "edit" ? "Change Part name or scope" : "Create a Part"}</h2><p>{mode === "edit" ? "The physical design identity stays fixed. This change creates a new metadata version." : "Create a stable Part identity first. Source assignments are saved separately in Part Mapping."}</p></div>
    {mode === "create" && <div className="parts-cascade-card"><h3>Intended Model Codes</h3><p>These codes record intended sharing only. They do not add this Part to configurations, set quantities or create costing history.</p><CascadeCodePicker scopes={scopes} productId={filterProductId} modelId={filterModelId} selectedIds={intendedCodeIds}
      onProductChange={changeFilterProduct} onModelChange={changeFilterModel} onSelectedChange={setIntendedCodeIds} disabled={formLocked} restrictSelectionToContext/></div>}
    <label>Name derives from<select disabled={formLocked} value={scope} onChange={event => { setNameAnchorTouched(true); setScope(event.target.value as Scope); }}>{scopes.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}</select></label>
    {scope !== "global" && <label>Scope target<select disabled={formLocked} value={targetId} onChange={event => setTargetId(event.target.value)}><option value="">Choose {scopeLabels[scope]}</option>{targets.map(item => <option key={item.id} value={item.id}>{item.label}{item.shortcode ? ` · ${item.shortcode}` : " · shortcode needed"}</option>)}</select></label>}
    {scope === "global" && <p className="parts-target-line">Safari Manufacturing · Global scope</p>}
    {target && <div className="parts-shortcode-box"><div><strong>Maintained shortcode</strong><span>{target.shortcode || "Not set for this target"}</span></div>
      {(!target.shortcode || editShortcode) && <div className="parts-shortcode-edit"><label>Shortcode<input value={shortcode} maxLength={20} onChange={event => setShortcode(event.target.value.toUpperCase())} placeholder="Letters and digits" disabled={formLocked}/></label><label>Why is this shortcode being set?<input value={shortcodeReason} onChange={event => setShortcodeReason(event.target.value)} disabled={formLocked}/></label><button className="button" disabled={formLocked || !shortcode.trim() || !shortcodeReason.trim()} onClick={() => void saveShortcode()}>{shortcodeAttempt ? "Retry shortcode save" : "Save shortcode"}</button></div>}
      {target.shortcode && !editShortcode && <button className="button ghost" disabled={formLocked} onClick={() => { setShortcode(target.shortcode || ""); setEditShortcode(true); }}>Review shortcode</button>}
    </div>}
    {shortcodeMissing && <p className="parts-validation warning">Resolve the missing shortcode before generating a Part name. The app will not guess it.</p>}
    <label>What is the Part called?<input value={description} maxLength={120} onChange={event => setDescription(event.target.value)} placeholder="For example, Chassis" disabled={formLocked}/></label>
    <label>What distinguishes this design?<input value={variant} maxLength={120} onChange={event => setVariant(event.target.value)} placeholder="Optional, such as Standard or Reinforced" disabled={formLocked || mode === "edit"}/>{mode === "edit" && <small>Design variants are fixed for this Part. Create a new Part for a distinct physical design.</small>}</label>
    {namePreview && <div className={`parts-name-preview ${namePreview.available ? "available" : "collision"}`} role="status"><span>{mode === "edit" ? "Before / after · server validated" : "Generated name · server validated"}</span>{mode === "edit" && namePreview.before && <small>{namePreview.before.name} →</small>}<strong>{namePreview.name}</strong>{mode === "edit" && <p>{namePreview.affectedSourceAssignments?.length || 0} saved source assignment row(s) reference this Part; they retain the same Part identity and are included in the stale-preview check. Per-code BOM uses are not available in this source-mapping history.</p>}{!namePreview.available && <><p>{namePreview.collision.some(item => item.source === "legacy") ? "A legacy Part already uses this name. Review its identity or add a meaningful distinction." : "A current or historical Part name already uses this name. Select the existing Part or add a meaningful distinction."}</p><div className="parts-collision-list">{namePreview.collision.map((collision, index) => <button key={`${collision.source}:${collision.id}:${index}`} className="button" onClick={() => void openCollision(collision)}>Open {collision.number ? `${collision.number} · ` : "legacy · "}{collision.source === "legacy" ? "existing Part" : "matching Part"}</button>)}</div></>}</div>}
    {mode === "create" && <div className="parts-readonly-facts"><span>Part number <strong>Allocated on Save</strong></span><span>Engineering revision <strong>Rev A</strong></span></div>}
    {mode === "edit" && selected && <div className="parts-readonly-facts"><span>Permanent number <strong>{selected.partNumber}</strong></span><span>Engineering revision <strong>Rev A · fixed until CR flow</strong></span></div>}
    <label>Why is this {mode === "edit" ? "metadata change" : "Part"} needed?<textarea value={reason} onChange={event => setReason(event.target.value)} rows={3} maxLength={2000} disabled={formLocked}/></label>
    <div className="parts-form-actions"><button className="button" disabled={busy || Boolean(createAttempt || metadataAttempt)} onClick={() => { setMode("selected"); setCreateAttempt(null); setMetadataAttempt(null); setError(""); }}>{mode === "edit" ? "Cancel" : "Back to Parts"}</button><button className="button primary" disabled={busy || (!createAttempt && (!namePreview?.available || !reason.trim()))} onClick={() => void (mode === "edit" ? saveMetadata() : savePart())}>{createAttempt || metadataAttempt ? "Retry same request" : mode === "edit" ? "Save metadata version" : "Save Part"}</button></div>
  </section>;

  return <main className="parts-page">
    <aside className="parts-explorer">
      <div className="panel-heading"><div><span>Parts explorer</span><small>Stable identity and history</small></div><Tags size={17}/></div>
      <label className="tree-search"><Search size={14}/><input aria-label="Search Parts by number, name or alias" value={query} onChange={event => { setQuery(event.target.value); void refresh(event.target.value); }} placeholder="Search number, name or alias"/></label>
      <button className="parts-new-button" onClick={startCreate}><Plus size={15}/> New Part</button>
      {loading && <div className="parts-loading"><LoaderCircle className="spin" size={16}/>Loading Parts…</div>}
      <div className="parts-list-scroll" role="tree" aria-label="Parts by sharing scope and target">
        {tree.map((entry, index) => <button key={entry.id} type="button" role="treeitem" aria-level={entry.depth + 1}
          aria-expanded={entry.hasChildren ? Boolean(entry.expanded) : undefined} aria-selected={entry.part ? selectedId === entry.part.id && mode === "selected" : undefined}
          data-parts-tree-index={index} className={`parts-tree-row ${entry.kind} ${entry.part && selectedId === entry.part.id && mode === "selected" ? "selected" : ""}`}
          style={{ "--tree-depth": entry.depth } as CSSProperties} onKeyDown={event => treeKeyDown(event, index, entry)}
          onClick={() => entry.part ? selectPart(entry.part) : toggleTree(entry)}>
          {entry.hasChildren ? (entry.expanded ? <ChevronDown size={13}/> : <ChevronRight size={13}/>) : <span className="parts-tree-spacer"/>}
          {entry.part ? <span className="parts-tree-part"><span className="parts-row-number">{entry.part.partNumber || "Legacy · unallocated"}</span><strong>{entry.part.name}</strong><small>{entry.part.legacy ? "Legacy · revision unverified" : `Rev A · ${entry.part.status}`}</small></span>
            : <><strong>{entry.label}</strong><small>{entry.count ?? 0}</small></>}
        </button>)}
        {!loading && !allParts.length && <div className="empty"><strong>No Parts found</strong><span>Try another search or create a Part.</span></div>}
      </div>
      <div className="panel-footer"><Archive size={15}/><div><strong>Permanent identity</strong><span>Retired numbers remain reserved</span></div></div>
    </aside>
    <section className="parts-work-panel">
      <div className="parts-work-heading"><div><span className="eyebrow">Safari Manufacturing</span><h1>Parts</h1><p>Managed identity, generated names, metadata history and Rev A.</p></div><div className="workbench-actions">{mappingReturn && <button className="button" onClick={() => onReturnToMapping?.(selected && !selected.legacy ? selected.id : mappingReturn.partId)}><ArrowLeft size={14}/>{selected && !selected.legacy ? "Use this Part in mapping" : "Return to Part Mapping"}</button>}<button className="button ghost" onClick={() => void refresh()} disabled={loading}><LoaderCircle size={14}/> Refresh</button></div></div>
      {error && <div className="parts-error" role="alert">{error}{(createAttempt || shortcodeAttempt || metadataAttempt || retireAttempt) && <small>Retry uses the original request key and payload.</small>}</div>}
      {notice && <p className="parts-notice" role="status"><Check size={14}/>{notice}</p>}
      {mode === "create" || mode === "edit" ? form : selected ? <>
        {fullDetails && <button className="parts-back-link" onClick={backToSummary}><ArrowLeft size={15}/> Back to Parts</button>}
        <div className="parts-selected-card"><div><span className="eyebrow">{selected.legacy ? "Legacy Part record" : `${scopeLabels[selected.scope as Scope] || selected.scope} · ${selected.scopeTarget}`}</span><h2>{selected.name}</h2><p>{selected.legacy ? "This name-derived identity has not been migrated or allocated a permanent number." : `${selected.description}${selected.variant ? ` · ${selected.variant}` : ""}`}</p></div><div className="parts-identifiers"><strong>{selected.partNumber || "Unallocated"}</strong><span>{selected.legacy ? "Engineering revision unverified" : "Engineering revision · A"}</span></div></div>
        {selected.legacy ? <div className="parts-legacy-callout"><strong>Legacy identity retained</strong><p>Existing Grist references and history remain unchanged. A numeric value in the legacy PartRevision table is not treated as evidence of CR approval. Migration requires a separately reviewed plan.</p>{selected.legacyRevisionValues?.length ? <small>Recorded numeric values: {selected.legacyRevisionValues.join(", ")} · unverified</small> : null}</div> : <>
          <div className="parts-summary-actions"><button className="button" onClick={openFullDetails}><FileText size={14}/> Open full Part details</button><button className="button" onClick={beginEdit} disabled={selected.status === "retired"}>Review name/scope change</button></div>
          {!fullDetails && <div className="parts-section-card"><h3>Overview</h3><dl><div><dt>Part number</dt><dd>{selected.partNumber}</dd></div><div><dt>Current name</dt><dd>{selected.name}</dd></div><div><dt>Sharing scope</dt><dd>{scopeLabels[selected.scope as Scope]} · {selected.scopeTarget}</dd></div><div><dt>Design variant</dt><dd>{selected.variant || "Not supplied"}</dd></div><div><dt>Engineering revision</dt><dd>Rev A · changes require the approved CR process</dd></div><div><dt>Metadata version</dt><dd>{selected.metadataVersion}</dd></div><div><dt>Status</dt><dd>{selected.status}</dd></div></dl></div>}
          {fullDetails && details ? <>
            <div className="parts-detail-tabs" aria-label="Part detail sections">{["Overview", "Intended sharing", "Process lines", "Components", "Purchase details", "Drawings", "Used in configurations", "History"].map(label => { const id = label.toLowerCase().replaceAll(" ", "-"); return <button key={label} type="button" onClick={() => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" })}>{label}</button>; })}</div>
            <div id="overview" className="parts-section-card"><h3>Overview</h3><dl><div><dt>Part number</dt><dd>{selected.partNumber}</dd></div><div><dt>Generated name</dt><dd>{selected.name}</dd></div><div><dt>Scope target</dt><dd>{scopeLabels[selected.scope as Scope]} · {selected.scopeTarget}</dd></div><div><dt>Engineering revision</dt><dd>Rev A</dd></div><div><dt>Metadata version</dt><dd>{selected.metadataVersion}</dd></div><div><dt>Created by</dt><dd>{selected.actor} · {selected.createdAt}</dd></div><div><dt>Reason</dt><dd>{selected.reason}</dd></div></dl><button className="button" onClick={beginEdit}>Review name/scope change</button></div>
            <IntendedSharingSection part={details.part} state={details.intendedSharing} scopes={scopes} onSaved={reloadSelectedDetails}/>
            <ProcessLineSection partId={selected.id} state={details.processLines} onSaved={reloadSelectedDetails}/>
            <ComponentSection partId={selected.id} currentPart={selected} parts={allParts} state={details.components} partState={details.part} onOpen={openNestedPart} onSaved={reloadSelectedDetails}/>
            <PurchaseDetailSection partId={selected.id} state={details.purchases} onSaved={reloadSelectedDetails}/>
            <DrawingSection partId={selected.id} state={details.drawings} onSaved={reloadSelectedDetails}/>
            <UsedInConfigurationsSection state={details.usedIn} intended={details.intendedSharing?.items || []}/>
            <div id="history" className="parts-section-card"><h3><History size={15}/> History</h3><h4>Engineering baseline</h4><p>Rev A · {details.part.compositionStatus || "draft"}. Finalized definitions are physically locked; later engineering revisions require the approved CR process.</p>{details.part.compositionStatus === "draft" && <FinalizeBaseline partId={selected.id} onSaved={reloadSelectedDetails}/>}<h4>Name and scope metadata</h4>{selected.metadataHistory?.map((item: any) => <article className="parts-history-item" key={`${item.part_id}:${item.version}`}><strong>Metadata v{item.version} · {item.display_name}</strong><span>{scopeLabels[item.scope_type as Scope] || item.scope_type} · {item.target_label} · {item.shortcode} · {item.occurred_at}</span><p>{item.description}{item.variant ? ` · ${item.variant}` : ""} · {item.actor} · {item.reason}</p></article>)}{selected.aliases.length > 0 && <p>Previous names searchable as aliases: {selected.aliases.join(" · ")}</p>}<h4>Lifecycle</h4>{details.part.lifecycleHistory?.length ? details.part.lifecycleHistory.map((item: any) => <article className="parts-history-item" key={item.event_id}><strong>{item.event_type} · {item.status}</strong><span>{item.occurred_at}</span><p>{item.actor} · {item.reason}</p></article>) : <p>No lifecycle changes recorded.</p>}<h4>Source and mapping audit</h4>{details.mappingHistory?.length ? details.mappingHistory.map((item: any, index: number) => <article className="parts-history-item" key={`${item.RequestKey}:${index}`}><strong>{item.SheetName} · row {item.SourceRow} · source mapping v{item.Version}</strong><span>{item.SourceHash} · association v{item.AssociationVersion} · {item.OccurredAt}</span><p>{item.Actor} · {item.Reason}</p></article>) : <p>No source mapping history is linked to this stable Part identity yet.</p>}{selected.status === "active" && <div className="parts-retire"><label>Reason for retirement<input value={retireReason} onChange={event => setRetireReason(event.target.value)} disabled={busy}/></label><button className="button" disabled={busy || !retireReason.trim()} onClick={() => void retire()}>{retireAttempt ? "Retry retirement" : "Retire Part"}</button></div>}</div>
          </> : <div className="parts-details-placeholder"><strong>Part summary</strong><span>Open full details for process lines, drawings, explicit code use and history.</span></div>}
        </>}
      </> : mode === "selected" ? <div className="parts-welcome"><Tags size={25}/><h2>Select a Part</h2><p>Search by number, current name or previous alias, or start a new Part.</p><button className="button primary" onClick={startCreate}><Plus size={14}/> New Part</button></div> : null}
    </section>
  </main>;
}

function buildPartsTree(scopes: ScopeOption[], parts: Part[], expandedState: Record<string, boolean>): TreeEntry[] {
  const output: TreeEntry[] = [];
  const byScope = (scope: Scope) => parts.filter(part => !part.legacy && part.scope === scope);
  const legacyParts = parts.filter(part => part.legacy);
  const targetList = (scope: Scope) => scopes.find(item => item.id === scope)?.targets || [];
  const globalTarget = scopes.find(item => item.id === "global")?.target || { id: "global", label: "Safari Manufacturing" };
  const expandable = (id: string, label: string, depth: number, parentId: string | undefined, count: number, hasChildren: boolean, defaultOpen: boolean, kind: TreeEntry["kind"] = "group") => {
    const expanded = expandedState[id] ?? defaultOpen;
    output.push({ id, parentId, label, depth, count, kind, hasChildren, expanded });
    return expanded;
  };
  const appendParts = (items: Part[], parentId: string, depth: number) => {
    [...items].sort((a, b) => (a.partNumber || a.name).localeCompare(b.partNumber || b.name)).forEach(part =>
      output.push({ id: `part:${part.id}`, parentId, label: part.name, depth, kind: "part", part }));
  };
  const appendScopeRoot = (scope: Scope | "legacy", label: string, count: number, hasChildren: boolean, children: (rootId: string) => void) => {
    const rootId = `scope:${scope}`;
    if (expandable(rootId, label, 0, undefined, count, hasChildren, true)) children(rootId);
  };

  const globalParts = byScope("global");
  appendScopeRoot("global", "Global", globalParts.length, true, rootId => {
    const id = "target:global:global";
    if (expandable(id, globalTarget.label, 1, rootId, globalParts.length, globalParts.length > 0, true, "target")) appendParts(globalParts, id, 2);
  });

  const products = targetList("product");
  const productParts = byScope("product");
  appendScopeRoot("product", "Product", productParts.length, products.length > 0 || productParts.length > 0, rootId => {
    const known = new Set<string>();
    products.forEach(product => {
      known.add(product.id);
      const items = productParts.filter(part => part.scopeTargetId === product.id);
      const id = `target:product:${product.id}`;
      if (expandable(id, product.label, 1, rootId, items.length, items.length > 0, false, "target")) appendParts(items, id, 2);
    });
    const unmatched = productParts.filter(part => !known.has(part.scopeTargetId || ""));
    if (unmatched.length) {
      const id = "target:product:unmatched";
      if (expandable(id, "Unmatched / retired target", 1, rootId, unmatched.length, true, false, "target")) appendParts(unmatched, id, 2);
    }
  });

  const models = targetList("product_model");
  const modelParts = byScope("product_model");
  appendScopeRoot("product_model", "Product Model", modelParts.length, models.length > 0 || modelParts.length > 0, rootId => {
    const known = new Set(models.map(model => model.id));
    products.forEach(product => {
      const childModels = models.filter(model => model.parentId === product.id);
      if (!childModels.length) return;
        const parentId = `parent-model:${product.id}`;
        if (expandable(parentId, product.label, 1, rootId, childModels.reduce((sum, model) => sum + modelParts.filter(part => part.scopeTargetId === model.id).length, 0), true, false)) {
        childModels.forEach(model => {
          const items = modelParts.filter(part => part.scopeTargetId === model.id);
          const id = `target:product_model:${model.id}`;
          if (expandable(id, model.label, 2, parentId, items.length, items.length > 0, false, "target")) appendParts(items, id, 3);
        });
      }
    });
    const unmatched = modelParts.filter(part => !known.has(part.scopeTargetId || ""));
    if (unmatched.length) {
      const id = "target:product_model:unmatched";
      if (expandable(id, "Unmatched / retired target", 1, rootId, unmatched.length, true, false, "target")) appendParts(unmatched, id, 2);
    }
  });

  const codes = targetList("model_code");
  const codeParts = byScope("model_code");
  appendScopeRoot("model_code", "Model Code", codeParts.length, codes.length > 0 || codeParts.length > 0, rootId => {
    const known = new Set(codes.map(code => code.id));
    products.forEach(product => {
      const productModels = models.filter(model => model.parentId === product.id);
      if (!productModels.length) return;
      const productNode = `parent-code-product:${product.id}`;
      const underProduct = productModels.reduce((sum, model) => sum + codes.filter(code => code.parentId === model.id).reduce((s, code) => s + codeParts.filter(part => part.scopeTargetId === code.id).length, 0), 0);
      if (expandable(productNode, product.label, 1, rootId, underProduct, true, false)) {
        productModels.forEach(model => {
          const modelCodes = codes.filter(code => code.parentId === model.id);
          if (!modelCodes.length) return;
          const modelNode = `parent-code-model:${model.id}`;
          const count = modelCodes.reduce((sum, code) => sum + codeParts.filter(part => part.scopeTargetId === code.id).length, 0);
            if (expandable(modelNode, model.label, 2, productNode, count, true, false)) {
            modelCodes.forEach(code => {
              const items = codeParts.filter(part => part.scopeTargetId === code.id);
              const id = `target:model_code:${code.id}`;
              if (expandable(id, code.label, 3, modelNode, items.length, items.length > 0, false, "target")) appendParts(items, id, 4);
            });
          }
        });
      }
    });
    const unmatched = codeParts.filter(part => !known.has(part.scopeTargetId || ""));
    if (unmatched.length) {
      const id = "target:model_code:unmatched";
      if (expandable(id, "Unmatched / retired target", 1, rootId, unmatched.length, true, false, "target")) appendParts(unmatched, id, 2);
    }
  });

  appendScopeRoot("legacy", "Legacy / unallocated", legacyParts.length, legacyParts.length > 0, rootId => appendParts(legacyParts, rootId, 1));
  return output;
}

function FinalizeBaseline({ partId, onSaved }: { partId: string; onSaved: () => Promise<void> }) {
  const [reason, setReason] = useState("");
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function save() {
    const next = attempt || { key: crypto.randomUUID(), payload: { reason } };
    setAttempt(next); setBusy(true); setError("");
    try { await api.finalizePartRevision(partId, next.payload, next.key); setAttempt(null); setReason(""); await onSaved(); }
    catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }
  return <div className="parts-capture-row"><label>Finalization reason<input value={reason} disabled={busy || Boolean(attempt)} onChange={event => setReason(event.target.value)}/></label><button className="button primary" disabled={busy || (!reason.trim() && !attempt)} onClick={() => void save()}>{attempt ? "Retry finalization" : "Finalize Rev A definition"}</button>{error && <p className="parts-error">{error}</p>}</div>;
}

function ComponentSection({ partId, currentPart, parts, state, partState, onOpen, onSaved }: { partId: string; currentPart: Part; parts: Part[]; state: any; partState: any; onOpen: (part: Part) => void; onSaved: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [childPartId, setChildPartId] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [uom, setUom] = useState("each");
  const [sourcingRoute, setSourcingRoute] = useState("auto");
  const [reason, setReason] = useState("");
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    const next = attempt || { key: crypto.randomUUID(), payload: { childPartId, quantity: Number(quantity), uom, reason, sourcingRoute } };
    setAttempt(next); setBusy(true); setError("");
    try { await api.addPartComponent(partId, next.payload, next.key); setAttempt(null); setOpen(false); setReason(""); await onSaved(); }
    catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }
  return <section id="components" className="parts-section-card"><h3><PackagePlus size={15}/> Components</h3>
    {state?.items?.length ? <div className="parts-linked-items">{state.items.map((item: any) => <article key={item.componentKey}><button className="parts-child-link" onClick={() => { const child = parts.find(part => part.id === item.childPartId); if (child) onOpen(child); }}><strong>{item.childPartNumber} · {item.childName}</strong></button><span>{item.quantity} {item.uom} · Rev {item.childRevisionLabel || "A"} · {item.sourcingRoute === "make" ? "Manufacture" : item.sourcingRoute === "buy" ? "Purchase" : "Configured route"}</span></article>)}</div> : <p>No child Parts are linked. Assemblies and purchased children are optional.</p>}
    {partState?.compositionStatus === "draft" && <><button className="button" onClick={() => setOpen(value => !value)}>{open ? "Close component form" : "Add child Part"}</button>{open && <div className="parts-capture-grid"><label>Child Part<select value={childPartId} disabled={busy || Boolean(attempt)} onChange={event => setChildPartId(event.target.value)}><option value="">Choose a canonical Part</option>{parts.filter(part => !part.legacy && part.id !== currentPart.id && part.status !== "retired").map(part => <option key={part.id} value={part.id}>{part.partNumber} · {part.name}</option>)}</select></label><label>Quantity per parent<input type="number" min="0.000001" step="any" value={quantity} disabled={busy || Boolean(attempt)} onChange={event => setQuantity(event.target.value)}/></label><label>Unit basis<input value={uom} disabled={busy || Boolean(attempt)} onChange={event => setUom(event.target.value)}/></label><label>Sourcing route<select value={sourcingRoute} disabled={busy || Boolean(attempt)} onChange={event => setSourcingRoute(event.target.value)}><option value="auto">Use configured Part route</option><option value="make">Manufacture this occurrence</option><option value="buy">Purchase this occurrence</option></select></label><label>Reason<input value={reason} disabled={busy || Boolean(attempt)} onChange={event => setReason(event.target.value)}/></label><button className="button primary" disabled={busy || (!childPartId && !attempt) || (!reason.trim() && !attempt)} onClick={() => void save()}>{attempt ? "Retry same component" : "Save child Part"}</button>{error && <p className="parts-error">{error}</p>}</div>}</>}
  </section>;
}

function ProcessLineSection({ partId, state, onSaved }: { partId: string; state: any; onSaved: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<any[]>([]);
  const [selectedLine, setSelectedLine] = useState<any>(null);
  const [quantity, setQuantity] = useState("1");
  const [uom, setUom] = useState("each");
  const [reason, setReason] = useState("");
  const [reassign, setReassign] = useState(false);
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function searchLines(value: string) {
    setQuery(value);
    try { const result = await api.partLineCandidates(value); setCandidates(result.items || []); } catch (cause) { setError(String(cause)); }
  }
  async function save() {
    if (!selectedLine) return;
    const next = attempt || { key: crypto.randomUUID(), payload: { lineMasterId: selectedLine.lineMasterId, lineRevisionId: selectedLine.lineRevisionId,
      quantity: Number(quantity), uom, reason, reassignOwner: reassign, expectedOwnerId: selectedLine.currentOwnerRecordId } };
    setAttempt(next); setBusy(true); setError("");
    try { await api.linkPartProcessLine(partId, next.payload, next.key); setAttempt(null); setSelectedLine(null); setOpen(false); setReason(""); await onSaved(); }
    catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }
  return <section id="process-lines" className="parts-section-card"><h3><Wrench size={15}/> Process lines</h3>
    {state?.items?.length ? <div className="parts-linked-items">{state.items.map((item: any, index: number) => <article key={item.partRevisionLine?.RevisionLineKey || index}><div><strong>{item.processType || "Process line"} · {item.sourcePartName || `Line ${item.lineMasterId}`}</strong><small>{item.quantityPerPart} {item.quantityUOM} · exact line revision {item.lineRevisionId}</small>{item.details?.map((detail: any, detailIndex: number) => <small key={detailIndex}>{detail.ItemName || detail.MaterialDisplayName || detail.ProcessOperation || detail.WorkCenter || detail.ItemName || "Typed process detail"} · qty {detail.Quantity ?? "—"} · weight {detail.WeightKg ?? detail.PartWeightKg ?? "—"} kg</small>)}</div><span>{item.lineStatus || "pinned"}</span></article>)}</div> : <p>No process requirements are pinned to this Part. MCL, Toolshop and CNC are all optional.</p>}
    <button className="button" onClick={() => { setOpen(value => !value); if (!open && !candidates.length) void searchLines(""); }}>{open ? "Close line picker" : "Review and link a process line"}</button>
    {open && <div className="parts-capture-grid"><label>Search existing lines<input value={query} disabled={busy || Boolean(attempt)} onChange={event => void searchLines(event.target.value)}/></label><label>Exact LineMaster and revision<select value={selectedLine ? `${selectedLine.lineMasterId}:${selectedLine.lineRevisionId}` : ""} disabled={busy || Boolean(attempt)} onChange={event => setSelectedLine(candidates.find(row => `${row.lineMasterId}:${row.lineRevisionId}` === event.target.value) || null)}><option value="">Choose a reviewed source line</option>{candidates.map(row => <option key={`${row.lineMasterId}:${row.lineRevisionId}`} value={`${row.lineMasterId}:${row.lineRevisionId}`}>{row.label} · {row.currentOwnerLabel}</option>)}</select></label>{selectedLine && selectedLine.currentOwnerPartId !== partId && <label className="parts-checkbox"><input type="checkbox" checked={reassign} disabled={busy || Boolean(attempt)} onChange={event => setReassign(event.target.checked)}/> I reviewed the current owner and authorize reassignment from {selectedLine.currentOwnerLabel}</label>}<label>Quantity per Part<input type="number" min="0.000001" step="any" value={quantity} disabled={busy || Boolean(attempt)} onChange={event => setQuantity(event.target.value)}/></label><label>Unit basis<input value={uom} disabled={busy || Boolean(attempt)} onChange={event => setUom(event.target.value)}/></label><label>Review reason<input value={reason} disabled={busy || Boolean(attempt)} onChange={event => setReason(event.target.value)}/></label><button className="button primary" disabled={busy || !selectedLine || (!reason.trim() && !attempt) || (selectedLine && selectedLine.currentOwnerPartId !== partId && !reassign && !attempt)} onClick={() => void save()}>{attempt ? "Retry same line link" : "Save reviewed line link"}</button>{error && <p className="parts-error">{error}</p>}</div>}
  </section>;
}

function DrawingSection({ partId, state, onSaved }: { partId: string; state: any; onSaved: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [identity, setIdentity] = useState("");
  const [linkType, setLinkType] = useState("local_file");
  const [filePath, setFilePath] = useState("");
  const [url, setUrl] = useState("");
  const [fileVersion, setFileVersion] = useState("");
  const [reason, setReason] = useState("");
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    const next = attempt || { key: crypto.randomUUID(), payload: { identity, linkType, filePath, externalUrl: url, fileVersion, reason } };
    setAttempt(next); setBusy(true); setError("");
    try { await api.addPartDrawing(partId, next.payload, next.key); setAttempt(null); setOpen(false); await onSaved(); }
    catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }
  return <section id="drawings" className="parts-section-card"><h3><FileText size={15}/> Drawings</h3>
    {state?.items?.length ? <div className="parts-linked-items">{state.items.map((item: any, index: number) => <article key={item.DrawingKey || index}><div><strong>{item.DrawingIdentity}</strong><small>File version {item.FileVersion || "unspecified"} · revision-specific link</small></div>{item.LinkType === "external_url" ? <a href={item.ExternalURL} target="_blank" rel="noreferrer">Open link</a> : <a href={`/api/parts/drawings/${encodeURIComponent(item.DrawingKey)}/open`} target="_blank" rel="noreferrer">Preview</a>}</article>)}</div> : <p>No drawings are linked to this revision. Drawings are optional.</p>}
    <button className="button" onClick={() => setOpen(value => !value)}>{open ? "Close drawing form" : "Link a drawing"}</button>
    {open && <div className="parts-capture-grid"><label>Drawing identity<input value={identity} disabled={busy || Boolean(attempt)} onChange={event => setIdentity(event.target.value)}/></label><label>Link type<select value={linkType} disabled={busy || Boolean(attempt)} onChange={event => setLinkType(event.target.value)}><option value="local_file">Local file in approved drawings root</option><option value="external_url">External HTTP(S) link</option></select></label>{linkType === "local_file" ? <label>Relative file path<input value={filePath} disabled={busy || Boolean(attempt)} onChange={event => setFilePath(event.target.value)} placeholder="Drawings/part.pdf"/></label> : <label>External URL<input value={url} disabled={busy || Boolean(attempt)} onChange={event => setUrl(event.target.value)}/></label>}<label>File version<input value={fileVersion} disabled={busy || Boolean(attempt)} onChange={event => setFileVersion(event.target.value)}/></label><label>Reason<input value={reason} disabled={busy || Boolean(attempt)} onChange={event => setReason(event.target.value)}/></label><button className="button primary" disabled={busy || (!identity.trim() && !attempt) || (!reason.trim() && !attempt)} onClick={() => void save()}>{attempt ? "Retry same drawing link" : "Save drawing link"}</button>{error && <p className="parts-error">{error}</p>}</div>}
  </section>;
}

function PurchaseDetailSection({ partId, state, onSaved }: { partId: string; state: any; onSaved: () => Promise<void> }) {
  const [openSpec, setOpenSpec] = useState(false);
  const [openVendor, setOpenVendor] = useState(false);
  const [openMapping, setOpenMapping] = useState(false);
  const [openPurchase, setOpenPurchase] = useState(false);
  const [specCode, setSpecCode] = useState(""); const [manufacturer, setManufacturer] = useState(""); const [mfrPart, setMfrPart] = useState("");
  const [description, setDescription] = useState(""); const [costingUom, setCostingUom] = useState("each"); const [currency, setCurrency] = useState("INR");
  const [vendorName, setVendorName] = useState(""); const [selectedVendor, setSelectedVendor] = useState(""); const [sku, setSku] = useState(""); const [vendorDescription, setVendorDescription] = useState("");
  const [selectedMapping, setSelectedMapping] = useState(""); const [transactionKey, setTransactionKey] = useState(""); const [transactionLine, setTransactionLine] = useState("1");
  const [transactionAt, setTransactionAt] = useState(""); const [quantity, setQuantity] = useState("1"); const [quantityUom, setQuantityUom] = useState("each");
  const [amount, setAmount] = useState(""); const [discount, setDiscount] = useState("0"); const [tax, setTax] = useState("0"); const [freight, setFreight] = useState("0"); const [charges, setCharges] = useState("0"); const [reason, setReason] = useState("");
  const [attempt, setAttempt] = useState<Attempt | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const specs = state?.specifications || [];
  const specification = specs[0];
  const vendors = state?.vendorCatalog || [];
  async function submit(kind: "spec" | "vendor" | "mapping" | "purchase", call: (payload: any, key: string) => Promise<any>, payload: any) {
    const next = attempt || { key: crypto.randomUUID(), payload };
    setAttempt(next); setBusy(true); setError("");
    try { await call(next.payload, next.key); setAttempt(null); setReason(""); setOpenSpec(false); setOpenVendor(false); setOpenMapping(false); setOpenPurchase(false); await onSaved(); }
    catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }
  const disabled = busy || Boolean(attempt);
  return <section id="purchase-details" className="parts-section-card"><h3><ShoppingCart size={15}/> Purchased Part details</h3>
    {!specs.length && <p>No purchase specification or actual purchase history is recorded. This Part has no costing rate until a reviewed actual purchase is captured.</p>}
    {specs.map((item: any) => <div className="parts-purchase-spec" key={item.specification.id}><h4>{item.specification.SpecificationCode} · {item.specification.ManufacturerPartNumber || item.specification.Description}</h4><p>{item.specification.Manufacturer} · costing basis {item.specification.CostingUOM} · {item.specification.CostingCurrency}</p><div className={`parts-rate ${item.rate.status}`}><strong>{item.rate.status === "available" ? `${Number(item.rate.rate).toFixed(4)} ${item.rate.currency} / ${item.rate.uom}` : item.rate.status}</strong><span>{item.rate.reason}</span>{item.rate.status === "available" && <small>{item.rate.ratePolicy} · transaction {item.rate.transactionAt} · selected purchase {item.rate.purchaseRecordId}</small>}</div>
      <h4>Vendor equivalents</h4>{item.vendors.length ? <div className="parts-linked-items">{item.vendors.map((vendor: any) => <article key={vendor.mappingId}><div><strong>{vendor.DisplayName} · {vendor.sku}</strong><small>{vendor.status}</small></div></article>)}</div> : <p>No reviewed vendor equivalents are linked.</p>}
      <h4>Actual purchase history</h4>{item.purchases.length ? <div className="parts-linked-items">{item.purchases.map((purchase: any, index: number) => <article key={purchase.PurchaseRecordKey || index}><div><strong>{purchase.Status} · {purchase.RecordType} · {purchase.TransactionAt}</strong><small>{purchase.TransactionKey}/{purchase.TransactionLineKey} · {purchase.Quantity} {purchase.QuantityUOM} · {purchase.Currency} {purchase.ExtendedAmount} · discount {purchase.DiscountAmount || 0}</small></div></article>)}</div> : <p>No vendor purchase records are available. Quotes are not actual purchases.</p>}
      {item.rate.status === "available" && <p className="parts-unavailable">Applied rate deducts explicit discounts and excludes tax ({item.rate.excludedCharges?.tax || 0}), freight ({item.rate.excludedCharges?.freight || 0}) and other charges ({item.rate.excludedCharges?.other || 0}). Different currencies or UOMs require dated approved conversion evidence.</p>}
      <div className="parts-conversion-list"><strong>Approved conversions</strong>{item.unitConversions.map((conversion: any, index: number) => <small key={`uom:${index}`}>{conversion.FromUOM} → {conversion.ToUOM} × {conversion.Factor} · {conversion.EvidenceReference}</small>)}{item.currencyConversions.map((conversion: any, index: number) => <small key={`currency:${index}`}>{conversion.FromCurrency} → {conversion.ToCurrency} × {conversion.Rate} from {conversion.RateDate} · {conversion.EvidenceReference}</small>)}<ConversionCapture partId={partId} specificationId={item.specification.id} onSaved={onSaved}/></div>
      <div className="parts-capture-actions"><button className="button" onClick={() => setOpenVendor(value => !value)}>Add vendor</button><button className="button" onClick={() => setOpenMapping(value => !value)}>Review vendor item</button><button className="button primary" onClick={() => setOpenPurchase(value => !value)}>Record actual purchase</button></div>
      {openVendor && <div className="parts-capture-grid"><label>Vendor name<input value={vendorName} disabled={disabled} onChange={event => setVendorName(event.target.value)}/></label><label>Reason<input value={reason} disabled={disabled} onChange={event => setReason(event.target.value)}/></label><button className="button primary" disabled={disabled || !vendorName.trim() || !reason.trim()} onClick={() => void submit("vendor", (p,k)=>api.createPartVendor(partId,p,k), {name:vendorName,reason})}>{attempt ? "Retry same request" : "Save vendor"}</button></div>}
      {openMapping && <div className="parts-capture-grid"><label>Vendor<select value={selectedVendor} disabled={disabled} onChange={event => setSelectedVendor(event.target.value)}><option value="">Choose vendor</option>{vendors.map((vendor: any)=><option key={vendor.id} value={vendor.id}>{vendor.DisplayName}</option>)}</select></label><label>Vendor SKU<input value={sku} disabled={disabled} onChange={event=>setSku(event.target.value)}/></label><label>Vendor description<input value={vendorDescription} disabled={disabled} onChange={event=>setVendorDescription(event.target.value)}/></label><label>Review reason<input value={reason} disabled={disabled} onChange={event=>setReason(event.target.value)}/></label><button className="button primary" disabled={disabled || !selectedVendor || !sku.trim() || !reason.trim()} onClick={()=>void submit("mapping",(p,k)=>api.createPartVendorMapping(partId,p,k),{specificationId:item.specification.id,vendorId:Number(selectedVendor),sku,description:vendorDescription,reason})}>{attempt ? "Retry same request" : "Save reviewed equivalent"}</button></div>}
      {openPurchase && <div className="parts-capture-grid"><label>Reviewed vendor SKU<select value={selectedMapping} disabled={disabled} onChange={event=>setSelectedMapping(event.target.value)}><option value="">Choose mapped SKU</option>{item.vendors.map((vendor: any)=><option key={vendor.mappingId} value={vendor.mappingId}>{vendor.DisplayName} · {vendor.sku}</option>)}</select></label><label>Transaction reference<input value={transactionKey} disabled={disabled} onChange={event=>setTransactionKey(event.target.value)}/></label><label>Transaction line<input value={transactionLine} disabled={disabled} onChange={event=>setTransactionLine(event.target.value)}/></label><label>Purchase date/time<input type="datetime-local" value={transactionAt} disabled={disabled} onChange={event=>setTransactionAt(event.target.value)}/></label><label>Quantity<input type="number" min="0.000001" step="any" value={quantity} disabled={disabled} onChange={event=>setQuantity(event.target.value)}/></label><label>Quantity unit<input value={quantityUom} disabled={disabled} onChange={event=>setQuantityUom(event.target.value)}/></label><label>Currency<input value={currency} disabled={disabled} onChange={event=>setCurrency(event.target.value.toUpperCase())}/></label><label>Extended merchandise amount<input type="number" min="0" step="any" value={amount} disabled={disabled} onChange={event=>setAmount(event.target.value)}/></label><label>Discount<input type="number" min="0" step="any" value={discount} disabled={disabled} onChange={event=>setDiscount(event.target.value)}/></label><label>Tax (excluded from applied rate)<input type="number" min="0" step="any" value={tax} disabled={disabled} onChange={event=>setTax(event.target.value)}/></label><label>Freight (excluded)<input type="number" min="0" step="any" value={freight} disabled={disabled} onChange={event=>setFreight(event.target.value)}/></label><label>Other charges (excluded)<input type="number" min="0" step="any" value={charges} disabled={disabled} onChange={event=>setCharges(event.target.value)}/></label><label>Reason and source evidence<input value={reason} disabled={disabled} onChange={event=>setReason(event.target.value)}/></label><button className="button primary" disabled={disabled || !selectedMapping || !transactionKey.trim() || !transactionAt || !amount || !reason.trim()} onClick={()=>void submit("purchase",(p,k)=>api.recordPartPurchase(partId,p,k),{specificationId:item.specification.id,vendorMappingId:Number(selectedMapping),transactionKey,transactionLineKey:transactionLine,recordType:"actual_purchase",status:"posted",transactionAt:new Date(transactionAt).toISOString(),documentReference:"",quantity:Number(quantity),quantityUOM:quantityUom,currency,extendedAmount:Number(amount),discountAmount:Number(discount),taxAmount:Number(tax),freightAmount:Number(freight),otherCharges:Number(charges),reason})}>{attempt ? "Retry same purchase" : "Save posted purchase"}</button></div>}
    </div>)}
    {!specs.length && <><button className="button" onClick={() => setOpenSpec(value => !value)}>{openSpec ? "Close specification form" : "Set up purchase specification"}</button>{openSpec && <div className="parts-capture-grid"><label>Specification code<input value={specCode} disabled={disabled} onChange={event=>setSpecCode(event.target.value)}/></label><label>Manufacturer<input value={manufacturer} disabled={disabled} onChange={event=>setManufacturer(event.target.value)}/></label><label>Manufacturer part number<input value={mfrPart} disabled={disabled} onChange={event=>setMfrPart(event.target.value)}/></label><label>Description<input value={description} disabled={disabled} onChange={event=>setDescription(event.target.value)}/></label><label>Costing unit<input value={costingUom} disabled={disabled} onChange={event=>setCostingUom(event.target.value)}/></label><label>Costing currency<input value={currency} disabled={disabled} onChange={event=>setCurrency(event.target.value.toUpperCase())}/></label><label>Reason<input value={reason} disabled={disabled} onChange={event=>setReason(event.target.value)}/></label><button className="button primary" disabled={disabled || !specCode.trim() || !reason.trim()} onClick={()=>void submit("spec",(p,k)=>api.createPartPurchaseSpecification(partId,p,k),{code:specCode,manufacturer,manufacturerPartNumber:mfrPart,description,costingUOM:costingUom,currency,reason})}>{attempt ? "Retry same specification" : "Save purchase specification"}</button></div>}</>}
    {error && <p className="parts-error">{error}</p>}
    <p className="parts-unavailable">This Part rate comes only from actual purchase history. MaterialRateLog and Default Material rates are not used. Current configuration and full Summary costing are not implemented in this Parts screen.</p>
  </section>;
}

function DetailSection({ id, title, icon, state }: { id: string; title: string; icon: ReactNode; state: any }) {
  return <section id={id} className="parts-section-card"><h3>{icon}{title}</h3>{state?.status === "unavailable" ? <p className="parts-unavailable">{state.message}</p> : state?.items?.length ? <div className="parts-linked-items">{state.items.map((item: any, index: number) => <article key={item.id || index}><div><strong>{item.name || item.label || item.sheet || `Record ${index + 1}`}</strong>{item.description && <small>{item.description}</small>}{item.sourceHash && <small>Source {item.sourceHash} · mapping v{item.mappingVersion} · association v{item.associationVersion}</small>}{item.actor && <small>{item.actor} · {item.reason}</small>}</div><span>{item.revision ? `Rev ${item.revision}` : item.version || (item.row ? `row ${item.row}` : "Available")}</span></article>)}</div> : <p>No {title.toLowerCase()} are linked to this Part.</p>}{state?.message && state.status !== "unavailable" && <p className="parts-unavailable">{state.message}</p>}</section>;
}

function CascadeCodePicker({ scopes, productId, modelId, selectedIds, onProductChange, onModelChange, onSelectedChange, disabled = false, restrictSelectionToContext = false }:
  { scopes: ScopeOption[]; productId: string; modelId: string; selectedIds: string[]; onProductChange: (value: string) => void; onModelChange: (value: string) => void; onSelectedChange: (value: string[]) => void; disabled?: boolean; restrictSelectionToContext?: boolean }) {
  const [search, setSearch] = useState("");
  const products = scopes.find(item => item.id === "product")?.targets || [];
  const models = (scopes.find(item => item.id === "product_model")?.targets || []).filter(item => !productId || item.parentId === productId);
  const codes = scopes.find(item => item.id === "model_code")?.targets || [];
  const modelById = new Map((scopes.find(item => item.id === "product_model")?.targets || []).map(item => [item.id, item]));
  const allowedModelIds = new Set(models.filter(item => !modelId || item.id === modelId).map(item => item.id));
  const visible = productId ? codes.filter(code => allowedModelIds.has(code.parentId || "")
    && `${code.label} ${modelById.get(code.parentId || "")?.label || ""}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())) : [];
  const selectedById = new Map(codes.map(code => [code.id, code]));
  const groups = new Map<string, Target[]>();
  visible.forEach(code => { const model = modelById.get(code.parentId || ""); const key = model?.id || "unknown"; groups.set(key, [...(groups.get(key) || []), code]); });

  function changeProduct(value: string) {
    if (restrictSelectionToContext) {
      const allowedModels = new Set((scopes.find(item => item.id === "product_model")?.targets || []).filter(item => item.parentId === value).map(item => item.id));
      const allowedCodes = new Set(codes.filter(code => allowedModels.has(code.parentId || "")).map(code => code.id));
      const removed = selectedIds.filter(id => !allowedCodes.has(id));
      if (removed.length && !window.confirm(`Changing Product filters will remove ${removed.length} selected Model Code(s) from this new Part. Continue?`)) return;
      if (removed.length) onSelectedChange(selectedIds.filter(id => allowedCodes.has(id)));
    }
    onProductChange(value);
  }
  function changeModel(value: string) {
    if (restrictSelectionToContext && value) {
      const allowedCodes = new Set(codes.filter(code => code.parentId === value).map(code => code.id));
      const removed = selectedIds.filter(id => !allowedCodes.has(id));
      if (removed.length && !window.confirm(`Changing Product Model filters will remove ${removed.length} selected Model Code(s) outside this Model from the new Part. Continue?`)) return;
      if (removed.length) onSelectedChange(selectedIds.filter(id => allowedCodes.has(id)));
    }
    onModelChange(value);
  }
  function toggleCode(id: string, checked: boolean) {
    onSelectedChange(checked ? [...new Set([...selectedIds, id])] : selectedIds.filter(value => value !== id));
  }

  return <div className="parts-cascade-controls">
    <div className="parts-cascade-filters">
      <label>Product filter<select aria-label="Intended sharing Product filter" value={productId} disabled={disabled} onChange={event => changeProduct(event.target.value)}><option value="">Choose a Product</option>{products.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
      <label>Product Model filter · optional<select aria-label="Intended sharing Product Model filter" value={modelId} disabled={disabled || !productId} onChange={event => changeModel(event.target.value)}><option value="">All Models under this Product</option>{models.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
    </div>
    <div className="parts-selected-code-summary" aria-live="polite"><strong>{selectedIds.length} intended Model Code{selectedIds.length === 1 ? "" : "s"}</strong><span>Choose codes explicitly; no descendants are selected automatically.</span></div>
    {selectedIds.length > 0 && <div className="parts-code-chips" aria-label="Selected intended Model Codes">{selectedIds.map(id => { const code = selectedById.get(id); const model = code ? modelById.get(code.parentId || "") : null; return <button type="button" key={id} disabled={disabled} aria-label={`Remove ${code?.label || id}`} onClick={() => toggleCode(id, false)}>{code?.label || `Code ${id}`} · {model?.label || "unknown Model"} ×</button>; })}</div>}
    <label className="parts-code-search">Search Model Codes<input aria-label="Search intended Model Codes" value={search} onChange={event => setSearch(event.target.value)} disabled={disabled || !productId} placeholder="Code or Model name"/></label>
    <div className="parts-cascade-actions"><button type="button" className="button ghost" disabled={disabled || !visible.length} onClick={() => onSelectedChange([...new Set([...selectedIds, ...visible.map(item => item.id)])])}>Select visible</button><button type="button" className="button ghost" disabled={disabled || !selectedIds.length} onClick={() => onSelectedChange([])}>Clear selection</button></div>
    {productId ? <div className="parts-code-options" aria-label="Available intended Model Codes">{[...groups.entries()].map(([groupId, items]) => <fieldset key={groupId}><legend>{modelById.get(groupId)?.label || "Model"}</legend>{items.map(code => <label key={code.id} className="parts-code-option"><input type="checkbox" checked={selectedIds.includes(code.id)} disabled={disabled} onChange={event => toggleCode(code.id, event.target.checked)}/><span><strong>{code.label}</strong><small>{modelById.get(code.parentId || "")?.label}</small></span></label>)}</fieldset>)}{!visible.length && <p>No active Model Codes match these filters.</p>}</div> : <p className="parts-cascade-empty">Choose one Product to browse its Models and Model Codes.</p>}
  </div>;
}

function IntendedSharingSection({ part, state, scopes, onSaved }: { part: Part; state: any; scopes: ScopeOption[]; onSaved: () => Promise<void> }) {
  const initialIds = (state?.items || []).map((item: any) => String(item.id));
  const [selectedIds, setSelectedIds] = useState<string[]>(initialIds);
  const [version, setVersion] = useState(Number(state?.version || 0));
  const [productId, setProductId] = useState("");
  const [modelId, setModelId] = useState("");
  const [reason, setReason] = useState("");
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { setSelectedIds((state?.items || []).map((item: any) => String(item.id))); setVersion(Number(state?.version || 0)); setAttempt(null); setReason(""); }, [part.id, state?.version, (state?.items || []).map((item: any) => item.id).join(",")]);
  const codeTargets = scopes.find(item => item.id === "model_code")?.targets || [];
  const currentLinks = state?.items || [];
  const allModels = scopes.find(item => item.id === "product_model")?.targets || [];
  const selectedLinks = selectedIds.map(id => {
    const saved = currentLinks.find((item: any) => String(item.id) === id);
    if (saved) return saved;
    const code = codeTargets.find(item => item.id === id);
    const model = code ? allModels.find(item => item.id === code.parentId) : null;
    return code ? { ...code, modelId: model?.id, productId: model?.parentId } : null;
  }).filter(Boolean);
  const outsideScope = selectedLinks.filter((item: any) => part.scope === "product" ? String(item.productId) !== part.scopeTargetId
    : part.scope === "product_model" ? String(item.modelId) !== part.scopeTargetId
      : part.scope === "model_code" ? String(item.id) !== part.scopeTargetId : false);
  function changeEditorProduct(value: string) {
    setProductId(value);
    setModelId("");
  }
  async function save() {
    const payload = { expectedVersion: version, intendedModelCodeIds: [...selectedIds], reason };
    const next = attempt || { key: crypto.randomUUID(), payload };
    setAttempt(next); setBusy(true); setError("");
    try { await api.savePartIntendedSharing(part.id, next.payload, next.key); setAttempt(null); setReason(""); await onSaved(); }
    catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }
  return <section id="intended-sharing" className="parts-section-card"><h3><Tags size={15}/> Intended sharing</h3>
    <p>Advisory links only. Saving these codes does not configure the Part, assign quantities or create costing history. Membership changes preserve its name, number, scope, Rev A, composition and snapshots.</p>
    {state?.status === "unavailable" ? <p className="parts-unavailable">{state.message || "Intended-sharing records are unavailable."}</p> : <>
      <CascadeCodePicker scopes={scopes} productId={productId} modelId={modelId} selectedIds={selectedIds} onProductChange={changeEditorProduct} onModelChange={setModelId} onSelectedChange={setSelectedIds} disabled={busy}/>
      {outsideScope.length > 0 && <p className="parts-validation warning">{outsideScope.length} intended code(s) are outside this Part's {part.scope} naming scope. This advisory is allowed and does not rename the Part.</p>}
      <label className="parts-sharing-reason">Why is intended sharing changing?<textarea value={reason} rows={2} maxLength={2000} disabled={busy} onChange={event => setReason(event.target.value)}/></label>
      {error && <p className="parts-error" role="alert">{error}{attempt && <small>Retry uses the original request key and selected codes.</small>}</p>}
      <div className="parts-capture-actions"><button className="button primary" disabled={busy || (!attempt && !reason.trim())} onClick={() => void save()}>{busy ? "Saving intended sharing…" : attempt ? "Retry same sharing request" : "Save intended sharing"}</button><span>Sharing version {version} · {selectedIds.length} selected</span></div>
    </>}
    <h4>Current intended codes</h4>{currentLinks.length ? <div className="parts-linked-items">{currentLinks.map((item: any) => <article key={item.id}><div><strong>{item.code}</strong><small>{item.product} · {item.model}</small><small>{item.createdBy} · {item.createdReason}</small></div><span>Intended</span></article>)}</div> : <p>No intended Model Codes are linked to this Part.</p>}
    <h4>Sharing change history</h4>{state?.history?.length ? <div className="parts-linked-items">{state.history.map((item: any, index: number) => <article key={item.SharingEventKey || index}><div><strong>{item.Action === "add" ? "Added" : "Removed"} · {item.Code || `Model Code ${item.ProductModelCode}`}</strong><small>{item.OccurredAt} · version {item.Version}</small><small>{item.Actor} · {item.Reason}</small></div></article>)}</div> : <p>No sharing changes recorded.</p>}
  </section>;
}

function UsedInConfigurationsSection({ state, intended }: { state: any; intended: any[] }) {
  const intendedIds = new Set(intended.map(item => String(item.id)));
  return <section id="used-in-configurations" className="parts-section-card"><h3><Tags size={15}/> Used in configurations</h3>
    <p>Actual usage comes from direct selections in current published costing configurations. Intended sharing and source mappings are not actual usage.</p>
    {state?.status === "unavailable" ? <p className="parts-unavailable">{state.message}</p> : state?.items?.length ? <div className="parts-linked-items">{state.items.map((item: any, index: number) => <article key={`${item.configurationId}:${item.selectionIdentity}:${index}`}><div><strong>{item.model} · {item.modelCode}</strong><small>{item.occurrenceLabel || item.selectionIdentity} · {item.quantity} {item.uom} · route {item.sourcingRoute}</small><small>Configuration v{item.configurationVersion} · current revision record {item.configurationRevisionId}</small>{!intendedIds.has(String(item.modelCodeId)) && <small className="parts-out-of-scope-note">Configured directly; no intended-sharing link is recorded.</small>}</div><span>Direct selection</span></article>)}</div> : <p>This Part is not selected directly in any current published configuration.</p>}
    {state?.message && <p className="parts-unavailable">{state.message}</p>}
  </section>;
}

function ConversionCapture({ partId, specificationId, onSaved }: { partId: string; specificationId: number; onSaved: () => Promise<void> }) {
  const [open, setOpen] = useState(false); const [kind, setKind] = useState("unit");
  const [from, setFrom] = useState(""); const [to, setTo] = useState(""); const [factor, setFactor] = useState("");
  const [rateDate, setRateDate] = useState(""); const [evidence, setEvidence] = useState(""); const [reason, setReason] = useState("");
  const [attempt, setAttempt] = useState<Attempt | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  async function save() {
    const payload = kind === "unit" ? { fromUOM: from, toUOM: to, factor: Number(factor), evidence, reason }
      : { fromCurrency: from, toCurrency: to, rate: Number(factor), rateDate: new Date(rateDate).toISOString(), evidence, reason };
    const next = attempt || { key: crypto.randomUUID(), payload };
    setAttempt(next); setBusy(true); setError("");
    try { if (kind === "unit") await api.addPartUnitConversion(partId, specificationId, next.payload, next.key); else await api.addPartCurrencyConversion(partId, specificationId, next.payload, next.key); setAttempt(null); setOpen(false); await onSaved(); }
    catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }
  return <><button className="button ghost" onClick={() => setOpen(value => !value)}>{open ? "Close conversion form" : "Add explicit conversion"}</button>{open && <div className="parts-capture-grid"><label>Conversion type<select value={kind} disabled={busy || Boolean(attempt)} onChange={event=>setKind(event.target.value)}><option value="unit">Quantity unit</option><option value="currency">Currency</option></select></label><label>From<input value={from} disabled={busy || Boolean(attempt)} onChange={event=>setFrom(kind === "currency" ? event.target.value.toUpperCase() : event.target.value)}/></label><label>To<input value={to} disabled={busy || Boolean(attempt)} onChange={event=>setTo(kind === "currency" ? event.target.value.toUpperCase() : event.target.value)}/></label><label>{kind === "unit" ? "Factor" : "Rate to costing currency"}<input type="number" min="0.000001" step="any" value={factor} disabled={busy || Boolean(attempt)} onChange={event=>setFactor(event.target.value)}/></label>{kind === "currency" && <label>Effective date/time<input type="datetime-local" value={rateDate} disabled={busy || Boolean(attempt)} onChange={event=>setRateDate(event.target.value)}/></label>}<label>Evidence reference<input value={evidence} disabled={busy || Boolean(attempt)} onChange={event=>setEvidence(event.target.value)}/></label><label>Reason<input value={reason} disabled={busy || Boolean(attempt)} onChange={event=>setReason(event.target.value)}/></label><button className="button primary" disabled={busy || !from.trim() || !to.trim() || !factor || !evidence.trim() || !reason.trim() || (kind === "currency" && !rateDate)} onClick={()=>void save()}>{attempt ? "Retry same conversion" : "Save approved conversion"}</button>{error && <p className="parts-error">{error}</p>}</div>}</>;
}
