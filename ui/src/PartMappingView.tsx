import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, ChevronLeft, ChevronRight } from "lucide-react";
import { api } from "./api";
import { PartBaselinePanel } from "./PartBaselinePanel";
import { MAPPING_PART_CREATE_ATTEMPT_KEY, PartCreationForm, restoredMappingPartCreateAttempt, type ExistingPart, type PartCreateAttempt, type PartCreationOrigin } from "./PartCreationForm";
import "./partMapping.css";

type Part = { id: string; name: string; description?: string; variant?: string; selectable: boolean; duplicateName: boolean; partNumber?: string | null; engineeringRevision?: string | null; legacy?: boolean; outOfScopeCodes?: { id: string; code: string }[] };
type SourceRow = { sheet: string; row: number; fields: Record<string, unknown>; sourceHeaders: Record<string, string>; sourceHeaderCells: Record<string, string>; sourceCells: Record<string, { cell?: string }>; headerRow?: number; availableFields: string[] };
type Group = { key: string; mappingPolicyVersion: string; evidenceFingerprint: string; sheet: string; labelField: string; sourceDiagnostic?: { status?: string }; description: string; blankDescription: boolean; rows: SourceRow[]; part: Part | null; reviewed: boolean; explicitlyUnassigned?: boolean; previousAssignment?: { partNumber?: string | null; name?: string | null; sourceDescription?: string; requiresReview: boolean; sourceChanged?: boolean; associationChanged?: boolean; previousRowsAgree?: boolean; previousRowsReviewed?: number; currentRows?: number; assignedPartIds?: string[]; hasPriorAssignment?: boolean; contextChanged?: boolean; groupingPolicyChanged?: boolean }; needsCompatibilityReview?: boolean; version: number };
type Detail = { sourceHash: string; associationKey: string; associationVersion: number; version: number; schemaAvailable: boolean; legacyHistoryAvailable?: boolean; mappingPolicyVersion: string; sourceSheets: Record<string, { present: boolean; status: string; labelField: string; labelHeader: string | null; labelHeaderCell: string | null; ambiguousLabelHeaders: string[]; headerRow: number | null }>; parts: Part[]; groups: Group[]; unresolvedGroups: number; history: { ReviewKey: string; Version: number; SourceDescription: string; SheetName: string; SourceRow: number; Actor: string; Reason: string; OccurredAt: string; NameUsed?: string; PartNumberUsed?: string }[] };
type Attempt = { key: string; payload: Record<string, unknown>; groupKeys: string[] };
type StaleDraft = { groupKey: string; sheet: string; description: string; rowCount: number; decision: string; part: Part | null; sourceHash: string; evidenceFingerprint: string; reason?: string };
type Draft = { path: string; sourceHash: string; version: number; associationKey: string; associationVersion: number; decisions: Record<string, string>; draftParts: Record<string, Part>; groupReasons?: Record<string, string>; expandedGroups?: Record<string, boolean>; expandedSourceDetails?: Record<string, boolean>; draftGroups?: {groupKey: string; sheet: string; description: string; rowCount: number; evidenceFingerprint: string}[]; staleDrafts: StaleDraft[]; reason: string; query: string; sheetFilter: string; reviewFilter: string; scrollTop: number; mappingAttempt: Attempt | null };
type ReturnSelection = { path: string; groupKey: string; partId: string | null; mode?: "select" | "view"; sourceHash?: string; associationKey?: string; associationVersion?: number; evidenceFingerprint?: string };
type SheetFilter = "all" | "mcl" | "toolshop" | "cnc";
type ReviewFilter = "all" | "needs-review" | "saved" | "unsaved";

const sourceFields: Record<string, { key: string; label: string }[]> = {
  "5. Material Cut List Price": [
    { key: "material_to_cut", label: "Material to Cut" }, { key: "dimension_to_cut_mm", label: "Dimension to Cut (mm)" },
    { key: "qty", label: "Quantity Nos" }, { key: "optional_item_group_1", label: "Item Group 1" }, { key: "in_use", label: "In Use" },
    { key: "remarks", label: "Remarks" }, { key: "cr_log", label: "CR Log" },
  ],
  "Tool Shop Items": [
    { key: "item_name", label: "Item Name / Material" }, { key: "material_to_cut", label: "Material Used" },
    { key: "dimension_to_cut_mm", label: "Dimension (mm)" }, { key: "qty", label: "Quantity" },
    { key: "optional_item_group_1", label: "Item Group 1" }, { key: "in_use", label: "In Use" },
    { key: "toolshop_part_name", label: "Tool Shop Part Name / Remarks" }, { key: "job_remarks", label: "Job Remarks" }, { key: "cr_log", label: "CR Log" },
  ],
  "CNC Cut List": [
    { key: "product_part_name", label: "Plate Part to Cut" }, { key: "material_to_cut", label: "Material Used" },
    { key: "length", label: "Length" }, { key: "width", label: "Width" }, { key: "thickness", label: "Thickness" },
    { key: "qty", label: "Qty Of Part Used" }, { key: "part_weight_kg", label: "Per Part Weight (Kg)" },
    { key: "total_weight_kg", label: "Total Weight (Kgs)" }, { key: "optional_item_group_1", label: "Item Group 1" }, { key: "in_use", label: "In Use" },
  ],
};

function draftKey(path: string) { return `part-mapping:${path}`; }
function readDraft(path: string): Draft | null {
  try {
    const draft = JSON.parse(sessionStorage.getItem(draftKey(path)) || "null");
    return draft && draft.path === path ? draft as Draft : null;
  } catch { return null; }
}
function formatPart(part: Part | null | undefined) {
  return part ? `${part.partNumber ? `${part.partNumber} · ` : ""}${part.name}${part.engineeringRevision ? ` · Rev ${part.engineeringRevision}` : ""}` : "No Part selected";
}
function familyFor(sheet: string): SheetFilter {
  return sheet === "5. Material Cut List Price" ? "mcl" : sheet === "Tool Shop Items" ? "toolshop" : "cnc";
}
function familyLabel(filter: SheetFilter) {
  return filter === "mcl" ? "MS List" : filter === "toolshop" ? "Toolshop" : filter === "cnc" ? "CNC" : "All sheets";
}
function groupTitle(group: Group) {
  return group.blankDescription ? "Blank Part label · individual source row" : group.description;
}
function narrowScreen() {
  return typeof window.matchMedia === "function" ? window.matchMedia("(max-width: 1000px)").matches : window.innerWidth <= 1000;
}

export function PartMappingView({ path, active = true, onOpenParts, returnSelection, onReturnSelectionConsumed }: { path: string; active?: boolean; onOpenParts?: (groupKey: string, partId: string | null, mode?: "select" | "view", context?: Partial<ReturnSelection>) => void; returnSelection?: ReturnSelection | null; onReturnSelectionConsumed?: () => void }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [decisions, setDecisions] = useState<Record<string, string>>({});
  const [draftParts, setDraftParts] = useState<Record<string, Part>>({});
  const [staleDrafts, setStaleDrafts] = useState<StaleDraft[]>([]);
  const [reason, setReason] = useState("");
  const [groupReasons, setGroupReasons] = useState<Record<string, string>>({});
  const [query, setQuery] = useState("");
  const [sheetFilter, setSheetFilter] = useState<SheetFilter>("all");
  const [reviewFilter, setReviewFilter] = useState<ReviewFilter>("all");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [mappingAttempt, setMappingAttempt] = useState<Attempt | null>(null);
  const [confirmBatch, setConfirmBatch] = useState(false);
  const [contextOpen, setContextOpen] = useState(() => localStorage.getItem("part-mapping:context-open")
    ? localStorage.getItem("part-mapping:context-open") !== "false"
    : !narrowScreen());
  const [narrowViewport, setNarrowViewport] = useState(false);
  const restoredCreateAttempt = restoredMappingPartCreateAttempt();
  const [createOrigin, setCreateOrigin] = useState<PartCreationOrigin | null>(() => restoredCreateAttempt?.origin || null);
  const [createAttemptPending, setCreateAttemptPending] = useState(Boolean(restoredCreateAttempt));
  const acceptedMappingRef = useRef({ path, detail, loading, createOrigin });
  acceptedMappingRef.current = { path, detail, loading, createOrigin };
  const generation = useRef(0);
  const inFlight = useRef(false);
  const tableScroll = useRef<HTMLDivElement>(null);
  const contextPanel = useRef<HTMLElement>(null);
  const contextToggle = useRef<HTMLButtonElement>(null);
  const createDialog = useRef<HTMLElement>(null);
  const createOpener = useRef<HTMLButtonElement | null>(null);

  useEffect(() => { localStorage.setItem("part-mapping:context-open", String(contextOpen)); }, [contextOpen]);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") {
      const update = () => setNarrowViewport(window.innerWidth <= 1000);
      update(); window.addEventListener("resize", update);
      return () => window.removeEventListener("resize", update);
    }
    const media = window.matchMedia("(max-width: 1000px)");
    const update = () => setNarrowViewport(media.matches);
    update(); media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);

  useEffect(() => {
    if (contextOpen && narrowViewport) {
      const first = contextPanel.current?.querySelector<HTMLElement>("button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])");
      window.requestAnimationFrame(() => (first || contextPanel.current)?.focus());
    }
  }, [contextOpen, narrowViewport]);

  useEffect(() => {
    if (!contextOpen || !narrowViewport || createOrigin) return;
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); setContextOpen(false); window.requestAnimationFrame(() => contextToggle.current?.focus()); return; }
      if (event.key !== "Tab" || !contextPanel.current) return;
      const focusable = Array.from(contextPanel.current.querySelectorAll<HTMLElement>("button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex='-1'])"));
      if (!focusable.length) { event.preventDefault(); contextPanel.current.focus(); return; }
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || !contextPanel.current.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !contextPanel.current.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [contextOpen, narrowViewport, createOrigin]);

  useEffect(() => {
    if (!createOrigin) return;
    const focusInitial = () => createDialog.current?.querySelector<HTMLElement>("input:not([disabled]),select:not([disabled]),textarea:not([disabled]),button:not([disabled])")?.focus();
    window.requestAnimationFrame(focusInitial);
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault(); event.stopPropagation();
        if (!createAttemptPending) closeCreateDialog();
        return;
      }
      if (event.key !== "Tab" || !createDialog.current) return;
      const focusable = Array.from(createDialog.current.querySelectorAll<HTMLElement>("button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex='-1'])"));
      if (!focusable.length) { event.preventDefault(); createDialog.current.focus(); return; }
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || !createDialog.current.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !createDialog.current.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [createOrigin, createAttemptPending]);

  async function load(revision: number, restore = true) {
    setLoading(true); setError("");
    try {
      const result = await api.partMappings(path) as Detail;
      if (revision !== generation.current) return;
      const saved = restore ? readDraft(path) : null;
      const sameSourceContext = Boolean(saved && saved.sourceHash === result.sourceHash && saved.associationKey === result.associationKey
        && saved.associationVersion === result.associationVersion);
      const sameEvidence = Boolean(sameSourceContext && saved?.version === result.version);
      let nextDecisions: Record<string, string> = {};
      let nextParts: Record<string, Part> = {};
      if (sameEvidence && saved) {
        nextDecisions = saved.decisions || {};
        nextParts = saved.draftParts || {};
        setGroupReasons(saved.groupReasons || {});
        setStaleDrafts(saved.staleDrafts || []);
        setReason(saved.reason || ""); setQuery(saved.query || "");
        setSheetFilter((saved.sheetFilter || "all") as SheetFilter); setReviewFilter((saved.reviewFilter || "all") as ReviewFilter);
        setMappingAttempt(saved.mappingAttempt || null);
        window.requestAnimationFrame(() => { if (tableScroll.current) tableScroll.current.scrollTop = saved.scrollTop || 0; });
      } else {
        const heldDrafts: StaleDraft[] = saved ? Object.entries(saved.decisions || {}).map(([groupKey, decision]) => {
          const prior = saved.draftGroups?.find(group => group.groupKey === groupKey);
          return {groupKey, sheet:prior?.sheet || "Previous source sheet", description:prior?.description || "",
            rowCount:prior?.rowCount || 0, decision, part:saved.draftParts?.[groupKey] || null,
            sourceHash:saved.sourceHash, evidenceFingerprint:prior?.evidenceFingerprint || "", reason:saved.groupReasons?.[groupKey] || saved.reason || ""};
        }) : [];
        setStaleDrafts([...(saved?.staleDrafts || []), ...heldDrafts]);
        setDecisions({}); setDraftParts({});
        setGroupReasons({});
        setReason(saved?.reason || ""); setQuery(saved?.query || "");
        setSheetFilter((saved?.sheetFilter || "all") as SheetFilter); setReviewFilter((saved?.reviewFilter || "all") as ReviewFilter);
        // A timed-out save must keep its exact idempotency key and payload. Retrying
        // lets Grist report the committed result or reject the stale request safely.
        setMappingAttempt(saved?.mappingAttempt || null);
        if (saved) {
          window.requestAnimationFrame(() => { if (tableScroll.current) tableScroll.current.scrollTop = saved.scrollTop || 0; });
          setNotice(heldDrafts.length
            ? "The review evidence changed. Pending choices were held for explicit review and were not applied to current source groups."
            : sameSourceContext
              ? "The saved review version changed. Saved Grist assignments are current; no unsaved choices were reapplied."
              : "The workbook or association changed. Saved Grist assignments are shown for the current evidence; previous choices were not reapplied.");
        }
      }
      if (saved) {
        const currentKeys = new Set(result.groups.map(group => group.key));
        setExpandedGroups(Object.fromEntries(Object.entries(saved.expandedGroups || {}).filter(([key]) => currentKeys.has(key))));
        setExpanded(Object.fromEntries(Object.entries(saved.expandedSourceDetails || {}).filter(([key]) => currentKeys.has(key))));
      }
      if (returnSelection?.path === path && returnSelection.mode === "select" && returnSelection.groupKey) {
        const target = result.groups.find(group => group.key === returnSelection.groupKey);
        const validContext = Boolean(target && returnSelection.sourceHash === result.sourceHash
          && returnSelection.associationKey === result.associationKey && returnSelection.associationVersion === result.associationVersion
          && returnSelection.evidenceFingerprint === target.evidenceFingerprint);
        if (validContext && target && returnSelection.partId) {
          try {
            const matches = await api.searchParts(returnSelection.partId, 0, 100, path);
            const selected = (matches.items || []).find((part: Part) => part.id === returnSelection.partId && part.selectable && !part.duplicateName);
            if (selected && revision === generation.current) {
              nextDecisions = { ...nextDecisions, [target.key]: selected.id }; nextParts = { ...nextParts, [target.key]: selected };
              setNotice(`${selected.partNumber || selected.name} added to the draft for ${groupTitle(target)}. Save the mapping to persist it.`);
            } else if (revision === generation.current) setNotice("The selected Part is no longer available for a new mapping. Search again and review this group.");
          } catch (cause) { if (revision === generation.current) setError(String(cause)); }
        } else if (!validContext) {
          setNotice("The source group changed while Parts was open. Review the refreshed group before assigning a Part.");
        }
        onReturnSelectionConsumed?.();
      }
      if (revision !== generation.current) return;
      setDecisions(nextDecisions); setDraftParts(nextParts); setDetail(result);
    } catch (cause) { if (revision === generation.current) { setDetail(null); setError(String(cause)); } }
    finally { if (revision === generation.current) setLoading(false); }
  }

  const loadedPath = useRef("");
  useEffect(() => {
    if (!active) return;
    const revision = ++generation.current;
    const returningToSamePath = loadedPath.current === path && Boolean(detail);
    if (!returningToSamePath) {
      setDetail(null); setDecisions({}); setDraftParts({}); setStaleDrafts([]); setReason("");
      setGroupReasons({}); setExpandedGroups({}); setExpanded({}); setNotice(""); setError(""); setBusy(false);
      setMappingAttempt(null); setConfirmBatch(false); inFlight.current = false;
    }
    if (path) { loadedPath.current = path; void load(revision); }
    return () => { ++generation.current; };
  }, [path, active]);

  useEffect(() => {
    if (!detail || !path) return;
    const saved: Draft = { path, sourceHash: detail.sourceHash, version: detail.version, associationKey: detail.associationKey,
      associationVersion: detail.associationVersion, decisions, draftParts, groupReasons, expandedGroups, expandedSourceDetails: expanded, reason, query, sheetFilter, reviewFilter,
      draftGroups: Object.keys(decisions).flatMap(groupKey => { const group = detail.groups.find(item => item.key === groupKey); return group ? [{groupKey,
        sheet:group.sheet, description:group.description, rowCount:group.rows.length, evidenceFingerprint:group.evidenceFingerprint}] : []; }),
      staleDrafts, scrollTop: tableScroll.current?.scrollTop || 0, mappingAttempt };
    sessionStorage.setItem(draftKey(path), JSON.stringify(saved));
  }, [path, detail, decisions, draftParts, groupReasons, expandedGroups, expanded, staleDrafts, reason, query, sheetFilter, reviewFilter, mappingAttempt]);

  function openParts(group: Group | null, partId: string | null, mode: "select" | "view") {
    if (!detail) return;
    onOpenParts?.(group?.key || "", partId, mode, group ? { sourceHash: detail.sourceHash, associationKey: detail.associationKey,
      associationVersion: detail.associationVersion, evidenceFingerprint: group.evidenceFingerprint } : { sourceHash: detail.sourceHash,
      associationKey: detail.associationKey, associationVersion: detail.associationVersion });
  }

  function currentOriginMatches(origin: PartCreationOrigin | null | undefined) {
    const current = acceptedMappingRef.current;
    const currentDetail = current.detail;
    if (!origin || !currentDetail || current.loading || origin.workbookPath !== current.path || origin.sourceHash !== currentDetail.sourceHash
      || origin.associationKey !== currentDetail.associationKey || origin.associationVersion !== currentDetail.associationVersion) return false;
    const group = currentDetail.groups.find(item => item.key === origin.groupKey);
    return Boolean(group && group.evidenceFingerprint === origin.evidenceFingerprint
      && group.sheet === origin.sheet && groupTitle(group) === origin.sourceDescription);
  }

  function closeCreateDialog() {
    if (createAttemptPending) return;
    setCreateOrigin(null); setCreateAttemptPending(false);
    window.requestAnimationFrame(() => (createOpener.current || contextToggle.current)?.focus());
  }

  function openCreateDialog(group: Group, button: HTMLButtonElement) {
    if (!detail || locked || !detail.schemaAvailable) return;
    createOpener.current = button;
    setCreateAttemptPending(false);
    setCreateOrigin({ workbookPath: path, groupKey: group.key, sheet: group.sheet, sourceDescription: groupTitle(group),
      sourceHash: detail.sourceHash, associationKey: detail.associationKey, associationVersion: detail.associationVersion,
      evidenceFingerprint: group.evidenceFingerprint });
  }

  function finishContextualCreation(part: ExistingPart, attempt?: PartCreateAttempt) {
    const origin = attempt?.origin || createOrigin;
    const current = acceptedMappingRef.current;
    if (origin && currentOriginMatches(origin)) {
      const group = current.detail?.groups.find(item => item.key === origin.groupKey);
      if (group) {
        const selectablePart = { ...part, selectable: true, duplicateName: false, legacy: false } as Part;
        selectPart(group, selectablePart);
        setNotice(`${selectablePart.partNumber || selectablePart.name} was created in Grist and added as an unsaved mapping selection. Save this group or the pending mappings to persist the assignment.`);
      }
    } else {
      setNotice(`${part.partNumber || part.name} exists in Grist. The originating workbook or source group changed, so it was not assigned. Review current evidence before selecting it.`);
    }
    setCreateAttemptPending(false); setCreateOrigin(null);
    window.requestAnimationFrame(() => (createOpener.current || contextToggle.current)?.focus());
  }

  function useExistingInContext(part: ExistingPart) {
    const origin = acceptedMappingRef.current.createOrigin;
    if (!origin || !currentOriginMatches(origin)) {
      setNotice(`${part.partNumber || part.name} is selectable, but the originating source context changed. Review the current group before assigning it.`);
    } else {
      const group = acceptedMappingRef.current.detail?.groups.find(item => item.key === origin.groupKey);
      if (group) {
        selectPart(group, { ...part, selectable: true, duplicateName: false, legacy: false } as Part);
        setNotice(`${part.partNumber || part.name} was added as an unsaved mapping selection. Save this group or the pending mappings to persist the assignment.`);
      }
    }
    closeCreateDialog();
  }

  function selectPart(group: Group, part: Part | null) {
    setError(""); setNotice(""); setActivePicker(null);
    setStaleDrafts(old => old.filter(draft => draft.groupKey !== group.key));
    if (part && group.part?.id === part.id) {
      setDecisions(old => { const next = { ...old }; delete next[group.key]; return next; });
      setDraftParts(old => { const next = { ...old }; delete next[group.key]; return next; });
      return;
    }
    if (!part) {
      if (!group.part) {
        setDecisions(old => { const next = { ...old }; delete next[group.key]; return next; });
        setDraftParts(old => { const next = { ...old }; delete next[group.key]; return next; });
      } else {
        setDecisions(old => ({ ...old, [group.key]: "" }));
        setDraftParts(old => { const next = { ...old }; delete next[group.key]; return next; });
      }
      return;
    }
    setDecisions(old => ({ ...old, [group.key]: part.id }));
    setDraftParts(old => ({ ...old, [group.key]: part }));
  }

  function reasonRequired(group: Group, nextPartId: string | null | undefined) {
    const priorIds = new Set<string>([
      ...(group.part?.id ? [group.part.id] : []),
      ...(group.previousAssignment?.assignedPartIds || []),
    ]);
    if (!priorIds.size) return false;
    if (!nextPartId) return true;
    return [...priorIds].some(id => id !== nextPartId);
  }

  function pendingReasonRequired(group: Group) {
    return groupPending(group) && reasonRequired(group, decisions[group.key] || null);
  }

  async function saveGroups(groupKeys: string[]) {
    if (!detail || !groupKeys.length || inFlight.current) return;
    let attempt = mappingAttempt;
    if (attempt) {
      if (attempt.groupKeys.join("\0") !== groupKeys.join("\0")) return;
    } else {
      const payloadDecisions = Object.fromEntries(groupKeys.map(key => [key, decisions[key]]));
      if (Object.values(payloadDecisions).some(value => value === undefined)) return;
      const needsReason = groupKeys.filter(key => {
        const group = detail.groups.find(item => item.key === key);
        return Boolean(group && reasonRequired(group, payloadDecisions[key] || null));
      });
      const payloadReasons = Object.fromEntries(needsReason.map(key => [key, groupReasons[key] || ""]));
      if (needsReason.some(key => !String(payloadReasons[key] || "").trim())) return;
      attempt = { key: crypto.randomUUID(), groupKeys: [...groupKeys], payload: { path, decisions: payloadDecisions, reasons: payloadReasons,
        expectedHash: detail.sourceHash, expectedVersion: detail.version, expectedAssociationKey: detail.associationKey,
        expectedAssociationVersion: detail.associationVersion } };
    }
    const requestRevision = generation.current;
    inFlight.current = true; setMappingAttempt(attempt); setBusy(true); setError(""); setConfirmBatch(false);
    try {
      const result = await api.savePartMappings(attempt.payload, attempt.key);
      if (requestRevision !== generation.current) return;
      const previousDetail = detail;
      const previousDecisions = decisions;
      const previousParts = draftParts;
      const sameSource = result.sourceHash === previousDetail.sourceHash && result.associationKey === previousDetail.associationKey
        && result.associationVersion === previousDetail.associationVersion;
      const retained: Record<string, string> = {};
      const retainedParts: Record<string, Part> = {};
      const retainedReasons: Record<string, string> = {};
      const dropped: string[] = [];
      for (const [key, value] of Object.entries(previousDecisions)) {
        if (attempt.groupKeys.includes(key)) continue;
        const oldGroup = previousDetail.groups.find(group => group.key === key);
        const newGroup = oldGroup;
        if (sameSource && oldGroup && newGroup) {
          retained[key] = value;
          if (previousParts[key]) retainedParts[key] = previousParts[key];
          if (groupReasons[key]) retainedReasons[key] = groupReasons[key];
        } else dropped.push(key);
      }
      const assignments = result.assignments || {};
      const confirmedRows = result.confirmedRows || [];
      const savedKeys = new Set(attempt.groupKeys);
      const nextGroups = previousDetail.groups.map(group => {
        if (!savedKeys.has(group.key)) return group;
        const assignment = assignments[group.key] || {};
        const partId = assignment.partId || null;
        const part = partId ? (previousParts[group.key] || (group.part?.id === partId ? group.part : null)) : null;
        return { ...group, part, reviewed: Boolean(partId), explicitlyUnassigned: !partId,
          needsCompatibilityReview: false, previousAssignment: undefined, version: result.version };
      });
      const fresh: Detail = { ...previousDetail, sourceHash: result.sourceHash || previousDetail.sourceHash,
        associationKey: result.associationKey || previousDetail.associationKey,
        associationVersion: result.associationVersion || previousDetail.associationVersion,
        version: result.version, groups: nextGroups,
        unresolvedGroups: nextGroups.filter(group => !group.reviewed).length,
        history: [...previousDetail.history, ...confirmedRows] };
      setDetail(fresh); setDecisions(retained); setDraftParts(retainedParts); setGroupReasons(retainedReasons); setMappingAttempt(null);
      setStaleDrafts(old => old.filter(draft => !attempt.groupKeys.includes(draft.groupKey)));
      setReason(Object.keys(retained).length ? reason : "");
      setNotice(`Grist confirmed ${result.savedRows} source-row assignment${result.savedRows === 1 ? "" : "s"} in review version ${result.version}.${dropped.length ? ` ${dropped.length} changed draft group(s) were cleared for review.` : ""}`);
      sessionStorage.setItem(draftKey(path), JSON.stringify({ path, sourceHash: fresh.sourceHash, version: fresh.version,
        associationKey: fresh.associationKey, associationVersion: fresh.associationVersion, decisions: retained, draftParts: retainedParts,
        groupReasons: retainedReasons, expandedGroups, expandedSourceDetails: expanded,
        draftGroups: Object.keys(retained).flatMap(groupKey => { const group = fresh.groups.find(item => item.key === groupKey); return group ? [{groupKey,
          sheet:group.sheet, description:group.description, rowCount:group.rows.length, evidenceFingerprint:group.evidenceFingerprint}] : []; }),
        staleDrafts: staleDrafts.filter(draft => !attempt.groupKeys.includes(draft.groupKey)), reason: Object.keys(retained).length ? reason : "", query, sheetFilter, reviewFilter,
        scrollTop: tableScroll.current?.scrollTop || 0, mappingAttempt: null } satisfies Draft));
    } catch (cause) { if (requestRevision === generation.current) setError(String(cause)); }
    finally { inFlight.current = false; if (requestRevision === generation.current) setBusy(false); }
  }

  async function reviewCurrentEvidence() {
    if (!detail || busy || loading || !mappingAttempt) return;
    const requestRevision = generation.current;
    const oldDetail = detail;
    const oldDecisions = decisions;
    const oldParts = draftParts;
    setLoading(true); setError("");
    try {
      const fresh = await api.partMappings(path) as Detail;
      if (requestRevision !== generation.current) return;
      const sameSource = fresh.sourceHash === oldDetail.sourceHash && fresh.associationKey === oldDetail.associationKey
        && fresh.associationVersion === oldDetail.associationVersion;
      const nextDecisions: Record<string, string> = {};
      const nextParts: Record<string, Part> = {};
      const movedToReview: StaleDraft[] = [];
      for (const [key, decision] of Object.entries(oldDecisions)) {
        const before = oldDetail.groups.find(group => group.key === key);
        const after = fresh.groups.find(group => group.key === key);
        const sameGroupEvidence = Boolean(sameSource && before && after && before.evidenceFingerprint === after.evidenceFingerprint
          && before.reviewed === after.reviewed && before.part?.id === after.part?.id
          && before.explicitlyUnassigned === after.explicitlyUnassigned);
        if (sameGroupEvidence) {
          nextDecisions[key] = decision;
          if (oldParts[key]) nextParts[key] = oldParts[key];
        } else if (before) {
          movedToReview.push({groupKey:key, sheet:before.sheet, description:before.description, rowCount:before.rows.length,
            decision, part:oldParts[key] || before.part, sourceHash:oldDetail.sourceHash,
            evidenceFingerprint:before.evidenceFingerprint, reason:groupReasons[key] || reason});
        }
      }
      setDetail(fresh); setDecisions(nextDecisions); setDraftParts(nextParts);
      setStaleDrafts(old => [...old.filter(item => !movedToReview.some(moved => moved.groupKey === item.groupKey)), ...movedToReview]);
      setMappingAttempt(null); setNotice(movedToReview.length
        ? `${movedToReview.length} stale draft group(s) were held for explicit review. Current source values are shown below; use Reapply only after reviewing them.`
        : "The current review is refreshed. Remaining drafts were retained because their source rows and saved assignment state are unchanged.");
      inFlight.current = false;
    } catch (cause) {
      if (requestRevision === generation.current) setError(String(cause));
    } finally { if (requestRevision === generation.current) setLoading(false); }
  }

  function reapplyStaleDraft(stale: StaleDraft) {
    const group = detail?.groups.find(item => item.key === stale.groupKey);
    if (!group) return;
    setDecisions(old => ({ ...old, [group.key]: stale.decision }));
    setDraftParts(old => stale.decision && stale.part ? ({ ...old, [group.key]: stale.part! }) : old);
    if (stale.reason) setGroupReasons(old => ({ ...old, [group.key]: old[group.key] || stale.reason! }));
    setStaleDrafts(old => old.filter(item => item.groupKey !== stale.groupKey));
    setNotice(`Previous choice reopened for ${groupTitle(group)}. Confirm the displayed current source rows and save with a review reason.`);
  }

  const [activePicker, setActivePicker] = useState<string | null>(null);
  if (!path) return <main className="part-mapping"><h1>Part Mapping</h1><p>Select a costing workbook in Files to review its Parts.</p></main>;

  const allGroups = detail?.groups || [];
  const baselineParts = [...new Map(allGroups.filter(group => group.reviewed && group.part && !group.part.legacy)
    .map(group => [group.part!.id, group.part!])).values()];
  const pendingGroups = allGroups.filter(group => Object.prototype.hasOwnProperty.call(decisions, group.key));
  const visibleGroups = allGroups.filter(group => {
    if (sheetFilter !== "all" && familyFor(group.sheet) !== sheetFilter) return false;
    const haystack = `${group.description} ${group.sheet} ${group.rows.map(row => `${row.row} ${Object.values(row.fields || {}).join(" ")}`).join(" ")}`.toLocaleLowerCase();
    if (query.trim() && !haystack.includes(query.trim().toLocaleLowerCase())) return false;
    if (reviewFilter === "needs-review" && (group.reviewed || Object.prototype.hasOwnProperty.call(decisions, group.key))) return false;
    if (reviewFilter === "saved" && !group.reviewed) return false;
    if (reviewFilter === "unsaved" && !Object.prototype.hasOwnProperty.call(decisions, group.key)) return false;
    return true;
  });
  const locked = busy || loading || Boolean(mappingAttempt);
  const canRefreshConflict = Boolean(mappingAttempt && /PART_REVIEW_STALE|PART_ASSOCIATION_STALE|PART_GROUP_UNKNOWN|PART_SOURCE_LABEL_UNAVAILABLE/.test(error));
  const totalRows = allGroups.reduce((total, group) => total + group.rows.length, 0);
  const groupPending = (group: Group) => Object.prototype.hasOwnProperty.call(decisions, group.key);
  const selectedPartFor = (group: Group) => groupPending(group) ? (decisions[group.key] ? draftParts[group.key] : null) : group.part;
  const sourceStatus = (group: Group) => {
    if (mappingAttempt?.groupKeys.includes(group.key)) return error ? "Failed" : "Saving";
    if (groupPending(group)) return "Unsaved";
    if (group.reviewed) return "Saved";
    return "Needs review";
  };
  const requiredReasonGroups = pendingGroups.filter(pendingReasonRequired);
  const displayedKeys = visibleGroups.map(group => group.key);
  const canonicalSummary = (group: Group) => groupPending(group)
    ? decisions[group.key] ? `Unsaved · ${formatPart(draftParts[group.key])}` : `Clear pending · ${formatPart(group.part)}`
    : group.part ? formatPart(group.part) : "No Part selected";
  const perSheet = (family: SheetFilter) => {
    const matching = allGroups.filter(group => family === "all" || familyFor(group.sheet) === family);
    return { groups: matching.length, rows: matching.reduce((sum, group) => sum + group.rows.length, 0) };
  };

  return <main className={`part-mapping-shell ${contextOpen ? "context-open" : "context-closed"} ${narrowViewport ? "narrow" : "wide"}`}>
    {detail && narrowViewport && contextOpen && <button type="button" className="part-context-backdrop" aria-label="Close Mapping context panel" onClick={() => { setContextOpen(false); window.requestAnimationFrame(() => contextToggle.current?.focus());}} />}
    {detail && <aside id="part-mapping-context" ref={contextPanel} tabIndex={-1} aria-hidden={!contextOpen} className={`part-context-panel ${contextOpen ? "open" : "closed"}`} aria-label="Workbook and review context">
      <header className="part-context-heading"><div><span>Workbook context</span><small>{path}</small></div><button type="button" className="button quiet" aria-label={contextOpen ? "Hide supporting information" : "Show supporting information"} aria-expanded={contextOpen} aria-controls="part-mapping-context" onClick={() => { setContextOpen(open => !open); if (contextOpen) window.requestAnimationFrame(() => contextToggle.current?.focus()); }}>{contextOpen ? <ChevronLeft size={16}/> : <ChevronRight size={16}/>}</button></header>
      <div className="part-context-scroll">
        <section className="part-context-summary" aria-label="Extended mapping statistics"><div><strong>{allGroups.filter(group => !group.reviewed && !groupPending(group)).length}</strong><span>groups needing review</span></div><div><strong>{pendingGroups.length}</strong><span>unsaved changes</span></div><div><strong>{totalRows}</strong><span>active source rows</span></div><div><strong>{detail.version}</strong><span>saved review version</span></div></section>
        <details className="part-review-details" open><summary>Review details</summary><div><p>Workbook SHA-256 · <code>{detail.sourceHash}</code></p><p>Mapping policy · {detail.mappingPolicyVersion} · review version {detail.version} · saved workbook evidence</p>
          {!detail.schemaAvailable && <p role="alert">Part review storage is unavailable.</p>}
          {detail.legacyHistoryAvailable === false && <p role="alert">Legacy Grist Part assignment history is unavailable. Existing history must be reconciled before those assignments can be relied on.</p>}
          {!detail.associationKey && <p role="alert">Save a file association before saving Part assignments.</p>}
          {Object.entries(detail.sourceSheets || {}).map(([sheet, diagnostic]) => <p key={sheet}><strong>{sheet}:</strong> {diagnostic.status === "ok" ? `${diagnostic.labelField === "part_category" ? "Part Category" : "Machine Piece Description"} from ${diagnostic.labelHeader} at ${diagnostic.labelHeaderCell}` : diagnostic.status === "sheet_missing" ? "Sheet is not present in this workbook." : diagnostic.status === "header_missing" ? "A source header row was not found." : diagnostic.status === "label_column_ambiguous" ? `Multiple possible Part label columns were found: ${diagnostic.ambiguousLabelHeaders.join(", ")}.` : "The configured Part label column is missing; blank rows cannot be mapped by substitution."}</p>)}
        </div></details>
        <PartBaselinePanel path={path} parts={baselineParts} groups={allGroups}
          onUseDifferentPart={(groupKey, part) => { const target = allGroups.find(group => group.key === groupKey); if (target) selectPart(target, part); }}/>
        {detail.history.length > 0 && <details className="part-history"><summary>Assignment history · {detail.history.length} source rows</summary>{detail.history.map(row => <p key={row.ReviewKey}><strong>v{row.Version} · {row.SheetName} row {row.SourceRow}</strong> · {row.SourceDescription || "Blank description"} · {row.PartNumberUsed || "No Part assigned"}{row.NameUsed ? ` · ${row.NameUsed}` : ""}<br/>{row.Actor} · {row.OccurredAt}<br/>{row.Reason}</p>)}</details>}
      </div>
    </aside>}
    {detail && narrowViewport && <button type="button" ref={contextToggle} className="part-context-mobile-toggle" aria-controls="part-mapping-context" aria-expanded={contextOpen} onClick={() => setContextOpen(open => !open)}>{contextOpen ? "Hide supporting information" : "Show supporting information"}</button>}
    <section className="part-mapping">
      <div className="part-heading part-heading-compact"><div><span className="eyebrow">Safari Manufacturing · Source assignments</span><h1>Part Mapping</h1><p className="part-workbook" title={path}>{path}</p></div><div className="workbench-actions"><button type="button" ref={!narrowViewport ? contextToggle : undefined} className="button part-context-desktop-toggle" aria-controls="part-mapping-context" aria-expanded={contextOpen} onClick={() => setContextOpen(open => !open)}>{contextOpen ? "Hide supporting information" : "Show supporting information"}</button><button type="button" className="button" disabled={locked} onClick={() => openParts(null, null, "select")}>Parts register</button><button type="button" className="button" disabled={busy || loading || (Boolean(mappingAttempt) && !canRefreshConflict)} onClick={() => canRefreshConflict ? void reviewCurrentEvidence() : void load(++generation.current)}>{canRefreshConflict ? "Review current source" : "Reload review"}</button></div></div>
      {error && <div role="alert" className="part-error-banner"><strong>{mappingAttempt ? "Save failed" : "Part Mapping needs attention"}</strong><span>{error}</span>{mappingAttempt && <small>Selections and reason are retained. Retry sends the same request key and payload so a committed Grist save can be recovered safely.</small>}{canRefreshConflict && <button className="button" onClick={() => void reviewCurrentEvidence()}>Refresh and hold changed drafts for review</button>}</div>}
      {notice && <p role="status" className="part-notice compact">{notice}</p>}
      {loading && <p role="status" className="part-loading-status">{detail ? "Refreshing workbook and saved mapping evidence… Draft selections remain unconfirmed until this check completes." : "Loading Part review…"}</p>}
      {!detail && !loading && !error && <p role="status">Part Mapping has no current workbook evidence.</p>}
      {detail && <>
        <section className="part-mapping-toolbar" aria-label="Filter Part mappings">
          <label className="part-search-label">Search descriptions or source fields<input name="part-mapping-source-search" autoComplete="off" spellCheck={false} autoCapitalize="none" aria-label="Search descriptions or source fields" value={query} onChange={event => setQuery(event.target.value)} placeholder="Part description, material, row…"/></label>
          <label>Source sheet<select name="part-mapping-sheet-filter" autoComplete="off" aria-label="Source sheet" value={sheetFilter} onChange={event => setSheetFilter(event.target.value as SheetFilter)}>{(["all", "mcl", "toolshop", "cnc"] as SheetFilter[]).map(value => { const count = perSheet(value); return <option key={value} value={value}>{familyLabel(value)} · {count.groups} groups · {count.rows} rows</option>; })}</select></label>
          <label>Review status<select name="part-mapping-status-filter" autoComplete="off" aria-label="Review status" value={reviewFilter} onChange={event => setReviewFilter(event.target.value as ReviewFilter)}>
            <option value="all">All · {allGroups.length} groups</option><option value="needs-review">Needs review · {allGroups.filter(group => !group.reviewed && !groupPending(group)).length} groups</option>
            <option value="saved">Saved · {allGroups.filter(group => group.reviewed).length} groups</option><option value="unsaved">Unsaved changes · {pendingGroups.length} groups</option>
          </select></label>
          <div className="part-group-expansion-actions" aria-label="Group expansion controls">
            <button type="button" className="button quiet" disabled={!visibleGroups.length} onClick={() => setExpandedGroups(old => ({ ...old, ...Object.fromEntries(displayedKeys.map(key => [key, true])) }))}>Expand all displayed</button>
            <button type="button" className="button quiet" disabled={!visibleGroups.length} onClick={() => setExpandedGroups(old => ({ ...old, ...Object.fromEntries(displayedKeys.map(key => [key, false])) }))}>Collapse all displayed</button>
          </div>
        </section>
        {staleDrafts.length > 0 && <section className="part-stale-drafts" aria-label="Drafts held for source review"><h2>Drafts held for source review</h2><p>These choices were not applied after workbook, association or group evidence changed. Review each current source group before reopening its choice.</p>{staleDrafts.map(stale => {
          const current = allGroups.find(group => group.key === stale.groupKey);
          return <article key={`${stale.groupKey}:${stale.sourceHash}`}><div><strong>{stale.sheet} · {stale.description || "Blank Part label"}</strong><span>{stale.rowCount} prior source row(s) · previous choice: {stale.decision ? formatPart(stale.part) : "Clear saved Part assignment"} · from workbook {stale.sourceHash.slice(0,12)}…</span></div>{current ? <button className="button" onClick={() => reapplyStaleDraft(stale)}>Reapply after reviewing current rows</button> : <span className="part-stale-missing">This group no longer exists; search and assign a current group manually.</span>}</article>;
        })}</section>}
        <p className="part-counts"><strong>Showing {visibleGroups.length} of {allGroups.length} source groups</strong> · {visibleGroups.reduce((sum, group) => sum + group.rows.length, 0)} displayed source rows. Filters never discard hidden drafts.</p>
        <div className="part-source-table" ref={tableScroll} onScroll={() => { const draft = readDraft(path); if (draft && tableScroll.current) sessionStorage.setItem(draftKey(path), JSON.stringify({ ...draft, scrollTop: tableScroll.current.scrollTop })); }}>
          {visibleGroups.length ? visibleGroups.map(group => {
            const groupIsExpanded = Boolean(expandedGroups[group.key]);
            const needsReason = pendingReasonRequired(group);
            const groupReason = groupReasons[group.key] || "";
            const selectedPart = selectedPartFor(group);
            const blocker = !detail.schemaAvailable ? "Part review storage is unavailable; saving is blocked." : !detail.associationKey ? "Save a file association before assigning Parts." : group.sourceDiagnostic?.status && group.sourceDiagnostic.status !== "ok" ? "Resolve the source label column before saving." : groupPending(group) ? "" : !selectedPart ? "Choose a canonical Part before saving." : "";
            return <article className={`part-source-group ${groupIsExpanded ? "is-expanded" : "is-collapsed"}`} key={group.key}>
              {group.previousAssignment && !group.reviewed && <div className="part-compatibility-note" role="status"><strong>Older assignment needs review.</strong>{group.previousAssignment.previousRowsAgree === false
                ? " Previous row assignments differ or do not cover every current source row; review the source-row history before assigning this group."
                : <> The previous group used {group.previousAssignment.sourceDescription || "a different source label"}{group.previousAssignment.partNumber ? ` and ${group.previousAssignment.partNumber}` : ""}{group.previousAssignment.name ? ` · ${group.previousAssignment.name}` : ""}.</>} It remains in history and was not carried into this sheet-specific group.</div>}
              {group.sourceDiagnostic?.status && group.sourceDiagnostic.status !== "ok" && <div className="part-compatibility-note" role="alert">Part label source is {group.sourceDiagnostic.status.replaceAll("_", " ")}. Resolve the source header before saving a Part assignment.</div>}
              <header className="part-group-heading">
                <button type="button" className="part-group-toggle" aria-label={`${groupIsExpanded ? "Collapse" : "Expand"} source evidence · ${groupTitle(group)}`} aria-expanded={groupIsExpanded} aria-controls={`part-group-content-${group.key}`} onClick={() => setExpandedGroups(old => ({ ...old, [group.key]: !Boolean(old[group.key]) }))}>
                  {groupIsExpanded ? <ChevronDown size={16}/> : <ChevronRight size={16}/>}<span className="part-group-heading-copy"><span className="part-sheet-chip">{group.sheet}</span><strong>{groupTitle(group)}</strong><small>{group.rows.length} source row{group.rows.length === 1 ? "" : "s"} · {group.labelField === "part_category" ? "Part Category" : "Machine Piece Description"}</small></span>
                </button>
                <div className="part-group-header-controls">
                  <PartSearchCombobox group={group} selected={selectedPart} disabled={locked || !detail.schemaAvailable || Boolean(group.sourceDiagnostic?.status && group.sourceDiagnostic.status !== "ok")}
                    path={path} open={activePicker === group.key} onOpen={() => setActivePicker(group.key)} onClose={() => setActivePicker(null)} onSelect={part => selectPart(group, part)}/>
                  <div className="part-group-actions">
                    <button type="button" className="button" disabled={locked || !detail.schemaAvailable} onClick={event => openCreateDialog(group, event.currentTarget)}>Create Part</button>
                    <button type="button" className="button" disabled={locked || !onOpenParts || !selectedPart} onClick={() => openParts(group, selectedPart?.id || null, "view")}>View Part</button>
                    {(group.part || decisions[group.key]) && <button type="button" className="button quiet" disabled={locked} onClick={() => selectPart(group, null)}>{group.part ? "Clear saved Part…" : "Clear selection"}</button>}
                    {groupPending(group) && <button type="button" className="button quiet" disabled={locked} onClick={() => { setDecisions(old => { const next = { ...old }; delete next[group.key]; return next; }); setDraftParts(old => { const next = { ...old }; delete next[group.key]; return next; }); }}>Revert draft</button>}
                    <button type="button" className="button primary" disabled={busy || loading || (Boolean(mappingAttempt) && !mappingAttempt?.groupKeys.includes(group.key)) || !detail.schemaAvailable || !detail.associationKey || Boolean(group.sourceDiagnostic?.status && group.sourceDiagnostic.status !== "ok") || (!mappingAttempt && (!groupPending(group) || (needsReason && !groupReason.trim())))}
                      onClick={() => void saveGroups(mappingAttempt?.groupKeys.includes(group.key) ? mappingAttempt.groupKeys : [group.key])}>{mappingAttempt?.groupKeys.includes(group.key) && error ? "Retry save" : "Save"}</button>
                    <span className={`part-state state-${sourceStatus(group).toLowerCase()}`}>{sourceStatus(group)}</span>
                  </div>
                </div>
              </header>
              {needsReason && <div className="part-group-reason-row"><label className="part-group-reason">Reason for mapping change<input name={`mapping-change-reason-${group.key}`} autoComplete="off" spellCheck={false} autoCapitalize="sentences" aria-label={`Reason for mapping change · ${groupTitle(group)}`} value={groupReason} disabled={locked} onChange={event => setGroupReasons(old => ({ ...old, [group.key]: event.target.value }))} placeholder="Required for replacement or clear"/></label></div>}
              {selectedPart?.outOfScopeCodes?.length ? <small className="part-scope-advisory">Scope advisory: this assignment remains saveable for {selectedPart.outOfScopeCodes.map(code => code.code).join(", ")}.</small> : null}
              {group.explicitlyUnassigned && <small className="part-unassigned-note">The last saved action explicitly cleared the Part. Historical assignments remain in History.</small>}
              {blocker && <small className="part-save-blocker">{blocker}</small>}
              <div id={`part-group-content-${group.key}`} className="part-group-content" hidden={!groupIsExpanded}><SourceDetails group={group} expanded={Boolean(expanded[group.key])} onToggle={() => setExpanded(old => ({ ...old, [group.key]: !old[group.key] }))}/></div>
            </article>}) : <div className="part-empty-state">No source groups match these filters.</div>}
        </div>
        <div className="part-sticky-save" role="region" aria-label="Save Part mapping drafts"><div><strong>{pendingGroups.length} unsaved group{pendingGroups.length === 1 ? "" : "s"}</strong><span>{pendingGroups.reduce((sum, group) => sum + group.rows.length, 0)} source rows · {requiredReasonGroups.length} changed group{requiredReasonGroups.length === 1 ? "" : "s"} need a reason</span></div>
          <small>Initial assignments need no reason. Replacement and clear reasons appear beside each affected group.</small>
          <button type="button" className="button primary" disabled={busy || loading || (mappingAttempt ? false : (!detail.schemaAvailable || !detail.associationKey || !pendingGroups.length || requiredReasonGroups.some(group => !(groupReasons[group.key] || "").trim())))} onClick={() => mappingAttempt ? void saveGroups(mappingAttempt.groupKeys) : setConfirmBatch(true)}>{mappingAttempt ? "Retry saving pending mappings" : "Save pending mappings"}</button>
        </div>
        {confirmBatch && <div className="part-modal-backdrop" role="presentation"><section className="part-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="part-confirm-title" aria-describedby="part-confirm-help">
          <h2 id="part-confirm-title">Save all pending mappings?</h2><p id="part-confirm-help">This includes every unsaved group, even groups hidden by the current filters. Review the full save scope below.</p>
          <div className="part-confirm-list">{pendingGroups.map(group => { const needsReason = pendingReasonRequired(group); return <article key={group.key}><strong>{group.sheet} · {groupTitle(group)}</strong><span>{decisions[group.key] ? formatPart(draftParts[group.key]) : "Clear saved Part assignment"} · {needsReason ? "Reason required" : "No reason required"}</span>{needsReason && <label>Reason for mapping change<input name={`mapping-batch-reason-${group.key}`} autoComplete="off" spellCheck={false} autoCapitalize="sentences" aria-label={`Batch mapping reason · ${groupTitle(group)}`} value={groupReasons[group.key] || ""} onChange={event => setGroupReasons(old => ({ ...old, [group.key]: event.target.value }))} placeholder="Why is this saved Part being replaced or cleared?" disabled={locked}/></label>}</article>; })}</div>
          <div className="part-confirm-actions"><button type="button" className="button" onClick={() => setConfirmBatch(false)}>Cancel</button><button type="button" className="button primary" disabled={locked || requiredReasonGroups.some(group => !(groupReasons[group.key] || "").trim())} onClick={() => void saveGroups(pendingGroups.map(group => group.key))}>Confirm save {pendingGroups.length} groups</button></div>
        </section></div>}
      </>}
    </section>
    {createOrigin && <div className="part-create-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget && !createAttemptPending) closeCreateDialog(); }}><section ref={createDialog} className="part-create-dialog" role="dialog" aria-modal="true" aria-labelledby="part-create-dialog-title" tabIndex={-1}>
      <header><div><span className="eyebrow">Part Mapping · {createOrigin.sheet}</span><h2 id="part-create-dialog-title">Create and assign a Part</h2><p>The canonical Part is published first. Its source mapping remains a draft until you save it.</p></div><button type="button" className="button quiet" aria-label="Close Part creation dialog" disabled={createAttemptPending} onClick={closeCreateDialog}>Close</button></header>
      <PartCreationForm key={`${createOrigin.workbookPath}:${createOrigin.groupKey}`} origin={createOrigin} originContextCurrent={currentOriginMatches(createOrigin)} onAttemptStateChange={setCreateAttemptPending}
        onCreated={(part, attempt) => finishContextualCreation(part, attempt)} onUseExisting={useExistingInContext} onCancel={closeCreateDialog} cancelLabel="Cancel before creation"/>
    </section></div>}
  </main>;}

function PartSearchCombobox({ group, selected, disabled, path, open, onOpen, onClose, onSelect }: { group: Group; selected: Part | null | undefined; disabled: boolean; path: string; open: boolean; onOpen: () => void; onClose: () => void; onSelect: (part: Part | null) => void }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Part[]>([]);
  const [resultsFor, setResultsFor] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [activeIndex, setActiveIndex] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [cacheAge, setCacheAge] = useState(0);
  const [menuStyle, setMenuStyle] = useState<CSSProperties>({ visibility: "hidden" });
  const generation = useRef(0);
  const input = useRef<HTMLInputElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const currentResults = resultsFor === query && !loading;
  const options = currentResults ? results.filter(part => part.selectable && !part.duplicateName) : [];
  useEffect(() => {
    if (!open) return;
    const request = ++generation.current;
    const controller = new AbortController();
    setLoading(true); setError(""); setResults([]); setResultsFor(null); setTotal(0); setCacheAge(0); setActiveIndex(0);
    const timer = window.setTimeout(async () => {
      try {
        const result = await api.searchParts(query, 0, 30, path, controller.signal);
        if (request !== generation.current) return;
        setResults(result.items || []); setResultsFor(query); setTotal(result.total || 0); setCacheAge(Number(result.registryCacheAgeSeconds || 0)); setError(""); setActiveIndex(0);
      } catch (cause) { if (request === generation.current && !controller.signal.aborted) { setResults([]); setResultsFor(query); setTotal(0); setError(String(cause)); } }
      finally { if (request === generation.current && !controller.signal.aborted) setLoading(false); }
    }, query ? 180 : 0);
    return () => { window.clearTimeout(timer); controller.abort(); ++generation.current; };
  }, [open, query, path]);
  useEffect(() => { if (open) input.current?.focus(); }, [open]);
  useLayoutEffect(() => {
    if (!open || !input.current) return;
    const position = () => {
      const rect = input.current?.getBoundingClientRect();
      if (!rect) return;
      const below = Math.max(0, window.innerHeight - rect.bottom - 8);
      const above = Math.max(0, rect.top - 8);
      const openBelow = below >= Math.min(180, above) || below >= above;
      const maxHeight = Math.max(120, Math.min(260, openBelow ? below : above));
      const top = openBelow ? Math.min(window.innerHeight - 40, rect.bottom + 4) : Math.max(4, rect.top - maxHeight - 4);
      setMenuStyle({ position: "fixed", top, left: Math.max(4, rect.left), width: Math.min(rect.width, window.innerWidth - Math.max(8, rect.left) - 4), maxHeight, visibility: "visible" });
    };
    position();
    window.addEventListener("resize", position);
    document.addEventListener("scroll", position, true);
    return () => { window.removeEventListener("resize", position); document.removeEventListener("scroll", position, true); };
  }, [open, currentResults, options.length]);
  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!root.current?.contains(target) && !menu.current?.contains(target)) onClose();
    };
    document.addEventListener("mousedown", closeOutside);
    return () => document.removeEventListener("mousedown", closeOutside);
  }, [open, onClose]);
  function choose(part: Part) {
    if (disabled || !open || !currentResults || !part.selectable || part.duplicateName) return;
    onSelect(part); setQuery(""); setResults([]); setResultsFor(null); setTotal(0);
  }
  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") { event.preventDefault(); onOpen(); setActiveIndex(index => Math.min(options.length - 1, index + 1)); }
    else if (event.key === "ArrowUp") { event.preventDefault(); setActiveIndex(index => Math.max(0, index - 1)); }
    else if (event.key === "Enter" && open) { event.preventDefault(); if (currentResults && options[activeIndex]) choose(options[activeIndex]); }
    else if (event.key === "Escape" && open) { event.preventDefault(); setQuery(""); onClose(); }
  }
  return <div ref={root} className="part-combobox-wrap">
    <label className="part-combobox-label" htmlFor={`part-search-${group.key}`}>Canonical Part · permanent number, name, description, variant or previous alias</label>
    <input ref={input} id={`part-search-${group.key}`} name="part-mapping-canonical-search" autoComplete="off" spellCheck={false} autoCapitalize="none" aria-label={`Part assignment for ${groupTitle(group)}`} role="combobox" aria-autocomplete="list" aria-expanded={open} aria-busy={open && loading} aria-controls={`part-options-${group.key}`} aria-activedescendant={open && currentResults && options[activeIndex] ? `part-option-${group.key}-${activeIndex}` : undefined}
      value={open ? query : formatPart(selected)} placeholder="Search canonical Parts…" disabled={disabled} onFocus={onOpen} onClick={() => { if (!open) { setQuery(""); onOpen(); } }} onChange={event => { setQuery(event.target.value); setResults([]); setResultsFor(null); setLoading(true); setError(""); setActiveIndex(0); if (!open) onOpen(); }} onKeyDown={onKeyDown} />
    {open && createPortal(<div ref={menu} id={`part-options-${group.key}`} className="part-combobox-options" style={menuStyle} role="listbox" aria-label="Matching canonical Parts">
      {loading || !currentResults ? <div role="status" className="part-search-loading">Searching canonical Parts…</div> : error ? <div role="alert" className="part-search-error">{error}</div> : options.length ? options.map((part, index) => <button id={`part-option-${group.key}-${index}`} type="button" role="option" aria-selected={selected?.id === part.id || activeIndex === index} key={part.id} onMouseEnter={() => setActiveIndex(index)} onMouseDown={event => event.preventDefault()} onClick={() => choose(part)}>
        <strong>{formatPart(part)}</strong><span>{part.description || part.variant || "Canonical Safari Manufacturing Part"}</span>
      </button>) : <div className="part-search-empty">No unambiguous active Part matches this search.</div>}
      {currentResults && !error && total > options.length && <small className="part-search-count">Showing {options.length} of {total} results. Type more to narrow the list.</small>}
      {currentResults && !error && cacheAge > 0 && <small className="part-search-count">Search index read from Grist {cacheAge.toFixed(1)} seconds ago; Save rechecks current Part data.</small>}
    </div>, document.body)}
  </div>;
}

function SourceDetails({ group, expanded, onToggle }: { group: Group; expanded: boolean; onToggle: () => void }) {
  const columns = sourceFields[group.sheet] || [];
  const visibleColumns = expanded ? columns : columns.slice(0, 5);
  return <section className={`part-source-details ${expanded ? "expanded" : ""}`} aria-label={`${group.sheet} source details`}>
    <div className="part-source-details-heading"><strong>Source evidence</strong><button type="button" className="button quiet" aria-expanded={expanded} onClick={onToggle}>{expanded ? "Show key fields" : "Expand source details"}</button></div>
    {!columns.length ? <p>Source-specific fields are not configured for this sheet.</p> : <div className="part-detail-scroll"><table><thead><tr>{visibleColumns.map(column => {
      const header = group.rows.map(row => row.sourceHeaders?.[column.key]).find(Boolean);
      const available = group.rows.some(row => row.availableFields?.includes(column.key));
      return <th key={column.key} title={header || (available ? column.label : "Column not found in source")}>{header || `${column.label}${available ? "" : " · not found"}`}</th>;
    })}<th>Source row</th></tr></thead><tbody>{group.rows.map(row => <tr key={`${row.sheet}:${row.row}`}>{visibleColumns.map(column => {
      const value = row.fields?.[column.key];
      const cell = row.sourceCells?.[column.key]?.cell;
      const available = row.availableFields?.includes(column.key);
      return <td key={column.key} title={cell ? `Source cell ${cell}${row.sourceHeaders?.[column.key] ? ` · ${row.sourceHeaders[column.key]}` : ""}` : undefined}>
        {value === null || value === undefined || String(value).trim() === "" ? <span className={available ? "part-source-blank" : "part-source-missing"}>{available ? "— blank in source" : "Not in sheet"}</span> : <>{String(value)}{cell && <small className="part-cell-ref">{cell}</small>}</>}
      </td>;
    })}<td><strong>{row.sheet}</strong><small>row {row.row}{row.headerRow ? ` · headers row ${row.headerRow}` : ""}</small></td></tr>)}</tbody></table></div>}
    {expanded && <p className="part-source-note">Values and units are copied from the saved workbook. Blank or unavailable values remain blank; source text is displayed as text. CR Log is source evidence only.</p>}
  </section>;
}
