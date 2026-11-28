import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "./api";
import "./partDefinition.css";

type Definition = {
  partId: string; partNumber: string; revisionId: number; revisionLabel: string; revisionStatus: string;
  applicability: Record<string, any>; requirements: any[]; components: any[]; purchases: any[];
  revisionHistory: any[]; draft?: any; canApprove?: boolean; pendingChangeRequests?: any[]; staleChangeRequests?: any[]; source?: any; actions?: any;
};

const FAMILY_LABEL: Record<string, string> = { mcl: "Material Cut List", toolshop: "Tool Shop", cnc: "CNC" };
const EDITABLE_FAMILIES = ["mcl", "toolshop", "cnc"] as const;
const FALLBACK_UNITS = ["Nos", "each", "pcs", "kg", "g", "mm", "cm", "m", "in", "inch", "hour", "minute"];
const LENGTH_TO_MM: Record<string, number> = { mm: 1, millimeter: 1, millimeters: 1, cm: 10, centimeter: 10, centimeters: 10,
  m: 1000, meter: 1000, meters: 1000, metre: 1000, metres: 1000, in: 25.4, inch: 25.4, inches: 25.4 };
const MASS_TO_KG: Record<string, number> = { kg: 1, kilogram: 1, kilograms: 1, g: 0.001, gram: 0.001, grams: 0.001 };
const fieldStatusText = (status: unknown) => status === "missing" ? "missing in source" : status === "blank" ? "blank in source" : status === "value" || status === "present" ? "value present" : "source status not recorded";
function savedTotalWeightLabel(item: any, detail: any) {
  const statuses = item?.fieldStatus || {};
  const kgStatus = statuses.WeightKg, gramsStatus = statuses.WeightGrams;
  if ([kgStatus, gramsStatus].includes("blank")) return "blank in source";
  if (kgStatus === "missing" && gramsStatus === "missing") return "not recorded";
  const kgKnown = detail.WeightKg != null && kgStatus !== "missing";
  const gramsKnown = detail.WeightGrams != null && gramsStatus !== "missing";
  if (kgKnown && gramsKnown) {
    const kg = Number(detail.WeightKg), grams = Number(detail.WeightGrams);
    if (Number.isFinite(kg) && Number.isFinite(grams)) {
      if (Math.abs(kg - grams / 1000) <= Math.max(1e-9, Math.abs(kg) * 1e-9)) return `${detail.WeightKg} kg`;
      return `${detail.WeightKg} kg / ${detail.WeightGrams} g (saved values conflict)`;
    }
  }
  if (kgKnown) return `${detail.WeightKg} kg`;
  if (gramsKnown) return `${detail.WeightGrams} g`;
  if ((detail.WeightKg != null && Number(detail.WeightKg) === 0) || (detail.WeightGrams != null && Number(detail.WeightGrams) === 0))
    return "0 (source status not recorded)";
  return "not recorded";
}
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
const stableValue = (value: any): any => Array.isArray(value) ? value.map(stableValue) : value && typeof value === "object"
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, stableValue(value[key])])) : value;
const sameValue = (left: any, right: any) => JSON.stringify(stableValue(left)) === JSON.stringify(stableValue(right));
function changeRequestDiff(before: any, after: any) {
  const changes: Array<{ path: string; kind: string; before: any; after: any }> = [];
  const identityFor = (value: any) => value?.requirementKey || value?.componentIdentity || value?.specificationIdentity || value?.identity;
  function visit(oldValue: any, newValue: any, path: string) {
    if (sameValue(oldValue, newValue)) return;
    if (Array.isArray(oldValue) && Array.isArray(newValue)) {
      const oldIds = oldValue.map(identityFor), newIds = newValue.map(identityFor);
      if (oldIds.every(Boolean) && newIds.every(Boolean)) {
        const oldById = new Map(oldValue.map((item: any) => [identityFor(item), item]));
        const newById = new Map(newValue.map((item: any) => [identityFor(item), item]));
        for (const id of [...new Set([...oldById.keys(), ...newById.keys()])].sort()) {
          const itemPath = `${path}[${String(id)}]`;
          if (!oldById.has(id)) changes.push({ path: itemPath, kind: "added", before: null, after: newById.get(id) });
          else if (!newById.has(id)) changes.push({ path: itemPath, kind: "removed", before: oldById.get(id), after: null });
          else visit(oldById.get(id), newById.get(id), itemPath);
        }
        return;
      }
      const length = Math.max(oldValue.length, newValue.length);
      for (let index = 0; index < length; index += 1) {
        const itemPath = `${path}[${index + 1}]`;
        if (index >= oldValue.length) changes.push({ path: itemPath, kind: "added", before: null, after: newValue[index] });
        else if (index >= newValue.length) changes.push({ path: itemPath, kind: "removed", before: oldValue[index], after: null });
        else visit(oldValue[index], newValue[index], itemPath);
      }
      return;
    }
    if (oldValue && newValue && typeof oldValue === "object" && typeof newValue === "object" && !Array.isArray(oldValue) && !Array.isArray(newValue)) {
      for (const key of [...new Set([...Object.keys(oldValue), ...Object.keys(newValue)])].sort()) {
        const fieldPath = path ? `${path}.${key}` : key;
        if (!(key in oldValue)) changes.push({ path: fieldPath, kind: "added", before: null, after: newValue[key] });
        else if (!(key in newValue)) changes.push({ path: fieldPath, kind: "removed", before: oldValue[key], after: null });
        else visit(oldValue[key], newValue[key], fieldPath);
      }
      return;
    }
    changes.push({ path, kind: "changed", before: oldValue, after: newValue });
  }
  visit(before || {}, after || {}, "");
  return changes;
}
const reviewValue = (value: any) => value === null || value === undefined ? "—"
  : typeof value === "object" ? JSON.stringify(value) : String(value);
function readableChange(change: any, before: any, after: any) {
  const match = String(change.path || "").match(/^(requirements|components|purchases)\[([^\]]+)\](?:\.(.*))?$/);
  if (!match) {
    const family = String(change.path || "").match(/^applicability\.(mcl|toolshop|cnc)\.applicable$/)?.[1];
    return { row: family ? FAMILY_LABEL[family] : "Part definition", occurrence: "—", field: family ? "Family applicability" : String(change.path || "Definition"),
      before: reviewValue(change.before), after: reviewValue(change.after) };
  }
  const [, collection, identity, rawField] = match;
  const rowsBefore = Array.isArray(before?.[collection]) ? before[collection] : [];
  const rowsAfter = Array.isArray(after?.[collection]) ? after[collection] : [];
  const identityFor = (item: any) => item?.requirementKey || item?.componentIdentity || item?.specificationIdentity || item?.identity;
  const oldRow: any = rowsBefore.find((item: any) => String(identityFor(item)) === identity);
  const newRow: any = rowsAfter.find((item: any) => String(identityFor(item)) === identity);
  const row: any = newRow || oldRow || {};
  const detail = row.detail || {};
  const itemName = detail.ItemName || detail.PlatePart || detail.MaterialDisplayName || row.childPartNumber || row.childName || row.childPartId || row.code || row.description || row.sourcePartName || collection;
  const family = collection === "requirements" ? FAMILY_LABEL[row.family] || "Other process" : collection === "components" ? "Store" : "Purchase";
  const field = String(rawField || "");
  const tail = field.split(".").at(-1) || "";
  const labels: Record<string, string> = { quantity: "Quantity per Part", uom: "Quantity unit", family: "Family", childPartId: "Canonical child Part",
    sourcingRoute: "Sourcing route", DimensionMM: "Dimension to Cut", DimensionInches: "Dimension to Cut", DimensionText: "Dimension to Cut",
    DimensionUOM: "Cut-length unit", DimensionToCut: "Dimension to Cut", PartWeightKg: "Per Part Weight", WeightKg: "Saved total weight", WeightGrams: "Saved total weight",
    Quantity: "Normalized consumption", QuantityUOM: "Costing unit", Material: "Material To Use", MaterialDisplayName: "Material To Use",
    ItemName: "Item name", PlatePart: "Plate part to cut", ToolShopRemarks: "Tool Shop remarks", EngineeringAttributes: "Advanced engineering attributes",
    Length: "Plate length", Width: "Plate width", Thickness: "Plate thickness", manufacturer: "Manufacturer", manufacturerPartNumber: "Manufacturer part number",
    description: "Description", code: "Specification code", costingUOM: "Costing unit", costingCurrency: "Currency" };
  const formatRow = (value: any) => {
    if (!value) return "Not present";
    if (collection === "requirements") return `${FAMILY_LABEL[value.family] || "Other process"} · ${value.detail?.ItemName || value.detail?.PlatePart || value.detail?.MaterialDisplayName || value.sourcePartName || "Requirement"} · ${value.quantity ?? "—"} ${value.uom || ""}`.trim();
    if (collection === "components") return `${value.childPartNumber || value.childName || value.childPartId || "Unresolved child Part"} · ${value.quantity ?? "—"} ${value.uom || ""} · ${value.sourcingRoute || "auto"}`;
    return `${value.code || value.description || value.specificationIdentity || "Purchase specification"} · ${value.manufacturer || ""}`.trim();
  };
  function formatField(value: any, sourceRow: any) {
    if (value == null) return "—";
    const normalized = tail.toLowerCase();
    if (typeof value === "object") return reviewValue(value);
    if (normalized === "quantity" && !field.startsWith("detail.")) return `${value} ${sourceRow?.uom || ""}`.trim();
    if (tail === "Quantity" && field.startsWith("detail.")) return `${value} ${sourceRow?.detail?.QuantityUOM || ""}`.trim();
    if (tail === "DimensionMM") return `${value} mm`;
    if (tail === "DimensionInches") return `${value} in`;
    if (tail === "PartWeightKg" || tail === "WeightKg") return `${value} kg`;
    if (tail === "WeightGrams") return `${value} g`;
    return String(value);
  }
  const isWholeRow = !field;
  return { row: `${family} · ${itemName}`, occurrence: identity,
    field: isWholeRow ? (collection === "requirements" ? "Requirement row" : collection === "components" ? "Store row" : "Purchase specification") : labels[tail] || tail.replace(/([A-Z])/g, " $1").trim(),
    before: isWholeRow ? formatRow(change.before) : formatField(change.before, oldRow),
    after: isWholeRow ? formatRow(change.after) : formatField(change.after, newRow) };
}
function definitionContentForDiff(value: any, parts: any[] = []) {
  const content = value?.content && typeof value.content === "object" ? value.content : value || {};
  const numeric = (value: any) => {
    if (typeof value !== "number" && typeof value !== "string") return value;
    const text = String(value).trim();
    if (!text || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(text)) return value;
    const parsed = Number(text);
    return Number.isFinite(parsed) ? parsed : value;
  };
  const applicability = Object.fromEntries(["mcl", "toolshop", "cnc"].map(family => [family, {
    applicable: Boolean(content.applicability?.[family]?.applicable),
  }]));
  const requirements = Array.isArray(content.requirements) ? content.requirements.map((row: any) => {
    const detail = { ...(row.detail || {}) };
    if (typeof detail.EngineeringAttributes === "string") {
      try {
        const attributes = JSON.parse(detail.EngineeringAttributes);
        if (attributes && typeof attributes === "object" && !Array.isArray(attributes)) {
          delete attributes.fieldStatus;
          detail.EngineeringAttributes = attributes;
        }
      } catch { /* keep unparsed legacy attributes visible */ }
    } else if (detail.EngineeringAttributes && typeof detail.EngineeringAttributes === "object") {
      detail.EngineeringAttributes = { ...detail.EngineeringAttributes };
      delete detail.EngineeringAttributes.fieldStatus;
    }
    delete detail.fieldStatus;
    if ([detail.DimensionMM, detail.DimensionInches, detail.DimensionText, detail.DimensionUOM].some(item => item != null && String(item) !== ""))
      detail.DimensionToCut = cutLengthText(detail);
    delete detail.DimensionMM; delete detail.DimensionInches; delete detail.DimensionText; delete detail.DimensionUOM;
    if (detail.Quantity != null) detail.Quantity = numeric(detail.Quantity);
    if (detail.PartWeightKg != null) detail.PartWeightKg = numeric(detail.PartWeightKg);
    delete detail.ExactQuantityText; delete detail.ExactWeightKgText;
    return { requirementKey: row.requirementKey, family: row.family, quantity: numeric(row.quantity), uom: row.uom,
      sourcePartName: row.sourcePartName, detail };
  }) : [];
  const components = Array.isArray(content.components) ? content.components.map((row: any) => {
    const child = parts.find(part => String(part.id) === String(row.childPartId));
    const childLabel = [child?.partNumber, child?.name].filter(Boolean).join(" · ");
    return { componentIdentity: row.componentIdentity, childPartId: row.childPartId, childPartRecordId: row.childPartRecordId,
      childRevisionId: row.childRevisionId, childPartNumber: row.childPartNumber || childLabel || row.childPartId,
      childName: row.childName || child?.name || "", quantity: numeric(row.quantity), uom: row.uom,
      sourcingRoute: row.sourcingRoute, status: row.status };
  }) : [];
  return { applicability, requirements, components, purchases: Array.isArray(content.purchases) ? content.purchases : [] };
}
function positiveNumber(value: unknown) {
  if (typeof value !== "number" && typeof value !== "string") return false;
  const text = String(value).trim();
  if (!text || !/^(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(text)) return false;
  const number = Number(text);
  return Number.isFinite(number) && number > 0;
}
function decimalProduct(left: unknown, right: unknown): string | null {
  const parse = (value: unknown) => {
    const match = String(value ?? "").trim().match(/^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/);
    if (!match) return null;
    const fraction = match[3] || "";
    const exponent = Number(match[4] || 0);
    if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > 1000) return null;
    let coefficient = BigInt(`${match[2]}${fraction}` || "0") * (match[1] === "-" ? -1n : 1n);
    let scale = fraction.length - exponent;
    if (scale < 0) { coefficient *= 10n ** BigInt(-scale); scale = 0; }
    return { coefficient, scale };
  };
  const a = parse(left), b = parse(right);
  if (!a || !b || !Number.isFinite(Number(left) * Number(right))) return null;
  const coefficient = a.coefficient * b.coefficient;
  const scale = a.scale + b.scale;
  const negative = coefficient < 0n;
  let digits = (negative ? -coefficient : coefficient).toString();
  if (scale > 0) {
    digits = digits.padStart(scale + 1, "0");
    const split = digits.length - scale;
    digits = `${digits.slice(0, split)}.${digits.slice(split)}`.replace(/\.?0+$/, "").replace(/\.$/, "");
  }
  return `${negative ? "-" : ""}${digits || "0"}`;
}
function cutLengthValue(detail: any) {
  if (detail?.DimensionUOM && ["in", "inch", "inches"].includes(String(detail.DimensionUOM).toLowerCase()))
    return detail.DimensionInches ?? (detail.DimensionMM == null ? "" : Number(detail.DimensionMM) / 25.4);
  return detail?.DimensionMM ?? (detail?.DimensionInches == null ? "" : Number(detail.DimensionInches) * 25.4);
}
function cutLengthUnit(detail: any) { return ["in", "inch", "inches"].includes(String(detail?.DimensionUOM || "").toLowerCase()) ? "in" : "mm"; }
function cutLengthMillimeters(detail: any) {
  const value = Number(cutLengthValue(detail));
  if (!Number.isFinite(value) || value <= 0) return null;
  return cutLengthUnit(detail) === "in" ? value * LENGTH_TO_MM.in : value;
}
function cutLengthText(detail: any) {
  const text = String(detail?.DimensionText || "").trim();
  const mm = detail?.DimensionMM, inches = detail?.DimensionInches;
  if (mm != null && inches != null && Number.isFinite(Number(mm)) && Number.isFinite(Number(inches))
      && Math.abs(Number(mm) - Number(inches) * 25.4) > Math.max(0.01, Math.abs(Number(mm)) * 1e-7))
    return `${text || "Conflicting saved values"} · ${mm} mm / ${inches} in`;
  if (text) {
    const hasNumericDimension = mm != null || inches != null;
    if (hasNumericDimension && Number.isFinite(Number(text)) && !/\b(?:mm|cm|in(?:ch(?:es)?)?|m|meters?|metres?)\s*$/i.test(text))
      return `${text} ${cutLengthUnit(detail)}`;
    return text;
  }
  if (mm != null) return `${mm} mm`;
  if (inches != null) return `${inches} in`;
  return "—";
}
function engineeringAttributes(detail: any) {
  const value = detail?.EngineeringAttributes;
  if (typeof value === "string") {
    try { const parsed = JSON.parse(value); return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {}; }
    catch { return {}; }
  }
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}
function editableEngineeringAttributes(detail: any) {
  const editable = { ...engineeringAttributes(detail) };
  delete editable.materialWeightEvidence;
  return editable;
}
function kgText(value: number | null) {
  if (value == null || !Number.isFinite(value) || value <= 0) return null;
  return Number(value.toPrecision(10)).toString();
}
function cutWeight(item: any, materials: any[] = []) {
  const detail = item?.detail || {};
  const selected = materials.find(material => String(material.id) === String(detail.Material));
  const evidence = selected?.weightEvidence;
  const factor = Number(selected?.kgPerMeter ?? evidence?.kgPerMeter);
  const mm = cutLengthMillimeters(detail);
  const quantity = Number(item?.quantity);
  if (!Number.isFinite(factor) || factor <= 0 || mm == null) return { evidence, perPiece: null, total: null };
  const perPiece = mm / 1000 * factor;
  const total = perPiece * quantity;
  return { evidence, perPiece: Number.isFinite(perPiece) && perPiece > 0 ? perPiece : null,
    total: Number.isFinite(total) && total > 0 ? total : null };
}
function LocalMaterialCombobox({ materials, value, savedLabel, rowName, invalid, onSelect }: {
  materials: any[]; value: string; savedLabel: string; rowName: string; invalid: boolean; onSelect: (id: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const selected = materials.find(material => String(material.id) === value);
  const display = selected?.name || savedLabel || (value ? `Unresolved material identity ${value}` : "");
  const needle = query.trim().toLocaleLowerCase();
  const matches = materials.filter(material => material.selectable !== false && material.weightStatus !== "identity_conflict" && (!needle
    || [material.name, material.code, ...(Array.isArray(material.sourceAliases) ? material.sourceAliases : [])]
      .some(candidate => String(candidate || "").toLocaleLowerCase().includes(needle)))).slice(0, 12);
  const listId = `material-options-${rowName.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  const choose = (material: any) => { onSelect(String(material.id)); setQuery(""); setOpen(false); setActive(0); };
  return <div className="parts-definition-combobox">
    <input role="combobox" aria-label={`Material To Use — ${rowName}`} aria-autocomplete="list" aria-expanded={open}
      aria-controls={listId} aria-activedescendant={open && matches[active] ? `${listId}-option-${active}` : undefined}
      aria-invalid={invalid} autoComplete="off" value={open ? query : display}
      placeholder="Type a material name or alias" onFocus={() => { setOpen(true); setQuery(""); setActive(0); }}
      onBlur={() => { window.setTimeout(() => setOpen(false), 100); }}
      onChange={event => { setQuery(event.target.value); setOpen(true); setActive(0); }}
      onKeyDown={event => {
        if (event.key === "ArrowDown" && matches.length) { event.preventDefault(); setOpen(true); setActive(index => Math.min(index + 1, matches.length - 1)); }
        else if (event.key === "ArrowUp" && matches.length) { event.preventDefault(); setActive(index => Math.max(index - 1, 0)); }
        else if (event.key === "Enter" && open && matches[active]) { event.preventDefault(); choose(matches[active]); }
        else if (event.key === "Escape") { setOpen(false); setQuery(""); }
      }}/>
    {open && <div className="parts-definition-combobox-options" id={listId} role="listbox" aria-label={`Local materials for ${rowName}`}>
      {matches.length ? matches.map((material, index) => <div key={material.id} id={`${listId}-option-${index}`} role="option"
        aria-selected={String(material.id) === value} className={index === active ? "is-active" : ""}
        onMouseDown={event => event.preventDefault()} onClick={() => choose(material)}>
        <strong>{material.name || material.canonicalName || `Material ${material.id}`}</strong>
        <small>{[material.code && `Code ${material.code}`, `Local ID ${material.id}`,
          ...(material.sourceAliases || []).map((alias: string) => `also ${alias}`)].filter(Boolean).join(" · ")}</small>
      </div>) : <p role="status">No local material matches. Existing saved text is retained until you choose a material.</p>}
    </div>}
  </div>;
}
function materialWeightFactor(detail: any, materials: any[] = []) {
  const selected = materials.find(material => String(material.id) === String(detail?.Material));
  const evidence = selected?.weightEvidence;
  const factor = Number(evidence?.kgPerMeter ?? selected?.kgPerMeter);
  return Number.isFinite(factor) && factor > 0 ? factor : null;
}
function stablePhysicalNumber(value: number) {
  return Number.isFinite(value) ? Number(value.toPrecision(15)) : null;
}
function derivedConsumptionForPhysicalBasis(family: string, detail: any, unit: string, materials: any[] = []) {
  const normalizedUnit = String(unit || "").trim().toLowerCase();
  if (["mcl", "toolshop"].includes(family)) {
    const mm = cutLengthMillimeters(detail);
    if (mm == null) return null;
    if (LENGTH_TO_MM[normalizedUnit]) return stablePhysicalNumber(mm / LENGTH_TO_MM[normalizedUnit]);
    const massFactor = MASS_TO_KG[normalizedUnit];
    const kgPerMeter = materialWeightFactor(detail, materials);
    if (massFactor && kgPerMeter) return stablePhysicalNumber((mm / 1000 * kgPerMeter) / massFactor);
  }
  if (family === "cnc" && MASS_TO_KG[normalizedUnit]) {
    const weight = Number(detail?.PartWeightKg);
    return Number.isFinite(weight) && weight > 0 ? stablePhysicalNumber(weight / MASS_TO_KG[normalizedUnit]) : null;
  }
  return null;
}
function usesPhysicalConsumptionBasis(family: string, unit: string) {
  const normalizedUnit = String(unit || "").trim().toLowerCase();
  return (["mcl", "toolshop"].includes(family) && (Boolean(LENGTH_TO_MM[normalizedUnit]) || Boolean(MASS_TO_KG[normalizedUnit])))
    || (family === "cnc" && (Boolean(MASS_TO_KG[normalizedUnit]) || Boolean(LENGTH_TO_MM[normalizedUnit])));
}
function savedTotalWeightDescription(item: any) {
  const detail = item?.detail || {};
  const status = item?.fieldStatus || {};
  const describe = (field: "WeightKg" | "WeightGrams", unit: string) => {
    if (status[field] === "missing") return "not recorded";
    if (status[field] === "blank") return "blank in source";
    const value = detail[field];
    if (value == null || value === "") return "not recorded";
    return `${value} ${unit}${status[field] ? "" : " · source status unavailable"}`;
  };
  const kg = describe("WeightKg", "kg"), grams = describe("WeightGrams", "g");
  if (kg === "not recorded" && grams === "not recorded") return "Saved total weight: not recorded";
  return `Saved total weight fields: WeightKg ${kg}; WeightGrams ${grams}`;
}
function isCrossDimensionConsumption(family: string, unit: string) {
  const normalized = String(unit || "").trim().toLowerCase();
  return (["mcl", "toolshop"].includes(family) && Boolean(MASS_TO_KG[normalized]))
    || (family === "cnc" && Boolean(LENGTH_TO_MM[normalized]));
}
function hasCrossDimensionPhysicalValue(row: any) {
  const detail = row?.detail || {};
  return ["mcl", "toolshop"].includes(row?.family)
    ? positiveNumber(cutLengthValue(detail))
    : row?.family === "cnc" && positiveNumber(detail.PartWeightKg);
}
function missingCrossDimensionConsumption(row: any) {
  return isCrossDimensionConsumption(String(row?.family || ""), String(row?.detail?.QuantityUOM || ""))
    && hasCrossDimensionPhysicalValue(row) && !positiveNumber(row?.detail?.Quantity);
}
function physicalNumberChanged(value: any, prior: any, fresh: boolean) {
  if (fresh) return true;
  const valueBlank = value == null || String(value).trim() === "";
  const priorBlank = prior == null || String(prior).trim() === "";
  if (valueBlank || priorBlank) return valueBlank !== priorBlank;
  const parsed = Number(value), oldParsed = Number(prior);
  if (Number.isFinite(parsed) && Number.isFinite(oldParsed)) return parsed !== oldParsed;
  return !sameValue(value, prior);
}
function ChangeReview({ before, after, title, parts = [] }: { before: any; after: any; title: string; parts?: any[] }) {
  const reviewBefore = definitionContentForDiff(before, parts), reviewAfter = definitionContentForDiff(after, parts);
  const changes = changeRequestDiff(reviewBefore, reviewAfter);
  return <section className="parts-definition-diff" aria-label={title}>
    <h4>{title}</h4>
    <p>Saved before/after values from this Change Request. The table includes additions, field changes and removals.</p>
    {changes.length === 0 ? <p>No definition differences were recorded.</p> : <div className="parts-definition-diff-scroll"><table>
      <thead><tr><th>Change</th><th>Family / item</th><th>Occurrence reference</th><th>Field</th><th>Before</th><th>After</th></tr></thead>
      <tbody>{changes.map((change, index) => <tr key={`${change.path}:${index}`}>
        {(() => { const readable = readableChange(change, reviewBefore, reviewAfter); return <><td>{change.kind}</td><td>{readable.row}</td><td>{readable.occurrence}</td><td>{readable.field}</td><td>{readable.before}</td><td>{readable.after}</td></>; })()}
      </tr>)}</tbody>
    </table></div>}
  </section>;
}
function savedAttempt(partId: string, action: string) {
  const storageKey = `safari.part-definition:${partId}:${action}`;
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || "null");
    return saved?.status === "pending" && saved?.key && typeof saved?.payload === "string"
      ? { key: saved.key as string, storageKey, payload: saved.payload as string } : null;
  } catch { return null; }
}
const savedDraftAttempt = (partId: string) => savedAttempt(partId, "draft-save");
function materialBindingAttempt(partId: string, requirementKey: string, payload: Record<string, unknown>) {
  const storageKey = `safari.part-definition:${partId}:material-binding:${encodeURIComponent(requirementKey)}`;
  const signature = JSON.stringify(payload);
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || "null");
    if (saved?.status === "pending" && saved?.key) {
      if (saved.signature === signature) return { key: String(saved.key), storageKey };
      throw new Error("Recover the earlier material review before changing its source, selection, or reason.");
    }
  } catch (cause) {
    if (cause instanceof Error && cause.message.startsWith("Recover the earlier material review")) throw cause;
  }
  const key = `material-binding:${crypto.randomUUID()}`;
  try { localStorage.setItem(storageKey, JSON.stringify({ key, signature, status: "pending" })); } catch { /* the server journal still protects a delivered request */ }
  return { key, storageKey };
}
function clearMaterialBindingAttempt(storageKey: string) {
  try { localStorage.removeItem(storageKey); } catch { /* server request journal remains authoritative */ }
}

export function PartDefinitionMaintenance({ partId, onSaved, onCompare }: { partId: string; onSaved: () => void; onCompare: () => void }) {
  const [definition, setDefinition] = useState<Definition | null>(null);
  const [options, setOptions] = useState<any>(null);
  const [content, setContent] = useState<any>(null);
  const [attributeText, setAttributeText] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [reopenReason, setReopenReason] = useState("");
  const [editing, setEditing] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pendingSave, setPendingSave] = useState<{ key: string; storageKey: string; payload: string } | null>(null);
  const [pendingSubmit, setPendingSubmit] = useState<{ key: string; storageKey: string; payload: string } | null>(null);
  const [pendingApproval, setPendingApproval] = useState<{ key: string; storageKey: string; payload: string } | null>(null);
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});
  const [materialMasterSearch, setMaterialMasterSearch] = useState<Record<string, string>>({});
  const [materialMasterSelection, setMaterialMasterSelection] = useState<Record<string, string>>({});
  const [pendingRemoval, setPendingRemoval] = useState<{ description: string; confirm: () => void } | null>(null);
  const removalCancelRef = useRef<HTMLButtonElement | null>(null);
  const removalDialogRef = useRef<HTMLDivElement | null>(null);
  const removalFocusRef = useRef<{ trigger: HTMLElement | null; fallback: HTMLElement | null }>({ trigger: null, fallback: null });
  const [focusRequirementKey, setFocusRequirementKey] = useState<string | null>(null);
  const [focusComponentIdentity, setFocusComponentIdentity] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function reload() {
    setLoading(true);
    setPendingSave(savedDraftAttempt(partId));
    setPendingSubmit(savedAttempt(partId, "change-request"));
    setPendingApproval(savedAttempt(partId, "approve-publish"));
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
  useEffect(() => {
    if (!focusRequirementKey) return;
    const row = [...document.querySelectorAll<HTMLElement>("[data-requirement-key]")]
      .find(element => element.dataset.requirementKey === focusRequirementKey);
    const first = row?.querySelector<HTMLElement>("select:not([disabled]), input:not([disabled]), textarea:not([disabled])");
    first?.focus(); first?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
    setFocusRequirementKey(null);
  }, [focusRequirementKey, content?.requirements]);
  useEffect(() => {
    if (!focusComponentIdentity) return;
    const row = [...document.querySelectorAll<HTMLElement>("[data-component-key]")]
      .find(element => element.dataset.componentKey === focusComponentIdentity);
    const first = row?.querySelector<HTMLElement>("select:not([disabled]), input:not([disabled]), textarea:not([disabled])");
    first?.focus(); first?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
    setFocusComponentIdentity(null);
  }, [focusComponentIdentity, content?.components]);
  useEffect(() => {
    if (!pendingRemoval) return;
    removalCancelRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault(); setPendingRemoval(null);
        window.setTimeout(() => {
          const { trigger, fallback } = removalFocusRef.current;
          (trigger?.isConnected ? trigger : fallback)?.focus();
        }, 0);
      } else if (event.key === "Tab") {
        const buttons = [...(removalDialogRef.current?.querySelectorAll<HTMLButtonElement>("button:not([disabled])") || [])];
        if (buttons.length < 2) return;
        const first = buttons[0], last = buttons[buttons.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [pendingRemoval]);

  const pending = useMemo(() => definition?.pendingChangeRequests?.[0] || null, [definition]);
  const pendingDependents = pending?.impact?.dependents || pending?.impact?.assemblies || [];
  const isFinalized = definition?.revisionStatus === "finalized";

  function patchContent(update: Record<string, unknown>) {
    setContent((old: any) => ({ ...old, ...update }));
    setDirty(true); setNotice(""); setValidationErrors({});
  }

  function requestRemoval(description: string, confirm: () => void) {
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const family = trigger?.closest(".parts-definition-family");
    const fallback = (family?.querySelector("h4") || document.querySelector(".parts-definition-heading h3")) as HTMLElement | null;
    removalFocusRef.current = { trigger, fallback };
    setPendingRemoval({ description, confirm });
  }

  function finishRemoval(confirmed: boolean) {
    if (!pendingRemoval) return;
    const pending = pendingRemoval;
    if (confirmed) pending.confirm();
    setPendingRemoval(null);
    window.setTimeout(() => {
      const { trigger, fallback } = removalFocusRef.current;
      (trigger?.isConnected ? trigger : fallback)?.focus();
    }, 0);
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
      fieldStatus: { ...(current.fieldStatus || {}), [field]: field === "Quantity"
        ? (value === "" ? "missing" : "manual-reaffirmed") : value === "" ? "blank" : "value" } };
    patchContent({ requirements: rows });
  }

  function updateRequirementMaterial(index: number, value: string) {
    const rows = [...(content?.requirements || [])];
    const current = rows[index] || {};
    const material = (options?.materials || []).find((item: any) => String(item.id) === value);
    const detail = { ...(current.detail || {}), Material: value ? Number(value) : null,
      ...(material ? { MaterialDisplayName: material.name } : {}) };
    const unit = String(detail.QuantityUOM || "").trim().toLowerCase();
    const derived = derivedConsumptionForPhysicalBasis(current.family, detail, unit, options?.materials || []);
    const pieceUnits = new Set(["nos", "no", "each", "ea", "pcs", "piece", "pieces"]);
    const legacyQuantity = Number(detail.Quantity), occurrenceQuantity = Number(current.quantity);
    const lengthMm = cutLengthMillimeters(detail) ?? NaN;
    const factor = material ? materialWeightFactor(detail, options?.materials || []) : null;
    const safelyResolveLegacyMcl = current.family === "mcl" && current.materialConsumptionStatus === "legacy_duplicate_ambiguous"
      && material && pieceUnits.has(unit) && Number.isFinite(legacyQuantity) && legacyQuantity > 0
      && Number.isFinite(occurrenceQuantity) && occurrenceQuantity > 0 && legacyQuantity === occurrenceQuantity
      && Number.isFinite(lengthMm) && lengthMm > 0 && factor != null && factor > 0;
    if (safelyResolveLegacyMcl) {
      // A known physical MCL row already contains its cut length and parent
      // occurrence count. Selecting the canonical material supplies the only
      // missing factor, so normalize the duplicated legacy count once to kg/piece.
      detail.QuantityUOM = "kg";
      detail.Quantity = lengthMm / 1000 * factor;
      detail.ExactQuantityText = String(detail.Quantity);
    }
    if (usesPhysicalConsumptionBasis(current.family, unit)) {
      detail.Quantity = derived == null ? "" : derived;
      detail.ExactQuantityText = derived == null ? "" : String(derived);
    }
    rows[index] = { ...current, detail, materialWeightEvidence: material?.weightEvidence || { status: "unavailable" },
      ...(safelyResolveLegacyMcl ? { materialConsumptionStatus: "resolved", materialConsumptionResolution: "mcl_kg_per_piece" } : {}),
      fieldStatus: { ...(current.fieldStatus || {}),
        ...(safelyResolveLegacyMcl ? { Quantity: "derived" } : {}),
        ...(usesPhysicalConsumptionBasis(current.family, unit) ? { Quantity: derived == null ? "missing" : "derived" } : {}) } };
    patchContent({ requirements: rows });
  }

  async function reviewAndBindMaterial(index: number, captureCanonicalIdentity = false) {
    const item = content?.requirements?.[index];
    const materialId = Number(item?.detail?.Material);
    const sourceMasterMaterialId = Number(materialMasterSelection[item?.requirementKey || ""]);
    if ((!captureCanonicalIdentity && (!Number.isInteger(materialId) || materialId <= 0))
        || !Number.isInteger(sourceMasterMaterialId) || sourceMasterMaterialId <= 0 || !reason.trim()) {
      setError("Choose a Costing-New master and enter the review reason before binding or capturing its canonical identity.");
      return;
    }
    setBusy(true); setError(""); setNotice("");
    const payload = { ...(captureCanonicalIdentity ? {} : { materialId }), sourceMasterMaterialId, reason: reason.trim(),
      ...(captureCanonicalIdentity ? { captureCanonicalIdentity: true } : {}) };
    let attempt: { key: string; storageKey: string };
    try {
      attempt = materialBindingAttempt(partId, String(item.requirementKey || "unidentified-row"), payload);
      const result = await api.bindPartDefinitionMaterial(partId, payload, attempt.key);
      clearMaterialBindingAttempt(attempt.storageKey);
      const nextOptions = await api.partDefinitionOptions(partId);
      setOptions(nextOptions);
      const selectedMaterialId = Number(result.materialId ?? materialId);
      const trustedMaterial = (nextOptions.materials || []).find((candidate: any) => String(candidate.id) === String(selectedMaterialId));
      if (captureCanonicalIdentity && !trustedMaterial) throw new Error("The reviewed identity was saved, but a fresh Safari read did not return its canonical Material. Reload before editing this row.");
      if (captureCanonicalIdentity && trustedMaterial) {
        setContent((old: any) => {
          const requirements = [...(old?.requirements || [])];
          const current = requirements[index];
          if (!current) return old;
          const detail = { ...(current.detail || {}), Material: selectedMaterialId, MaterialDisplayName: trustedMaterial.name };
          const unit = String(detail.QuantityUOM || "").trim().toLowerCase();
          const derived = derivedConsumptionForPhysicalBasis(current.family, detail, unit, nextOptions.materials || []);
          if (usesPhysicalConsumptionBasis(current.family, unit)) {
            detail.Quantity = derived == null ? "" : derived;
            detail.ExactQuantityText = derived == null ? "" : String(derived);
          }
          requirements[index] = { ...current, detail, materialWeightEvidence: trustedMaterial.weightEvidence || { status: "unavailable" },
            fieldStatus: { ...(current.fieldStatus || {}), ...(usesPhysicalConsumptionBasis(current.family, unit)
              ? { Quantity: derived == null ? "missing" : "derived" } : {}) } };
          return { ...old, requirements };
        });
        setDirty(true);
      } else {
        setContent((old: any) => ({ ...old, requirements: (old?.requirements || []).map((row: any, rowIndex: number) =>
          rowIndex === index ? { ...row, materialWeightEvidence: trustedMaterial?.weightEvidence || { status: "unavailable" } } : row) }));
      }
      const masterName = nextOptions.materialCatalog?.find((candidate: any) => candidate.masterMaterialId === sourceMasterMaterialId)?.canonicalName || "Costing-New material";
      setNotice(captureCanonicalIdentity
        ? `Reviewed ${masterName} is now a canonical Safari Material and is selected for this row. Save the draft to persist the row selection.`
        : `Reviewed ${masterName} binding is saved. The selected Safari Material identity remains ${trustedMaterial?.name || materialId}.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The material binding could not be saved.");
    } finally { setBusy(false); }
  }

  function addRequirement(family: typeof EDITABLE_FAMILIES[number]) {
    if (!content || busy) return;
    const requirementKey = `safari-authored-requirement:${crypto.randomUUID()}`;
    const processType = family === "mcl" ? "material_cut" : family === "toolshop" ? "tool_shop" : "cnc";
    const row = { requirementKey, identityStatus: "safari_authored", family, quantity: "1", uom: "Nos", processType,
      detail: { ProcessType: processType, Quantity: "", QuantityUOM: "kg", EngineeringAttributes: {} },
      provenance: { type: "safari_authored", evidence: {} } };
    patchContent({ requirements: [...(content.requirements || []), row], applicability: {
      ...content.applicability, [family]: { ...(content.applicability?.[family] || {}), applicable: true, status: "applicable" },
    } });
    setFocusRequirementKey(requirementKey);
    setNotice(`${FAMILY_LABEL[family]} marked applicable in this draft because you chose Add ${family.toUpperCase()} row.`);
  }

  function updateCutLength(index: number, value: string, unit: "mm" | "in") {
    const rows = [...(content?.requirements || [])];
    const current = rows[index] || {};
    const detail = { ...(current.detail || {}) };
    const number = value.trim() === "" ? NaN : Number(value);
    detail.DimensionUOM = unit;
    detail.DimensionText = value.trim() ? `${value} ${unit}` : "";
    if (Number.isFinite(number) && number > 0) {
      const mm = unit === "mm" ? number : number * 25.4;
      detail.DimensionMM = mm;
      detail.DimensionInches = mm / 25.4;
    } else {
      detail.DimensionMM = value;
      detail.DimensionInches = value;
    }
    const rateUnit = String(detail.QuantityUOM || "").trim().toLowerCase();
    const derived = derivedConsumptionForPhysicalBasis(current.family, detail, rateUnit, options?.materials || []);
    if (usesPhysicalConsumptionBasis(current.family, rateUnit)) {
      detail.Quantity = derived == null ? "" : derived;
      detail.ExactQuantityText = derived == null ? "" : String(derived);
    }
    rows[index] = { ...current, detail, fieldStatus: { ...(current.fieldStatus || {}), DimensionText: value ? "value" : "blank",
      ...(usesPhysicalConsumptionBasis(current.family, rateUnit) ? { Quantity: derived == null ? "missing" : "derived" } : {}) } };
    patchContent({ requirements: rows });
  }

  function updatePartWeight(index: number, value: string) {
    const rows = [...(content?.requirements || [])];
    const current = rows[index] || {};
    const detail = { ...(current.detail || {}), PartWeightKg: value, ExactWeightKgText: value };
    const number = value.trim() === "" ? NaN : Number(value);
    const rateUnit = String(detail.QuantityUOM || "").trim().toLowerCase();
    const factor = MASS_TO_KG[rateUnit];
    const derivedMass = Number.isFinite(number) && number > 0 && factor;
    if (derivedMass) { detail.Quantity = number / factor; detail.ExactQuantityText = String(detail.Quantity); }
    else if (factor || LENGTH_TO_MM[rateUnit]) { detail.Quantity = ""; detail.ExactQuantityText = ""; }
    rows[index] = { ...current, detail, fieldStatus: { ...(current.fieldStatus || {}), PartWeightKg: value ? "value" : "blank",
      ...(derivedMass ? { Quantity: "derived" } : (factor || LENGTH_TO_MM[rateUnit]) ? { Quantity: "missing" } : {}) } };
    patchContent({ requirements: rows });
  }

  function validateDraftContent() {
    const errors: Record<string, string> = {};
    const oldRequirements = new Map((definition?.requirements || []).map((item: any) => [item.requirementKey, item]));
    for (const row of content?.requirements || []) {
      const old: any = oldRequirements.get(row.requirementKey) || (row.legacyOccurrenceKey
        ? (definition?.requirements || []).find((item: any) => item.requirementKey === row.legacyOccurrenceKey) : undefined);
      const fresh = !old;
      const changed = (field: string, source: any = row, prior: any = old) => fresh || !sameValue(source?.[field], prior?.[field]);
      const error = (field: string, message: string) => { errors[`${row.requirementKey}:${field}`] = message; };
      if (changed("quantity") && !positiveNumber(row.quantity)) error("quantity", "Enter a finite quantity greater than zero.");
      if (changed("uom") && !String(row.uom || "").trim()) error("uom", "Choose a quantity unit.");
      const detail = row.detail || {}, oldDetail = old?.detail || {};
      const legacyBasisChanged = ["quantity", "uom"].some(field => changed(field))
        || ["Material", "Quantity", "QuantityUOM", "DimensionMM", "DimensionInches", "DimensionText", "DimensionUOM"]
          .some(field => changed(field, detail, oldDetail));
      if (row.materialConsumptionStatus === "legacy_duplicate_ambiguous" && legacyBasisChanged
          && !["weight_per_piece", "one_item_per_occurrence", "mcl_kg_per_piece"].includes(row.materialConsumptionResolution))
        error("materialBasis", "Choose a canonical material with a verified factor to resolve this legacy physical row.");
      for (const name of ["DimensionMM", "DimensionInches", "PartWeightKg", "Length", "Width", "Thickness"]) {
        if (!physicalNumberChanged(detail[name], oldDetail[name], fresh) || detail[name] == null || String(detail[name]).trim() === "") continue;
        if (!positiveNumber(detail[name])) error(name === "PartWeightKg" ? "partWeight" : name === "DimensionMM" || name === "DimensionInches" ? "cutLength" : "dimensions",
          `${name} must be a finite number greater than zero.`);
      }
      const rateNeedsValidation = fresh || changed("Quantity", detail, oldDetail) || changed("QuantityUOM", detail, oldDetail);
      const missingConversion = missingCrossDimensionConsumption(row);
      if (rateNeedsValidation && !positiveNumber(detail.Quantity) && !missingConversion) error("rateQuantity", "Enter finite positive consumption per occurrence.");
      if (rateNeedsValidation && !String(detail.QuantityUOM || "").trim()) error("rateUnit", "Choose the consumption unit used by the existing rate basis.");
      if (["mcl", "toolshop"].includes(row.family) && (fresh || changed("Material", detail, oldDetail)) && !detail.Material)
        error("material", "Choose the canonical material identity.");
      const cutChanged = fresh || ["DimensionMM", "DimensionInches", "DimensionText", "DimensionUOM"].some(field => changed(field, detail, oldDetail));
      const rateUnit = String(detail.QuantityUOM || "").trim().toLowerCase();
      const rateBasisChanged = fresh || changed("QuantityUOM", detail, oldDetail);
      if ((cutChanged || rateBasisChanged) && ["mcl", "toolshop"].includes(row.family) && LENGTH_TO_MM[rateUnit]
          && !positiveNumber(cutLengthValue(detail))) error("cutLength", "Enter a finite positive cut length for this length-based rate.");
      const weightChanged = fresh || changed("PartWeightKg", detail, oldDetail);
      if ((weightChanged || rateBasisChanged) && row.family === "cnc" && MASS_TO_KG[rateUnit]
          && !positiveNumber(detail.PartWeightKg)) error("partWeight", "Enter Per Part Weight in kg for this kg-based rate.");
      const hasPlateDimensions = [detail.Length, detail.Width, detail.Thickness].some(value => value != null && String(value).trim() !== "");
      const plateDimensionsChanged = ["Length", "Width", "Thickness", "DimensionUOM"].some(field => physicalNumberChanged(detail[field], oldDetail[field], fresh));
      if (row.family === "cnc" && plateDimensionsChanged && hasPlateDimensions
          && (![detail.Length, detail.Width, detail.Thickness].every(positiveNumber) || !LENGTH_TO_MM[String(detail.DimensionUOM || "").toLowerCase()]))
        error("dimensions", "CNC plate length, width and thickness must be finite positive values with a supported dimension unit.");
    }
    for (const row of content?.components || []) {
      const old: any = (definition?.components || []).find((item: any) => item.componentIdentity === row.componentIdentity);
      const fresh = !old;
      if ((fresh || row.childPartId !== old?.childPartId) && !(options?.parts || []).some((item: any) => item.id === row.childPartId))
        errors[`${row.componentIdentity}:childPart`] = "Choose an available canonical child Part.";
      if ((fresh || row.quantity !== old?.quantity) && !positiveNumber(row.quantity)) errors[`${row.componentIdentity}:quantity`] = "Enter a finite quantity greater than zero.";
      if ((fresh || row.uom !== old?.uom) && !String(row.uom || "").trim()) errors[`${row.componentIdentity}:uom`] = "Choose a quantity unit.";
    }
    setValidationErrors(errors);
    return Object.keys(errors).length === 0;
  }

  function updateComponent(index: number, update: Record<string, unknown>) {
    const rows = [...(content?.components || [])]; rows[index] = { ...rows[index], ...update };
    patchContent({ components: rows });
  }

  function updatePurchase(index: number, update: Record<string, unknown>) {
    const rows = [...(content?.purchases || [])]; rows[index] = { ...rows[index], ...update };
    patchContent({ purchases: rows });
  }

  function addStoreRow() {
    const componentIdentity = `ui-component:${crypto.randomUUID()}`;
    patchContent({ components: [...(content?.components || []), { componentIdentity, childPartId: "", quantity: "1", uom: "Nos", sourcingRoute: "buy", provenance: "Safari-authored component" }] });
    setFocusComponentIdentity(componentIdentity);
  }

  async function saveDraft() {
    if (!definition || !reason.trim() || pendingSave || pendingSubmit || pendingApproval) { setError(pendingSave || pendingSubmit || pendingApproval ? "Resolve the saved request before editing or saving another draft." : "Enter the engineering reason before saving a draft."); return; }
    if (!validateDraftContent()) { setError("Fix the highlighted requirement and Store row fields before saving this draft."); return; }
    const activeAttributeTexts = [
      ...(content?.requirements || []).map((item: any) => attributeText[`requirement:${item.requirementKey}`]),
      ...(content?.purchases || []).map((item: any) => attributeText[`purchase:${item.specificationIdentity}`]),
    ].filter((value): value is string => value !== undefined);
    if (activeAttributeTexts.some(value => { try { const parsed = JSON.parse(value); return !parsed || typeof parsed !== "object" || Array.isArray(parsed); } catch { return true; } })) {
      setError("Finish each attributes field with a valid JSON object before saving the draft."); return;
    }
    setBusy(true); setError(""); setNotice("");
    const authoredContent = JSON.parse(JSON.stringify(content));
    const payload = { ...authoredContent, baseRevisionId: definition.revisionId, draftKey: definition.draft?.draftKey,
      expectedSaveVersion: definition.draft?.saveVersion || 0, reason };
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
        else { setContent(authoredContent); setDirty(true); }
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
    if (pendingSubmit || pendingApproval) { setError("Confirm the original request before requesting another approval."); return; }
    if (!definition?.draft || dirty || !reason.trim()) { setError("Save the current draft and reason before requesting approval."); return; }
    setBusy(true); setError(""); setNotice("");
    const payload = { draftKey: definition.draft.draftKey, reason };
    const attempt = operationKey(partId, "change-request", payload);
    if ("conflict" in attempt && attempt.conflict) { setPendingSubmit(savedAttempt(partId, "change-request")); setError("Confirm the original submission before requesting another approval."); setBusy(false); return; }
    const saved = { key: attempt.key, storageKey: attempt.storageKey, payload: JSON.stringify(payload) };
    setPendingSubmit(saved);
    await confirmSubmission(saved);
  }

  async function confirmSubmission(attempt: { key: string; storageKey: string; payload: string }) {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await api.submitPartDefinitionChangeRequest(partId, JSON.parse(attempt.payload), attempt.key);
      clearOperationKey(attempt.storageKey);
      setPendingSubmit(null);
      await reload(); setNotice(`Change Request submitted for review. ${result.impact?.dependents?.length ?? result.impact?.assemblies?.length ?? 0} current dependent Part/assembly successor(s) and ${result.impact?.modelCodeConfigurations?.length || 0} configuration successor(s) are in the reviewed closure.`);
      setEditing(false);
    } catch (cause) {
      if (cause instanceof ApiError && cause.retryDisposition === "safe_to_edit" && ["PART_CHANGE_REQUEST_INPUT_REQUIRED", "PART_DEFINITION_DRAFT_REQUIRED", "PART_DEFINITION_DRAFT_IMMUTABLE", "PART_DEFINITION_STALE_BASE", "PART_DEFINITION_DRAFT_INVALID", "PART_CHANGE_REQUEST_IMPACT_BLOCKED"].includes(cause.code)) {
        clearOperationKey(attempt.storageKey); setPendingSubmit(null);
        await reload();
      }
      setError(cause instanceof Error ? cause.message : "The Change Request could not be submitted.");
    }
    finally { setBusy(false); }
  }

  async function reopenStaleChangeRequest(changeRequest: any) {
    if (pendingSubmit || pendingApproval) { setError("Confirm the original request before reopening another change."); return; }
    if (!reopenReason.trim()) { setError("Enter a reason before reopening the stale Change Request as a new draft."); return; }
    setBusy(true); setError(""); setNotice("");
    const payload = { reason: reopenReason.trim() };
    const attempt = operationKey(partId, `reopen-${changeRequest.key}`, payload);
    try {
      const result = await api.reopenPartDefinitionChangeRequest(partId, changeRequest.key, payload, attempt.key);
      clearOperationKey(attempt.storageKey);
      await reload(); setEditing(true); setNotice(`Saved changes reopened as draft v${result.saveVersion}. Review the current accepted definition and update the draft before requesting fresh approval.`);
    } catch (cause) {
      if (cause instanceof ApiError && cause.status < 500) { clearOperationKey(attempt.storageKey); await reload(); }
      setError(cause instanceof Error ? cause.message : "The stale Change Request could not be reopened.");
    } finally { setBusy(false); }
  }

  async function approveAndPublish() {
    if (!pending?.key || pendingApproval) return;
    setBusy(true); setError(""); setNotice("");
    let attempt: { key: string; storageKey: string; conflict?: boolean };
    const previous = savedAttempt(partId, "approve-publish");
    if (pending.approvalRecoveryKey && !previous) {
      const storageKey = `safari.part-definition:${partId}:approve-publish`;
      const payload = JSON.stringify({ changeRequestKey: pending.key });
      attempt = { key: pending.approvalRecoveryKey, storageKey };
      try { localStorage.setItem(storageKey, JSON.stringify({ key: attempt.key, payload, status: "pending" })); } catch { /* recovery remains available from the server */ }
    } else {
      attempt = operationKey(partId, "approve-publish", { changeRequestKey: pending.key });
    }
    if ("conflict" in attempt && attempt.conflict) { setPendingApproval(savedAttempt(partId, "approve-publish")); setError("Recover the original approval before starting another."); setBusy(false); return; }
    const saved = { key: attempt.key, storageKey: attempt.storageKey, payload: JSON.stringify({ changeRequestKey: pending.key }) };
    setPendingApproval(saved);
    await confirmApproval(saved);
  }

  async function confirmApproval(attempt: { key: string; storageKey: string; payload: string }) {
    let changeRequestKey: string;
    try {
      const payload = JSON.parse(attempt.payload);
      if (!payload || Object.keys(payload).length !== 1 || typeof payload.changeRequestKey !== "string" || !payload.changeRequestKey) throw new Error();
      changeRequestKey = payload.changeRequestKey;
    } catch { setError("The saved approval envelope is invalid; recover the verified publication from its saved status."); return; }
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await api.approveAndPublishPartDefinition(changeRequestKey, attempt.key);
      clearOperationKey(attempt.storageKey);
      setPendingApproval(null);
      await reload(); onSaved(); setEditing(false);
      setNotice(`Accepted revision ${result.publishedRevisionLabel} is published. ${result.dependentPartCount} current dependent Part/assembly successor(s) and ${result.configurationCount} current configuration successor(s) were versioned.`);
    } catch (cause) {
      if (cause instanceof ApiError && cause.retryDisposition === "safe_to_edit") { clearOperationKey(attempt.storageKey); setPendingApproval(null); await reload(); }
      setError(cause instanceof Error ? cause.message : "The approved Change Request could not be published. Retry the same request if Grist reports an uncertain result.");
    }
    finally { setBusy(false); }
  }

  const unitOptions: string[] = [...new Set([...(options?.units || []), ...FALLBACK_UNITS])];
  function updateRateBasisUnit(index: number, value: string) {
    const rows = [...(content?.requirements || [])];
    const current = rows[index] || {};
    const detail = { ...(current.detail || {}), QuantityUOM: value };
    const unit = value.trim().toLowerCase();
    const derived = derivedConsumptionForPhysicalBasis(current.family, detail, unit, options?.materials || []);
    if (usesPhysicalConsumptionBasis(current.family, unit)) {
      detail.Quantity = derived == null ? "" : derived;
      detail.ExactQuantityText = derived == null ? "" : String(derived);
    }
    rows[index] = { ...current, detail };
    if (usesPhysicalConsumptionBasis(current.family, unit) && derived == null) {
      rows[index] = { ...rows[index], fieldStatus: { ...(current.fieldStatus || {}), Quantity: "missing" } };
    } else if (usesPhysicalConsumptionBasis(current.family, unit)) {
      rows[index] = { ...rows[index], fieldStatus: { ...(current.fieldStatus || {}), Quantity: "derived" } };
    }
    patchContent({ requirements: rows });
  }

  function renderRequirementTable(family: typeof EDITABLE_FAMILIES[number]) {
    const rows = (content?.requirements || []).filter((item: any) => item.family === family);
    const familyLabel = family === "mcl" ? "MCL" : family === "toolshop" ? "Tool Shop" : "CNC";
    return <section className="parts-definition-family" key={family} aria-label={`${familyLabel} requirements`}>
      <div className="parts-definition-list-heading"><h4 tabIndex={-1}>{familyLabel}</h4><button type="button" className="button" disabled={busy} onClick={() => addRequirement(family)}>Add {familyLabel} row</button></div>
      {rows.length === 0 ? <p className="parts-help">No {familyLabel} rows in this draft. Add a row to mark this family applicable in the draft.</p> : <div className="parts-definition-table-scroll"><table className="parts-definition-table">
        <thead><tr>{family === "cnc" && <th>Plate part to cut</th>}<th>Material To Use</th><th>Qty per Part</th>{family === "cnc" ? <><th>Per Part Weight (kg)</th><th>Total weight (kg)</th></> : <><th>Dimension to Cut</th><th>Calculated weight (kg)</th></>}<th>Remarks</th><th>Actions</th></tr></thead>
        <tbody>{rows.map((item: any, rowPosition: number) => {
          const index = (content?.requirements || []).indexOf(item);
          const rowName = `${familyLabel} row ${rowPosition + 1}`;
          const detail = item.detail || {};
          const editableAttributes = editableEngineeringAttributes(detail);
          const materialValue = String(detail.Material ?? "");
          const masterSearch = String(materialMasterSearch[item.requirementKey] || "").trim().toLowerCase();
          const masterChoices = (options?.materialCatalog || []).filter((master: any) => !masterSearch
            || String(master.canonicalName || "").toLowerCase().includes(masterSearch)
            || String(master.code || "").toLowerCase().includes(masterSearch)).slice(0, 40);
          const selectedMasterId = String(materialMasterSelection[item.requirementKey] || "");
          const selectedMaster = (options?.materialCatalog || []).find((master: any) => String(master.masterMaterialId) === selectedMasterId);
          const oldRateUnit = String(detail.QuantityUOM || "");
          const cutUnit = String(detail.DimensionUOM || cutLengthUnit(detail));
          const cutValue = cutLengthValue(detail);
          const derivedTotal = decimalProduct(item.quantity, detail.Quantity);
          const weight = cutWeight(item, options?.materials || []);
          const itemName = detail.MaterialDisplayName || detail.PlatePart || detail.ItemName || item.sourcePartName || "Requirement";
          const itemError = validationErrors[`${item.requirementKey}:material`];
          const quantityError = validationErrors[`${item.requirementKey}:quantity`];
          const unitError = validationErrors[`${item.requirementKey}:uom`];
          const rateError = validationErrors[`${item.requirementKey}:rateQuantity`];
          const rateUnitError = validationErrors[`${item.requirementKey}:rateUnit`];
          const cutError = validationErrors[`${item.requirementKey}:cutLength`];
          const weightError = validationErrors[`${item.requirementKey}:partWeight`];
          const dimensionsError = validationErrors[`${item.requirementKey}:dimensions`];
          return <tr key={item.requirementKey} data-requirement-key={item.requirementKey}>
            {family === "cnc" && <td><label>Plate part to cut<input aria-label={`Plate part to cut — ${rowName}`} value={detail.PlatePart ?? ""} onChange={event => updateRequirementDetail(index, "PlatePart", event.target.value)}/></label>
              <details><summary>Plate dimensions</summary><div className="parts-definition-inline-fields">{(["Length", "Width", "Thickness"] as const).map(field => <label key={field}>{field}<input aria-label={`${field} — ${rowName}`} inputMode="decimal" value={detail[field] ?? ""} onChange={event => updateRequirementDetail(index, field, event.target.value)}/></label>)}
                <label>Dimension unit<select aria-label={`Dimension unit — ${rowName}`} value={detail.DimensionUOM || "mm"} onChange={event => updateRequirementDetail(index, "DimensionUOM", event.target.value)}><option value="mm">mm</option><option value="in">inch</option></select></label></div></details></td>}
            <td className="parts-definition-item-cell">
              <label>Material To Use<LocalMaterialCombobox materials={options?.materials || []} value={materialValue}
                savedLabel={detail.MaterialDisplayName || detail.ItemName || ""} rowName={rowName} invalid={Boolean(itemError)}
                onSelect={value => updateRequirementMaterial(index, value)}/>{itemError && <small className="parts-definition-error-text">{itemError}</small>}</label>
              {materialValue && !(options?.materials || []).some((material: any) => String(material.id) === materialValue)
                && <small role="status">Saved material text is retained; its current local identity/factor is unavailable. Choose a matching local material only when confirmed.</small>}
              {!materialValue && detail.MaterialDisplayName && <small role="status">Saved label: {detail.MaterialDisplayName} · material identity unresolved</small>}
              {(detail.identityStatus === "legacy_unclassified" || item.identityStatus?.startsWith("legacy")) && <small>Saved legacy identity is retained for review.</small>}
              <details className="parts-definition-material-binding"><summary>Search and review a Costing-New material binding</summary>
                {options?.materialWeightSource?.status === "available" ? <>
                  <label>Search source catalogue<input aria-label={`Search Costing-New materials — ${rowName}`} value={materialMasterSearch[item.requirementKey] || ""}
                    onChange={event => setMaterialMasterSearch(old => ({ ...old, [item.requirementKey]: event.target.value }))}/></label>
                  <label>Costing-New material<select aria-label={`Costing-New material — ${rowName}`} value={selectedMasterId}
                    onChange={event => setMaterialMasterSelection(old => ({ ...old, [item.requirementKey]: event.target.value }))}>
                    <option value="">Choose from {options.materialCatalog?.length || 0} source rows</option>
                    {masterChoices.map((master: any) => <option key={master.sourceKey} value={String(master.masterMaterialId)}>
                      {master.canonicalName}{master.code ? ` · ${master.code}` : ""} · {master.bindingStatus === "bound" ? `bound to Safari Material ${master.materialId}` : master.bindingStatus}
                    </option>)}
                  </select></label>
                  {masterSearch && masterChoices.length === 0 && <small>No Costing-New materials match this search.</small>}
                  {masterChoices.length === 40 && <small>Showing the first 40 matches; refine the search to narrow the catalogue.</small>}
                  {selectedMaster?.bindingStatus === "unbound" && <>
                    {materialValue && <>
                      <button type="button" className="button" disabled={busy || !reason.trim()} onClick={() => void reviewAndBindMaterial(index)}>
                        Review and bind to selected existing Material
                      </button>
                      <small>Use this only when both names refer to the same real material identity.</small>
                    </>}
                    <button type="button" className="button" disabled={busy || !reason.trim()} onClick={() => void reviewAndBindMaterial(index, true)}>
                      Review and capture as canonical Material
                    </button>
                    <small>This records an approver-reviewed, signed source link. If no matching Safari identity exists, it creates one reviewable Material with a new local Grist ID and then selects it here. CNC weights remain operator-entered.</small>
                  </>}
                  {selectedMaster?.bindingStatus === "bound" && <small>Already bound to Safari Material {selectedMaster.materialId}; changing it requires reviewed data repair.</small>}
                  {selectedMaster?.bindingStatus === "ambiguous" && <small>This source identity has conflicting Safari bindings and cannot be selected.</small>}
                </> : <small role="status">Costing-New is unavailable. Existing saved material selections and verified factor evidence remain available offline.</small>}
              </details>
            </td>
            <td><div className="parts-definition-inline-fields">
              <label>Quantity<input aria-label={`Quantity — ${rowName}`} inputMode="decimal" value={item.quantity ?? ""} aria-invalid={Boolean(quantityError)} onChange={event => updateRequirement(index, { quantity: event.target.value })}/>{quantityError && <small className="parts-definition-error-text">{quantityError}</small>}</label>
              <label>Unit<select aria-label={`Unit — ${rowName}`} value={String(item.uom || "")} aria-invalid={Boolean(unitError)} onChange={event => updateRequirement(index, { uom: event.target.value })}>
                <option value="">Choose unit</option>{item.uom && !unitOptions.some(unit => unit.toLowerCase() === String(item.uom).toLowerCase()) && <option value={item.uom}>{item.uom} · saved</option>}
                {unitOptions.map(unit => <option key={unit} value={unit}>{unit}</option>)}
              </select>{unitError && <small className="parts-definition-error-text">{unitError}</small>}</label>
            </div><small>Occurrence quantity used once per parent Part.</small></td>
            {family !== "cnc" && <td><div className="parts-definition-inline-fields"><label>Dimension to Cut<input aria-label={`Dimension to Cut — ${rowName}`} inputMode="decimal" value={cutValue ?? ""} aria-invalid={Boolean(cutError)} onChange={event => updateCutLength(index, event.target.value, cutUnit.toLowerCase().startsWith("in") ? "in" : "mm")}/>{cutError && <small className="parts-definition-error-text">{cutError}</small>}</label>
                <label>Cut-length unit<select aria-label={`Cut-length unit — ${rowName}`} value={cutUnit} onChange={event => {
                  const nextUnit = event.target.value === "in" ? "in" : "mm";
                  const currentMm = cutLengthMillimeters(detail);
                  const nextValue = currentMm == null ? String(cutValue ?? "")
                    : String(nextUnit === "in" ? currentMm / LENGTH_TO_MM.in : currentMm);
                  updateCutLength(index, nextValue, nextUnit);
                }}>
                  {!["mm", "in"].includes(cutUnit) && <option value={cutUnit}>{cutUnit} · saved</option>}<option value="mm">mm</option><option value="in">inch</option>
                </select></label></div><small>{cutLengthText(detail)} · saved cut-length evidence</small></td>}
            {family === "cnc" && <td><label>Per Part Weight (kg)<input aria-label={`Per Part Weight (kg) — ${rowName}`} inputMode="decimal" value={detail.PartWeightKg ?? ""} aria-invalid={Boolean(weightError)} onChange={event => updatePartWeight(index, event.target.value)}/>{weightError && <small className="parts-definition-error-text">{weightError}</small>}</label></td>}
            <td>{family === "cnc" ? <><strong>{positiveNumber(detail.PartWeightKg) && positiveNumber(item.quantity) ? `${kgText(Number(detail.PartWeightKg) * Number(item.quantity))} kg` : "Unavailable"}</strong><small>Per Part: {positiveNumber(detail.PartWeightKg) ? `${detail.PartWeightKg} kg` : "weight unavailable"} · Qty per Part counted once.</small></>
              : <><strong>{weight.perPiece == null ? "Unavailable" : `${kgText(weight.perPiece)} kg / piece`}</strong><small>{weight.total == null ? "Total per parent Part is unavailable." : `${kgText(weight.total)} kg per parent Part`}</small>
                {weight.evidence?.kgPerMeter && <small>Current local factor: {weight.evidence.kgPerMeter} kg/m{weight.evidence.sourceDate ? ` · source date ${weight.evidence.sourceDate}` : " · source date unavailable"}</small>}
                {!weight.evidence?.kgPerMeter && <small role="status">No verified kg/m factor; calculated weight and mass-based cost are incomplete.</small>}</>}
              <details><summary>Calculation and costing details</summary>
                <p className="parts-help">Normalized consumption is stored per requirement occurrence. Qty per Part and any parent assembly quantity are applied separately, once each.</p>
                {item.materialConsumptionStatus === "legacy_duplicate_ambiguous" && <small role="status">Legacy physical consumption is preserved as saved. Choose a canonical material with a verified local kg/m factor to normalize it when editing this row.</small>}
                <label>Existing costing unit<select aria-label={`Existing costing unit — ${rowName}`} value={oldRateUnit} aria-invalid={Boolean(rateUnitError)} onChange={event => updateRateBasisUnit(index, event.target.value)}>
                  <option value="">Choose unit</option>{oldRateUnit && !unitOptions.some(unit => unit.toLowerCase() === oldRateUnit.toLowerCase()) && <option value={oldRateUnit}>{oldRateUnit} · saved</option>}{unitOptions.map(unit => <option key={unit} value={unit}>{unit}</option>)}
                </select>{rateUnitError && <small className="parts-definition-error-text">{rateUnitError}</small>}</label>
                <label>Normalized consumption<input aria-label={`Normalized consumption — ${rowName}`} value={detail.Quantity ?? ""} readOnly aria-invalid={Boolean(rateError)}/>{rateError && <small className="parts-definition-error-text">{rateError}</small>}</label>
                {missingCrossDimensionConsumption(item) && <small role="status">No verified conversion maps this physical input to {oldRateUnit}; cost stays incomplete.</small>}
                {!missingCrossDimensionConsumption(item) && isCrossDimensionConsumption(family, oldRateUnit) && item.fieldStatus?.Quantity === "manual" && <small role="status">Legacy manual consumption is retained. No physical conversion was inferred.</small>}
                {dimensionsError && <small className="parts-definition-error-text">{dimensionsError}</small>}
                <small>{derivedTotal === null ? "Total normalized consumption: not available" : `Total normalized consumption: ${derivedTotal} ${oldRateUnit}`}</small>
                {weight.evidence && <small>Material conversion: {weight.evidence.status || "available"}{weight.evidence.sourceDate ? ` · factor date ${weight.evidence.sourceDate}` : " · factor date unavailable"}</small>}
              </details>
            </td>
            <td><label>Remarks<input aria-label={`Remarks — ${rowName}`} value={detail.ToolShopRemarks ?? ""} onChange={event => updateRequirementDetail(index, "ToolShopRemarks", event.target.value)}/></label><small>{fieldStatusText(item.fieldStatus?.ToolShopRemarks)}</small></td>
            <td><button type="button" className="button danger" disabled={busy} onClick={() => {
              requestRemoval(`Remove ${FAMILY_LABEL[family]} row “${itemName}” from this draft? The accepted revision changes only after approval.`,
                () => patchContent({ requirements: (content.requirements || []).filter((row: any) => row.requirementKey !== item.requirementKey) }));
            }}>Remove row</button>
              <details><summary>Advanced saved fields</summary>{item.identityStatus === "legacy_duplicate_unclassified" && <button type="button" className="button" disabled={busy} onClick={() => updateRequirement(index, { requirementKey: `safari-authored-requirement:${crypto.randomUUID()}`, identityStatus: "safari_authored", legacyOccurrenceKey: item.requirementKey })}>Assign reviewed occurrence identity</button>}
                {item.lineIdentityStatus === "unresolved" && <p className="parts-definition-error" role="status">Physical line identity is unresolved and remains blocked from publication until reviewed.</p>}
                <p className="parts-help">Provenance: {item.provenance?.type || "legacy / unclassified"}. A Stores issue route records routing only; it does not create a purchase.</p>
                <label>Issue route<input aria-label={`Issue route — ${rowName}`} value={detail.IssueRoute ?? ""} onChange={event => updateRequirementDetail(index, "IssueRoute", event.target.value)}/></label>
                <label>Engineering attributes (JSON)<textarea aria-label={`Engineering attributes (JSON) — ${rowName}`} value={attributeText[`requirement:${item.requirementKey}`] ?? JSON.stringify(editableAttributes, null, 2)} onChange={event => {
                  const text = event.target.value; setAttributeText(old => ({ ...old, [`requirement:${item.requirementKey}`]: text }));
                  try { const parsed = JSON.parse(text); if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) updateRequirementDetail(index, "EngineeringAttributes", parsed); else setDirty(true); }
                  catch { setDirty(true); }
                 }}/></label><small>Verified material conversion evidence is managed by the server and cannot be edited here.</small>
              </details>
            </td>
          </tr>;
        })}</tbody>
      </table></div>}
    </section>;
  }

  if (loading && !definition) return <section className="parts-definition-card"><p>Loading saved Safari definition…</p></section>;
  if (error && !definition) return <section className="parts-definition-card"><p className="parts-definition-error" role="alert">{error}</p></section>;
  if (!definition) return null;

  return <section className="parts-section-card parts-definition-card" aria-label="Saved Part definition">
    <header className="parts-definition-heading"><div><h3 tabIndex={-1}>Saved Part definition</h3><p>{definition.partNumber} · accepted Rev {definition.revisionLabel} · {definition.revisionStatus.replaceAll("_", " ")}</p></div>
      <div className="parts-definition-actions"><button type="button" className="button" onClick={() => { setEditing(true); setNotice(""); }} disabled={busy || Boolean(pending) || Boolean(pendingSave) || Boolean(pendingSubmit) || Boolean(pendingApproval) || !definition.actions?.editPart}>Edit Part</button>
        <button type="button" className="button" onClick={() => document.getElementById("part-definition-history")?.scrollIntoView({ behavior: "smooth", block: "center" })}>Revision history</button>
        <button type="button" className="button" onClick={onCompare}>Compare with ODS</button></div></header>
    {error && <p className="parts-definition-error" role="alert">{error}</p>}{notice && <p className="parts-definition-notice" role="status">{notice}</p>}
    <p className="parts-help">Safari Grist is the saved definition authority. Viewing, editing and costing use this saved definition; the recorded workbook is opened only when you explicitly compare it.</p>
    {!definition.actions?.editPart && <p className="parts-definition-notice" role="status">Part-definition maintenance tables are not installed in this Grist document yet. Existing accepted definitions remain readable.</p>}
    {definition.source?.workbookName && <p className="parts-definition-source">Origin provenance: {definition.source.workbookName} · {definition.source.sourceHash || "source hash unavailable"} · read only</p>}

    {pendingSave && <p className="parts-definition-error" role="alert">A draft save has an uncertain result. Editing is locked until its original payload is retried with the same request key. <button type="button" className="button" onClick={() => void retryPendingSave()} disabled={busy}>{busy ? "Retrying…" : "Retry original draft save"}</button></p>}
    {pendingSubmit && <p className="parts-definition-error" role="alert">A Change Request submission has an uncertain result. Confirm the original request before continuing. <button type="button" className="button" onClick={() => void confirmSubmission(pendingSubmit)} disabled={busy}>{busy ? "Confirming…" : "Confirm original submission"}</button></p>}
    {pendingApproval && <p className="parts-definition-error" role="alert">An approval is unresolved. Retry the original operation before making another change. <button type="button" className="button" onClick={() => void confirmApproval(pendingApproval)} disabled={busy}>{busy ? "Recovering…" : "Retry original approval"}</button></p>}
    {editing && <fieldset className="parts-definition-editor" aria-label="Edit saved Part definition" disabled={busy || Boolean(pendingSave) || Boolean(pendingSubmit) || Boolean(pendingApproval)}>
      {definition.draft?.stale && <p className="parts-definition-error" role="status">The saved draft uses an older accepted base. It is retained. Review the current values here and reconcile them manually before saving a new draft on Rev {definition.revisionLabel}.</p>}
      {options?.materialWeightSource?.status === "available"
        ? <p className="parts-definition-notice" role="status">Material choices and current kg/m factors come from the saved local master version ({options.materials?.length || 0} selectable materials). Historical evidence remains attached to its accepted revision.</p>
        : <p className="parts-definition-error" role="status">The saved local material version is unavailable. Saved labels remain visible; current calculated material weight and mass-based cost are incomplete.</p>}
      <div className="parts-definition-applicability"><strong>Family applicability</strong>{Object.entries(FAMILY_LABEL).map(([family, label]) => <label key={family}><input type="checkbox" checked={Boolean(content?.applicability?.[family]?.applicable)} onChange={event => patchContent({ applicability: { ...content.applicability, [family]: { ...(content.applicability?.[family] || {}), applicable: event.target.checked } } })}/>{label}</label>)}</div>
      <div className="parts-definition-list-heading"><h4>Manufacturing requirements</h4><span>Choose the family before adding a row. First-row selection also records applicability in this draft.</span></div>
      {EDITABLE_FAMILIES.map(renderRequirementTable)}
      {(content?.requirements || []).some((item: any) => !EDITABLE_FAMILIES.includes(item.family)) && <details className="parts-definition-legacy"><summary>Preserved legacy / other-process rows</summary>
        <div className="parts-definition-items">{(content?.requirements || []).filter((item: any) => !EDITABLE_FAMILIES.includes(item.family)).map((item: any) => <article key={item.requirementKey}>
          <strong>{item.detail?.ItemName || item.detail?.PlatePart || item.detail?.MaterialDisplayName || item.sourcePartName || "Unclassified saved process"}</strong>
          <span>{item.quantity ?? "—"} {item.uom || ""} · {cutLengthText(item.detail)} · per part weight {item.detail?.PartWeightKg ?? "—"} kg</span>
          <small>{item.identityStatus === "legacy_unclassified" || item.identityStatus === "legacy_duplicate_unclassified" ? "Legacy identity requires review; saved row remains unchanged." : item.requirementKey} · {item.provenance?.type || "legacy / unclassified"}</small>
        </article>)}</div></details>}

      <section className="parts-definition-family" aria-label="Store requirements">
        <div className="parts-definition-list-heading"><h4 tabIndex={-1}>Store</h4><button type="button" className="button" disabled={busy} onClick={addStoreRow}>Add Store row</button></div>
        <p className="parts-help">Store rows select a canonical child Part and reuse existing purchase/component routing. Issue Slip choices stay separate.</p>
        {(content?.components || []).length ? <div className="parts-definition-table-scroll"><table className="parts-definition-table"><thead><tr><th>Canonical child Part</th><th>Quantity</th><th>Unit</th><th>Sourcing route</th><th>Revision pin</th><th>Actions</th></tr></thead><tbody>
          {(content?.components || []).map((item: any, index: number) => {
            const selectedId = String(item.childPartId || "");
            const selectedKnown = (options?.parts || []).some((part: any) => part.id === item.childPartId);
            const partError = validationErrors[`${item.componentIdentity}:childPart`];
            const quantityError = validationErrors[`${item.componentIdentity}:quantity`];
            const unitError = validationErrors[`${item.componentIdentity}:uom`];
            const rowLabel = `Store row ${index + 1}`;
            return <tr key={item.componentIdentity} data-component-key={item.componentIdentity}>
              <td><label>Canonical child Part<select aria-label={`Canonical child Part — ${rowLabel}`} value={selectedId} aria-invalid={Boolean(partError)} onChange={event => updateComponent(index, { childPartId: event.target.value, childRevisionId: undefined })}>
                <option value="">Choose a Part</option>{selectedId && !selectedKnown && <option value={selectedId}>{item.childPartNumber || item.childName || `Unresolved Part ${selectedId}`}</option>}
                {(options?.parts || []).filter((part: any) => part.id !== partId).map((part: any) => <option key={part.id} value={part.id}>{part.partNumber} · {part.name || "Unnamed Part"}</option>)}
              </select>{partError && <small className="parts-definition-error-text">{partError}</small>}</label></td>
              <td><label>Quantity<input aria-label={`Quantity — ${rowLabel}`} inputMode="decimal" value={item.quantity ?? ""} aria-invalid={Boolean(quantityError)} onChange={event => updateComponent(index, { quantity: event.target.value })}/>{quantityError && <small className="parts-definition-error-text">{quantityError}</small>}</label></td>
              <td><label>Unit<select aria-label={`Unit — ${rowLabel}`} value={String(item.uom || "")} aria-invalid={Boolean(unitError)} onChange={event => updateComponent(index, { uom: event.target.value })}><option value="">Choose unit</option>
                {item.uom && !unitOptions.some(unit => unit.toLowerCase() === String(item.uom).toLowerCase()) && <option value={item.uom}>{item.uom} · saved</option>}{unitOptions.map(unit => <option key={unit} value={unit}>{unit}</option>)}
              </select>{unitError && <small className="parts-definition-error-text">{unitError}</small>}</label></td>
              <td><label>Sourcing route<select aria-label={`Sourcing route — ${rowLabel}`} value={item.sourcingRoute || "auto"} onChange={event => updateComponent(index, { sourcingRoute: event.target.value })}><option value="auto">Automatic</option><option value="make">Manufacture</option><option value="buy">Purchased / Stores</option></select></label></td>
              <td>{item.childRevisionLabel || item.childRevisionId || "Current accepted child revision"}<small>Changing the child creates a different occurrence identity.</small></td>
              <td><button type="button" className="button danger" disabled={busy} onClick={() => {
                requestRemoval(`Remove Store row “${item.childPartNumber || item.childName || "unselected child Part"}” from this draft? The accepted revision changes only after approval.`,
                  () => patchContent({ components: (content.components || []).filter((row: any) => row.componentIdentity !== item.componentIdentity) }));
              }}>Remove row</button></td>
            </tr>;
          })}
        </tbody></table></div> : <p className="parts-help">No Store rows in this draft.</p>}
      </section>

      <details className="parts-definition-purchases"><summary>Purchase specifications · {(content?.purchases || []).length} saved</summary>
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
      </div><button type="button" className="button danger" disabled={busy} onClick={() => requestRemoval(
        `Remove purchase specification “${item.code || item.description || item.specificationIdentity}” from this draft?`,
        () => patchContent({ purchases: content.purchases.filter((row: any) => row.specificationIdentity !== item.specificationIdentity) }))}>Remove purchase specification</button></fieldset>)}
      </details>

      {!isFinalized && <label className="parts-definition-finalize"><input type="checkbox" checked={Boolean(content?.finalizeOnPublication)} onChange={event => patchContent({ finalizeOnPublication: event.target.checked })}/> Finalize this successor definition through the approved Change Request</label>}
      <label className="parts-definition-reason">Engineering change reason<input value={reason} onChange={event => { setReason(event.target.value); setDirty(true); }}/></label>
      <div className="parts-definition-actions"><button type="button" className="button primary" onClick={() => void saveDraft()} disabled={busy || Boolean(pendingSubmit) || !reason.trim()}>{busy ? "Saving…" : "Save draft"}</button>
        <button type="button" className="button" onClick={() => { setEditing(false); setDirty(false); if (definition) setContent(definition.draft && !definition.draft.stale ? definition.draft.content : emptyContent(definition)); }} disabled={busy}>Close editor</button>
        {definition.draft && !definition.draft.stale && <button type="button" className="button primary" onClick={() => void submitForApproval()} disabled={busy || dirty || !reason.trim()}>Request approval</button>}</div>
    </fieldset>}

    {!editing && <>
      <div className="parts-definition-applicability"><strong>Applicable families</strong>{Object.entries(FAMILY_LABEL).map(([family, label]) => <span key={family}>{label}: {definition.applicability?.[family]?.applicable === null ? "unknown" : definition.applicability?.[family]?.applicable ? "applicable" : "not applicable"}</span>)}</div>
      <div className="parts-definition-list-heading"><h4>Accepted requirements</h4><span>{definition.requirements.length} saved</span></div>
      {definition.requirements.some((item: any) => item.lineIdentityStatus === "unresolved") && <p className="parts-definition-error">A saved physical line identity is unresolved and requires review before publication.</p>}
      {definition.requirements.length ? <div className="parts-definition-saved-families">{EDITABLE_FAMILIES.map(family => {
        const rows = definition.requirements.filter((item: any) => item.family === family);
        const label = family === "mcl" ? "MCL" : family === "toolshop" ? "Tool Shop" : "CNC";
        return <section className="parts-definition-family" key={family} aria-label={`Accepted ${label} requirements`}><h4>{label}</h4>
          {rows.length ? <div className="parts-definition-table-scroll"><table className="parts-definition-table parts-definition-readonly"><thead><tr>{family === "cnc" && <th>Plate part to cut</th>}<th>Material To Use</th><th>Qty per Part</th>{family === "cnc" ? <><th>Per Part Weight (kg)</th><th>Total weight (kg)</th></> : <><th>Dimension to Cut</th><th>Calculated weight (kg)</th></>}<th>Remarks</th><th>Saved details</th></tr></thead><tbody>
            {rows.map((item: any) => {
              const detail = item.detail || {};
              const material = (options?.materials || []).find((candidate: any) => String(candidate.id) === String(detail.Material));
              const displayName = detail.MaterialDisplayName || material?.name || detail.ItemName || item.sourcePartName || "Saved material label unavailable";
              const weight = cutWeight(item, options?.materials || []);
              const totalWeight = positiveNumber(detail.PartWeightKg) && positiveNumber(item.quantity) ? kgText(Number(detail.PartWeightKg) * Number(item.quantity)) : null;
              const attrs = engineeringAttributes(detail);
              return <tr key={item.requirementKey}>
                {family === "cnc" && <td>{detail.PlatePart || "—"}</td>}
                <td><strong>{displayName}</strong>{!detail.Material && <small>Saved label retained · Safari material identity unresolved</small>}</td>
                <td>{item.quantity ?? "—"} {item.uom || ""}</td>
                {family === "cnc" ? <><td>{positiveNumber(detail.PartWeightKg) ? `${detail.PartWeightKg} kg` : "Unavailable"}</td><td>{totalWeight == null ? "Unavailable" : `${totalWeight} kg`}</td></>
                  : <><td>{cutLengthText(detail)}</td><td><strong>{weight.perPiece == null ? "Unavailable" : `${kgText(weight.perPiece)} kg / piece`}</strong><small>{weight.total == null ? "Total per parent Part unavailable" : `${kgText(weight.total)} kg per parent Part`}</small>{weight.evidence?.kgPerMeter && <small>Current local factor: {weight.evidence.kgPerMeter} kg/m{weight.evidence.sourceDate ? ` · source date ${weight.evidence.sourceDate}` : " · source date unavailable"}</small>}<details><summary>Historical factor evidence</summary><small>{JSON.stringify(item.materialWeightEvidence || {status:"unavailable"})}</small></details></td></>}
                <td>{detail.ToolShopRemarks || "—"}</td>
                <td><details><summary>Saved calculation and identity</summary><small>{detail.Quantity == null ? "Normalized consumption unavailable" : `${detail.Quantity} ${detail.QuantityUOM || "unit not recorded"} per requirement occurrence`}</small>
                  <small>{savedTotalWeightDescription(item)}</small>
                  {attrs.materialWeightEvidence && <small>Weight source evidence: {JSON.stringify(attrs.materialWeightEvidence)}</small>}
                  {detail.ItemName && <small>Saved source item label: {detail.ItemName}</small>}
                  <small>{item.identityStatus === "legacy_unclassified" || item.identityStatus === "legacy_duplicate_unclassified" ? "Legacy identity needs review; no old row was rewritten." : item.provenance?.type || "legacy / unclassified"}{item.lineIdentityStatus === "unresolved" ? " · physical line unresolved" : ""}</small>
                </details></td></tr>;
            })}
          </tbody></table></div> : <p className="parts-help">No saved {label} rows.</p>}
        </section>;
      })}{definition.requirements.some((item: any) => !EDITABLE_FAMILIES.includes(item.family)) && <section className="parts-definition-family"><h4>Other saved process rows</h4><div className="parts-definition-items">{definition.requirements.filter((item: any) => !EDITABLE_FAMILIES.includes(item.family)).map((item: any) => <article key={item.requirementKey}><strong>{item.detail?.ItemName || item.sourcePartName || "Unclassified process"}</strong><span>{item.quantity ?? "—"} {item.uom || ""} · {cutLengthText(item.detail)} · Part weight {item.detail?.PartWeightKg ?? "—"} kg</span><small>{item.requirementKey} · {item.identityStatus || "legacy"}</small></article>)}</div></section>}</div> : <p>No manufacturing line requirements are saved on this revision.</p>}
      {definition.components.length > 0 && <><h4>Store / purchased components</h4><div className="parts-definition-items">{definition.components.map((item: any) => <article key={item.componentIdentity}><strong>{item.childPartNumber || "Unresolved child Part"} · {item.childName}</strong><span>{item.quantity} {item.uom} · {item.sourcingRoute || "auto"} · pinned Rev {item.childRevisionLabel || "unknown"}</span><small>{item.componentIdentity} · {item.identityStatus || "stable"}</small></article>)}</div></>}
      {definition.purchases.length > 0 && <><h4>Purchase specifications</h4><div className="parts-definition-items">{definition.purchases.map((item: any) => <article key={item.specificationIdentity}><strong>{item.code} · {item.description || item.manufacturerPartNumber}</strong><span>{item.manufacturer} · {item.costingUOM} · {item.costingCurrency}</span><small>{item.specificationIdentity} · {item.identityStatus || "stable"}</small></article>)}</div></>}
      {definition.draft && <p className={definition.draft.stale ? "parts-definition-error" : "parts-definition-notice"} role="status">{definition.draft.stale ? `Draft v${definition.draft.saveVersion} is based on an older accepted revision. Its saved content is retained; review the current accepted definition, reconcile changes, then save a new draft.` : `Saved draft v${definition.draft.saveVersion} · ${definition.draft.updatedAt}. Accepted Rev ${definition.revisionLabel} is unchanged.`}</p>}
     {pending && <div className="parts-definition-pending"><strong>Change Request · {pending.status.replaceAll("_", " ")}</strong><p>{pending.reason}</p>
       <ChangeReview before={pending.before} after={pending.after} parts={options?.parts || []} title="Submitted definition before and after"/>
       <span>{pendingDependents.length} current dependent Part/assembly successor(s) · {pending.impact?.modelCodeConfigurations?.length || 0} Model Code configuration successor(s)</span>
        {pending.impact?.target && <p>Part {pending.impact.target.partId} · record {pending.impact.target.partRecordId}: Rev {pending.impact.target.currentRevisionLabel} → proposed Rev {pending.impact.target.proposedRevisionLabel || "unclassified"}</p>}
        {pendingDependents.length > 0 && <div className="parts-definition-impact"><h4>Current dependent Part/assembly closure</h4>{pendingDependents.map((dependent: any) => <article key={dependent.partId}><strong>{dependent.kind === "part" ? "Part" : "Assembly"} · {dependent.partNumber || dependent.partId} · {dependent.partId} (record {dependent.partRecordId})</strong><span>Current Rev {dependent.currentRevisionLabel} → proposed Rev {dependent.proposedRevisionLabel || "unclassified"}</span>
          {(dependent.pathDetails || []).map((path: any, pathIndex: number) => <div className="parts-definition-impact-path" key={`${dependent.partId}:${pathIndex}`}><small>Path: {path.partPath.join(" → ")}</small>{path.edges.map((edge: any, edgeIndex: number) => <small key={`${edge.componentIdentity || edge.componentRecordId}:${edgeIndex}`}>{edge.componentIdentity || `unclassified component record ${edge.componentRecordId}`} · {edge.parentPartId} Rev {edge.parentRevisionLabel} → {edge.proposedParentRevisionLabel} contains {edge.childPartId} pinned Rev {edge.pinnedChildRevisionLabel} → proposed Rev {edge.proposedChildRevisionLabel} · {edge.quantity ?? "—"} {edge.uom || ""} · {edge.sourcingRoute || "auto"}</small>)}</div>)}</article>)}</div>}
        {pending.impact?.modelCodeConfigurations?.length > 0 && <div className="parts-definition-impact"><h4>Current Model Code configuration closure</h4>{pending.impact.modelCodeConfigurations.map((use: any) => <article key={`${use.modelCodeRecordId}:${use.selectionIdentity || use.selectionRecordId}`}><strong>{use.modelCode || "Model Code"} · record {use.modelCodeRecordId} · selection {use.selectionIdentity || `unclassified record ${use.selectionRecordId}`}</strong><span>Configuration record {use.configurationRecordId} Rev {use.currentConfigurationRevision} → proposed Rev {use.proposedConfigurationRevision} · Part {use.partId} pinned Rev {use.partRevisionId} → proposed Rev {use.proposedPartRevisionLabel} · {use.quantity ?? "—"} {use.uom || ""} · {use.sourcingRoute || "auto"} · {use.label || "unlabeled occurrence"}{use.optionGroup ? ` · option ${use.optionGroup}` : ""}</span>{(use.dependencyPaths || []).filter((path: string[]) => path.length > 1).map((path: string[], index: number) => <small key={`${use.selectionIdentity}:${index}`}>Affected through: {path.join(" → ")}</small>)}</article>)}</div>}
        {pending.impact?.spares?.message && <p>{pending.impact.spares.message}</p>}
        {pending.impact?.unknown?.length > 0 && <p className="parts-definition-error">Impact has gaps: {pending.impact.unknown.map((item: any) => item.code).join(", ")}</p>}
        {definition.canApprove && <button type="button" className="button primary" onClick={() => void approveAndPublish()} disabled={busy || Boolean(pendingSubmit) || Boolean(pendingApproval) || pending.status === "pending_approval" && !pending.impact?.fingerprint}>{busy ? "Publishing…" : pending.approvalRecoveryKey ? "Recover saved approval" : "Approve and publish as Irshad"}</button>}
      </div>}
     {(definition.staleChangeRequests || []).length > 0 && <div className="parts-definition-pending parts-definition-stale-requests">
       <h4>Change Requests returned for review</h4>
       {(definition.staleChangeRequests || []).map((stale: any) => <article key={stale.key}>
         <strong>Stale Change Request · {stale.key}</strong><p>{stale.reason}</p>
         <p className="parts-definition-error">{stale.staleReason || "The accepted base or reviewed dependency closure changed."}</p>
         <small>{stale.staleCode || "Stale review"} · detected {stale.staleAt || "date not recorded"}</small>
         <details><summary>Review the preserved submitted definition</summary><ChangeReview before={stale.before} after={stale.after} parts={options?.parts || []} title="Stale request before and after"/></details>
         {stale.canReopen ? <><label className="parts-definition-reason">Reason for reopening this saved change<input value={reopenReason} onChange={event => setReopenReason(event.target.value)}/></label>
           <button type="button" className="button primary" onClick={() => void reopenStaleChangeRequest(stale)} disabled={busy || Boolean(pendingSubmit) || Boolean(pendingApproval) || !reopenReason.trim()}>Reopen saved changes as a new draft</button></>
           : <p className="parts-definition-notice" role="status">This stale request already has a linked reopened draft. Edit that saved draft and request fresh approval.</p>}
       </article>)}
     </div>}
       <details id="part-definition-history" className="parts-definition-history"><summary>Engineering revision history</summary>{definition.revisionHistory.map((revision: any) => <article key={revision.id}><strong>Rev {revision.revisionLabel} · {revision.status.replaceAll("_", " ")}{revision.current ? " · current" : ""}</strong><span>{revision.actor || "Actor not recorded"} · {revision.occurredAt || "Date not recorded"}</span><p>{revision.reason || "No reason recorded"}{revision.publicationCause ? ` · cause ${revision.publicationCause}` : ""}</p>
         <details className="parts-definition-changed-rows"><summary>Changed rows · {revision.changedRows?.length ?? "earlier evidence unavailable"}</summary>{Array.isArray(revision.changedRows) && revision.changedRows.length > 0 ? <div className="parts-definition-table-scroll"><table className="parts-definition-table"><thead><tr><th>Change</th><th>Family / item / occurrence</th><th>Field</th><th>Before</th><th>After</th></tr></thead><tbody>{revision.changedRows.map((change: any, index: number) => <tr key={`${change.occurrence}:${change.field}:${index}`}><td>{change.kind}</td><td>{change.family} · {change.item} · {change.occurrence}</td><td>{change.field}</td><td>{change.before ?? "—"}</td><td>{change.after ?? "—"}</td></tr>)}</tbody></table></div> : <p>{revision.changedRows ? "No business-field changes were recorded for this revision." : "Saved before-values are unavailable for this earlier revision."}</p>}</details>
       </article>)}</details>
    </>}
    {pendingRemoval && <div className="parts-definition-confirm-backdrop">
      <div ref={removalDialogRef} className="parts-definition-confirm-dialog" role="alertdialog" aria-modal="true"
        aria-labelledby="parts-definition-confirm-title" aria-describedby="parts-definition-confirm-description" tabIndex={-1}>
        <h3 id="parts-definition-confirm-title">Remove this draft row?</h3>
        <p id="parts-definition-confirm-description">{pendingRemoval.description}</p>
        <div className="parts-definition-confirm-actions">
          <button ref={removalCancelRef} type="button" className="button" onClick={() => finishRemoval(false)}>Cancel removal</button>
          <button type="button" className="button danger" onClick={() => finishRemoval(true)}>Remove from draft</button>
        </div>
      </div>
    </div>}
  </section>;
}
