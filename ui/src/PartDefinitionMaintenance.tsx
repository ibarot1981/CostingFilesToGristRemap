import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "./api";
import "./partDefinition.css";

type Definition = {
  partId: string; partNumber: string; revisionId: number; revisionLabel: string; revisionStatus: string;
  applicability: Record<string, any>; requirements: any[]; components: any[]; purchases: any[];
  revisionHistory: any[]; draft?: any; canApprove?: boolean; pendingChangeRequests?: any[]; source?: any; actions?: any;
};

const FAMILY_LABEL: Record<string, string> = { mcl: "Material Cut List", toolshop: "Tool Shop", cnc: "CNC" };
const fieldStatusText = (status: unknown) => status === "missing" ? "missing in source" : status === "blank" ? "blank in source" : status === "value" || status === "present" ? "value present" : "source status not recorded";
const emptyContent = (value: Definition) => ({ baseRevisionId: value.revisionId, applicability: value.applicability,
  requirements: value.requirements, components: value.components, purchases: value.purchases,
  finalizeOnPublication: value.revisionStatus === "finalized" });
const key = (prefix: string) => `${prefix}:${crypto.randomUUID()}`;
function operationKey(partId: string, action: string, payload: unknown) {
  const storageKey = `safari.part-definition:${partId}:${action}`;
  const payloadText = JSON.stringify(payload);
  try {
    const previous = JSON.parse(localStorage.getItem(storageKey) || "null");
    if (previous?.key && previous?.status === "pending") {
      if (previous.payload === payloadText) return { key: previous.key, storageKey };
      return { key: previous.key, storageKey, conflict: true };
    }
    const nextKey = key(`part-definition-${action}`);
    localStorage.setItem(storageKey, JSON.stringify({ key: nextKey, payload: payloadText, status: "pending" }));
    return { key: nextKey, storageKey };
  } catch {
    return { key: key(`part-definition-${action}`), storageKey };
  }
}
function clearOperationKey(storageKey: string) { try { localStorage.removeItem(storageKey); } catch { /* storage can be disabled */ } }
function savedDraftAttempt(partId: string) {
  const storageKey = `safari.part-definition:${partId}:draft-save`;
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || "null");
    return saved?.status === "pending" && saved?.key && typeof saved?.payload === "string"
      ? { key: saved.key as string, storageKey, payload: saved.payload as string } : null;
  } catch { return null; }
}

export function PartDefinitionMaintenance({ partId, onSaved, onCompare }: { partId: string; onSaved: () => void; onCompare: () => void }) {
  const [definition, setDefinition] = useState<Definition | null>(null);
  const [options, setOptions] = useState<any>(null);
  const [content, setContent] = useState<any>(null);
  const [attributeText, setAttributeText] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [editing, setEditing] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pendingSave, setPendingSave] = useState<{ key: string; storageKey: string; payload: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function reload() {
    setLoading(true);
    setPendingSave(savedDraftAttempt(partId));
    try {
      const [next, nextOptions] = await Promise.all([api.partDefinition(partId), api.partDefinitionOptions(partId)]);
      setDefinition(next);
      setOptions(nextOptions);
      setContent(next.draft && !next.draft.stale ? next.draft.content : emptyContent(next));
      setAttributeText({});
      setReason(next.draft?.reason || "");
      setDirty(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The saved Part definition could not be loaded.");
    } finally { setLoading(false); }
  }

  useEffect(() => { void reload(); }, [partId]);

  const pending = useMemo(() => definition?.pendingChangeRequests?.[0] || null, [definition]);
  const pendingDependents = pending?.impact?.dependents || pending?.impact?.assemblies || [];
  const isFinalized = definition?.revisionStatus === "finalized";

  function patchContent(update: Record<string, unknown>) {
    setContent((old: any) => ({ ...old, ...update }));
    setDirty(true); setNotice("");
  }

  function updateRequirement(index: number, update: Record<string, unknown>) {
    const rows = [...(content?.requirements || [])];
    rows[index] = { ...rows[index], ...update };
    patchContent({ requirements: rows });
  }

  function updateRequirementDetail(index: number, field: string, value: unknown) {
    const rows = [...(content?.requirements || [])];
    const current = rows[index] || {};
    rows[index] = { ...current, detail: { ...(current.detail || {}), [field]: value },
      fieldStatus: { ...(current.fieldStatus || {}), [field]: value === "" ? "blank" : "value" } };
    patchContent({ requirements: rows });
  }

  function updateComponent(index: number, update: Record<string, unknown>) {
    const rows = [...(content?.components || [])]; rows[index] = { ...rows[index], ...update };
    patchContent({ components: rows });
  }

  function updatePurchase(index: number, update: Record<string, unknown>) {
    const rows = [...(content?.purchases || [])]; rows[index] = { ...rows[index], ...update };
    patchContent({ purchases: rows });
  }

  async function saveDraft() {
    if (!definition || !reason.trim() || pendingSave) { setError(pendingSave ? "Resolve the saved request before editing or saving another draft." : "Enter the engineering reason before saving a draft."); return; }
    const activeAttributeTexts = [
      ...(content?.requirements || []).map((item: any) => attributeText[`requirement:${item.requirementKey}`]),
      ...(content?.purchases || []).map((item: any) => attributeText[`purchase:${item.specificationIdentity}`]),
    ].filter((value): value is string => value !== undefined);
    if (activeAttributeTexts.some(value => { try { const parsed = JSON.parse(value); return !parsed || typeof parsed !== "object" || Array.isArray(parsed); } catch { return true; } })) {
      setError("Finish each attributes field with a valid JSON object before saving the draft."); return;
    }
    setBusy(true); setError(""); setNotice("");
    const payload = { ...content, baseRevisionId: definition.revisionId, expectedSaveVersion: definition.draft?.saveVersion || 0, reason };
    const attempt = operationKey(partId, "draft-save", payload);
    if ("conflict" in attempt && attempt.conflict) { setPendingSave(savedDraftAttempt(partId)); setError("A previous draft save is still unresolved. Retry its original saved request first."); setBusy(false); return; }
    const savedAttempt = { key: attempt.key, storageKey: attempt.storageKey, payload: JSON.stringify(payload) };
    setPendingSave(savedAttempt);
    try {
      const result = await api.savePartDefinitionDraft(partId, payload, attempt.key);
      clearOperationKey(attempt.storageKey);
      setPendingSave(null);
      setContent(result.content); setDirty(false); setEditing(true);
      await reload(); setNotice("Draft saved in Safari Grist. It has not changed the accepted revision.");
    } catch (cause) {
      if (cause instanceof ApiError && cause.status < 500) {
        clearOperationKey(attempt.storageKey); setPendingSave(null);
        if (["PART_DEFINITION_DRAFT_STALE", "PART_DEFINITION_DRAFT_REPLAY_SUPERSEDED"].includes(cause.code)) await reload();
      }
      setError(cause instanceof Error ? cause.message : "The draft could not be saved.");
    }
    finally { setBusy(false); }
  }

  async function retryPendingSave() {
    if (!pendingSave) return;
    let payload: unknown;
    try { payload = JSON.parse(pendingSave.payload); }
    catch { setError("The saved retry payload could not be read. Reload the current draft before continuing."); return; }
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await api.savePartDefinitionDraft(partId, payload, pendingSave.key);
      clearOperationKey(pendingSave.storageKey); setPendingSave(null);
      setContent(result.content); setDirty(false); setEditing(true);
      await reload(); setNotice("The original draft save was confirmed in Safari Grist.");
    } catch (cause) {
      if (cause instanceof ApiError && cause.status < 500) {
        clearOperationKey(pendingSave.storageKey); setPendingSave(null);
        if (["PART_DEFINITION_DRAFT_STALE", "PART_DEFINITION_DRAFT_REPLAY_SUPERSEDED"].includes(cause.code)) await reload();
      }
      setError(cause instanceof Error ? cause.message : "The saved draft request could not be confirmed.");
    } finally { setBusy(false); }
  }

  async function submitForApproval() {
    if (!definition?.draft || dirty || !reason.trim()) { setError("Save the current draft and reason before requesting approval."); return; }
    setBusy(true); setError(""); setNotice("");
    const payload = { draftKey: definition.draft.draftKey, reason };
    const attempt = operationKey(partId, "change-request", payload);
    try {
      const result = await api.submitPartDefinitionChangeRequest(partId, payload, attempt.key);
      clearOperationKey(attempt.storageKey);
      await reload(); setNotice(`Change Request submitted for review. ${result.impact?.dependents?.length ?? result.impact?.assemblies?.length ?? 0} current dependent Part/assembly successor(s) and ${result.impact?.modelCodeConfigurations?.length || 0} configuration successor(s) are in the reviewed closure.`);
      setEditing(false);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "The Change Request could not be submitted."); }
    finally { setBusy(false); }
  }

  async function approveAndPublish() {
    if (!pending?.key) return;
    setBusy(true); setError(""); setNotice("");
    const attempt = operationKey(partId, "approve-publish", { changeRequestKey: pending.key });
    try {
      const result = await api.approveAndPublishPartDefinition(pending.key, attempt.key);
      clearOperationKey(attempt.storageKey);
      await reload(); onSaved(); setEditing(false);
      setNotice(`Accepted revision ${result.publishedRevisionLabel} is published. ${result.dependentPartCount} current dependent Part/assembly successor(s) and ${result.configurationCount} current configuration successor(s) were versioned.`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "The approved Change Request could not be published. Retry the same request if Grist reports an uncertain result."); }
    finally { setBusy(false); }
  }

  if (loading && !definition) return <section className="parts-definition-card"><p>Loading saved Safari definition…</p></section>;
  if (error && !definition) return <section className="parts-definition-card"><p className="parts-definition-error" role="alert">{error}</p></section>;
  if (!definition) return null;

  return <section className="parts-section-card parts-definition-card" aria-label="Saved Part definition">
    <header className="parts-definition-heading"><div><h3>Saved Part definition</h3><p>{definition.partNumber} · accepted Rev {definition.revisionLabel} · {definition.revisionStatus.replaceAll("_", " ")}</p></div>
      <div className="parts-definition-actions"><button type="button" className="button" onClick={() => { setEditing(true); setNotice(""); }} disabled={busy || Boolean(pending) || Boolean(pendingSave) || !definition.actions?.editPart}>Edit Part</button>
        <button type="button" className="button" onClick={() => document.getElementById("part-definition-history")?.scrollIntoView({ behavior: "smooth", block: "center" })}>Revision history</button>
        <button type="button" className="button" onClick={onCompare}>Compare with ODS</button></div></header>
    {error && <p className="parts-definition-error" role="alert">{error}</p>}{notice && <p className="parts-definition-notice" role="status">{notice}</p>}
    <p className="parts-help">Safari Grist is the saved definition authority. Viewing, editing and costing use this saved definition; the recorded workbook is opened only when you explicitly compare it.</p>
    {!definition.actions?.editPart && <p className="parts-definition-notice" role="status">Part-definition maintenance tables are not installed in this Grist document yet. Existing accepted definitions remain readable.</p>}
    {definition.source?.workbookName && <p className="parts-definition-source">Origin provenance: {definition.source.workbookName} · {definition.source.sourceHash || "source hash unavailable"} · read only</p>}

    {pendingSave && <p className="parts-definition-error" role="alert">A draft save has an uncertain result. Editing is locked until its original payload is retried with the same request key. <button type="button" className="button" onClick={() => void retryPendingSave()} disabled={busy}>{busy ? "Retrying…" : "Retry original draft save"}</button></p>}
    {editing && <fieldset className="parts-definition-editor" aria-label="Edit saved Part definition" disabled={busy || Boolean(pendingSave)}>
      {definition.draft?.stale && <p className="parts-definition-error" role="status">The saved draft uses an older accepted base. It is retained. Review the current values here and reconcile them manually before saving a new draft on Rev {definition.revisionLabel}.</p>}
      <div className="parts-definition-applicability"><strong>Family applicability</strong>{Object.entries(FAMILY_LABEL).map(([family, label]) => <label key={family}><input type="checkbox" checked={Boolean(content?.applicability?.[family]?.applicable)} onChange={event => patchContent({ applicability: { ...content.applicability, [family]: { ...(content.applicability?.[family] || {}), applicable: event.target.checked } } })}/>{label}</label>)}</div>
      <div className="parts-definition-list-heading"><h4>Manufacturing requirements</h4><button type="button" className="button" disabled={busy || !content?.applicability?.mcl?.applicable && !content?.applicability?.toolshop?.applicable && !content?.applicability?.cnc?.applicable} onClick={() => patchContent({ requirements: [...(content.requirements || []), { requirementKey: `ui-authored:${crypto.randomUUID()}`, identityStatus: "safari_authored", family: content.applicability?.mcl?.applicable ? "mcl" : content.applicability?.toolshop?.applicable ? "toolshop" : "cnc", quantity: "1", uom: "Nos", processType: "", detail: { Quantity: "", QuantityUOM: "" }, provenance: { type: "safari_authored", evidence: {} } }] })}>Add requirement</button></div>
      {(content?.requirements || []).map((item: any, index: number) => <fieldset className="parts-definition-row" key={`${item.requirementKey}:${index}`}>
        <legend>{FAMILY_LABEL[item.family] || "Legacy requirement"} · {item.identityStatus === "legacy_unclassified" || item.identityStatus === "legacy_duplicate_unclassified" ? "legacy identity needs review" : item.requirementKey}</legend>
        {item.identityStatus === "legacy_duplicate_unclassified" && <div className="parts-definition-notice" role="status">Several historical requirement rows share legacy key “{item.legacyRequirementKey}”. Each row remains unchanged in its original revision. Assign an identity only after reviewing this occurrence.</div>}
        <div className="parts-definition-grid">
          {item.identityStatus === "legacy_duplicate_unclassified" && <button type="button" className="button" disabled={busy} onClick={() => updateRequirement(index, { requirementKey: `safari-authored-requirement:${crypto.randomUUID()}`, identityStatus: "safari_authored" })}>Assign reviewed requirement identity</button>}
          <label>Family<select value={item.family} disabled={item.family === "other"} onChange={event => updateRequirement(index, { family: event.target.value, processType: event.target.value === "mcl" ? "material_cut" : event.target.value === "toolshop" ? "tool_shop" : "cnc" })}><option value="mcl">MCL</option><option value="toolshop">Tool Shop</option><option value="cnc">CNC</option>{item.family === "other" && <option value="other">Legacy / other</option>}</select></label>
          <label>Quantity<input inputMode="decimal" value={item.quantity ?? ""} onChange={event => updateRequirement(index, { quantity: event.target.value })}/></label>
          <label>Unit<input value={item.uom || ""} onChange={event => updateRequirement(index, { uom: event.target.value })}/></label>
          <label>Item name<input value={item.detail?.ItemName ?? ""} onChange={event => updateRequirementDetail(index, "ItemName", event.target.value)}/></label>
          <label>Material display name<input value={item.detail?.MaterialDisplayName ?? ""} onChange={event => updateRequirementDetail(index, "MaterialDisplayName", event.target.value)}/></label>
          <label>Material identity<select value={item.detail?.Material || ""} onChange={event => updateRequirementDetail(index, "Material", event.target.value || null)}><option value="">Not assigned</option>{(options?.materials || []).map((material: any) => <option key={material.id} value={material.id}>{material.name}{material.code ? ` · ${material.code}` : ""}</option>)}</select></label>
          <label>Rate-basis quantity<input inputMode="decimal" value={item.detail?.Quantity ?? ""} onChange={event => updateRequirementDetail(index, "Quantity", event.target.value)}/></label>
          <label>Rate-basis unit<input value={item.detail?.QuantityUOM ?? ""} onChange={event => updateRequirementDetail(index, "QuantityUOM", event.target.value)}/></label>
          <label>Length<input inputMode="decimal" value={item.detail?.Length ?? ""} onChange={event => updateRequirementDetail(index, "Length", event.target.value === "" ? null : event.target.value)}/></label>
          <label>Width<input inputMode="decimal" value={item.detail?.Width ?? ""} onChange={event => updateRequirementDetail(index, "Width", event.target.value === "" ? null : event.target.value)}/></label>
          <label>Thickness<input inputMode="decimal" value={item.detail?.Thickness ?? ""} onChange={event => updateRequirementDetail(index, "Thickness", event.target.value === "" ? null : event.target.value)}/></label>
          <label>Dimension unit<select value={item.detail?.DimensionUOM || ""} onChange={event => updateRequirementDetail(index, "DimensionUOM", event.target.value)}><option value="">Not supplied</option><option value="mm">mm</option><option value="in">inch</option></select></label>
          {item.family === "toolshop" && <label>Tool Shop remarks<input value={item.detail?.ToolShopRemarks ?? ""} onChange={event => updateRequirementDetail(index, "ToolShopRemarks", event.target.value)}/><small>{fieldStatusText(item.fieldStatus?.ToolShopRemarks)}</small></label>}
          {item.family === "cnc" && <label>Plate part to cut<input value={item.detail?.PlatePart ?? ""} onChange={event => updateRequirementDetail(index, "PlatePart", event.target.value)}/><small>{fieldStatusText(item.fieldStatus?.PlatePart)}</small></label>}
          <label>Issue route<input value={item.detail?.IssueRoute ?? ""} onChange={event => updateRequirementDetail(index, "IssueRoute", event.target.value)}/></label>
          <label className="parts-definition-wide">Engineering attributes (JSON)<textarea value={attributeText[`requirement:${item.requirementKey}`] ?? JSON.stringify(item.detail?.EngineeringAttributes || {}, null, 2)} onChange={event => {
            const text = event.target.value; setAttributeText(old => ({ ...old, [`requirement:${item.requirementKey}`]: text }));
            try { const parsed = JSON.parse(text); if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) updateRequirementDetail(index, "EngineeringAttributes", parsed); else setDirty(true); }
            catch { setDirty(true); }
          }}/></label>
        </div><p className="parts-help">A Stores issue route is line-level routing evidence; it does not create a purchased component. Provenance: {item.provenance?.type || "legacy / unclassified"}.</p>
        <button type="button" className="button danger" disabled={busy} onClick={() => patchContent({ requirements: content.requirements.filter((_: any, rowIndex: number) => rowIndex !== index) })}>Remove from new revision</button>
      </fieldset>)}

      <div className="parts-definition-list-heading"><h4>Purchased or Stores components</h4><button type="button" className="button" disabled={busy || !(options?.parts || []).length} onClick={() => patchContent({ components: [...(content.components || []), { componentIdentity: `ui-component:${crypto.randomUUID()}`, childPartId: "", quantity: "1", uom: "Nos", sourcingRoute: "buy", provenance: "Safari-authored component" }] })}>Add component</button></div>
      {(content?.components || []).map((item: any, index: number) => <fieldset className="parts-definition-row" key={`${item.componentIdentity}:${index}`}><legend>{item.identityStatus === "legacy_unclassified" ? "Legacy component identity needs review" : item.componentIdentity}</legend><div className="parts-definition-grid">
        <label>Canonical child Part<select value={item.childPartId || ""} onChange={event => updateComponent(index, { childPartId: event.target.value, childRevisionId: undefined })}><option value="">Choose a Part</option>{(options?.parts || []).filter((part: any) => part.id !== partId).map((part: any) => <option key={part.id} value={part.id}>{part.partNumber} · {part.name}</option>)}</select></label>
        <label>Quantity<input inputMode="decimal" value={item.quantity ?? ""} onChange={event => updateComponent(index, { quantity: event.target.value })}/></label><label>Unit<input value={item.uom || ""} onChange={event => updateComponent(index, { uom: event.target.value })}/></label>
        <label>Route<select value={item.sourcingRoute || "auto"} onChange={event => updateComponent(index, { sourcingRoute: event.target.value })}><option value="auto">Automatic</option><option value="make">Manufacture</option><option value="buy">Purchased / Stores</option></select></label>
      </div><p className="parts-help">Child pin: {item.childRevisionLabel || item.childRevisionId || "current accepted revision"}. Replacing a child requires a new occurrence identity.</p><button type="button" className="button danger" disabled={busy} onClick={() => patchContent({ components: content.components.filter((_: any, rowIndex: number) => rowIndex !== index) })}>Remove component</button></fieldset>)}

      <div className="parts-definition-list-heading"><h4>Purchase specifications</h4><button type="button" className="button" disabled={busy} onClick={() => patchContent({ purchases: [...(content.purchases || []), { specificationIdentity: `ui-spec:${crypto.randomUUID()}`, purchaseItemId: null, code: "", manufacturer: "", manufacturerPartNumber: "", description: "", costingUOM: "each", costingCurrency: "INR", attributes: {} }] })}>Add purchase specification</button></div>
      {(content?.purchases || []).map((item: any, index: number) => <fieldset className="parts-definition-row" key={`${item.specificationIdentity}:${index}`}><legend>{item.identityStatus === "legacy_unclassified" ? "Legacy purchase identity needs review" : item.specificationIdentity}</legend><div className="parts-definition-grid">
        <label>Purchase item<select value={item.purchaseItemId || ""} onChange={event => updatePurchase(index, { purchaseItemId: event.target.value ? Number(event.target.value) : null })}><option value="">No canonical item</option>{(options?.purchaseItems || []).map((purchase: any) => <option key={purchase.id} value={purchase.id}>{purchase.name}{purchase.code ? ` · ${purchase.code}` : ""}</option>)}</select></label>
        <label>Specification code<input value={item.code || ""} onChange={event => updatePurchase(index, { code: event.target.value })}/></label><label>Manufacturer<input value={item.manufacturer || ""} onChange={event => updatePurchase(index, { manufacturer: event.target.value })}/></label>
        <label>Manufacturer part number<input value={item.manufacturerPartNumber || ""} onChange={event => updatePurchase(index, { manufacturerPartNumber: event.target.value })}/></label><label>Description<input value={item.description || ""} onChange={event => updatePurchase(index, { description: event.target.value })}/></label>
        <label>Costing unit<input value={item.costingUOM || ""} onChange={event => updatePurchase(index, { costingUOM: event.target.value })}/></label><label>Currency<input value={item.costingCurrency || "INR"} onChange={event => updatePurchase(index, { costingCurrency: event.target.value.toUpperCase() })}/></label>
        <label className="parts-definition-wide">Purchase attributes (JSON)<textarea value={attributeText[`purchase:${item.specificationIdentity}`] ?? JSON.stringify(item.attributes || {}, null, 2)} onChange={event => {
          const text = event.target.value; setAttributeText(old => ({ ...old, [`purchase:${item.specificationIdentity}`]: text }));
          try { const parsed = JSON.parse(text); if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) updatePurchase(index, { attributes: parsed }); else setDirty(true); }
          catch { setDirty(true); }
        }}/></label>
      </div><button type="button" className="button danger" disabled={busy} onClick={() => patchContent({ purchases: content.purchases.filter((_: any, rowIndex: number) => rowIndex !== index) })}>Remove purchase specification</button></fieldset>)}

      {!isFinalized && <label className="parts-definition-finalize"><input type="checkbox" checked={Boolean(content?.finalizeOnPublication)} onChange={event => patchContent({ finalizeOnPublication: event.target.checked })}/> Finalize this successor definition through the approved Change Request</label>}
      <label className="parts-definition-reason">Engineering change reason<input value={reason} onChange={event => { setReason(event.target.value); setDirty(true); }}/></label>
      <div className="parts-definition-actions"><button type="button" className="button primary" onClick={() => void saveDraft()} disabled={busy || !reason.trim()}>{busy ? "Saving…" : "Save draft"}</button>
        <button type="button" className="button" onClick={() => { setEditing(false); setDirty(false); if (definition) setContent(definition.draft && !definition.draft.stale ? definition.draft.content : emptyContent(definition)); }} disabled={busy}>Close editor</button>
        {definition.draft && !definition.draft.stale && <button type="button" className="button primary" onClick={() => void submitForApproval()} disabled={busy || dirty || !reason.trim()}>Request approval</button>}</div>
    </fieldset>}

    {!editing && <>
      <div className="parts-definition-applicability"><strong>Applicable families</strong>{Object.entries(FAMILY_LABEL).map(([family, label]) => <span key={family}>{label}: {definition.applicability?.[family]?.applicable === null ? "unknown" : definition.applicability?.[family]?.applicable ? "applicable" : "not applicable"}</span>)}</div>
      <div className="parts-definition-list-heading"><h4>Accepted requirements</h4><span>{definition.requirements.length} saved</span></div>
      {definition.requirements.length ? <div className="parts-definition-items">{definition.requirements.map((item: any, index: number) => <article key={`${item.requirementKey}:${index}`}><strong>{FAMILY_LABEL[item.family] || "Other process"} · {item.detail?.ItemName || item.detail?.PlatePart || item.detail?.MaterialDisplayName || item.sourcePartName || "Requirement"}</strong><span>{item.quantity ?? "—"} {item.uom} · {item.detail?.MaterialDisplayName || "Material not named"} · {item.detail?.Length ?? "—"} × {item.detail?.Width ?? "—"} × {item.detail?.Thickness ?? "—"} {item.detail?.DimensionUOM || ""}</span><small>{item.identityStatus === "legacy_unclassified" ? "Legacy identity is unclassified; no historical row was rewritten." : `Requirement ${item.requirementKey}`} · {item.provenance?.type || "legacy / unclassified"}</small></article>)}</div> : <p>No manufacturing line requirements are saved on this revision.</p>}
      {definition.components.length > 0 && <><h4>Components</h4><div className="parts-definition-items">{definition.components.map((item: any) => <article key={item.componentIdentity}><strong>{item.childPartNumber} · {item.childName}</strong><span>{item.quantity} {item.uom} · {item.sourcingRoute || "auto"} · pinned Rev {item.childRevisionLabel || "unknown"}</span><small>{item.componentIdentity} · {item.identityStatus || "stable"}</small></article>)}</div></>}
      {definition.purchases.length > 0 && <><h4>Purchase specifications</h4><div className="parts-definition-items">{definition.purchases.map((item: any) => <article key={item.specificationIdentity}><strong>{item.code} · {item.description || item.manufacturerPartNumber}</strong><span>{item.manufacturer} · {item.costingUOM} · {item.costingCurrency}</span><small>{item.specificationIdentity} · {item.identityStatus || "stable"}</small></article>)}</div></>}
      {definition.draft && <p className={definition.draft.stale ? "parts-definition-error" : "parts-definition-notice"} role="status">{definition.draft.stale ? `Draft v${definition.draft.saveVersion} is based on an older accepted revision. Its saved content is retained; review the current accepted definition, reconcile changes, then save a new draft.` : `Saved draft v${definition.draft.saveVersion} · ${definition.draft.updatedAt}. Accepted Rev ${definition.revisionLabel} is unchanged.`}</p>}
      {pending && <div className="parts-definition-pending"><strong>Change Request · {pending.status.replaceAll("_", " ")}</strong><p>{pending.reason}</p><span>{pendingDependents.length} current dependent Part/assembly successor(s) · {pending.impact?.modelCodeConfigurations?.length || 0} Model Code configuration successor(s)</span>
        {pending.impact?.target && <p>Part {pending.impact.target.partId} · record {pending.impact.target.partRecordId}: Rev {pending.impact.target.currentRevisionLabel} → proposed Rev {pending.impact.target.proposedRevisionLabel || "unclassified"}</p>}
        {pendingDependents.length > 0 && <div className="parts-definition-impact"><h4>Current dependent Part/assembly closure</h4>{pendingDependents.map((dependent: any) => <article key={dependent.partId}><strong>{dependent.kind === "part" ? "Part" : "Assembly"} · {dependent.partNumber || dependent.partId} · {dependent.partId} (record {dependent.partRecordId})</strong><span>Current Rev {dependent.currentRevisionLabel} → proposed Rev {dependent.proposedRevisionLabel || "unclassified"}</span>
          {(dependent.pathDetails || []).map((path: any, pathIndex: number) => <div className="parts-definition-impact-path" key={`${dependent.partId}:${pathIndex}`}><small>Path: {path.partPath.join(" → ")}</small>{path.edges.map((edge: any, edgeIndex: number) => <small key={`${edge.componentIdentity || edge.componentRecordId}:${edgeIndex}`}>{edge.componentIdentity || `unclassified component record ${edge.componentRecordId}`} · {edge.parentPartId} Rev {edge.parentRevisionLabel} → {edge.proposedParentRevisionLabel} contains {edge.childPartId} pinned Rev {edge.pinnedChildRevisionLabel} → proposed Rev {edge.proposedChildRevisionLabel} · {edge.quantity ?? "—"} {edge.uom || ""} · {edge.sourcingRoute || "auto"}</small>)}</div>)}</article>)}</div>}
        {pending.impact?.modelCodeConfigurations?.length > 0 && <div className="parts-definition-impact"><h4>Current Model Code configuration closure</h4>{pending.impact.modelCodeConfigurations.map((use: any) => <article key={`${use.modelCodeRecordId}:${use.selectionIdentity || use.selectionRecordId}`}><strong>{use.modelCode || "Model Code"} · record {use.modelCodeRecordId} · selection {use.selectionIdentity || `unclassified record ${use.selectionRecordId}`}</strong><span>Configuration record {use.configurationRecordId} Rev {use.currentConfigurationRevision} → proposed Rev {use.proposedConfigurationRevision} · Part {use.partId} pinned Rev {use.partRevisionId} → proposed Rev {use.proposedPartRevisionLabel} · {use.quantity ?? "—"} {use.uom || ""} · {use.sourcingRoute || "auto"} · {use.label || "unlabeled occurrence"}{use.optionGroup ? ` · option ${use.optionGroup}` : ""}</span>{(use.dependencyPaths || []).filter((path: string[]) => path.length > 1).map((path: string[], index: number) => <small key={`${use.selectionIdentity}:${index}`}>Affected through: {path.join(" → ")}</small>)}</article>)}</div>}
        {pending.impact?.spares?.message && <p>{pending.impact.spares.message}</p>}
        {pending.impact?.unknown?.length > 0 && <p className="parts-definition-error">Impact has gaps: {pending.impact.unknown.map((item: any) => item.code).join(", ")}</p>}
        {definition.canApprove && <button type="button" className="button primary" onClick={() => void approveAndPublish()} disabled={busy || pending.status === "pending_approval" && !pending.impact?.fingerprint}>{busy ? "Publishing…" : "Approve and publish as Irshad"}</button>}
      </div>}
      <details id="part-definition-history" className="parts-definition-history"><summary>Engineering revision history</summary>{definition.revisionHistory.map((revision: any) => <article key={revision.id}><strong>Rev {revision.revisionLabel} · {revision.status.replaceAll("_", " ")}{revision.current ? " · current" : ""}</strong><span>{revision.actor || "Actor not recorded"} · {revision.occurredAt || "Date not recorded"}</span><p>{revision.reason || "No reason recorded"}{revision.publicationCause ? ` · cause ${revision.publicationCause}` : ""}</p></article>)}</details>
    </>}
  </section>;
}
