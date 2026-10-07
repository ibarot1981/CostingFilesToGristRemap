import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Archive, ArrowLeft, Check, FileText, History, LoaderCircle, Plus, Search, Tags, Wrench } from "lucide-react";
import { api } from "./api";
import "./parts.css";

type Scope = "global" | "product" | "product_model" | "model_code";
type Target = { id: string; label: string; parentId?: string; shortcode?: string | null };
type ScopeOption = { id: Scope; label: string; target?: Target; targets?: Target[] };
type Part = { id: string; partNumber: string | null; name: string; description: string; variant: string; scope: string; scopeTargetId: string | null; scopeTarget: string; shortcode?: string; engineeringRevision: string | null; status: string; metadataVersion: number | null; aliases: string[]; legacy: boolean; createdAt?: string; actor?: string; reason?: string; legacyRevisionValues?: string[]; partKey?: string; ambiguousName?: boolean; metadataHistory?: any[]; lifecycleHistory?: any[] };
type Preview = { name: string; available: boolean; collision: { source: string; id: string; number: string | null; status: string }[]; before?: any; after?: any; affectedSourceAssignments?: any[]; usageFingerprint?: string };
type Attempt = { key: string; payload: Record<string, unknown> };
type Register = { items: Part[]; legacyItems: Part[] };

const scopeLabels: Record<Scope, string> = { global: "Global", product: "Product", product_model: "Product Model", model_code: "Model Code" };
const emptyRegister: Register = { items: [], legacyItems: [] };

export function PartsView({ mappingReturn = null, onReturnToMapping }: { mappingReturn?: { path: string; groupKey: string; partId: string | null } | null; onReturnToMapping?: (partId: string | null) => void }) {
  const [scopes, setScopes] = useState<ScopeOption[]>([]);
  const [register, setRegister] = useState<Register>(emptyRegister);
  const [query, setQuery] = useState(() => sessionStorage.getItem("parts:search") || "");
  const [selectedId, setSelectedId] = useState<string | null>(() => {
    const route = window.location.hash.match(/^#parts\/(.+)$/);
    return route ? decodeURIComponent(route[1]) : sessionStorage.getItem("parts:selected");
  });
  const [mode, setMode] = useState<"selected" | "create" | "edit">("selected");
  const [fullDetails, setFullDetails] = useState(Boolean(window.location.hash.match(/^#parts\//)));
  const [details, setDetails] = useState<any>(null);
  const [scope, setScope] = useState<Scope>("global");
  const [targetId, setTargetId] = useState("global");
  const [description, setDescription] = useState("");
  const [variant, setVariant] = useState("");
  const [reason, setReason] = useState("");
  const [namePreview, setNamePreview] = useState<Preview | null>(null);
  const [shortcode, setShortcode] = useState("");
  const [shortcodeReason, setShortcodeReason] = useState("");
  const [editShortcode, setEditShortcode] = useState(false);
  const [retireReason, setRetireReason] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [createAttempt, setCreateAttempt] = useState<Attempt | null>(null);
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

  useEffect(() => {
    setTargetId(scope === "global" ? "global" : "");
    setNamePreview(null);
  }, [scope]);

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
  const grouped = useMemo(() => {
    const groups = new Map<string, Part[]>();
    allParts.forEach(part => {
      const key = part.legacy ? "Legacy / unallocated" : `${scopeLabels[part.scope as Scope] || part.scope} · ${part.scopeTarget}`;
      groups.set(key, [...(groups.get(key) || []), part]);
    });
    return [...groups.entries()];
  }, [allParts]);
  const selected = allParts.find(part => part.id === selectedId) || null;

  function startCreate() {
    setError(""); setNotice(""); setSelectedId(null); setDetails(null); setMode("create"); setFullDetails(false);
    setDescription(""); setVariant(""); setReason(""); setScope("global"); setTargetId("global"); setNamePreview(null);
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
    if (!namePreview?.available) return;
    const attempt = createAttempt || { key: crypto.randomUUID(), payload: { scope, targetId, description, variant, expectedName: namePreview.name, reason } };
    setCreateAttempt(attempt); setBusy(true); setError("");
    try {
      const result = await api.createManagedPart(attempt.payload, attempt.key);
      setCreateAttempt(null); setSelectedId(result.part.id); setMode("selected"); setFullDetails(false);
      setNotice(`Created ${result.part.partNumber} · ${result.part.name} · Rev A.`);
      await refresh(""); setQuery("");
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
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
    <label>Where is this Part intended to be shared?<select disabled={formLocked} value={scope} onChange={event => setScope(event.target.value as Scope)}>{scopes.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}</select></label>
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
    <div className="parts-form-actions"><button className="button" disabled={formLocked} onClick={() => { setMode("selected"); setCreateAttempt(null); setMetadataAttempt(null); setError(""); }}>{mode === "edit" ? "Cancel" : "Back to Parts"}</button><button className="button primary" disabled={formLocked || !namePreview?.available || !reason.trim()} onClick={() => void (mode === "edit" ? saveMetadata() : savePart())}>{createAttempt || metadataAttempt ? "Retry same request" : mode === "edit" ? "Save metadata version" : "Save Part"}</button></div>
  </section>;

  return <main className="parts-page">
    <aside className="parts-explorer">
      <div className="panel-heading"><div><span>Parts explorer</span><small>Stable identity and history</small></div><Tags size={17}/></div>
      <label className="tree-search"><Search size={14}/><input aria-label="Search Parts by number, name or alias" value={query} onChange={event => { setQuery(event.target.value); void refresh(event.target.value); }} placeholder="Search number, name or alias"/></label>
      <button className="parts-new-button" onClick={startCreate}><Plus size={15}/> New Part</button>
      {loading && <div className="parts-loading"><LoaderCircle className="spin" size={16}/>Loading Parts…</div>}
      <div className="parts-list-scroll">{grouped.map(([groupName, items]) => <section className="parts-group" key={groupName}><h3>{groupName}<span>{items.length}</span></h3>{items.map(part => <button key={part.id} className={`parts-row ${selectedId === part.id && mode === "selected" ? "selected" : ""}`} onClick={() => selectPart(part)}><span className="parts-row-number">{part.partNumber || "Legacy · unallocated"}</span><strong>{part.name}</strong><small>{part.legacy ? "Legacy record · revision unverified" : `Rev A · ${part.status}`}</small></button>)}</section>)}{!loading && !allParts.length && <div className="empty"><strong>No Parts found</strong><span>Try another search or create a Part.</span></div>}</div>
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
            <div className="parts-detail-tabs" aria-label="Part detail sections">{["Overview", "Process lines", "Drawings", "Used in", "History"].map(label => { const id = label.toLowerCase().replaceAll(" ", "-"); return <button key={label} type="button" onClick={() => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" })}>{label}</button>; })}</div>
            <div id="overview" className="parts-section-card"><h3>Overview</h3><dl><div><dt>Part number</dt><dd>{selected.partNumber}</dd></div><div><dt>Generated name</dt><dd>{selected.name}</dd></div><div><dt>Scope target</dt><dd>{scopeLabels[selected.scope as Scope]} · {selected.scopeTarget}</dd></div><div><dt>Engineering revision</dt><dd>Rev A</dd></div><div><dt>Metadata version</dt><dd>{selected.metadataVersion}</dd></div><div><dt>Created by</dt><dd>{selected.actor} · {selected.createdAt}</dd></div><div><dt>Reason</dt><dd>{selected.reason}</dd></div></dl><button className="button" onClick={beginEdit}>Review name/scope change</button></div>
            <DetailSection id="process-lines" title="Process lines" icon={<Wrench size={15}/>} state={details.processLines}/>
            <DetailSection id="drawings" title="Drawings" icon={<FileText size={15}/>} state={details.drawings}/>
            <DetailSection id="used-in" title="Used in" icon={<Tags size={15}/>} state={details.usedIn}/>
            <div id="history" className="parts-section-card"><h3><History size={15}/> History</h3><h4>Engineering baseline</h4><p>Rev A · created as the initial design baseline. Later engineering revisions require an approved CR.</p><h4>Name and scope metadata</h4>{selected.metadataHistory?.map((item: any) => <article className="parts-history-item" key={`${item.part_id}:${item.version}`}><strong>Metadata v{item.version} · {item.display_name}</strong><span>{scopeLabels[item.scope_type as Scope] || item.scope_type} · {item.target_label} · {item.shortcode} · {item.occurred_at}</span><p>{item.description}{item.variant ? ` · ${item.variant}` : ""} · {item.actor} · {item.reason}</p></article>)}{selected.aliases.length > 0 && <p>Previous names searchable as aliases: {selected.aliases.join(" · ")}</p>}<h4>Lifecycle</h4>{details.part.lifecycleHistory?.length ? details.part.lifecycleHistory.map((item: any) => <article className="parts-history-item" key={item.event_id}><strong>{item.event_type} · {item.status}</strong><span>{item.occurred_at}</span><p>{item.actor} · {item.reason}</p></article>) : <p>No lifecycle changes recorded.</p>}<h4>Source and mapping audit</h4>{details.mappingHistory?.length ? details.mappingHistory.map((item: any, index: number) => <article className="parts-history-item" key={`${item.RequestKey}:${index}`}><strong>{item.SheetName} · row {item.SourceRow} · source mapping v{item.Version}</strong><span>{item.SourceHash} · association v{item.AssociationVersion} · {item.OccurredAt}</span><p>{item.Actor} · {item.Reason}</p></article>) : <p>No source mapping history is linked to this stable Part identity yet.</p>}{selected.status === "active" && <div className="parts-retire"><label>Reason for retirement<input value={retireReason} onChange={event => setRetireReason(event.target.value)} disabled={busy}/></label><button className="button" disabled={busy || !retireReason.trim()} onClick={() => void retire()}>{retireAttempt ? "Retry retirement" : "Retire Part"}</button></div>}</div>
          </> : <div className="parts-details-placeholder"><strong>Part summary</strong><span>Open full details for process lines, drawings, explicit code use and history.</span></div>}
        </>}
      </> : mode === "selected" ? <div className="parts-welcome"><Tags size={25}/><h2>Select a Part</h2><p>Search by number, current name or previous alias, or start a new Part.</p><button className="button primary" onClick={startCreate}><Plus size={14}/> New Part</button></div> : null}
    </section>
  </main>;
}

function DetailSection({ id, title, icon, state }: { id: string; title: string; icon: ReactNode; state: any }) {
  return <section id={id} className="parts-section-card"><h3>{icon}{title}</h3>{state?.status === "unavailable" ? <p className="parts-unavailable">{state.message}</p> : state?.items?.length ? <div className="parts-linked-items">{state.items.map((item: any, index: number) => <article key={item.id || index}><div><strong>{item.name || item.label || item.sheet || `Record ${index + 1}`}</strong>{item.description && <small>{item.description}</small>}{item.sourceHash && <small>Source {item.sourceHash} · mapping v{item.mappingVersion} · association v{item.associationVersion}</small>}{item.actor && <small>{item.actor} · {item.reason}</small>}</div><span>{item.revision ? `Rev ${item.revision}` : item.version || (item.row ? `row ${item.row}` : "Available")}</span></article>)}</div> : <p>No {title.toLowerCase()} are linked to this Part.</p>}{state?.message && state.status !== "unavailable" && <p className="parts-unavailable">{state.message}</p>}</section>;
}
