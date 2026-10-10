import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "./api";

export type PartScope = "global" | "product" | "product_model" | "model_code";
export type PartTarget = { id: string; label: string; parentId?: string; shortcode?: string | null };
export type PartScopeOption = { id: PartScope; label: string; target?: PartTarget; targets?: PartTarget[] };
export type PartCreationOrigin = {
  workbookPath: string;
  groupKey: string;
  sheet: string;
  sourceDescription: string;
  sourceHash: string;
  associationKey: string;
  associationVersion: number;
  evidenceFingerprint: string;
};
type NamePreview = { name: string; available: boolean; collision: { source: string; id: string; number: string | null; status: string }[] };
export type PartCreateAttempt = { key: string; payload: Record<string, unknown>; origin?: PartCreationOrigin | null;
  formState?: { description?: string; variant?: string; reason?: string; productId?: string; modelId?: string; intendedIds?: string[] } };
export type ExistingPart = { id: string; name: string; partNumber?: string | null; selectable?: boolean; duplicateName?: boolean; legacy?: boolean; [key: string]: unknown };

const scopeLabels: Record<PartScope, string> = { global: "Global", product: "Product", product_model: "Product Model", model_code: "Model Code" };
export const MAPPING_PART_CREATE_ATTEMPT_KEY = "part-mapping:create-attempt";
const STANDALONE_PART_CREATE_ATTEMPT_KEY = "parts:create-attempt";
const STANDALONE_SHORTCODE_ATTEMPT_STORAGE_KEY = "part-shortcode:attempt";
const MAPPING_SHORTCODE_ATTEMPT_STORAGE_KEY = "part-mapping:shortcode-attempt";

function readAttempt(key: string): PartCreateAttempt | null {
  try {
    const attempt = JSON.parse(sessionStorage.getItem(key) || "null");
    return attempt && typeof attempt.key === "string" && attempt.payload && typeof attempt.payload === "object" ? attempt as PartCreateAttempt : null;
  } catch { return null; }
}

function updateScopeShortcode(scopes: PartScopeOption[], scope: PartScope, targetId: string, shortcode: string): PartScopeOption[] {
  return scopes.map(option => {
    if (option.id !== scope) return option;
    if (scope === "global") return option.target?.id === targetId ? { ...option, target: { ...option.target, shortcode } } : option;
    return { ...option, targets: (option.targets || []).map(target => target.id === targetId ? { ...target, shortcode } : target) };
  });
}

export function restoredMappingPartCreateAttempt(): PartCreateAttempt | null {
  const createAttempt = readAttempt(MAPPING_PART_CREATE_ATTEMPT_KEY);
  if (createAttempt) return createAttempt;
  const shortcodeAttempt = readAttempt(MAPPING_SHORTCODE_ATTEMPT_STORAGE_KEY);
  return shortcodeAttempt?.origin ? shortcodeAttempt : null;
}

export function CascadeCodePicker({ scopes, productId, modelId, selectedIds, onProductChange, onModelChange, onSelectedChange, disabled = false, restrictSelectionToContext = false }:
  { scopes: PartScopeOption[]; productId: string; modelId: string; selectedIds: string[]; onProductChange: (value: string) => void; onModelChange: (value: string) => void; onSelectedChange: (value: string[]) => void; disabled?: boolean; restrictSelectionToContext?: boolean }) {
  const [search, setSearch] = useState("");
  const products = scopes.find(item => item.id === "product")?.targets || [];
  const models = (scopes.find(item => item.id === "product_model")?.targets || []).filter(item => !productId || item.parentId === productId);
  const codes = scopes.find(item => item.id === "model_code")?.targets || [];
  const modelById = new Map((scopes.find(item => item.id === "product_model")?.targets || []).map(item => [item.id, item]));
  const allowedModelIds = new Set(models.filter(item => !modelId || item.id === modelId).map(item => item.id));
  const visible = productId ? codes.filter(code => allowedModelIds.has(code.parentId || "")
    && `${code.label} ${modelById.get(code.parentId || "")?.label || ""}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())) : [];
  const selectedById = new Map(codes.map(code => [code.id, code]));
  const groups = new Map<string, PartTarget[]>();
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
      if (removed.length && !window.confirm(`Changing Product Model filters will remove ${removed.length} selected Model Code(s) outside this Model from this new Part. Continue?`)) return;
      if (removed.length) onSelectedChange(selectedIds.filter(id => allowedCodes.has(id)));
    }
    onModelChange(value);
  }
  function toggleCode(id: string, checked: boolean) {
    onSelectedChange(checked ? [...new Set([...selectedIds, id])] : selectedIds.filter(value => value !== id));
  }

  return <div className="parts-cascade-controls">
    <div className="parts-cascade-filters">
      <label>Intended sharing Product filter<select name="part-intended-product" autoComplete="off" disabled={disabled} value={productId} onChange={event => changeProduct(event.target.value)}><option value="">Choose a Product</option>{products.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
      <label>Intended sharing Product Model filter · optional<select name="part-intended-product-model" autoComplete="off" disabled={disabled || !productId} value={modelId} onChange={event => changeModel(event.target.value)}><option value="">All Models under this Product</option>{models.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
    </div>
    <div className="parts-selected-code-summary" aria-live="polite"><strong>{selectedIds.length} intended Model Code{selectedIds.length === 1 ? "" : "s"}</strong><span>Choose codes explicitly; no descendants are selected automatically.</span></div>
    {selectedIds.length > 0 && <div className="parts-code-chips" aria-label="Selected intended Model Codes">{selectedIds.map(id => { const code = selectedById.get(id); const model = code ? modelById.get(code.parentId || "") : null; return <button type="button" key={id} disabled={disabled} aria-label={`Remove ${code?.label || id}`} onClick={() => toggleCode(id, false)}>{code?.label || `Code ${id}`} · {model?.label || "unknown Model"} ×</button>; })}</div>}
    <label className="parts-code-search">Search Model Codes<input name="part-intended-code-search" autoComplete="off" spellCheck={false} autoCapitalize="none" aria-label="Search intended Model Codes" value={search} onChange={event => setSearch(event.target.value)} disabled={disabled || !productId} placeholder="Code or Model name"/></label>
    <div className="parts-cascade-actions"><button type="button" className="button ghost" disabled={disabled || !visible.length} onClick={() => onSelectedChange([...new Set([...selectedIds, ...visible.map(item => item.id)])])}>Select visible</button><button type="button" className="button ghost" disabled={disabled || !selectedIds.length} onClick={() => onSelectedChange([])}>Clear selection</button></div>
    {productId ? <div className="parts-code-options" aria-label="Available intended Model Codes">{[...groups.entries()].map(([groupId, items]) => <fieldset key={groupId}><legend>{modelById.get(groupId)?.label || "Model"}</legend>{items.map(code => <label key={code.id} className="parts-code-option"><input type="checkbox" checked={selectedIds.includes(code.id)} disabled={disabled} onChange={event => toggleCode(code.id, event.target.checked)}/><span><strong>{code.label}</strong><small>{modelById.get(code.parentId || "")?.label}</small></span></label>)}</fieldset>)}{!visible.length && <p className="parts-cascade-empty">No active Model Codes match these filters.</p>}</div> : <p className="parts-cascade-empty">Choose one Product to browse its Models and Model Codes.</p>}
  </div>;
}

export function PartCreationForm({ origin = null, originContextCurrent = true, onCreated, onUseExisting, onCancel, onAttemptStateChange, cancelLabel = "Cancel" }:
  { origin?: PartCreationOrigin | null; originContextCurrent?: boolean; onCreated: (part: ExistingPart, attempt: PartCreateAttempt) => void; onUseExisting: (part: ExistingPart) => void; onCancel: () => void; onAttemptStateChange?: (pending: boolean) => void; cancelLabel?: string }) {
  const storageKey = origin ? MAPPING_PART_CREATE_ATTEMPT_KEY : STANDALONE_PART_CREATE_ATTEMPT_KEY;
  const shortcodeStorageKey = origin ? MAPPING_SHORTCODE_ATTEMPT_STORAGE_KEY : STANDALONE_SHORTCODE_ATTEMPT_STORAGE_KEY;
  const initialAttempt = useMemo(() => readAttempt(storageKey), [storageKey]);
  const initialShortcodeAttempt = useMemo(() => readAttempt(shortcodeStorageKey), [shortcodeStorageKey]);
  const restoredForm = initialShortcodeAttempt?.formState;
  const [scopes, setScopes] = useState<PartScopeOption[]>([]);
  const [scope, setScope] = useState<PartScope>((initialAttempt?.payload.scope as PartScope) || (initialShortcodeAttempt?.payload.scope as PartScope) || (origin ? "product_model" : "global"));
  const [targetId, setTargetId] = useState(String(initialAttempt?.payload.targetId || initialShortcodeAttempt?.payload.targetId || (origin ? "" : "global")));
  const [description, setDescription] = useState(String(initialAttempt?.payload.description || restoredForm?.description || origin?.sourceDescription || ""));
  const [variant, setVariant] = useState(String(initialAttempt?.payload.variant || restoredForm?.variant || ""));
  const [reason, setReason] = useState(String(initialAttempt?.payload.reason || restoredForm?.reason || ""));
  const [productId, setProductId] = useState(String(initialAttempt?.payload.selectedProductId || restoredForm?.productId || ""));
  const [modelId, setModelId] = useState(String(initialAttempt?.payload.selectedProductModelId || restoredForm?.modelId || ""));
  const [intendedIds, setIntendedIds] = useState<string[]>(Array.isArray(initialAttempt?.payload.intendedModelCodeIds)
    ? (initialAttempt.payload.intendedModelCodeIds as unknown[]).map(String) : restoredForm?.intendedIds || []);
  const [attempt, setAttempt] = useState<PartCreateAttempt | null>(initialAttempt);
  const [preview, setPreview] = useState<NamePreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [associationMessage, setAssociationMessage] = useState("");
  const [associationLoaded, setAssociationLoaded] = useState(!origin);
  const [error, setError] = useState("");
  const [shortcode, setShortcode] = useState(String(initialShortcodeAttempt?.payload.shortcode || ""));
  const [shortcodeReason, setShortcodeReason] = useState(String(initialShortcodeAttempt?.payload.reason || ""));
  const [shortcodeAttempt, setShortcodeAttempt] = useState<PartCreateAttempt | null>(initialShortcodeAttempt);
  const [editingShortcode, setEditingShortcode] = useState(false);
  const [busy, setBusy] = useState(false);
  const [previewVersion, setPreviewVersion] = useState(0);
  const generation = useRef(0);
  const userEdited = useRef(Boolean(initialAttempt || initialShortcodeAttempt));
  const attemptRef = useRef<PartCreateAttempt | null>(initialAttempt);
  const scopeOption = scopes.find(item => item.id === scope);
  const targets = scope === "global" ? (scopeOption?.target ? [scopeOption.target] : []) : (scopeOption?.targets || []);
  const target = targets.find(item => item.id === targetId);
  const shortcodeMissing = Boolean(scopeOption && target && !target.shortcode);
  const locked = busy || Boolean(attempt) || Boolean(shortcodeAttempt) || loading || !associationLoaded;

  useEffect(() => {
    onAttemptStateChange?.(Boolean(attempt || shortcodeAttempt));
    if (attempt) sessionStorage.setItem(storageKey, JSON.stringify(attempt));
    else sessionStorage.removeItem(storageKey);
    if (shortcodeAttempt) sessionStorage.setItem(shortcodeStorageKey, JSON.stringify(shortcodeAttempt));
    else sessionStorage.removeItem(shortcodeStorageKey);
  }, [attempt, shortcodeAttempt, onAttemptStateChange, storageKey, shortcodeStorageKey]);

  useEffect(() => {
    let active = true;
    const loadContext = async () => {
      try {
        const targetsResult = await api.partScopeTargets();
        if (!active) return;
        const availableTargets = (targetsResult.scopes || []) as PartScopeOption[];
        setScopes(availableTargets);
        if (!origin) { setAssociationLoaded(true); return; }
        try {
          const saved = await api.fileAssociation(origin.workbookPath);
          if (!active) return;
          setAssociationMessage(saved.current ? "Suggestions are based on the saved Product Model association. Confirm or edit them before creating." : "This workbook has no saved Product Model association. Choose any missing naming and intended-sharing context below.");
          setAssociationLoaded(true);
          if (attemptRef.current || userEdited.current) return;
          const product = saved.product?.id || saved.current?.product_id || "";
          const model = (saved.model as any)?.id || saved.current?.model_id || "";
          const hasProduct = (availableTargets.find(item => item.id === "product")?.targets || []).some(item => item.id === product);
          const hasModel = (availableTargets.find(item => item.id === "product_model")?.targets || []).some(item => item.id === model);
          const codeIds = (saved.codes || []).map(code => String(code.id));
          if (hasProduct) setProductId(product);
          if (hasModel) { setModelId(model); setScope("product_model"); setTargetId(model); }
          else if (hasProduct) { setScope("product"); setTargetId(product); }
          if (hasProduct && codeIds.length) setIntendedIds(codeIds);
        } catch {
          if (active) { setAssociationLoaded(true); setAssociationMessage("Saved workbook association suggestions could not be loaded. Select the required context below; no scope was selected automatically."); }
        }
      } catch (cause) { if (active) { setError(String(cause)); setAssociationLoaded(true); } }
      finally { if (active) setLoading(false); }
    };
    void loadContext();
    return () => { active = false; };
    // Scopes and workbook context are intentionally read once for this mounted creation attempt.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [origin?.workbookPath]);

  useEffect(() => {
    if (scope === "global") { setTargetId("global"); return; }
    if (!targets.length || targets.some(item => item.id === targetId)) return;
    setTargetId("");
  }, [scope, scopes]);

  useEffect(() => {
    const request = ++generation.current;
    if (!targetId || !description.trim() || !target?.shortcode) { setPreview(null); return; }
    const timer = window.setTimeout(async () => {
      try {
        const result = await api.partNamePreview(scope, targetId, description, variant);
        if (request === generation.current) setPreview(result);
      } catch (cause) { if (request === generation.current) { setPreview(null); setError(String(cause)); } }
    }, 220);
    return () => window.clearTimeout(timer);
  }, [scope, targetId, target?.shortcode, description, variant, previewVersion]);

  function changeProduct(value: string) {
    userEdited.current = true;
    const modelTargets = scopes.find(item => item.id === "product_model")?.targets || [];
    const modelIds = new Set(modelTargets.filter(item => item.parentId === value).map(item => item.id));
    const codeTargets = scopes.find(item => item.id === "model_code")?.targets || [];
    const codeIds = new Set(codeTargets.filter(item => modelIds.has(item.parentId || "")).map(item => item.id));
    setProductId(value); setModelId(""); setIntendedIds(current => current.filter(id => codeIds.has(id)));
    if (!nameAnchorTouched.current) {
      if (value) { setScope("product"); setTargetId(value); }
      else { setScope("global"); setTargetId("global"); }
    }
  }

  const nameAnchorTouched = useRef(Boolean(initialAttempt || initialShortcodeAttempt));
  function changeModel(value: string) {
    userEdited.current = true;
    setModelId(value);
    if (!nameAnchorTouched.current) {
      if (value) { setScope("product_model"); setTargetId(value); }
      else if (productId) { setScope("product"); setTargetId(productId); }
      else { setScope("global"); setTargetId("global"); }
    }
    if (value) {
      const codes = scopes.find(item => item.id === "model_code")?.targets || [];
      const allowed = new Set(codes.filter(item => item.parentId === value).map(item => item.id));
      setIntendedIds(current => current.filter(id => allowed.has(id)));
    }
  }

  async function submit() {
    if (loading || busy || shortcodeAttempt || (!attempt && (!preview?.available || !reason.trim()))) return;
    const next: PartCreateAttempt = attempt || { key: crypto.randomUUID(), payload: { scope, targetId, description, variant, expectedName: preview?.name || "", reason,
      selectedProductId: productId || null, selectedProductModelId: modelId || null, intendedModelCodeIds: [...intendedIds] }, origin };
    attemptRef.current = next;
    sessionStorage.setItem(storageKey, JSON.stringify(next));
    setAttempt(next); setBusy(true); setError("");
    try {
      const result = await api.createManagedPart(next.payload, next.key);
      const part = result?.part;
      if (!part || !part.id || !part.partNumber || !part.name) throw new Error("The Part publication response was incomplete. The outcome may be uncertain; retry the saved request.");
      sessionStorage.removeItem(storageKey); attemptRef.current = null; setAttempt(null);
      onCreated(part as ExistingPart, next);
    } catch (cause) {
      setError(String(cause));
      if (cause instanceof ApiError && cause.retryDisposition === "safe_to_edit") {
        // The backend explicitly confirms validation failed before publication began.
        sessionStorage.removeItem(storageKey); attemptRef.current = null; setAttempt(null);
      } else {
        // Keep the exact request key, payload and captured source group for recovery after reload.
        setAttempt(next);
      }
    } finally { setBusy(false); }
  }

  async function useCollision(collision: NonNullable<NamePreview["collision"]>[number]) {
    setError("");
    try {
      const id = collision.source === "legacy" ? `legacy:${collision.id}` : String(collision.id);
      let part: ExistingPart | undefined;
      if (origin) {
        const records = await api.searchParts(preview?.name || description, 0, 100, origin.workbookPath);
        part = (records.items || []).find((item: ExistingPart) => item.id === id);
      } else {
        const records = await api.parts(preview?.name || description);
        part = [...(records.items || []), ...(records.legacyItems || [])].find((item: ExistingPart) => item.id === id);
      }
      if (!part) { setError("The matching Part could not be reloaded from Grist. Refresh the register and review the name before creating another Part."); return; }
      if (origin && (!part.selectable || part.duplicateName || part.legacy)) { setError("This matching record is not a selectable canonical Part. Review the legacy record; it cannot be assigned here."); return; }
      onUseExisting(part);
    } catch (cause) { setError(String(cause)); }
  }

  async function saveShortcode() {
    const next = shortcodeAttempt || { key: crypto.randomUUID(), payload: { scope, targetId, shortcode, reason: shortcodeReason }, origin,
      formState: { description, variant, reason, productId, modelId, intendedIds: [...intendedIds] } };
    sessionStorage.setItem(shortcodeStorageKey, JSON.stringify(next));
    setShortcodeAttempt(next); setBusy(true); setError("");
    try {
      const result = await api.maintainPartShortcode(next.payload, next.key);
      const confirmedShortcode = typeof result?.shortcode === "string" ? result.shortcode : "";
      const requestedShortcode = String(next.payload.shortcode || "").trim().toUpperCase();
      if (!confirmedShortcode || confirmedShortcode !== requestedShortcode) {
        throw new Error("The shortcode response did not confirm the requested value. The outcome may be uncertain; retry the saved request.");
      }
      const savedScope = next.payload.scope as PartScope;
      const savedTargetId = String(next.payload.targetId || "");
      generation.current += 1;
      setPreview(null);
      setScopes(current => updateScopeShortcode(current, savedScope, savedTargetId, confirmedShortcode));
      try {
        const refreshed = await api.partScopeTargets();
        const refreshedScopes = (refreshed.scopes || []) as PartScopeOption[];
        const refreshedTarget = savedScope === "global"
          ? refreshedScopes.find(item => item.id === savedScope)?.target
          : refreshedScopes.find(item => item.id === savedScope)?.targets?.find(item => item.id === savedTargetId);
        setScopes(refreshedTarget?.shortcode === confirmedShortcode
          ? refreshedScopes
          : updateScopeShortcode(refreshedScopes, savedScope, savedTargetId, confirmedShortcode));
      } catch {
        // The write response confirms the value; retain the local target update if the follow-up read is unavailable.
      }
      sessionStorage.removeItem(shortcodeStorageKey);
      setShortcodeAttempt(null); setShortcode(confirmedShortcode); setShortcodeReason(""); setEditingShortcode(false); setPreviewVersion(value => value + 1);
    }
    catch (cause) {
      setError(String(cause));
      if (cause instanceof ApiError && cause.retryDisposition === "safe_to_edit") {
        sessionStorage.removeItem(shortcodeStorageKey);
        setShortcodeAttempt(null);
      }
    }
    finally { setBusy(false); }
  }

  const form = <section className="parts-form-card part-creation-form" aria-label="Create a canonical Part">
    <div><span className="eyebrow">Guided creation</span><h2>Create a Part</h2><p>Create a stable canonical identity. The source mapping remains unsaved until you confirm it separately.</p></div>
    {origin && <section className="part-creation-origin" aria-label="Originating workbook and source group"><strong>Originating source · read only</strong><span>Workbook · {origin.workbookPath || "Unavailable"}</span><span>Sheet · {origin.sheet || "Unavailable"}</span><span>Source description · {origin.sourceDescription || "Blank Part label"}</span>{associationMessage && <small role="status">{associationMessage}</small>}</section>}
    {origin && !originContextCurrent && <p className="parts-validation warning" role="alert">This workbook or source group changed after the dialog opened. Part creation/recovery is safe, but this Part will not be assigned to the changed group. Review current source evidence before assigning it.</p>}
    <div className="parts-cascade-card"><h3>Suggested intended sharing</h3><p>These editable suggestions record intent only. They do not configure Model Codes, set quantities or create costing history.</p><CascadeCodePicker scopes={scopes} productId={productId} modelId={modelId} selectedIds={intendedIds}
      onProductChange={changeProduct} onModelChange={changeModel} onSelectedChange={ids => { userEdited.current = true; setIntendedIds(ids); }} disabled={locked} restrictSelectionToContext/></div>
    <label>Name derives from<select name="part-name-scope" autoComplete="off" disabled={locked} value={scope} onChange={event => { userEdited.current = true; nameAnchorTouched.current = true; setScope(event.target.value as PartScope); }}><option value="global">Global</option><option value="product">Product</option><option value="product_model">Product Model</option><option value="model_code">Model Code</option></select></label>
    {scope !== "global" && <label>Scope target<select name="part-name-target" autoComplete="off" disabled={locked} value={targetId} onChange={event => { userEdited.current = true; nameAnchorTouched.current = true; setTargetId(event.target.value); }}><option value="">Choose {scopeLabels[scope]}</option>{targets.map(item => <option key={item.id} value={item.id}>{item.label}{item.shortcode ? ` · ${item.shortcode}` : " · shortcode needed"}</option>)}</select></label>}
    {scope === "global" && <p className="parts-target-line">Safari Manufacturing · Global scope</p>}
    {target && <div className="parts-shortcode-box"><div><strong>Maintained shortcode</strong><span>{target.shortcode || "Not set for this target"}</span></div>
      {(!target.shortcode || editingShortcode || Boolean(shortcodeAttempt)) && <div className="parts-shortcode-edit"><label>Shortcode<input name="part-shortcode" autoComplete="off" spellCheck={false} autoCapitalize="characters" value={shortcode} maxLength={20} onChange={event => setShortcode(event.target.value.toUpperCase())} placeholder="Letters and digits" disabled={locked}/></label><label>Why is this shortcode being set?<input name="part-shortcode-reason" autoComplete="off" spellCheck={false} autoCapitalize="sentences" value={shortcodeReason} onChange={event => setShortcodeReason(event.target.value)} disabled={locked}/></label><button type="button" className="button" disabled={busy || Boolean(attempt) || loading || !associationLoaded || (!shortcodeAttempt && (!shortcode.trim() || !shortcodeReason.trim()))} onClick={() => void saveShortcode()}>{shortcodeAttempt ? "Retry shortcode save" : "Save shortcode"}</button></div>}
      {target.shortcode && !editingShortcode && <button type="button" className="button ghost" disabled={locked} onClick={() => { setShortcode(target.shortcode || ""); setEditingShortcode(true); }}>Review shortcode</button>}
    </div>}
    {shortcodeMissing && <p className="parts-validation warning">Resolve the missing shortcode before generating a Part name. The app will not guess it.</p>}
    <label>What is the Part called?<input name="part-description" autoComplete="off" spellCheck={false} autoCapitalize="sentences" value={description} maxLength={120} onChange={event => { userEdited.current = true; setDescription(event.target.value); }} placeholder="For example, Chassis" disabled={locked}/></label>
    <label>What distinguishes this design?<input name="part-design-variant" autoComplete="off" spellCheck={false} autoCapitalize="sentences" value={variant} maxLength={120} onChange={event => { userEdited.current = true; setVariant(event.target.value); }} placeholder="Optional, such as Standard or Reinforced" disabled={locked}/></label>
    {preview && <div className={`parts-name-preview ${preview.available ? "available" : "collision"}`} role="status"><span>Generated name · server validated</span><strong>{preview.name}</strong>{!preview.available && <><p>{preview.collision.some(item => item.source === "legacy") ? "A legacy Part already uses this name. Review its identity or add a meaningful distinction." : "A current or historical Part name already uses this name. Select the existing Part or add a meaningful distinction."}</p><div className="parts-collision-list">{preview.collision.map((collision, index) => <button type="button" key={`${collision.source}:${collision.id}:${index}`} className="button" onClick={() => void useCollision(collision)}>{collision.number ? `${collision.number} · ` : collision.source === "legacy" ? "Legacy · " : ""}{origin ? "Use existing Part" : "Open existing Part"}</button>)}</div></>}</div>}
    <div className="parts-readonly-facts"><span>Part number <strong>Allocated on Save</strong></span><span>Engineering revision <strong>Rev A</strong></span></div>
    <label>Why is this Part needed?<textarea name="part-creation-reason" autoComplete="off" spellCheck={false} autoCapitalize="sentences" value={reason} onChange={event => { userEdited.current = true; setReason(event.target.value); }} rows={3} maxLength={2000} disabled={locked}/></label>
    {error && <div className="parts-error" role="alert">{error}{attempt && <small>The canonical Part may already exist. Retry uses the original request key and payload; do not start another creation.</small>}</div>}
    <div className="parts-form-actions"><button type="button" className="button" disabled={busy || Boolean(attempt) || Boolean(shortcodeAttempt)} onClick={onCancel}>{attempt || shortcodeAttempt ? "Recovery is required" : cancelLabel}</button><button type="button" className="button primary" disabled={busy || loading || !associationLoaded || Boolean(shortcodeAttempt) || (!attempt && (!preview?.available || !reason.trim()))} onClick={() => void submit()}>{busy ? "Creating Part in Grist…" : attempt ? "Retry same creation request" : origin ? "Create and assign" : "Save Part"}</button></div>
  </section>;

  return form;
}
