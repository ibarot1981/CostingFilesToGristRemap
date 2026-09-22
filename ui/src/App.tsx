import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Check, ChevronDown, ChevronRight, Database, ExternalLink, FileSpreadsheet, Folder, FolderOpen, FolderTree, LoaderCircle, Map as MapIcon, Maximize2, Minimize2, RefreshCw, Search, ShieldCheck, Tags, X } from "lucide-react";
import { api } from "./api";
import { deriveMappedFileGroups, mappedFileIssueLabels } from "./mappedFilesViewModel";
import type { AssociationValidation, CatalogSummary, ExplorerItem, MappedFile, ModelCode, Preview, Product, ProductModel } from "./types";

type TreeMap = Record<string, ExplorerItem[]>;
type ExplorerSearch = { tree: TreeMap; total: number; itemCount: number; source?: "filesystem" | "cached-report" };
const emptyValidation: AssociationValidation = { valid: false, errors: [], warnings: [] };

export function App() {
  const [view, setView] = useState<"explorer" | "mapped">("explorer");
  const [summary, setSummary] = useState<CatalogSummary | null>(null);
  const [products, setProducts] = useState<Product[]>([]);
  const [models, setModels] = useState<ProductModel[]>([]);
  const [codes, setCodes] = useState<ModelCode[]>([]);
  const [tree, setTree] = useState<TreeMap>({});
  const [searchResults, setSearchResults] = useState<ExplorerSearch | null>(null);
  const [searchExpanded, setSearchExpanded] = useState<Set<string>>(new Set());
  const [searchPending, setSearchPending] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selectedPath, setSelectedPath] = useState("");
  const [selectedFile, setSelectedFile] = useState<ExplorerItem | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [mapped, setMapped] = useState<MappedFile[]>([]);
  const [productId, setProductId] = useState("");
  const [modelId, setModelId] = useState("");
  const [selectedCodes, setSelectedCodes] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  const [supersede, setSupersede] = useState(false);
  const [validation, setValidation] = useState(emptyValidation);
  const [requestKey, setRequestKey] = useState<string | null>(null);
  const validationRevision = useRef(0);
  const [filter, setFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [classificationFilter, setClassificationFilter] = useState("all");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [adapter, setAdapter] = useState("in-memory");

  const invalidateAssociation = () => {
    validationRevision.current += 1;
    setValidation(emptyValidation);
    setRequestKey(null);
  };

  const load = async () => {
    setLoading(true); setError("");
    try {
      const [nextSummary, nextProducts, root, state] = await Promise.all([api.summary(), api.products(), api.tree(), api.associations()]);
      setSummary(nextSummary); setProducts(nextProducts); setTree({ "": root.items }); setAdapter(state.adapter);
    } catch (cause) { setError(message(cause)); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);

  useEffect(() => {
    const query = filter.trim();
    if (!query) {
      setSearchResults(null);
      setSearchExpanded(new Set());
      setSearchPending(false);
      return;
    }
    let active = true;
    setSearchResults(null);
    setSearchPending(true);
    const timer = window.setTimeout(() => {
      void api.files(query).then((result) => {
        if (!active) return;
        const built = buildExplorerSearchTree(result.items);
        setSearchResults({ tree: built.tree, total: result.total, itemCount: result.items.length, source: result.source });
        setSearchExpanded(new Set(built.directories));
      }).catch((cause) => {
        if (active) {
          setSearchResults({ tree: { "": [] }, total: 0, itemCount: 0 });
          setError(message(cause));
        }
      }).finally(() => { if (active) setSearchPending(false); });
    }, 180);
    return () => { active = false; window.clearTimeout(timer); };
  }, [filter]);

  const loadModels = async (nextProductId: string) => {
    invalidateAssociation(); setProductId(nextProductId); setModelId(""); setCodes([]); setSelectedCodes([]);
    if (!nextProductId) { setModels([]); return; }
    try { setModels(await api.models(nextProductId)); } catch (cause) { setError(message(cause)); }
  };
  const loadCodes = async (nextModelId: string) => {
    invalidateAssociation(); setModelId(nextModelId); setSelectedCodes([]);
    if (!nextModelId) { setCodes([]); return; }
    try { setCodes((await api.codes(nextModelId)).active); } catch (cause) { setError(message(cause)); }
  };
  const toggleFolder = async (path: string) => {
    if (searchResults) {
      setSearchExpanded((old) => { const next = new Set(old); if (next.has(path)) next.delete(path); else next.add(path); return next; });
      return;
    }
    if (expanded.has(path)) { setExpanded((old) => { const next = new Set(old); next.delete(path); return next; }); return; }
    if (!tree[path]) {
      try { const result = await api.tree(path); setTree((old) => ({ ...old, [path]: result.items })); } catch (cause) { setError(message(cause)); return; }
    }
    setExpanded((old) => new Set(old).add(path));
  };
  const chooseFile = async (item: ExplorerItem) => {
    const path = item.relative_path;
    invalidateAssociation(); setSelectedPath(path); setSelectedFile(item); setPreview(null); setNotice(""); setProductId(""); setModels([]); setModelId(""); setCodes([]); setSelectedCodes([]); setReason(""); setSupersede(false); setPreviewing(true);
    try { const [inspected, nextPreview] = await Promise.all([api.inspect(path), api.preview(path)]); setSelectedFile(inspected); setPreview(nextPreview); }
    catch (cause) { setError(message(cause)); }
    finally { setPreviewing(false); }
  };
  const chooseSheet = async (sheet: string) => {
    if (!selectedPath) return;
    setPreviewing(true); try { setPreview(await api.preview(selectedPath, sheet)); } catch (cause) { setError(message(cause)); } finally { setPreviewing(false); }
  };
  const validate = async () => {
    if (!selectedPath || !productId || !modelId || !selectedCodes.length) return;
    const revision = validationRevision.current;
    setBusy(true); setError("");
    try {
      const result = await api.validateAssociation({ path: selectedPath, productId, modelId, codeIds: selectedCodes, reason, supersede, expectedHash: selectedFile?.content_hash || selectedFile?.contentHash || undefined });
      if (revision !== validationRevision.current) return;
      setValidation(result);
      setRequestKey(result.valid ? crypto.randomUUID() : null);
    } catch (cause) { if (revision === validationRevision.current) setError(message(cause)); }
    finally { setBusy(false); }
  };
  const save = async () => {
    if (!validation.valid || !requestKey) return;
    setBusy(true); setError("");
    const validatedProposal = validation.proposal || {};
    try {
      await api.saveAssociation({ path: selectedPath, productId, modelId, codeIds: selectedCodes, reason, supersede, expectedVersion: validatedProposal.expectedVersion, expectedHash: validatedProposal.expectedHash }, requestKey);
      setNotice("Association saved and queued for read-only processing."); setValidation(emptyValidation); setRequestKey(null);
    }
    catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  };
  const openMapped = async () => { setView("mapped"); try { setMapped((await api.mappedFiles()).items); } catch (cause) { setError(message(cause)); } };
  const matchesExplorer = (item: ExplorerItem) => (!filter || item.relative_path.toLowerCase().includes(filter.toLowerCase())) && (statusFilter === "all" || (item.mapping_status || "unmapped") === statusFilter) && (typeFilter === "all" || item.type === typeFilter) && (classificationFilter === "all" || (item.candidate_classification || "unknown") === classificationFilter);
  const searchMode = Boolean(filter.trim());
  const explorerTree = searchMode ? (searchResults?.tree || { "": [] }) : tree;
  const explorerExpanded = searchMode ? searchExpanded : expanded;
  const visibleExplorerItem = useMemo(() => {
    const visit = (item: ExplorerItem): boolean => {
      if (item.type !== "directory") return matchesExplorer(item);
      const children = explorerTree[item.relative_path];
      if (searchMode) return (typeFilter === "directory" && matchesExplorer(item)) || Boolean(children?.some(visit));
      if (children === undefined) return true;
      return matchesExplorer(item) || children.some(visit);
    };
    return visit;
  }, [explorerTree, searchMode, filter, statusFilter, typeFilter, classificationFilter]);
  const visibleRoot = useMemo(() => {
    if (searchMode && !searchResults) return [];
    return (explorerTree[""] || []).filter(visibleExplorerItem);
  }, [explorerTree, searchMode, searchResults, visibleExplorerItem]);

  if (loading) return <div className="loading"><div className="brand-mark"><Tags size={20}/></div><LoaderCircle className="spin"/>Loading Safari Manufacturing…</div>;
  return <div className="app-shell">
    <header className="topbar"><div className="brand"><div className="brand-mark"><Tags size={20}/></div><div><strong>Safari Manufacturing</strong><span>Costing workbench</span></div></div><nav><button className={view === "explorer" ? "active" : ""} onClick={() => setView("explorer")}><FileSpreadsheet size={15}/> Costing Explorer</button><button className={view === "mapped" ? "active" : ""} onClick={() => void openMapped()}><MapIcon size={15}/> Mapped Files</button></nav><div className="top-actions"><span className="auth"><ShieldCheck size={15}/> {adapter === "in-memory" ? "Local in-memory adapter" : "Safari Manufacturing Grist"}</span><button className="button ghost" onClick={() => void load()}><RefreshCw size={15}/> Refresh</button></div></header>
    {error && <div className="error-banner"><AlertTriangle size={16}/><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError("")}><X size={15}/></button></div>}
    {notice && <div className="notice-banner"><Check size={16}/>{notice}</div>}
    {view === "mapped" ? <MappedView rows={mapped} onSelect={(path) => { setView("explorer"); void chooseFile({ id: path, name: path.split("/").pop() || path, type: "file", relative_path: path, extension: ".ods" }); }}/>
    : <main className="workspace"><Explorer tree={explorerTree} expanded={explorerExpanded} visibleRoot={visibleRoot} filter={filter} setFilter={setFilter} statusFilter={statusFilter} setStatusFilter={setStatusFilter} typeFilter={typeFilter} setTypeFilter={setTypeFilter} classificationFilter={classificationFilter} setClassificationFilter={setClassificationFilter} matchesExplorer={visibleExplorerItem} toggleFolder={(path) => void toggleFolder(path)} chooseFile={(item) => void chooseFile(item)} selectedPath={selectedPath} summary={summary} searchPending={searchPending} searchTotal={searchResults?.total ?? null} searchItemCount={searchResults?.itemCount ?? 0} searchSource={searchResults?.source}/><AssociationPanel selectedFile={selectedFile} previewing={previewing} products={products} models={models} codes={codes} productId={productId} modelId={modelId} selectedCodes={selectedCodes} reason={reason} supersede={supersede} validation={validation} requestKeyReady={Boolean(requestKey)} busy={busy} setReason={(value) => { invalidateAssociation(); setReason(value); }} setSupersede={(value) => { invalidateAssociation(); setSupersede(value); }} loadModels={(id) => void loadModels(id)} loadCodes={(id) => void loadCodes(id)} toggleCode={(id) => { invalidateAssociation(); setSelectedCodes((old) => old.includes(id) ? old.filter((item) => item !== id) : [...old, id]); }} validate={() => void validate()} save={() => void save()}/><PreviewPanel selectedFile={selectedFile} preview={preview} previewing={previewing} chooseSheet={(sheet) => void chooseSheet(sheet)} summary={summary}/></main>}
  </div>;
}

function Explorer(props: { tree: TreeMap; expanded: Set<string>; visibleRoot: ExplorerItem[]; filter: string; setFilter: (value: string) => void; statusFilter: string; setStatusFilter: (value: string) => void; typeFilter: string; setTypeFilter: (value: string) => void; classificationFilter: string; setClassificationFilter: (value: string) => void; matchesExplorer: (item: ExplorerItem) => boolean; toggleFolder: (path: string) => void; chooseFile: (item: ExplorerItem) => void; selectedPath: string; summary: CatalogSummary | null; searchPending: boolean; searchTotal: number | null; searchItemCount: number; searchSource?: "filesystem" | "cached-report" }) {
  const searchMessage = props.searchPending ? "Searching all ODS paths…" : props.searchTotal === null ? "" : props.searchTotal === 0 ? "No ODS files match this path." : props.searchTotal > props.searchItemCount ? `Showing ${props.searchItemCount} of ${props.searchTotal} matches; refine the search for more.` : `${props.searchTotal} ODS file${props.searchTotal === 1 ? "" : "s"} match.`;
  const [treeWidth, setTreeWidth] = useState(0);
  const treeRef = useRef<HTMLDivElement>(null);
  const horizontalRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const tree = treeRef.current;
    if (!tree) return;
    const updateWidth = () => setTreeWidth(tree.scrollWidth);
    updateWidth();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(updateWidth);
    observer.observe(tree);
    if (tree.firstElementChild instanceof HTMLElement) observer.observe(tree.firstElementChild);
    return () => observer.disconnect();
  }, [props.tree, props.visibleRoot, props.expanded, props.searchPending]);

  const syncHorizontalScroll = () => {
    if (treeRef.current && horizontalRef.current && treeRef.current.scrollLeft !== horizontalRef.current.scrollLeft) horizontalRef.current.scrollLeft = treeRef.current.scrollLeft;
  };
  const syncTreeScroll = () => {
    if (treeRef.current && horizontalRef.current && treeRef.current.scrollLeft !== horizontalRef.current.scrollLeft) treeRef.current.scrollLeft = horizontalRef.current.scrollLeft;
  };

  return <aside className="explorer-panel"><div className="panel-heading"><div><span>Explorer</span><small>Product Costing filesystem</small></div><FolderTree size={17}/></div><label className="tree-search"><Search size={14}/><input aria-label="Search ODS costing files" value={props.filter} onChange={(event) => props.setFilter(event.target.value)} placeholder="Search ODS files by path"/></label>{props.filter.trim() && <div className="search-results" role="status" aria-live="polite">{props.searchSource === "cached-report" && !props.searchPending ? "Cached catalog · " : ""}{searchMessage}</div>}<div className="explorer-filters"><label>Status<select aria-label="Filter by mapping status" value={props.statusFilter} onChange={(event) => props.setStatusFilter(event.target.value)}><option value="all">All statuses</option><option value="mapped">Mapped</option><option value="unmapped">Unmapped</option><option value="conflict">Conflict</option></select></label><label>Type<select aria-label="Filter by file type" value={props.typeFilter} onChange={(event) => props.setTypeFilter(event.target.value)}><option value="all">All types</option><option value="directory">Folders</option><option value="file">Files</option></select></label><label>Class<select aria-label="Filter by candidate classification" value={props.classificationFilter} onChange={(event) => props.setClassificationFilter(event.target.value)}><option value="all">All classes</option><option value="costing_candidate">Costing</option><option value="master_or_template">Master/template</option><option value="archive">Archive</option><option value="generated_output">Generated</option><option value="unsupported">Unsupported</option></select></label></div><div className="tree-root"><FolderOpen size={15}/><strong>Product Costing</strong><span>{props.summary?.file_count ?? "—"} files</span></div><div className="tree-scroll-shell"><div className="tree-scroll" ref={treeRef} onScroll={syncHorizontalScroll} aria-label="File explorer tree"><div className="tree-scroll-content">{props.visibleRoot.map((item) => <TreeItem key={item.id} item={item} depth={0} tree={props.tree} expanded={props.expanded} matchesExplorer={props.matchesExplorer} toggleFolder={props.toggleFolder} chooseFile={props.chooseFile} selectedPath={props.selectedPath}/>)}{props.searchPending && <div className="empty search-empty"><LoaderCircle className="spin"/>Searching nested folders…</div>}</div></div><div className="explorer-horizontal-scroll" ref={horizontalRef} onScroll={syncTreeScroll} tabIndex={0} role="region" aria-label="File explorer horizontal scroll"><div style={{ width: `${Math.max(treeWidth, 1)}px`, height: "1px" }}/></div></div><div className="panel-footer"><Database size={15}/><div><strong>Read-only source</strong><span>Hashes are computed on inspection</span></div></div></aside>;
}

function TreeItem(props: { item: ExplorerItem; depth: number; tree: TreeMap; expanded: Set<string>; matchesExplorer: (item: ExplorerItem) => boolean; toggleFolder: (path: string) => void; chooseFile: (item: ExplorerItem) => void; selectedPath: string }) {
  const { item } = props; const open = props.expanded.has(item.relative_path); const children = props.tree[item.relative_path] || [];
  const visibleChildren = children.filter(props.matchesExplorer);
  const mapped = item.mapping_status || item.mappingStatus;
  const unreadable = item.readable === false || item.readState === "encrypted" || item.readState === "parse_error";
  const external = item.external_link_warning || item.externalLinkWarning;
  return <div className="tree-node"><button className={`tree-row ${props.selectedPath === item.relative_path ? "selected" : ""}`} style={{ paddingLeft: `${10 + props.depth * 15}px` }} onClick={() => item.type === "directory" ? props.toggleFolder(item.relative_path) : props.chooseFile(item)} aria-expanded={item.type === "directory" ? open : undefined}><span className="tree-chevron">{item.type === "directory" ? (open ? <ChevronDown size={14}/> : <ChevronRight size={14}/>) : <span/>}</span>{item.type === "directory" ? (open ? <FolderOpen size={15}/> : <Folder size={15}/>) : <FileSpreadsheet size={15}/>}<span className="tree-name">{item.name}</span>{mapped === "conflict" ? <span className="status-badge conflict" style={{ backgroundColor: "#fff0e9", color: "#9e3d23" }}>Conflict</span> : mapped === "mapped" && <span className="status-badge">Mapped</span>}{unreadable && <span className="health-badge danger" title={item.parse_error || "Workbook is not readable"}>Unreadable</span>}{external && <span className="health-badge warning" title="Workbook contains external references">External links</span>}</button>{open && visibleChildren.map((child) => <TreeItem key={child.id} item={child} depth={props.depth + 1} tree={props.tree} expanded={props.expanded} matchesExplorer={props.matchesExplorer} toggleFolder={props.toggleFolder} chooseFile={props.chooseFile} selectedPath={props.selectedPath}/>)}</div>;
}

export function AssociationPanel(props: { selectedFile: ExplorerItem | null; previewing: boolean; products: Product[]; models: ProductModel[]; codes: ModelCode[]; productId: string; modelId: string; selectedCodes: string[]; reason: string; supersede: boolean; validation: AssociationValidation; requestKeyReady: boolean; busy: boolean; setReason: (value: string) => void; setSupersede: (value: boolean) => void; loadModels: (id: string) => void; loadCodes: (id: string) => void; toggleCode: (id: string) => void; validate: () => void; save: () => void }) {
  return <section className="association-panel"><div className="content-heading"><div><div className="eyebrow">Costing Explorer</div><h1>Associate a costing workbook</h1><p>Choose one Product, one Model, and the active codes served by this file.</p></div><span className="mode-pill">Read-only workbook access</span></div><div className="selected-file-card"><div className="selection-icon">{props.selectedFile ? <Check size={18}/> : <FileSpreadsheet size={18}/>}</div><div><strong>{props.selectedFile?.name || "No workbook selected"}</strong><span>{props.selectedFile?.relative_path || "Select an ODS file in the Explorer."}</span>{props.selectedFile && <small className="breadcrumb-text">Product Costing / {props.selectedFile.relative_path}</small>}</div>{props.selectedFile?.external_link_warning && <span className="warning-tag"><ExternalLink size={12}/> External links</span>}</div><div className="form-card"><label>Product<select value={props.productId} onChange={(event) => props.loadModels(event.target.value)}><option value="">Select Product</option>{props.products.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>Product Model<select value={props.modelId} disabled={!props.productId} onChange={(event) => props.loadCodes(event.target.value)}><option value="">Select Model</option>{props.models.map((item) => <option key={item.id} value={item.id}>{item.model_number}{item.name ? ` · ${item.name}` : ""}</option>)}</select></label><fieldset><legend>Eligible Product Model Codes <span>{props.selectedCodes.length} selected</span></legend>{props.codes.length ? <div className="code-options">{props.codes.map((code) => <label className="code-option" key={code.id}><input type="checkbox" checked={props.selectedCodes.includes(code.id)} onChange={() => props.toggleCode(code.id)}/><span><strong>{code.code}</strong><small>{code.description || "No description"}</small></span>{code.owner && <em title={code.owner.relativePath || code.owner.name}>Owned by {code.owner.name}</em>}</label>)}</div> : <p className="field-hint">Select a Product and Model to load active codes. Bush variants are excluded from active choices.</p>}</fieldset><label>Reason <textarea value={props.reason} onChange={(event) => props.setReason(event.target.value)} placeholder="Optional for a new association; required for supersede actions." rows={3}/></label><label className="supersede-option"><input type="checkbox" checked={props.supersede} onChange={(event) => props.setSupersede(event.target.checked)}/><span><strong>Explicitly supersede existing ownership</strong><small>This closes prior active ownership and preserves its history. A reason is required.</small></span></label></div>{(props.validation.errors.length > 0 || props.validation.warnings.length > 0) && <div className={`validation-box ${props.validation.valid ? "valid" : "invalid"}`}><strong>{props.validation.valid ? "Ready to save" : "Validation needs attention"}</strong>{[...props.validation.errors, ...props.validation.warnings].map((item) => <div className="validation-line" key={`${item.code}-${item.message}`}><span>{item.code}</span>{item.message}</div>)}</div>}<div className="proposal"><div><strong>Proposed change</strong><span>{props.selectedFile ? `${props.selectedFile.name} → ${props.selectedCodes.length || 0} active code(s)` : "Select a workbook to begin"}</span>{props.supersede && <small className="destructive-note">Supersede is explicit; prior history will remain available.</small>}</div><div className="workbench-actions"><button className="button" disabled={props.busy || props.previewing || !props.selectedFile || !props.productId || !props.modelId || !props.selectedCodes.length} onClick={props.validate}>{props.busy ? <LoaderCircle className="spin" size={15}/> : <ShieldCheck size={15}/>} Validate</button><button className="button primary" disabled={props.busy || !props.validation.valid || !props.requestKeyReady} onClick={props.save}><Check size={15}/> Save association</button></div></div><small className="action-hint">Validate checks the current file, model/code rules, conflicts, and supersede requirements. Save rechecks the proposal and records it in the selected repository with audit and processing-queue records.</small></section>;
}

export function PreviewPanel(props: { selectedFile: ExplorerItem | null; preview: Preview | null; previewing: boolean; chooseSheet: (sheet: string) => void; summary: CatalogSummary | null }) {
  const preview = props.preview;
  const [expanded, setExpanded] = useState(false);
  const [gridWidth, setGridWidth] = useState(0);
  const gridRef = useRef<HTMLDivElement>(null);
  const horizontalRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!preview || !gridRef.current) {
      setGridWidth(0);
      return;
    }
    const grid = gridRef.current;
    const updateWidth = () => setGridWidth(grid.scrollWidth);
    updateWidth();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(updateWidth);
    observer.observe(grid);
    if (grid.firstElementChild instanceof HTMLElement) observer.observe(grid.firstElementChild);
    return () => observer.disconnect();
  }, [preview]);

  useEffect(() => {
    if (!expanded) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") setExpanded(false); };
    document.addEventListener("keydown", closeOnEscape);
    return () => { document.body.style.overflow = previousOverflow; document.removeEventListener("keydown", closeOnEscape); };
  }, [expanded]);

  const syncHorizontalScroll = () => {
    if (gridRef.current && horizontalRef.current && gridRef.current.scrollLeft !== horizontalRef.current.scrollLeft) horizontalRef.current.scrollLeft = gridRef.current.scrollLeft;
  };
  const syncGridScroll = () => {
    if (gridRef.current && horizontalRef.current && gridRef.current.scrollLeft !== horizontalRef.current.scrollLeft) gridRef.current.scrollLeft = horizontalRef.current.scrollLeft;
  };

  return <aside className={`preview-panel${expanded ? " preview-panel-expanded" : ""}`} role={expanded ? "dialog" : undefined} aria-modal={expanded ? true : undefined} aria-label={expanded ? "Expanded workbook preview" : undefined}><div className="panel-heading"><div><span>Workbook preview</span><small>{props.selectedFile?.name || "Select a file"}</small></div><div className="preview-heading-actions"><button className="button preview-expand" type="button" disabled={!preview} onClick={() => setExpanded((old) => !old)} aria-label={expanded ? "Exit full-page workbook preview" : "Expand workbook preview"} title={expanded ? "Exit full-page preview (Esc)" : "Expand workbook preview"}>{expanded ? <Minimize2 size={15}/> : <Maximize2 size={15}/>}</button><FileSpreadsheet size={17}/></div></div><div className="preview-readonly"><ShieldCheck size={13}/> Read-only · source ODS is never saved</div><div className="preview-body">{props.previewing ? <div className="empty"><LoaderCircle className="spin"/>Reading workbook…</div> : preview ? <><div className="sheet-tabs">{preview.sheets.map((sheet) => <button className={sheet === preview.sheet ? "active" : ""} key={sheet} onClick={() => props.chooseSheet(sheet)}>{sheet}</button>)}</div><div className="preview-meta"><span>{preview.totalRows.toLocaleString("en-IN")} rows × {(preview.totalColumns || 0).toLocaleString("en-IN")} columns</span>{preview.externalLinkWarning && <span className="warning-text"><ExternalLink size={12}/> {preview.externalReferenceCount} external references</span>}</div><div className="grid-scroll" ref={gridRef} onScroll={syncHorizontalScroll} aria-label="Workbook grid"><table className="preview-grid"><tbody>{preview.rows.map((row, rowIndex) => <tr key={rowIndex}><th>{preview.startRow + rowIndex}</th>{row.map((cell, columnIndex) => { const detail = preview.cells?.[rowIndex]?.[columnIndex]; return <td className={detail?.kind === "formula" ? "formula-cell" : ""} title={detail?.formula || undefined} key={columnIndex}>{String(detail?.value ?? cell ?? "")}</td>; })}</tr>)}</tbody></table></div><div className="preview-note">Showing {preview.rows.length} bounded rows{preview.truncatedRows ? "; more rows are available" : ""}{preview.truncatedColumns ? "; more columns are available" : ""}. Formula cells are marked with an indicator.</div><div className="preview-horizontal-scroll" ref={horizontalRef} onScroll={syncGridScroll} tabIndex={0} role="region" aria-label="Workbook horizontal scroll"><div style={{ width: `${Math.max(gridWidth, 1)}px`, height: "1px" }}/></div></> : <div className="empty"><FileSpreadsheet size={28}/><strong>Preview an ODS workbook</strong><span>Select a file to inspect sheets, dimensions, and external-link warnings.</span></div>}</div>{props.summary && <div className="catalog-metrics"><div><span>ODS files</span><strong>{props.summary.file_count}</strong></div><div><span>External-link files</span><strong>{props.summary.files_with_external_references ?? "—"}</strong></div><div><span>Unreadable</span><strong>{props.summary.unreadable_count ?? "—"}</strong></div></div>}</aside>;
}

export function MappedView(props: { rows: MappedFile[]; onSelect: (path: string) => void }) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [sort, setSort] = useState("path");
  const [details, setDetails] = useState<MappedFile | null>(null);
  const groups = useMemo(() => deriveMappedFileGroups(props.rows, { query, status, sort }), [props.rows, query, status, sort]);
  const issueLabels = mappedFileIssueLabels;
  const renderRows = groups.flatMap(({ label, rows }) => [
    <tr className="mapped-group-row" key={`group-${label}`}><th colSpan={7}>{label}<small>{rows.length} file{rows.length === 1 ? "" : "s"}</small></th></tr>,
      ...rows.map((row) => {
        const file = row.file as { relative_path?: string; name?: string; modified_at?: string };
      const association = row.association as { version?: number } | null;
      const batch = row.processingBatch;
      const change = row.lastObservedChange;
      const changeAt = change?.modifiedAt || file.modified_at;
      const reviewStatus = row.reviewStatus || (association ? "mapped" : "unmapped");
      const openDetails = () => setDetails(row);
      return <tr key={String(file.relative_path)} tabIndex={0} aria-label={`Open details for ${file.relative_path || file.name || "file"}`} onClick={openDetails} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openDetails(); } }}><td><strong>{file.name || file.relative_path}</strong><small>{file.relative_path}</small></td><td>{String((row.product as { name?: string } | null)?.name || "—")}<small>{String((row.model as { model_number?: string } | null)?.model_number || "Unassigned")}</small></td><td>{row.codes.map((item) => String(item.code || "")).join(", ") || "—"}</td><td><span className={`review-badge ${reviewStatus}`}>{reviewStatus === "conflict" ? "Conflict" : reviewStatus === "mapped" ? "Mapped" : "Unmapped"}</span>{row.issues?.map((issue) => <span className={`health-badge ${issue === "duplicate_code_ownership" || issue === "catalog_mismatch" || issue === "missing_file" || issue === "parse_error" ? "danger" : "warning"}`} key={issue}>{issueLabels[issue] || issue}</span>)}</td><td>{changeAt ? new Date(changeAt).toLocaleString("en-IN") : "—"}<small>{change?.source === "observation" ? `Observed ${change.observedAt ? new Date(change.observedAt).toLocaleDateString("en-IN") : ""}` : "File timestamp · no observation"}</small></td><td>{association?.version || "—"}</td><td>{batch ? <><span className="status-badge" style={{ backgroundColor: "#fff0e5", color: "#8c522f" }}>{batch.status === "queued" ? "Queued" : batch.status}</span><small>{batch.outcome}</small></> : "—"}</td></tr>;
    }),
  ]);
  return <main className="mapped-view">
    <div className="mapped-header"><div><div className="eyebrow">Registry review</div><h1>Mapped Files</h1><p>Grouped ownership, observed changes, conflicts, and preserved history.</p></div><span className="mode-pill">Conflicts are surfaced, never auto-resolved</span></div>
    <div className="mapped-toolbar"><label><Search size={14}/><input aria-label="Search mapped files" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search path, product, model, code, or issue"/></label><label>Status<select aria-label="Filter mapped file review status" value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">All statuses</option><option value="mapped">Mapped</option><option value="unmapped">Unmapped</option><option value="conflict">Conflicts</option><option value="needs-review">Needs review</option></select></label><label>Sort<select aria-label="Sort mapped files" value={sort} onChange={(event) => setSort(event.target.value)}><option value="path">Path</option><option value="name">Name</option><option value="last-change">Last observed change</option></select></label></div>
      <div className="mapped-card"><table className="mapped-table"><thead><tr><th>File / path</th><th>Product / model</th><th>Owned codes</th><th>Review status</th><th>Last observed change</th><th>Version</th><th>Processing</th></tr></thead><tbody>{renderRows.length ? renderRows : <tr><td colSpan={7} className="empty-cell">No files match the current filter.</td></tr>}</tbody></table></div>
    {details && <aside className="details-drawer"><div className="drawer-header"><div><strong>{String((details.file as { name?: string }).name || "File details")}</strong><small>{String((details.file as { relative_path?: string }).relative_path || "")}</small></div><button className="button" aria-label="Close details" onClick={() => setDetails(null)}><X size={15}/></button></div><div className="drawer-section"><strong>Current ownership</strong><span>{String((details.product as { name?: string } | null)?.name || "Unassigned")} / {String((details.model as { model_number?: string } | null)?.model_number || "Unassigned")}</span><span>{details.codes.map((item) => String(item.code || "")).join(", ") || "No active codes"}</span></div><div className="drawer-section"><strong>Discrepancies</strong>{details.issues?.length ? details.issues.map((issue) => <span key={issue}>{issueLabels[issue] || issue}{issue === "duplicate_code_ownership" && details.conflictCodes?.length ? `: ${details.conflictCodes.join(", ")}` : issue === "moved_file" && details.movedTo ? `: ${details.movedTo}` : ""}</span>) : <span>No discrepancies reported.</span>}<small>{details.lastObservedAt ? `Last repository observation: ${new Date(details.lastObservedAt).toLocaleString("en-IN")}` : "No repository observation recorded; displayed timestamp is the filesystem modification time."}</small></div><div className="drawer-section"><strong>Processing status</strong>{details.processingBatch ? <><span>{details.processingBatch.status}</span><small>{details.processingBatch.outcome}{details.processingBatch.started_at ? ` · ${details.processingBatch.started_at}` : ""}</small></> : <span>No association batch recorded.</span>}</div><div className="drawer-section"><strong>Association history</strong>{details.history.length ? details.history.map((entry, index) => { const association = entry.association as { created_at?: string; superseded_at?: string; active?: boolean; actor?: string; reason?: string }; const codes = entry.codes as { code?: string }[] | undefined; const auditEvents = entry.auditEvents as { id?: string; event_type?: string; actor?: string; occurred_at?: string; reason?: string }[] | undefined; return <div className="history-entry" key={index}><span>{association.active ? "Active" : "Superseded"} · {association.created_at || ""}</span><small>{association.actor || ""}{association.reason ? ` · ${association.reason}` : ""}</small><small>Codes: {codes?.map((item) => item.code || "").join(", ") || "—"}</small>{auditEvents?.map((event, eventIndex) => <small key={event.id || eventIndex}>Audit: {event.event_type} · {event.actor} · {event.occurred_at}{event.reason ? ` · ${event.reason}` : ""}</small>)}</div>; }) : <span>No history recorded.</span>}</div><button className="button primary" onClick={() => props.onSelect(String((details.file as { relative_path?: string }).relative_path || ""))}>Open in Explorer</button></aside>}
  </main>;
}

function message(cause: unknown) { return cause instanceof Error ? cause.message : String(cause); }

function buildExplorerSearchTree(items: ExplorerItem[]): { tree: TreeMap; directories: string[] } {
  const tree: TreeMap = { "": [] };
  const directories = new Set<string>();
  for (const item of items) {
    const parts = item.relative_path.split("/").filter(Boolean);
    const name = parts.pop();
    if (!name) continue;
    let parent = "";
    for (const part of parts) {
      const path = parent ? `${parent}/${part}` : part;
      const siblings = tree[parent] || (tree[parent] = []);
      if (!siblings.some((candidate) => candidate.relative_path === path)) {
        siblings.push({ id: path, name: part, type: "directory", relative_path: path, extension: "", candidate_classification: "directory" });
      }
      directories.add(path);
      parent = path;
      tree[parent] ||= [];
    }
    const siblings = tree[parent] || (tree[parent] = []);
    if (!siblings.some((candidate) => candidate.relative_path === item.relative_path)) siblings.push(item);
  }
  for (const siblings of Object.values(tree)) {
    siblings.sort((left, right) => Number(right.type === "directory") - Number(left.type === "directory") || left.name.localeCompare(right.name, undefined, { sensitivity: "base" }));
  }
  return { tree, directories: [...directories] };
}
