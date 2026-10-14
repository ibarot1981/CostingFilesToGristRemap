import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, ChevronLeft, ChevronRight } from "lucide-react";
import { api } from "./api";
import { PartBaselinePanel } from "./PartBaselinePanel";
import { MAPPING_PART_CREATE_ATTEMPT_KEY, PartCreationForm, restoredMappingPartCreateAttempt, type ExistingPart, type PartCreateAttempt, type PartCreationOrigin } from "./PartCreationForm";
import "./partMapping.css";

type Part = { id: string; name: string; description?: string; variant?: string; selectable: boolean; duplicateName: boolean; partNumber?: string | null; engineeringRevision?: string | null; legacy?: boolean; outOfScopeCodes?: { id: string; code: string }[] };
type SourceRow = { sheet: string; row: number; fields: Record<string, unknown>; sourceHeaders: Record<string, string>; sourceHeaderCells: Record<string, string>; sourceCells: Record<string, { cell?: string }>; headerRow?: number; availableFields: string[]; lineKey?: string; semanticEvidence?: { fingerprint?: string }; part?: Part | null; reviewed?: boolean; explicitlyUnassigned?: boolean; previousAssignment?: PreviousAssignment | null; semanticComparison?: SemanticComparison };
type SemanticField = { field: string; state: string; value?: string | null; display?: string | null };
type SemanticLine = { fingerprint: string; fields: SemanticField[] };
type SemanticDifference = { type: string; previous: SemanticLine | null; current: SemanticLine | null; reason?: string; fieldDifferences?: { field: string; previous?: SemanticField; current?: SemanticField }[] };
type SemanticComparison = { status: string; previousFingerprint?: string | null; currentFingerprint?: string | null; previousPolicyVersion?: string; sourceHashChanged?: boolean; associationChanged?: boolean; groupingPolicyChanged?: boolean; differences: SemanticDifference[]; requiresReview: boolean };
type PreviousAssignment = { partId?: string | null; partNumber?: string | null; name?: string | null; sourceDescription?: string; requiresReview: boolean; sourceChanged?: boolean; associationChanged?: boolean; previousRowsAgree?: boolean; previousRowsReviewed?: number; currentRows?: number; assignedPartIds?: string[]; hasPriorAssignment?: boolean; contextChanged?: boolean; groupingPolicyChanged?: boolean; evidenceStatus?: string };
type DeletedLineReview = { reviewKey: string; priorReviewKey?: string; lineKey?: string; groupKey: string; sheet: string; description: string; row: number | null; semanticEvidence: SemanticLine; previousAssignment: PreviousAssignment; semanticComparison: SemanticComparison; provenance?: { reviewKey?: string; version?: number; actor?: string; reason?: string; occurredAt?: string; sourceHash?: string }; reviewed?: boolean; resolution?: { actor?: string; reason?: string; version?: number } | null };
type Group = { key: string; mappingPolicyVersion: string; evidenceFingerprint: string; semanticFingerprint?: string; semanticComparison?: SemanticComparison; assignmentSummary?: { partCount: number; mappedLineCount: number; unassignedLineCount: number }; pendingLineKeys?: string[]; deletedLines?: DeletedLineReview[]; sheet: string; labelField: string; sourceDiagnostic?: { status?: string }; description: string; blankDescription: boolean; rows: SourceRow[]; part: Part | null; reviewed: boolean; explicitlyUnassigned?: boolean; previousAssignment?: PreviousAssignment; needsCompatibilityReview?: boolean; version: number };
type RemovedGroup = { key: string; sheet: string; description: string; removed: true; reviewed?: boolean; deletedLines?: DeletedLineReview[]; previousAssignment?: PreviousAssignment; semanticComparison?: SemanticComparison };
type Detail = { sourceHash: string; associationKey: string; associationVersion: number; version: number; schemaAvailable: boolean; schemaMessage?: string | null; legacyHistoryAvailable?: boolean; mappingPolicyVersion: string; semanticPolicyVersion?: string; sourceSheets: Record<string, { present: boolean; status: string; labelField: string; labelHeader: string | null; labelHeaderCell: string | null; ambiguousLabelHeaders: string[]; headerRow: number | null }>; parts: Part[]; groups: Group[]; removedGroups?: RemovedGroup[]; unresolvedGroups: number; unresolvedLines?: number; unresolvedDeletedLines?: number; history: { ReviewKey: string; Version: number; SourceDescription: string; SheetName: string; SourceRow: number; Actor: string; Reason: string; OccurredAt: string; ActionType?: string; NameUsed?: string; PartNumberUsed?: string }[] };
type Attempt = { key: string; payload: Record<string, unknown>; groupKeys: string[]; lineKeys?: string[]; deletedLineReviewKeys?: string[] };
type StaleDraft = { groupKey: string; sheet: string; description: string; rowCount: number; decision: string; hasGroupDecision?: boolean; part: Part | null; sourceHash: string; evidenceFingerprint: string; reason?: string; lineDecisions?: Record<string, string>; lineParts?: Record<string, Part>; lineEvidence?: Record<string, string>; lineReasons?: Record<string, string> };
type Draft = { path: string; sourceHash: string; version: number; associationKey: string; associationVersion: number; decisions: Record<string, string>; draftParts: Record<string, Part>; lineDecisions?: Record<string, string>; lineDraftParts?: Record<string, Part>; selectedLineKeys?: string[]; splitModes?: Record<string, boolean>; lineReasons?: Record<string, string>; deletedLineReasons?: Record<string, string>; groupReasons?: Record<string, string>; expandedGroups?: Record<string, boolean>; expandedSourceDetails?: Record<string, boolean>; draftGroups?: {groupKey: string; sheet: string; description: string; rowCount: number; evidenceFingerprint: string; lineKeys?: string[]; lineEvidence?: Record<string, string>}[]; staleDrafts: StaleDraft[]; reason: string; query: string; sheetFilter: string; reviewFilter: string; scrollTop: number; mappingAttempt: Attempt | null };
type ReturnSelection = { path: string; groupKey: string; partId: string | null; mode?: "select" | "view"; sourceHash?: string; associationKey?: string; associationVersion?: number; evidenceFingerprint?: string; lineKeys?: string[]; selectedLineEvidenceFingerprint?: string };
type SheetFilter = "all" | "mcl" | "toolshop" | "cnc";
type ReviewFilter = "all" | "needs-review" | "saved" | "unsaved" | "unassigned";

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
    { key: "toolshop_remarks", label: "Tool Shop Items Remarks" }, { key: "toolshop_part_name", label: "Legacy Tool Shop Part Name" },
    { key: "job_remarks", label: "Job Remarks" }, { key: "cr_log", label: "CR Log" },
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
function lineEvidenceContext(rows: SourceRow[]) {
  if (rows.some(row => !row.lineKey || !row.semanticEvidence?.fingerprint)) return "";
  return JSON.stringify(rows.map(row => ({lineKey:String(row.lineKey), fingerprint:String(row.semanticEvidence!.fingerprint)}))
    .sort((left, right) => left.lineKey.localeCompare(right.lineKey)));
}
function sheetFamilyClass(sheet: string) {
  return sheet === "5. Material Cut List Price" ? "part-sheet-family-mcl"
    : sheet === "Tool Shop Items" ? "part-sheet-family-toolshop"
      : sheet === "CNC Cut List" ? "part-sheet-family-cnc" : "";
}
function groupTitle(group: Group) {
  return group.blankDescription ? "Blank Part label · individual source row" : group.description;
}
function semanticFieldLabel(field: string) {
  return field.replaceAll("_", " ").replace(/\b\w/g, letter => letter.toUpperCase());
}
function semanticFieldValue(value?: SemanticField) {
  if (!value) return "Not recorded";
  if (value.state === "unavailable") return "Column unavailable in source";
  if (value.state === "blank") return "Blank in source";
  return String(value.display ?? value.value ?? "(blank)");
}
function mappingRefreshNotice(saved: Draft, result: Detail) {
  const associationChanged = saved.associationKey !== result.associationKey || saved.associationVersion !== result.associationVersion;
  const statuses = result.groups.map(group => group.semanticComparison?.status || "");
  const affected = statuses.filter(status => ["changed", "ambiguous_correspondence", "ambiguous_identity", "assignment_conflict", "grouping_policy_changed"].includes(status)).length;
  const groupingChanged = statuses.filter(status => status === "grouping_policy_changed").length;
  const legacy = statuses.filter(status => status === "legacy_evidence_missing").length;
  const preserved = result.groups.filter(group => group.reviewed && group.semanticComparison?.status === "unchanged").length;
  const removed = result.removedGroups?.length || 0;
  const deletedLines = result.unresolvedDeletedLines || 0;
  if (associationChanged) {
    return `The file’s Product/Model association changed. Prior Part assignments remain proposals and need explicit review for this association; ${affected} group(s) also have changed or ambiguous manufacturing evidence. Pending choices were held for explicit review and not applied.`;
  }
  if (saved.sourceHash !== result.sourceHash) {
    return `Workbook hash changed: ${preserved} group(s) retain their saved Part assignment because relevant manufacturing evidence matches; ${affected - groupingChanged} group(s) need review for relevant line or correspondence changes; ${groupingChanged} group(s) need review because grouping policy changed; ${legacy} group(s) need one-time evidence confirmation; ${removed} saved group(s) no longer occur in this workbook; ${deletedLines} previously mapped line(s) require explicit removal review. Rate/price totals, formatting and document metadata do not trigger remapping. Pending choices were held for explicit review and not applied.`;
  }
  return "The saved mapping version changed. Grist assignments were refreshed; unsaved choices were held and not applied.";
}
function SemanticDifferenceList({ comparison }: { comparison?: SemanticComparison }) {
  if (!comparison?.differences?.length) return null;
  return <details className="part-semantic-differences" open={comparison.requiresReview}>
    <summary>Relevant manufacturing differences · {comparison.differences.length}</summary>
    <div>{comparison.differences.map((difference, index) => {
      const line = difference.current || difference.previous;
      const values = new Map((line?.fields || []).map(value => [value.field, value]));
      return <article key={`${difference.type}:${line?.fingerprint || index}`}>
        <strong>{difference.type === "modified" ? "Modified source line" : difference.type === "added" ? "Added source line" : difference.type === "deleted" ? "Deleted source line" : "Ambiguous source-line match"}</strong>
        {difference.reason && <span>{difference.reason}</span>}
        {difference.fieldDifferences?.length ? <dl>{difference.fieldDifferences.map(change => <div key={change.field}>
          <dt>{semanticFieldLabel(change.field)}</dt><dd>{semanticFieldValue(change.previous)} → {semanticFieldValue(change.current)}</dd>
        </div>)}</dl> : line && <dl>{[...values.values()].map(value => <div key={value.field}>
          <dt>{semanticFieldLabel(value.field)}</dt><dd>{semanticFieldValue(value)}</dd>
        </div>)}</dl>}
      </article>;
    })}</div>
  </details>;
}
function DeletedLineReviewCard({ line, reason, disabled, onReasonChange, onReview }: {
  line: DeletedLineReview; reason: string; disabled: boolean;
  onReasonChange: (value: string) => void; onReview: () => void;
}) {
  const prior = line.previousAssignment;
  const part = prior.partNumber ? `${prior.partNumber} · ${prior.name || "Prior Part"}` : prior.name || "No verified prior Part";
  const location = `${line.sheet} · row ${line.row ?? "not recorded"}`;
  return <article className={`part-deleted-line-review ${line.reviewed ? "is-reviewed" : "needs-review"}`}>
    <header><strong>{location} · {line.description || "Blank Part label"}</strong><span>{part}</span></header>
    <p>{line.reviewed
      ? "This previously mapped source line was explicitly reviewed as removed. Its Part requirements and quantities were not changed."
      : line.semanticComparison.status === "deleted"
        ? "This mapped line is absent from the current workbook. Confirm its removal only after checking the prior evidence."
        : "This previously mapped line has no current semantic match. Check whether it was removed before recording that review."}</p>
    <SemanticDifferenceList comparison={line.semanticComparison}/>
    <details><summary>Prior mapping provenance</summary><small>{line.provenance?.reviewKey || line.priorReviewKey || "Legacy evidence"}
      {line.provenance?.version ? ` · review version ${line.provenance.version}` : ""}
      {line.provenance?.actor ? ` · ${line.provenance.actor}` : ""}
      {line.provenance?.sourceHash ? ` · workbook ${line.provenance.sourceHash.slice(0,12)}…` : ""}
      {line.provenance?.reason ? ` · ${line.provenance.reason}` : ""}</small></details>
    {line.reviewed ? <p className="part-deleted-line-resolved" role="status">Removal review recorded{line.resolution?.actor ? ` by ${line.resolution.actor}` : ""}{line.resolution?.reason ? `: ${line.resolution.reason}` : ""}.</p>
      : <div className="part-deleted-line-actions"><label>Reason for confirming this source line was removed<input name={`deleted-line-reason-${line.reviewKey}`} autoComplete="off" spellCheck={false} aria-label={`Reason for confirming removed source line · ${location}`} value={reason} disabled={disabled} onChange={event => onReasonChange(event.target.value)} placeholder="Required for the audit record"/></label>
        <button type="button" className="button primary" disabled={disabled || !reason.trim()} onClick={onReview}>Confirm source line removed</button></div>}
  </article>;
}
function narrowScreen() {
  return typeof window.matchMedia === "function" ? window.matchMedia("(max-width: 1000px)").matches : window.innerWidth <= 1000;
}

export function PartMappingView({ path, active = true, onOpenParts, returnSelection, onReturnSelectionConsumed }: { path: string; active?: boolean; onOpenParts?: (groupKey: string, partId: string | null, mode?: "select" | "view", context?: Partial<ReturnSelection>) => void; returnSelection?: ReturnSelection | null; onReturnSelectionConsumed?: () => void }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [decisions, setDecisions] = useState<Record<string, string>>({});
  const [draftParts, setDraftParts] = useState<Record<string, Part>>({});
  const [lineDecisions, setLineDecisions] = useState<Record<string, string>>({});
  const [lineDraftParts, setLineDraftParts] = useState<Record<string, Part>>({});
  const [selectedLineKeys, setSelectedLineKeys] = useState<string[]>([]);
  const [splitModes, setSplitModes] = useState<Record<string, boolean>>({});
  const [lineReasons, setLineReasons] = useState<Record<string, string>>({});
  const [deletedLineReasons, setDeletedLineReasons] = useState<Record<string, string>>({});
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
    if (active && contextOpen && narrowViewport) {
      const first = contextPanel.current?.querySelector<HTMLElement>("button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])");
      window.requestAnimationFrame(() => (first || contextPanel.current)?.focus());
    }
  }, [active, contextOpen, narrowViewport]);

  useEffect(() => {
    if (!active || !contextOpen || !narrowViewport || createOrigin) return;
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
  }, [active, contextOpen, narrowViewport, createOrigin]);

  useEffect(() => {
    if (!active || !createOrigin) return;
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
  }, [active, createOrigin, createAttemptPending]);

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
      let nextLineDecisions: Record<string, string> = {};
      let nextLineParts: Record<string, Part> = {};
      if (sameEvidence && saved) {
        nextDecisions = saved.decisions || {};
        nextParts = saved.draftParts || {};
        nextLineDecisions = saved.lineDecisions || {};
        nextLineParts = saved.lineDraftParts || {};
        setSelectedLineKeys((saved.selectedLineKeys || []).filter(key => result.groups.some(group => group.rows.some(row => row.lineKey === key))));
        setSplitModes(saved.splitModes || {});
        setLineReasons(saved.lineReasons || {});
        setDeletedLineReasons(saved.deletedLineReasons || {});
        setGroupReasons(saved.groupReasons || {});
        setStaleDrafts(saved.staleDrafts || []);
        setReason(saved.reason || ""); setQuery(saved.query || "");
        setSheetFilter((saved.sheetFilter || "all") as SheetFilter); setReviewFilter((saved.reviewFilter || "all") as ReviewFilter);
        setMappingAttempt(saved.mappingAttempt || null);
        window.requestAnimationFrame(() => { if (tableScroll.current) tableScroll.current.scrollTop = saved.scrollTop || 0; });
      } else {
        const heldDrafts: StaleDraft[] = [];
        if (saved) for (const prior of saved.draftGroups || []) {
          const lineEntries = Object.entries(saved.lineDecisions || {}).filter(([key]) => prior.lineKeys?.includes(key));
          const decision = saved.decisions?.[prior.groupKey];
          if (decision === undefined && !lineEntries.length) continue;
          heldDrafts.push({groupKey:prior.groupKey, sheet:prior.sheet, description:prior.description,
            rowCount:prior.rowCount, decision:decision || "", hasGroupDecision:decision !== undefined, part:saved.draftParts?.[prior.groupKey] || null,
            sourceHash:saved.sourceHash, evidenceFingerprint:prior.evidenceFingerprint || "",
            reason:saved.groupReasons?.[prior.groupKey] || saved.reason || "",
            lineDecisions:Object.fromEntries(lineEntries),
            lineParts:Object.fromEntries(lineEntries.flatMap(([key]) => saved.lineDraftParts?.[key] ? [[key, saved.lineDraftParts[key]]] : [])),
            lineEvidence:Object.fromEntries(lineEntries.flatMap(([key]) => prior.lineEvidence?.[key] ? [[key, prior.lineEvidence[key]]] : [])),
            lineReasons:Object.fromEntries(lineEntries.flatMap(([key]) => saved.lineReasons?.[key] ? [[key, saved.lineReasons[key]]] : []))});
        }
        setStaleDrafts([...(saved?.staleDrafts || []), ...heldDrafts]);
        setDecisions({}); setDraftParts({});
        setLineDecisions({}); setLineDraftParts({}); setSelectedLineKeys([]); setSplitModes({}); setLineReasons({});
        setDeletedLineReasons({});
        setGroupReasons({});
        setReason(saved?.reason || ""); setQuery(saved?.query || "");
        setSheetFilter((saved?.sheetFilter || "all") as SheetFilter); setReviewFilter((saved?.reviewFilter || "all") as ReviewFilter);
        // A timed-out save must keep its exact idempotency key and payload. Retrying
        // lets Grist report the committed result or reject the stale request safely.
        setMappingAttempt(saved?.mappingAttempt || null);
        if (saved) {
          window.requestAnimationFrame(() => { if (tableScroll.current) tableScroll.current.scrollTop = saved.scrollTop || 0; });
          const refreshNotice = mappingRefreshNotice(saved, result);
          setNotice(heldDrafts.length
            ? `${refreshNotice} ${heldDrafts.length} unsaved choice(s) remain listed for explicit review.`
            : refreshNotice);
        }
      }
      if (saved) {
        const currentKeys = new Set(result.groups.map(group => group.key));
        setExpandedGroups(Object.fromEntries(Object.entries(saved.expandedGroups || {}).filter(([key]) => currentKeys.has(key))));
        setExpanded(Object.fromEntries(Object.entries(saved.expandedSourceDetails || {}).filter(([key]) => currentKeys.has(key))));
      }
      if (returnSelection?.path === path && returnSelection.mode === "select" && returnSelection.groupKey) {
        const target = result.groups.find(group => group.key === returnSelection.groupKey);
        const requestedLineKeys = returnSelection.lineKeys || [];
        const selectedRows = (target?.rows || []).filter(row => requestedLineKeys.includes(String(row.lineKey || "")));
        const currentLineKeys = selectedRows.map(row => String(row.lineKey || "")).sort();
        const lineSetMatches = !requestedLineKeys.length || (currentLineKeys.length === requestedLineKeys.length
          && currentLineKeys.join("\0") === [...requestedLineKeys].sort().join("\0")
          && Boolean(returnSelection.selectedLineEvidenceFingerprint)
          && lineEvidenceContext(selectedRows) === returnSelection.selectedLineEvidenceFingerprint);
        const validContext = Boolean(target && returnSelection.sourceHash === result.sourceHash
          && returnSelection.associationKey === result.associationKey && returnSelection.associationVersion === result.associationVersion
          && returnSelection.evidenceFingerprint === target.evidenceFingerprint && lineSetMatches);
        if (validContext && target && returnSelection.partId) {
          try {
            const matches = await api.searchParts(returnSelection.partId, 0, 100, path);
            const selected = (matches.items || []).find((part: Part) => part.id === returnSelection.partId && part.selectable && !part.duplicateName);
            if (selected && revision === generation.current) {
              if (requestedLineKeys.length) {
                nextLineDecisions = { ...nextLineDecisions, ...Object.fromEntries(requestedLineKeys.map(key => [key, selected.id])) };
                nextLineParts = { ...nextLineParts, ...Object.fromEntries(requestedLineKeys.map(key => [key, selected])) };
                setNotice(`${selected.partNumber || selected.name} added to the draft for ${requestedLineKeys.length} selected line(s). Save the mapping to persist it.`);
              } else {
                nextDecisions = { ...nextDecisions, [target.key]: selected.id }; nextParts = { ...nextParts, [target.key]: selected };
                setNotice(`${selected.partNumber || selected.name} added to the draft for ${groupTitle(target)}. Save the mapping to persist it.`);
              }
            } else if (revision === generation.current) setNotice("The selected Part is no longer available for a new mapping. Search again and review this group.");
          } catch (cause) { if (revision === generation.current) setError(String(cause)); }
        } else if (!validContext) {
          setNotice("The source group changed while Parts was open. Review the refreshed group before assigning a Part.");
        }
        onReturnSelectionConsumed?.();
      }
      if (revision !== generation.current) return;
      setDecisions(nextDecisions); setDraftParts(nextParts); setLineDecisions(nextLineDecisions); setLineDraftParts(nextLineParts); setDetail(result);
    } catch (cause) { if (revision === generation.current) { setDetail(null); setError(String(cause)); } }
    finally { if (revision === generation.current) setLoading(false); }
  }

  const loadedPath = useRef("");
  useEffect(() => {
    if (!active) return;
    const revision = ++generation.current;
    const returningToSamePath = loadedPath.current === path && Boolean(detail);
    if (!returningToSamePath) {
      setDetail(null); setDecisions({}); setDraftParts({}); setLineDecisions({}); setLineDraftParts({}); setSelectedLineKeys([]); setSplitModes({}); setLineReasons({}); setDeletedLineReasons({}); setStaleDrafts([]); setReason("");
      setGroupReasons({}); setExpandedGroups({}); setExpanded({}); setNotice(""); setError(""); setBusy(false);
      setMappingAttempt(null); setConfirmBatch(false); inFlight.current = false;
    }
    if (path) { loadedPath.current = path; void load(revision); }
    return () => { ++generation.current; };
  }, [path, active]);

  useEffect(() => {
    if (!detail || !path) return;
    const saved: Draft = { path, sourceHash: detail.sourceHash, version: detail.version, associationKey: detail.associationKey,
      associationVersion: detail.associationVersion, decisions, draftParts, lineDecisions, lineDraftParts, selectedLineKeys, splitModes, lineReasons,
      groupReasons, expandedGroups, expandedSourceDetails: expanded, reason, query, sheetFilter, reviewFilter,
      draftGroups: detail.groups.filter(group => Object.prototype.hasOwnProperty.call(decisions, group.key)
        || group.rows.some(row => row.lineKey && Object.prototype.hasOwnProperty.call(lineDecisions, row.lineKey))).map(group => ({groupKey:group.key,
        sheet:group.sheet, description:group.description, rowCount:group.rows.length, evidenceFingerprint:group.evidenceFingerprint,
        lineKeys:group.rows.flatMap(row => row.lineKey ? [row.lineKey] : []),
        lineEvidence:Object.fromEntries(group.rows.flatMap(row => row.lineKey && row.semanticEvidence?.fingerprint ? [[row.lineKey, row.semanticEvidence.fingerprint]] : []))})),
      deletedLineReasons, staleDrafts, scrollTop: tableScroll.current?.scrollTop || 0, mappingAttempt };
    sessionStorage.setItem(draftKey(path), JSON.stringify(saved));
  }, [path, detail, decisions, draftParts, lineDecisions, lineDraftParts, selectedLineKeys, splitModes, lineReasons,
    deletedLineReasons, groupReasons, expandedGroups, expanded, staleDrafts, reason, query, sheetFilter, reviewFilter, mappingAttempt]);

  function openParts(group: Group | null, partId: string | null, mode: "select" | "view", selectedRows: SourceRow[] = []) {
    if (!detail) return;
    const lineKeys = selectedRows.map(row => String(row.lineKey || "")).filter(Boolean).sort();
    onOpenParts?.(group?.key || "", partId, mode, group ? { sourceHash: detail.sourceHash, associationKey: detail.associationKey,
      associationVersion: detail.associationVersion, evidenceFingerprint: group.evidenceFingerprint,
      ...(lineKeys.length ? { lineKeys, selectedLineEvidenceFingerprint: lineEvidenceContext(selectedRows) } : {}) } : { sourceHash: detail.sourceHash,
      associationKey: detail.associationKey, associationVersion: detail.associationVersion });
  }

  function currentOriginMatches(origin: PartCreationOrigin | null | undefined) {
    const current = acceptedMappingRef.current;
    const currentDetail = current.detail;
    if (!origin || !currentDetail || current.loading || origin.workbookPath !== current.path || origin.sourceHash !== currentDetail.sourceHash
      || origin.associationKey !== currentDetail.associationKey || origin.associationVersion !== currentDetail.associationVersion) return false;
    const group = currentDetail.groups.find(item => item.key === origin.groupKey);
    if (!group || group.evidenceFingerprint !== origin.evidenceFingerprint
      || group.sheet !== origin.sheet || groupTitle(group) !== origin.sourceDescription) return false;
    if (origin.lineKeys?.length) {
      const selectedRows = group.rows.filter(row => origin.lineKeys?.includes(String(row.lineKey || "")));
      const selected = selectedRows.map(row => String(row.lineKey || "")).sort();
      return selected.length === origin.lineKeys.length && selected.join("\0") === [...origin.lineKeys].sort().join("\0")
        && Boolean(origin.selectedLineEvidenceFingerprint)
        && lineEvidenceContext(selectedRows) === origin.selectedLineEvidenceFingerprint;
    }
    return true;
  }

  function closeCreateDialog() {
    if (createAttemptPending) return;
    setCreateOrigin(null); setCreateAttemptPending(false);
    if (active) window.requestAnimationFrame(() => (createOpener.current || contextToggle.current)?.focus());
  }

  function openCreateDialog(group: Group, button: HTMLButtonElement, selectedRows: SourceRow[] = []) {
    if (!detail || locked || !detail.schemaAvailable) return;
    createOpener.current = button;
    setCreateAttemptPending(false);
    const lineKeys = selectedRows.map(row => String(row.lineKey || "")).filter(Boolean).sort();
    setCreateOrigin({ workbookPath: path, groupKey: group.key, sheet: group.sheet, sourceDescription: groupTitle(group),
      sourceHash: detail.sourceHash, associationKey: detail.associationKey, associationVersion: detail.associationVersion,
      evidenceFingerprint: group.evidenceFingerprint, ...(lineKeys.length ? { lineKeys, selectedLineEvidenceFingerprint: lineEvidenceContext(selectedRows) } : {}) });
  }

  function finishContextualCreation(part: ExistingPart, attempt?: PartCreateAttempt) {
    const origin = attempt?.origin || createOrigin;
    const current = acceptedMappingRef.current;
    if (origin && currentOriginMatches(origin)) {
      const group = current.detail?.groups.find(item => item.key === origin.groupKey);
      if (group) {
        const selectablePart = { ...part, selectable: true, duplicateName: false, legacy: false } as Part;
        if (origin.lineKeys?.length) {
          assignPartToLines(group, selectablePart, origin.lineKeys);
          setNotice(`${selectablePart.partNumber || selectablePart.name} was created in Grist and added to ${origin.lineKeys.length} selected lines as an unsaved mapping. Save the selected lines to persist it.`);
        } else {
          selectPart(group, selectablePart);
          setNotice(`${selectablePart.partNumber || selectablePart.name} was created in Grist and added as an unsaved whole-group mapping. Save the group to persist it.`);
        }
      }
    } else {
      setNotice(`${part.partNumber || part.name} exists in Grist. The originating workbook or source group changed, so it was not assigned. Review current evidence before selecting it.`);
    }
    setCreateAttemptPending(false); setCreateOrigin(null);
    if (active) window.requestAnimationFrame(() => (createOpener.current || contextToggle.current)?.focus());
  }

  function useExistingInContext(part: ExistingPart) {
    const origin = acceptedMappingRef.current.createOrigin;
    if (!origin || !currentOriginMatches(origin)) {
      setNotice(`${part.partNumber || part.name} is selectable, but the originating source context changed. Review the current group before assigning it.`);
    } else {
      const group = acceptedMappingRef.current.detail?.groups.find(item => item.key === origin.groupKey);
      if (group) {
        const selectablePart = { ...part, selectable: true, duplicateName: false, legacy: false } as Part;
        if (origin.lineKeys?.length) {
          assignPartToLines(group, selectablePart, origin.lineKeys);
          setNotice(`${part.partNumber || part.name} was added to ${origin.lineKeys.length} selected lines as an unsaved mapping. Save the selected lines to persist it.`);
        } else {
          selectPart(group, selectablePart);
          setNotice(`${part.partNumber || part.name} was added as an unsaved whole-group mapping. Save the group to persist the assignment.`);
        }
      }
    }
    closeCreateDialog();
  }

  function selectPart(group: Group, part: Part | null) {
    setError(""); setNotice(""); setActivePicker(null);
    setStaleDrafts(old => old.filter(draft => draft.groupKey !== group.key));
    const groupLineKeys = new Set(group.rows.flatMap(row => row.lineKey ? [row.lineKey] : []));
    const replacingLineDraft = group.rows.some(row => row.lineKey && Object.prototype.hasOwnProperty.call(lineDecisions, row.lineKey));
    setLineDecisions(old => Object.fromEntries(Object.entries(old).filter(([key]) => !groupLineKeys.has(key))));
    setLineDraftParts(old => Object.fromEntries(Object.entries(old).filter(([key]) => !groupLineKeys.has(key))));
    setLineReasons(old => Object.fromEntries(Object.entries(old).filter(([key]) => !groupLineKeys.has(key))));
    setSelectedLineKeys(old => old.filter(key => !groupLineKeys.has(key)));
    if (replacingLineDraft) setGroupReasons(old => { const next = {...old}; delete next[group.key]; return next; });
    const currentPartIds = new Set(group.rows.filter(row => row.reviewed && row.part).map(row => row.part!.id));
    const currentAllSame = group.rows.length > 0 && group.rows.every(row => row.reviewed && row.part?.id === part?.id);
    if (part && currentAllSame && currentPartIds.size === 1 && group.part?.id === part.id) {
      setDecisions(old => { const next = { ...old }; delete next[group.key]; return next; });
      setDraftParts(old => { const next = { ...old }; delete next[group.key]; return next; });
      return;
    }
    if (!part) {
      const alreadyUnassigned = group.rows.length > 0 && group.rows.every(row => row.reviewed && !row.part);
      if (alreadyUnassigned) {
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

  function assignPartToLines(group: Group, part: Part | null, lineKeys: string[] = selectedLineKeys) {
    const chosen = new Set(lineKeys);
    const targets = group.rows.filter(row => row.lineKey && chosen.has(row.lineKey));
    if (!targets.length || targets.length !== chosen.size) {
      setNotice("The selected source lines changed. Review the current rows and select them again before assigning.");
      return;
    }
    setError(""); setActivePicker(null);
    setDecisions(old => { const next = {...old}; delete next[group.key]; return next; });
    setDraftParts(old => { const next = {...old}; delete next[group.key]; return next; });
    setGroupReasons(old => { const next = {...old}; delete next[group.key]; return next; });
    const ids = targets.map(row => row.lineKey!);
    setLineDecisions(old => ({ ...old, ...Object.fromEntries(ids.map(key => [key, part?.id || ""])) }));
    setLineDraftParts(old => {
      const next = { ...old };
      for (const key of ids) { if (part) next[key] = part; else delete next[key]; }
      return next;
    });
    setStaleDrafts(old => old.filter(draft => draft.groupKey !== group.key));
  }

  function reasonRequired(group: Group, nextPartId: string | null | undefined) {
    return group.rows.some(row => lineReasonRequired(row, nextPartId));
  }

  function lineReasonRequired(row: SourceRow, nextPartId: string | null | undefined) {
    const previousIds = new Set<string>([
      ...(row.part?.id ? [row.part.id] : []),
      ...(row.previousAssignment?.assignedPartIds || []),
      ...(row.previousAssignment?.partId ? [row.previousAssignment.partId] : []),
    ]);
    if (!previousIds.size) return false;
    if (!nextPartId) return true;
    return [...previousIds].some(id => id !== nextPartId);
  }

  function pendingReasonRequired(group: Group) {
    if (Object.prototype.hasOwnProperty.call(decisions, group.key)) return reasonRequired(group, decisions[group.key] || null);
    return group.rows.some(row => row.lineKey && Object.prototype.hasOwnProperty.call(lineDecisions, row.lineKey)
      && lineReasonRequired(row, lineDecisions[row.lineKey] || null));
  }

  async function saveGroups(groupKeys: string[]) {
    if (!detail || !groupKeys.length || inFlight.current) return;
    let attempt = mappingAttempt;
    if (attempt) {
      if (attempt.deletedLineReviewKeys?.length || attempt.groupKeys.join("\0") !== groupKeys.join("\0")) return;
    } else {
      const payloadDecisions = Object.fromEntries(groupKeys.flatMap(key => Object.prototype.hasOwnProperty.call(decisions, key) ? [[key, decisions[key]]] : []));
      const payloadLineDecisions = Object.fromEntries(groupKeys.flatMap(key => {
        if (Object.prototype.hasOwnProperty.call(decisions, key)) return [];
        const group = detail.groups.find(item => item.key === key);
        return (group?.rows || []).flatMap(row => row.lineKey && Object.prototype.hasOwnProperty.call(lineDecisions, row.lineKey)
          ? [[row.lineKey, lineDecisions[row.lineKey]]] : []);
      }));
      if (!Object.keys(payloadDecisions).length && !Object.keys(payloadLineDecisions).length) return;
      const affectedGroups = groupKeys.filter(key => {
        const group = detail.groups.find(item => item.key === key);
        return Boolean(group && (Object.prototype.hasOwnProperty.call(payloadDecisions, key)
          ? reasonRequired(group, payloadDecisions[key] || null)
          : group.rows.some(row => row.lineKey && Object.prototype.hasOwnProperty.call(payloadLineDecisions, row.lineKey)
            && lineReasonRequired(row, payloadLineDecisions[row.lineKey] || null))));
      });
      const payloadReasons = Object.fromEntries(affectedGroups.map(key => [key, groupReasons[key] || ""]));
      const requiredLineReasons = Object.keys(payloadLineDecisions).filter(lineKey => {
        const group = detail.groups.find(item => item.rows.some(row => row.lineKey === lineKey));
        const row = group?.rows.find(item => item.lineKey === lineKey);
        return Boolean(row && lineReasonRequired(row, payloadLineDecisions[lineKey] || null)
          && !(groupReasons[group!.key] || "").trim());
      });
      const payloadLineReasons = Object.fromEntries(requiredLineReasons.map(key => [key, lineReasons[key] || ""]));
      const missingGroupReason = Object.keys(payloadDecisions).some(key => affectedGroups.includes(key)
        && !(groupReasons[key] || "").trim());
      const missingLineReason = requiredLineReasons.some(key => !(payloadLineReasons[key] || "").trim());
      if (missingGroupReason || missingLineReason) return;
      attempt = { key: crypto.randomUUID(), groupKeys: [...groupKeys], lineKeys: Object.keys(payloadLineDecisions), payload: { path, decisions: payloadDecisions,
        lineDecisions: payloadLineDecisions, reasons: payloadReasons, lineReasons: payloadLineReasons,
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
      const previousLineDecisions = lineDecisions;
      const previousLineParts = lineDraftParts;
      const retained: Record<string, string> = {};
      const retainedParts: Record<string, Part> = {};
      const retainedLines: Record<string, string> = {};
      const retainedLineParts: Record<string, Part> = {};
      const retainedReasons: Record<string, string> = {};
      const retainedLineReasons: Record<string, string> = {};
      for (const [key, value] of Object.entries(previousDecisions)) {
        if (attempt.groupKeys.includes(key)) continue;
        retained[key] = value;
        if (previousParts[key]) retainedParts[key] = previousParts[key];
        if (groupReasons[key]) retainedReasons[key] = groupReasons[key];
      }
      for (const [key, value] of Object.entries(previousLineDecisions)) {
        const ownerGroup = previousDetail.groups.find(group => group.rows.some(row => row.lineKey === key));
        if (!ownerGroup || attempt.groupKeys.includes(ownerGroup.key)) continue;
        retainedLines[key] = value;
        if (previousLineParts[key]) retainedLineParts[key] = previousLineParts[key];
        if (lineReasons[key]) retainedLineReasons[key] = lineReasons[key];
      }
      const assignments = result.lineAssignments || {};
      const confirmedRows = result.confirmedRows || [];
      const savedKeys = new Set(attempt.groupKeys);
      const partById = new Map<string, Part>();
      for (const part of [...previousDetail.parts, ...Object.values(previousParts), ...Object.values(previousLineParts)]) partById.set(part.id, part);
      const nextGroups = previousDetail.groups.map(group => {
        if (!savedKeys.has(group.key)) return group;
        const rows = group.rows.map(row => {
          const assignment = row.lineKey ? assignments[row.lineKey] : null;
          if (!assignment) return row;
          const partId = assignment.partId || null;
          const part = partId ? (partById.get(partId) || previousParts[group.key] || previousLineParts[row.lineKey || ""] || null) : null;
          return { ...row, part, reviewed: true, explicitlyUnassigned: !partId,
            previousAssignment: undefined, semanticComparison: { status: "unchanged", differences: [], requiresReview: false } };
        });
        const mapped = rows.filter(row => row.reviewed && row.part);
        const uniqueParts = new Set(mapped.map(row => row.part!.id));
        const reviewed = rows.length > 0 && rows.every(row => row.reviewed);
        return { ...group, rows, part: reviewed && mapped.length === rows.length && uniqueParts.size === 1 ? mapped[0].part || null : null, reviewed,
          assignmentSummary: { partCount: uniqueParts.size, mappedLineCount: mapped.length, unassignedLineCount: rows.length - mapped.length },
          pendingLineKeys: [], explicitlyUnassigned: rows.length > 0 && rows.every(row => row.explicitlyUnassigned),
          needsCompatibilityReview: false, previousAssignment: undefined, version: result.version };
      });
      const fresh: Detail = { ...previousDetail, sourceHash: result.sourceHash || previousDetail.sourceHash,
        associationKey: result.associationKey || previousDetail.associationKey,
        associationVersion: result.associationVersion || previousDetail.associationVersion,
        version: result.version, groups: nextGroups,
        unresolvedGroups: nextGroups.filter(group => !group.reviewed).length,
        unresolvedLines: nextGroups.reduce((sum, group) => sum + group.rows.filter(row => !row.reviewed).length, 0),
        history: [...previousDetail.history, ...confirmedRows] };
      setDetail(fresh); setDecisions(retained); setDraftParts(retainedParts); setLineDecisions(retainedLines); setLineDraftParts(retainedLineParts);
      setLineReasons(retainedLineReasons); setSelectedLineKeys(old => old.filter(key => !Object.prototype.hasOwnProperty.call(assignments, key)));
      setGroupReasons(retainedReasons); setMappingAttempt(null);
      setStaleDrafts(old => old.filter(draft => !attempt.groupKeys.includes(draft.groupKey)));
      setReason(Object.keys(retained).length ? reason : "");
      setNotice(`Grist confirmed ${result.savedRows} source-line assignment${result.savedRows === 1 ? "" : "s"} in review version ${result.version}. Untouched line drafts remain pending.`);
      sessionStorage.setItem(draftKey(path), JSON.stringify({ path, sourceHash: fresh.sourceHash, version: fresh.version,
        associationKey: fresh.associationKey, associationVersion: fresh.associationVersion, decisions: retained, draftParts: retainedParts,
        lineDecisions: retainedLines, lineDraftParts: retainedLineParts, selectedLineKeys: selectedLineKeys.filter(key => !Object.prototype.hasOwnProperty.call(assignments, key)),
        splitModes, lineReasons: retainedLineReasons,
        groupReasons: retainedReasons, expandedGroups, expandedSourceDetails: expanded,
        draftGroups: fresh.groups.filter(group => Object.prototype.hasOwnProperty.call(retained, group.key)
          || group.rows.some(row => row.lineKey && Object.prototype.hasOwnProperty.call(retainedLines, row.lineKey))).map(group => ({groupKey:group.key,
          sheet:group.sheet, description:group.description, rowCount:group.rows.length, evidenceFingerprint:group.evidenceFingerprint,
          lineKeys:group.rows.flatMap(row => row.lineKey ? [row.lineKey] : []),
          lineEvidence:Object.fromEntries(group.rows.flatMap(row => row.lineKey && row.semanticEvidence?.fingerprint ? [[row.lineKey, row.semanticEvidence.fingerprint]] : []))})),
        staleDrafts: staleDrafts.filter(draft => !attempt.groupKeys.includes(draft.groupKey)), reason: Object.keys(retained).length ? reason : "", query, sheetFilter, reviewFilter,
        scrollTop: tableScroll.current?.scrollTop || 0, mappingAttempt: null } satisfies Draft));
    } catch (cause) { if (requestRevision === generation.current) setError(String(cause)); }
    finally { inFlight.current = false; if (requestRevision === generation.current) setBusy(false); }
  }

  async function reviewDeletedLine(reviewKey: string) {
    if (!detail || busy || loading || inFlight.current) return;
    let attempt = mappingAttempt;
    if (attempt) {
      if (!attempt.deletedLineReviewKeys?.includes(reviewKey)) return;
    } else {
      const deleted = [...detail.groups.flatMap(group => group.deletedLines || []), ...(detail.removedGroups || []).flatMap(group => group.deletedLines || [])]
        .find(line => line.reviewKey === reviewKey && !line.reviewed);
      const lineReason = (deletedLineReasons[reviewKey] || "").trim();
      if (!deleted || !lineReason) return;
      attempt = { key: crypto.randomUUID(), groupKeys: [], lineKeys: [], deletedLineReviewKeys: [reviewKey], payload: {
        path, expectedHash: detail.sourceHash, expectedVersion: detail.version, expectedAssociationKey: detail.associationKey,
        expectedAssociationVersion: detail.associationVersion, decisions: {}, lineDecisions: {}, deletedLineReviews: { [reviewKey]: lineReason },
      } };
    }
    const requestRevision = generation.current;
    inFlight.current = true; setMappingAttempt(attempt); setBusy(true); setError("");
    try {
      const result = await api.savePartMappings(attempt.payload, attempt.key);
      if (requestRevision !== generation.current) return;
      const fresh = await api.partMappings(path) as Detail;
      if (requestRevision !== generation.current) return;
      setDetail(fresh); setMappingAttempt(null);
      setDeletedLineReasons(old => { const next = { ...old }; delete next[reviewKey]; return next; });
      setNotice(`Grist recorded the explicit source-line removal review in mapping version ${result.version}. The prior Part and its requirements remain unchanged.`);
    } catch (cause) { if (requestRevision === generation.current) setError(String(cause)); }
    finally { inFlight.current = false; if (requestRevision === generation.current) setBusy(false); }
  }

  async function reviewCurrentEvidence() {
    if (!detail || busy || loading || !mappingAttempt) return;
    const requestRevision = generation.current;
    const oldDetail = detail;
    const oldDecisions = decisions;
    const oldParts = draftParts;
    const oldLineDecisions = lineDecisions;
    const oldLineParts = lineDraftParts;
    const oldLineReasons = lineReasons;
    const oldDeletedLineReviewKeys = mappingAttempt.deletedLineReviewKeys || [];
    setLoading(true); setError("");
    try {
      const fresh = await api.partMappings(path) as Detail;
      if (requestRevision !== generation.current) return;
      const sameSource = fresh.sourceHash === oldDetail.sourceHash && fresh.associationKey === oldDetail.associationKey
        && fresh.associationVersion === oldDetail.associationVersion;
      const nextDecisions: Record<string, string> = {};
      const nextParts: Record<string, Part> = {};
      const nextLineDecisions: Record<string, string> = {};
      const nextLineParts: Record<string, Part> = {};
      const nextLineReasons: Record<string, string> = {};
      const movedByGroup = new Map<string, StaleDraft>();
      const hold = (before: Group, patch: Partial<StaleDraft>) => {
        const current = movedByGroup.get(before.key) || {groupKey:before.key, sheet:before.sheet, description:before.description,
          rowCount:before.rows.length, decision:"", hasGroupDecision:false, part:null, sourceHash:oldDetail.sourceHash,
          evidenceFingerprint:before.evidenceFingerprint};
        movedByGroup.set(before.key, { ...current, ...patch });
      };
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
          hold(before, {decision, hasGroupDecision:true, part:oldParts[key] || before.part, reason:groupReasons[key] || reason});
        }
      }
      for (const [oldKey, decision] of Object.entries(oldLineDecisions)) {
        const before = oldDetail.groups.find(group => group.rows.some(row => row.lineKey === oldKey));
        const oldRow = before?.rows.find(row => row.lineKey === oldKey);
        const after = before && fresh.groups.find(group => group.key === before.key);
        const currentRow = after?.rows.find(row => row.lineKey === oldKey);
        const sameLineEvidence = Boolean(sameSource && oldRow && currentRow
          && oldRow.semanticEvidence?.fingerprint
          && oldRow.semanticEvidence.fingerprint === currentRow.semanticEvidence?.fingerprint
          && oldRow.reviewed === currentRow.reviewed && oldRow.part?.id === currentRow.part?.id
          && oldRow.explicitlyUnassigned === currentRow.explicitlyUnassigned);
        if (sameLineEvidence) {
          nextLineDecisions[oldKey] = decision;
          if (oldLineParts[oldKey]) nextLineParts[oldKey] = oldLineParts[oldKey];
          if (oldLineReasons[oldKey]) nextLineReasons[oldKey] = oldLineReasons[oldKey];
        } else if (before && oldRow) {
          const stale = movedByGroup.get(before.key) || {groupKey:before.key, sheet:before.sheet, description:before.description,
            rowCount:before.rows.length, decision:"", hasGroupDecision:false, part:null, sourceHash:oldDetail.sourceHash,
            evidenceFingerprint:before.evidenceFingerprint};
          movedByGroup.set(before.key, { ...stale,
            lineDecisions:{...(stale.lineDecisions || {}), [oldKey]:decision},
            lineParts:oldLineParts[oldKey] ? {...(stale.lineParts || {}), [oldKey]:oldLineParts[oldKey]} : stale.lineParts,
            lineEvidence:oldRow.semanticEvidence?.fingerprint ? {...(stale.lineEvidence || {}), [oldKey]:oldRow.semanticEvidence.fingerprint} : stale.lineEvidence,
            lineReasons:oldLineReasons[oldKey] ? {...(stale.lineReasons || {}), [oldKey]:oldLineReasons[oldKey]} : stale.lineReasons,
            reason:stale.reason || reason});
        }
      }
      const movedToReview = [...movedByGroup.values()];
      const freshDeletedKeys = new Set([...fresh.groups.flatMap(group => group.deletedLines || []),
        ...(fresh.removedGroups || []).flatMap(group => group.deletedLines || [])].filter(line => !line.reviewed).map(line => line.reviewKey));
      setDetail(fresh); setDecisions(nextDecisions); setDraftParts(nextParts);
      setLineDecisions(nextLineDecisions); setLineDraftParts(nextLineParts); setLineReasons(nextLineReasons);
      setDeletedLineReasons(old => Object.fromEntries(Object.entries(old).filter(([key]) => freshDeletedKeys.has(key)
        || !oldDeletedLineReviewKeys.includes(key))));
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
    if (stale.hasGroupDecision ?? Boolean(stale.decision)) {
      const keys = new Set(group.rows.flatMap(row => row.lineKey ? [row.lineKey] : []));
      setLineDecisions(old => Object.fromEntries(Object.entries(old).filter(([key]) => !keys.has(key))));
      setLineDraftParts(old => Object.fromEntries(Object.entries(old).filter(([key]) => !keys.has(key))));
      setLineReasons(old => Object.fromEntries(Object.entries(old).filter(([key]) => !keys.has(key))));
      setDecisions(old => ({ ...old, [group.key]: stale.decision }));
      setDraftParts(old => stale.decision && stale.part ? ({ ...old, [group.key]: stale.part! }) : old);
      if (stale.reason) setGroupReasons(old => ({ ...old, [group.key]: old[group.key] || stale.reason! }));
      setStaleDrafts(old => old.filter(item => item !== stale));
      setNotice(`Previous whole-group choice reopened for ${groupTitle(group)}. Confirm the displayed current source rows and save with a review reason.`);
      return;
    }

    const reapplied: Record<string, string> = {};
    const reappliedParts: Record<string, Part> = {};
    const reappliedReasons: Record<string, string> = {};
    const unmatched: Record<string, string> = {};
    const unmatchedParts: Record<string, Part> = {};
    const unmatchedEvidence: Record<string, string> = {};
    const unmatchedReasons: Record<string, string> = {};
    for (const [oldKey, decision] of Object.entries(stale.lineDecisions || {})) {
      const fingerprint = stale.lineEvidence?.[oldKey];
      const matches = fingerprint ? group.rows.filter(row => row.semanticEvidence?.fingerprint === fingerprint && row.lineKey) : [];
      if (matches.length === 1) {
        const currentKey = String(matches[0].lineKey);
        reapplied[currentKey] = decision;
        if (stale.lineParts?.[oldKey]) reappliedParts[currentKey] = stale.lineParts[oldKey];
        if (stale.lineReasons?.[oldKey]) reappliedReasons[currentKey] = stale.lineReasons[oldKey];
      } else {
        unmatched[oldKey] = decision;
        if (stale.lineParts?.[oldKey]) unmatchedParts[oldKey] = stale.lineParts[oldKey];
        if (fingerprint) unmatchedEvidence[oldKey] = fingerprint;
        if (stale.lineReasons?.[oldKey]) unmatchedReasons[oldKey] = stale.lineReasons[oldKey];
      }
    }
    const currentKeys = Object.keys(reapplied);
    if (currentKeys.length) {
      setLineDecisions(old => ({...old, ...reapplied}));
      setLineDraftParts(old => ({...old, ...reappliedParts}));
      setLineReasons(old => ({...old, ...reappliedReasons}));
      setSelectedLineKeys(old => [...new Set([...old, ...currentKeys])]);
      setSplitModes(old => ({...old, [group.key]:true}));
      setExpandedGroups(old => ({...old, [group.key]:true}));
      setExpanded(old => ({...old, [group.key]:true}));
    }
    if (Object.keys(unmatched).length) {
      setStaleDrafts(old => old.map(item => item === stale ? {...item, lineDecisions:unmatched, lineParts:unmatchedParts,
        lineEvidence:unmatchedEvidence, lineReasons:unmatchedReasons, rowCount:Object.keys(unmatched).length} : item));
      setNotice(`${currentKeys.length} prior line choice(s) were matched to unique current semantic rows. ${Object.keys(unmatched).length} line choice(s) remain held because their evidence is missing, changed, deleted or ambiguous; select current rows manually.`);
    } else {
      setStaleDrafts(old => old.filter(item => item !== stale));
      setNotice(`${currentKeys.length} prior line choice(s) were matched to unique current semantic rows for ${groupTitle(group)}. Review each selected row and save the mapping explicitly.`);
    }
  }

  const [activePicker, setActivePicker] = useState<string | null>(null);
  useEffect(() => { if (!active) setActivePicker(null); }, [active]);
  if (!path) return <main className="part-mapping"><h1>Part Mapping</h1><p>Select a costing workbook in Files to review its Parts.</p></main>;

  const allGroups = detail?.groups || [];
  const groupPending = (group: Group) => Object.prototype.hasOwnProperty.call(decisions, group.key)
    || group.rows.some(row => row.lineKey && Object.prototype.hasOwnProperty.call(lineDecisions, row.lineKey));
  const effectivePartForLine = (group: Group, row: SourceRow) => {
    if (Object.prototype.hasOwnProperty.call(decisions, group.key)) return decisions[group.key] ? draftParts[group.key] || null : null;
    if (row.lineKey && Object.prototype.hasOwnProperty.call(lineDecisions, row.lineKey)) return lineDecisions[row.lineKey] ? lineDraftParts[row.lineKey] || null : null;
    return row.reviewed ? row.part || null : null;
  };
  const pendingGroups = allGroups.filter(groupPending);
  const baselinePartMap = new Map<string, Part>();
  for (const group of allGroups) for (const row of group.rows) if (row.reviewed && row.part && !row.part.legacy) baselinePartMap.set(row.part.id, row.part);
  const baselineParts = [...baselinePartMap.values()];
  const visibleGroups = allGroups.filter(group => {
    if (sheetFilter !== "all" && familyFor(group.sheet) !== sheetFilter) return false;
    const haystack = `${group.description} ${group.sheet} ${group.rows.map(row => `${row.row} ${Object.values(row.fields || {}).join(" ")}`).join(" ")}`.toLocaleLowerCase();
    if (query.trim() && !haystack.includes(query.trim().toLocaleLowerCase())) return false;
    if (reviewFilter === "needs-review" && group.reviewed) return false;
    if (reviewFilter === "saved" && !group.reviewed) return false;
    if (reviewFilter === "unsaved" && !groupPending(group)) return false;
    if (reviewFilter === "unassigned" && !group.rows.some(row => !effectivePartForLine(group, row))) return false;
    return true;
  });
  const locked = busy || loading || Boolean(mappingAttempt);
  const canRefreshConflict = Boolean(mappingAttempt && /PART_REVIEW_STALE|PART_ASSOCIATION_STALE|PART_GROUP_UNKNOWN|PART_LINE_UNKNOWN|PART_DELETED_LINE_UNKNOWN|PART_DELETED_LINE_ALREADY_REVIEWED|PART_SEMANTIC_EVIDENCE_UNAVAILABLE|PART_SOURCE_LABEL_UNAVAILABLE/.test(error));
  const totalRows = allGroups.reduce((total, group) => total + group.rows.length, 0);
  const selectedPartFor = (group: Group) => groupPending(group) ? (decisions[group.key] ? draftParts[group.key] : null) : group.part;
  const selectedLinesFor = (group: Group) => group.rows.filter(row => row.lineKey && selectedLineKeys.includes(row.lineKey));
  const selectedLinesPartFor = (group: Group) => {
    const selectedRows = selectedLinesFor(group);
    const parts = selectedRows.map(row => row.lineKey && Object.prototype.hasOwnProperty.call(lineDecisions, row.lineKey)
      ? (lineDecisions[row.lineKey] ? lineDraftParts[row.lineKey] : null) : row.part || null);
    const ids = new Set(parts.map(part => part?.id || ""));
    return ids.size === 1 && parts[0] ? parts[0] : null;
  };
  const sourceStatus = (group: Group) => {
    if (mappingAttempt?.groupKeys.includes(group.key)) return error ? "Failed" : "Saving";
    if (groupPending(group)) return "Unsaved";
    if (group.reviewed) return "Saved";
    return "Needs review";
  };
  const requiredReasonGroups = pendingGroups.filter(pendingReasonRequired);
  const pendingLineCount = pendingGroups.reduce((sum, group) => sum + (Object.prototype.hasOwnProperty.call(decisions, group.key)
    ? group.rows.length : group.rows.filter(row => row.lineKey && Object.prototype.hasOwnProperty.call(lineDecisions, row.lineKey)).length), 0);
  const displayedKeys = visibleGroups.map(group => group.key);
  const contextualSelection = allGroups.map(group => ({group, rows:group.rows.filter(row => row.lineKey && selectedLineKeys.includes(row.lineKey))}))
    .find(item => item.rows.length > 0);
  const canonicalSummary = (group: Group) => {
    if (Object.prototype.hasOwnProperty.call(decisions, group.key)) {
      const part = decisions[group.key] ? draftParts[group.key] || null : null;
      const mappedLineCount = part ? group.rows.length : 0;
      const unassignedLineCount = group.rows.length - mappedLineCount;
      const mappedLabel = `${mappedLineCount} mapped line${mappedLineCount === 1 ? "" : "s"}`;
      return part ? `Unsaved · ${formatPart(part)} · ${mappedLabel} · ${unassignedLineCount} unassigned`
        : `Clear pending · ${mappedLabel} · ${unassignedLineCount} unassigned`;
    }
    const lineParts = group.rows.map(row => effectivePartForLine(group, row)).filter((part): part is Part => Boolean(part));
    const partCount = new Set(lineParts.map(part => part.id)).size;
    const mappedLineCount = lineParts.length;
    const unassignedLineCount = group.rows.length - mappedLineCount;
    const pendingLineCount = group.rows.filter(row => row.lineKey && Object.prototype.hasOwnProperty.call(lineDecisions, row.lineKey)).length;
    const mixedOrPartial = partCount > 1 || unassignedLineCount > 0 || (group.assignmentSummary?.partCount || 0) > 1;
    if (mixedOrPartial) {
      const partLabel = `${partCount} Part${partCount === 1 ? "" : "s"}`;
      const lineLabel = `${mappedLineCount} mapped line${mappedLineCount === 1 ? "" : "s"}`;
      return `${pendingLineCount ? `${pendingLineCount} unsaved · ` : ""}${partLabel} · ${lineLabel} · ${unassignedLineCount} unassigned`;
    }
    if (pendingLineCount) return `Unsaved · ${formatPart(lineParts[0] || null)}`;
    return group.part ? formatPart(group.part) : `${group.assignmentSummary?.mappedLineCount || 0} mapped lines · ${group.assignmentSummary?.unassignedLineCount ?? group.rows.length} unassigned`;
  };
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
          {!detail.schemaAvailable && <p role="alert">{detail.schemaMessage || "Part review storage is unavailable."}</p>}
          {detail.legacyHistoryAvailable === false && <p role="alert">Legacy Grist Part assignment history is unavailable. Existing history must be reconciled before those assignments can be relied on.</p>}
          {!detail.associationKey && <p role="alert">Save a file association before saving Part assignments.</p>}
          {Object.entries(detail.sourceSheets || {}).map(([sheet, diagnostic]) => <p key={sheet}><strong>{sheet}:</strong> {diagnostic.status === "ok" ? `${diagnostic.labelField === "part_category" ? "Part Category" : "Machine Piece Description"} from ${diagnostic.labelHeader} at ${diagnostic.labelHeaderCell}` : diagnostic.status === "sheet_missing" ? "Sheet is not present in this workbook." : diagnostic.status === "header_missing" ? "A source header row was not found." : diagnostic.status === "label_column_ambiguous" ? `Multiple possible Part label columns were found: ${diagnostic.ambiguousLabelHeaders.join(", ")}.` : "The configured Part label column is missing; blank rows cannot be mapped by substitution."}</p>)}
        </div></details>
        <PartBaselinePanel path={path} parts={baselineParts} groups={allGroups}
          onUseDifferentPart={(groupKey, part, lineKey, lineFingerprint) => {
            const target = allGroups.find(group => group.key === groupKey);
            if (!target || !lineKey || !lineFingerprint) { setNotice("The comparison no longer identifies a current source line. Choose the affected line manually before saving a mapping."); return; }
            const matches = target.rows.filter(row => row.lineKey && row.semanticEvidence?.fingerprint === lineFingerprint);
            if (matches.length !== 1) { setNotice("The compared line has changed or is ambiguous in the current workbook. Review and select the current line manually."); return; }
            const currentLineKey = String(matches[0].lineKey);
            setSplitModes(old => ({...old, [target.key]:true}));
            setSelectedLineKeys(old => [...new Set([...old, currentLineKey])]);
            assignPartToLines(target, part, [currentLineKey]);
          }}/>
        {detail.history.length > 0 && <details className="part-history"><summary>Assignment and removal review history · {detail.history.length} events</summary>{detail.history.map(row => <p key={row.ReviewKey}><strong>v{row.Version} · {row.ActionType === "source_deletion_review" ? "Source line removal review" : "Part assignment"} · {row.SheetName} row {row.SourceRow}</strong> · {row.SourceDescription || "Blank description"} · {row.PartNumberUsed || "No Part assigned"}{row.NameUsed ? ` · ${row.NameUsed}` : ""}<br/>{row.Actor} · {row.OccurredAt}<br/>{row.Reason}</p>)}</details>}
      </div>
    </aside>}
    {detail && narrowViewport && <button type="button" ref={contextToggle} className="part-context-mobile-toggle" aria-controls="part-mapping-context" aria-expanded={contextOpen} onClick={() => setContextOpen(open => !open)}>{contextOpen ? "Hide supporting information" : "Show supporting information"}</button>}
    <section className="part-mapping">
      <div className="part-heading part-heading-compact"><div><span className="eyebrow">Safari Manufacturing · Source assignments</span><h1>Part Mapping</h1><p className="part-workbook" title={path}>{path}</p></div><div className="workbench-actions"><button type="button" ref={!narrowViewport ? contextToggle : undefined} className="button part-context-desktop-toggle" aria-controls="part-mapping-context" aria-expanded={contextOpen} onClick={() => setContextOpen(open => !open)}>{contextOpen ? "Hide supporting information" : "Show supporting information"}</button><button type="button" className="button" disabled={locked} onClick={() => contextualSelection
        ? openParts(contextualSelection.group, null, "select", contextualSelection.rows) : openParts(null, null, "select")}>{contextualSelection ? `Assign ${contextualSelection.rows.length} selected line${contextualSelection.rows.length === 1 ? "" : "s"}` : "Parts register"}</button><button type="button" className="button" disabled={busy || loading || (Boolean(mappingAttempt) && !canRefreshConflict)} onClick={() => canRefreshConflict ? void reviewCurrentEvidence() : void load(++generation.current)}>{canRefreshConflict ? "Review current source" : "Reload review"}</button></div></div>
      {error && <div role="alert" className="part-error-banner"><strong>{mappingAttempt ? "Save failed" : "Part Mapping needs attention"}</strong><span>{error}</span>{mappingAttempt && <small>Selections and reason are retained. Retry sends the same request key and payload so a committed Grist save can be recovered safely.</small>}{canRefreshConflict && <button className="button" onClick={() => void reviewCurrentEvidence()}>Refresh and hold changed drafts for review</button>}</div>}
      {notice && <p role="status" className="part-notice compact">{notice}</p>}
      {loading && <p role="status" className="part-loading-status">{detail ? "Refreshing workbook and saved mapping evidence… Draft selections remain unconfirmed until this check completes." : "Loading Part review…"}</p>}
      {!detail && !loading && !error && <p role="status">Part Mapping has no current workbook evidence.</p>}
      {detail && <>
        {Boolean(detail.unresolvedDeletedLines) && <p className="part-deleted-lines-banner" role="alert">{detail.unresolvedDeletedLines} previously mapped source line(s) are absent or unmatched and need an explicit, reasoned review. Current line assignments and Part requirements remain unchanged until reviewed.</p>}
        <section className="part-mapping-toolbar" aria-label="Filter Part mappings">
          <label className="part-search-label">Search descriptions or source fields<input name="part-mapping-source-search" autoComplete="off" spellCheck={false} autoCapitalize="none" aria-label="Search descriptions or source fields" value={query} onChange={event => setQuery(event.target.value)} placeholder="Part description, material, row…"/></label>
          <label>Source sheet<select name="part-mapping-sheet-filter" autoComplete="off" aria-label="Source sheet" value={sheetFilter} onChange={event => setSheetFilter(event.target.value as SheetFilter)}>{(["all", "mcl", "toolshop", "cnc"] as SheetFilter[]).map(value => { const count = perSheet(value); return <option key={value} value={value}>{familyLabel(value)} · {count.groups} groups · {count.rows} rows</option>; })}</select></label>
          <label>Review status<select name="part-mapping-status-filter" autoComplete="off" aria-label="Review status" value={reviewFilter} onChange={event => setReviewFilter(event.target.value as ReviewFilter)}>
            <option value="all">All · {allGroups.length} groups</option><option value="needs-review">Needs review · {allGroups.filter(group => !group.reviewed && !groupPending(group)).length} groups</option>
            <option value="saved">Saved · {allGroups.filter(group => group.reviewed).length} groups</option><option value="unsaved">Unsaved changes · {pendingGroups.length} groups</option>
            <option value="unassigned">Unassigned lines · {allGroups.reduce((sum, group) => sum + group.rows.filter(row => !effectivePartForLine(group, row)).length, 0)} lines</option>
          </select></label>
          <div className="part-group-expansion-actions" aria-label="Group expansion controls">
            <button type="button" className="button quiet" disabled={!visibleGroups.length} onClick={() => setExpandedGroups(old => ({ ...old, ...Object.fromEntries(displayedKeys.map(key => [key, true])) }))}>Expand all displayed</button>
            <button type="button" className="button quiet" disabled={!visibleGroups.length} onClick={() => setExpandedGroups(old => ({ ...old, ...Object.fromEntries(displayedKeys.map(key => [key, false])) }))}>Collapse all displayed</button>
          </div>
        </section>
        {staleDrafts.length > 0 && <section className="part-stale-drafts" aria-label="Drafts held for source review"><h2>Drafts held for source review</h2><p>These choices were not applied after workbook, association or group evidence changed. Review each current source group before reopening its choice.</p>{staleDrafts.map(stale => {
          const current = allGroups.find(group => group.key === stale.groupKey);
          const hasWholeGroup = stale.hasGroupDecision ?? Boolean(stale.decision);
          const staleLines = Object.entries(stale.lineDecisions || {});
          return <article key={`${stale.groupKey}:${stale.sourceHash}`}><div><strong>{stale.sheet} · {stale.description || "Blank Part label"}</strong><span>{hasWholeGroup ? `Whole group · ${stale.decision ? formatPart(stale.part) : "Clear saved Part assignment"}` : `${staleLines.length} prior line choice(s) held`} · from workbook {stale.sourceHash.slice(0,12)}…</span>{staleLines.map(([oldKey, decision]) => <small key={oldKey}>Prior line choice · {decision ? formatPart(stale.lineParts?.[oldKey]) : "Clear saved line assignment"} · {stale.lineEvidence?.[oldKey] ? `evidence ${stale.lineEvidence[oldKey].slice(0,12)}…` : "semantic evidence unavailable"}</small>)}</div>{current ? <button className="button" onClick={() => reapplyStaleDraft(stale)}>Review current rows and reapply</button> : <span className="part-stale-missing">This group no longer exists; search and assign current rows manually.</span>}</article>;
        })}</section>}
        {Boolean(detail.removedGroups?.length) && <section className="part-removed-groups" aria-label="Saved groups removed from the workbook">
          <h2>Saved groups removed from this workbook · {detail.removedGroups?.length}</h2>
          <p>These groups have no current source lines. Their previous Part assignments remain historical proposals and were not reused for another group.</p>
          {detail.removedGroups?.map(group => <article key={group.key}><header><strong>{group.sheet} · {group.description || "Blank Part label"}</strong>
            <span>{group.reviewed ? "Removed source group reviewed" : "Removal review required"} · {group.previousAssignment?.partNumber ? `${group.previousAssignment.partNumber} · ` : ""}{group.previousAssignment?.name || "No prior Part assignment"}</span></header>
            <SemanticDifferenceList comparison={group.semanticComparison}/>
            {(group.deletedLines || []).map(line => <DeletedLineReviewCard key={line.reviewKey} line={line}
              reason={deletedLineReasons[line.reviewKey] || ""}
              disabled={busy || loading || !detail.schemaAvailable || !detail.associationKey
                || Boolean(mappingAttempt && !mappingAttempt.deletedLineReviewKeys?.includes(line.reviewKey))}
              onReasonChange={value => setDeletedLineReasons(old => ({ ...old, [line.reviewKey]: value }))}
              onReview={() => void reviewDeletedLine(line.reviewKey)}/>)}
          </article>)}
        </section>}
        <p className="part-counts"><strong>Showing {visibleGroups.length} of {allGroups.length} source groups</strong> · {visibleGroups.reduce((sum, group) => sum + group.rows.length, 0)} displayed source rows. Filters never discard hidden drafts.</p>
        <div className="part-source-table" ref={tableScroll} onScroll={() => { const draft = readDraft(path); if (draft && tableScroll.current) sessionStorage.setItem(draftKey(path), JSON.stringify({ ...draft, scrollTop: tableScroll.current.scrollTop })); }}>
          {visibleGroups.length ? visibleGroups.map(group => {
            const groupIsExpanded = Boolean(expandedGroups[group.key]);
            const needsReason = pendingReasonRequired(group);
            const groupReason = groupReasons[group.key] || "";
            const selectedPart = selectedPartFor(group);
            const proposedPart = group.previousAssignment?.partId ? detail.parts.find(part => part.id === group.previousAssignment?.partId) || null : null;
            const semanticStatus = group.semanticComparison?.status || "";
            const compatibilityMessage = semanticStatus === "legacy_evidence_missing"
              ? "The saved mapping has no versioned manufacturing snapshot. This is a one-time confirmation; old manufacturing data was not inferred from the current workbook."
              : semanticStatus === "association_changed"
                ? "The file association changed. Review this prior assignment in the current Product/Model context before confirming it."
              : semanticStatus === "grouping_policy_changed"
                ? "Source grouping policy changed. The prior Part remains a proposal and needs explicit review; group identity is not a Part revision."
                : semanticStatus === "ambiguous_identity" || semanticStatus === "ambiguous_correspondence"
                  ? "The source label or line correspondence is ambiguous. The prior Part remains a proposal and needs explicit review."
              : semanticStatus === "assignment_conflict"
                    ? "The saved rows disagree about their canonical Part. Resolve the assignment explicitly."
                    : "Relevant manufacturing evidence changed. The prior Part remains a proposal until you review and save this group.";
            const groupSelectionRows = selectedLinesFor(group);
            const selectedLinePart = selectedLinesPartFor(group);
            const groupAssigned = group.rows.some(row => row.reviewed && row.part);
            const blocker = !detail.schemaAvailable ? (detail.schemaMessage || "Part review storage is unavailable; saving is blocked.") : !detail.associationKey ? "Save a file association before assigning Parts." : group.sourceDiagnostic?.status && group.sourceDiagnostic.status !== "ok" ? "Resolve the source label column before saving." : groupPending(group) ? "" : !selectedPart && !groupAssigned ? "Choose a canonical Part before saving." : "";
            return <article className={`part-source-group ${groupIsExpanded ? "is-expanded" : "is-collapsed"}`} key={group.key}>
              {group.previousAssignment && !group.reviewed && <div className="part-compatibility-note" role="status"><strong>{semanticStatus === "legacy_evidence_missing" ? "One-time source evidence confirmation required." : semanticStatus === "association_changed" ? "Prior assignment needs association review." : semanticStatus === "grouping_policy_changed" ? "Prior assignment needs grouping-policy review." : "Prior assignment needs source review."}</strong>
                <span>{compatibilityMessage}{group.previousAssignment.name ? ` Prior canonical Part: ${group.previousAssignment.partNumber ? `${group.previousAssignment.partNumber} · ` : ""}${group.previousAssignment.name}.` : ""}</span>
                {proposedPart?.selectable && !proposedPart.duplicateName && <button type="button" className="button" disabled={locked} onClick={() => selectPart(group, proposedPart)}>Use proposed Part in draft</button>}
                {group.previousAssignment.previousRowsAgree === false && <small>Previous source-row assignments disagree or do not cover every current row.</small>}
              </div>}
              {group.sourceDiagnostic?.status && group.sourceDiagnostic.status !== "ok" && <div className="part-compatibility-note" role="alert">Part label source is {group.sourceDiagnostic.status.replaceAll("_", " ")}. Resolve the source header before saving a Part assignment.</div>}
              <header className="part-group-heading">
                <button type="button" className="part-group-toggle" aria-label={`${groupIsExpanded ? "Collapse" : "Expand"} source evidence · ${groupTitle(group)} · ${canonicalSummary(group)}`} aria-expanded={groupIsExpanded} aria-controls={`part-group-content-${group.key}`} onClick={() => setExpandedGroups(old => ({ ...old, [group.key]: !Boolean(old[group.key]) }))}>
                  {groupIsExpanded ? <ChevronDown size={16}/> : <ChevronRight size={16}/>}<span className="part-group-heading-copy"><span className={`part-sheet-chip ${sheetFamilyClass(group.sheet)}`}>{group.sheet}</span><strong>{groupTitle(group)}</strong><small>{group.rows.length} source row{group.rows.length === 1 ? "" : "s"} · {group.labelField === "part_category" ? "Part Category" : "Machine Piece Description"} · {canonicalSummary(group)}</small></span>
                </button>
                <div className="part-group-header-controls">
                  <PartSearchCombobox group={group} selected={selectedPart} disabled={locked || !detail.schemaAvailable || Boolean(group.sourceDiagnostic?.status && group.sourceDiagnostic.status !== "ok")}
                    path={path} open={active && activePicker === group.key} onOpen={() => setActivePicker(group.key)} onClose={() => setActivePicker(null)} onSelect={part => selectPart(group, part)}/>
                  <div className="part-group-actions">
                    <button type="button" className={`button ${splitModes[group.key] ? "primary" : ""}`} aria-pressed={Boolean(splitModes[group.key])} disabled={locked} onClick={() => { setSplitModes(old => ({ ...old, [group.key]: !old[group.key] })); setExpandedGroups(old => ({ ...old, [group.key]: true })); }}>{splitModes[group.key] ? "Done selecting" : "Split assignments"}</button>
                    <button type="button" className="button" disabled={locked || !detail.schemaAvailable} onClick={event => openCreateDialog(group, event.currentTarget)}>Create Part</button>
                    <button type="button" className="button" disabled={locked || !onOpenParts || !selectedPart} onClick={() => openParts(group, selectedPart?.id || null, "view")}>View Part</button>
                    {(groupAssigned || decisions[group.key] !== undefined) && <button type="button" className="button quiet" disabled={locked} onClick={() => selectPart(group, null)}>{groupAssigned ? "Clear saved Part…" : "Clear selection"}</button>}
                    {groupPending(group) && <button type="button" className="button quiet" disabled={locked} onClick={() => { setDecisions(old => { const next = { ...old }; delete next[group.key]; return next; }); setDraftParts(old => { const next = { ...old }; delete next[group.key]; return next; }); const keys = group.rows.flatMap(row => row.lineKey ? [row.lineKey] : []); setLineDecisions(old => Object.fromEntries(Object.entries(old).filter(([key]) => !keys.includes(key)))); setLineDraftParts(old => Object.fromEntries(Object.entries(old).filter(([key]) => !keys.includes(key)))); setLineReasons(old => Object.fromEntries(Object.entries(old).filter(([key]) => !keys.includes(key)))); }}>Revert draft</button>}
                    <button type="button" className="button primary" disabled={busy || loading || (Boolean(mappingAttempt) && !mappingAttempt?.groupKeys.includes(group.key)) || !detail.schemaAvailable || !detail.associationKey || Boolean(group.sourceDiagnostic?.status && group.sourceDiagnostic.status !== "ok") || (!mappingAttempt && (!groupPending(group) || (needsReason && !groupReason.trim())))}
                      onClick={() => void saveGroups(mappingAttempt?.groupKeys.includes(group.key) ? mappingAttempt.groupKeys : [group.key])}>{mappingAttempt?.groupKeys.includes(group.key) && error ? "Retry save" : Object.prototype.hasOwnProperty.call(decisions, group.key) ? "Save group" : "Save lines"}</button>
                    <span className={`part-state state-${sourceStatus(group).toLowerCase()}`}>{sourceStatus(group)}</span>
                  </div>
                </div>
              </header>
              {needsReason && <div className="part-group-reason-row"><label className="part-group-reason">Reason for mapping change<input name={`mapping-change-reason-${group.key}`} autoComplete="off" spellCheck={false} autoCapitalize="sentences" aria-label={`Reason for mapping change · ${groupTitle(group)}`} value={groupReason} disabled={locked} onChange={event => setGroupReasons(old => ({ ...old, [group.key]: event.target.value }))} placeholder="Required for replacement or clear"/></label></div>}
              {splitModes[group.key] && <section className="part-line-assignment-editor" aria-label={`Assign selected lines · ${groupTitle(group)}`}>
                <div className="part-line-assignment-actions"><strong>{groupSelectionRows.length} line{groupSelectionRows.length === 1 ? "" : "s"} selected</strong>
                  <button type="button" className="button quiet" disabled={locked} onClick={() => setSelectedLineKeys(old => [...new Set([...old, ...group.rows.flatMap(row => row.lineKey ? [row.lineKey] : [])])])}>Select all group lines</button>
                  <button type="button" className="button quiet" disabled={locked || !groupSelectionRows.length} onClick={() => setSelectedLineKeys(old => old.filter(key => !groupSelectionRows.some(row => row.lineKey === key)))}>Clear selection</button>
                  <PartSearchCombobox controlKey={`${group.key}-selected-lines`} group={group} selected={selectedLinePart}
                    label={`Canonical Part for ${groupSelectionRows.length} selected lines`} disabled={locked || !groupSelectionRows.length || !detail.schemaAvailable || Boolean(group.sourceDiagnostic?.status && group.sourceDiagnostic.status !== "ok")}
                    path={path} open={active && activePicker === `${group.key}:lines`} onOpen={() => setActivePicker(`${group.key}:lines`)} onClose={() => setActivePicker(null)} onSelect={part => assignPartToLines(group, part)}/>
                  <button type="button" className="button" disabled={locked || !groupSelectionRows.length || !detail.schemaAvailable} onClick={event => openCreateDialog(group, event.currentTarget, groupSelectionRows)}>Create Part for selected lines</button>
                  {selectedLinePart && <button type="button" className="button" disabled={locked || !onOpenParts} onClick={() => openParts(group, selectedLinePart.id, "view", groupSelectionRows)}>View selected Part</button>}
                  <button type="button" className="button quiet" disabled={locked || !groupSelectionRows.length} onClick={() => assignPartToLines(group, null)}>Clear selected lines</button>
                </div>
                {groupSelectionRows.length === 0 && <small>Select one or more source lines below. Assigning or creating a Part only changes those lines’ draft; Save remains explicit.</small>}
                {needsReason && <label className="part-group-reason">Reason for changing previously assigned selected lines<input name={`mapping-selected-line-reason-${group.key}`} autoComplete="off" spellCheck={false} autoCapitalize="sentences" aria-label={`Reason for selected line mapping change · ${groupTitle(group)}`} value={groupReason} disabled={locked} onChange={event => setGroupReasons(old => ({ ...old, [group.key]: event.target.value }))} placeholder="Required only where an existing assignment changes or clears"/></label>}
              </section>}
              {group.semanticComparison?.status === "unchanged" && group.reviewed && group.semanticComparison.sourceHashChanged && <p className="part-semantic-stable" role="status">Relevant manufacturing evidence is unchanged; this saved Part assignment remains valid across the workbook hash change. Rates, prices, totals, formatting and document metadata are excluded.</p>}
              {(group.deletedLines || []).length > 0 && <section className="part-deleted-lines" aria-label={`Previously mapped lines removed from ${groupTitle(group)}`}>
                <h3>Previously mapped lines absent from the workbook · {(group.deletedLines || []).length}</h3>
                {(group.deletedLines || []).map(line => <DeletedLineReviewCard key={line.reviewKey} line={line}
                  reason={deletedLineReasons[line.reviewKey] || ""}
                  disabled={busy || loading || !detail.schemaAvailable || !detail.associationKey
                    || Boolean(mappingAttempt && !mappingAttempt.deletedLineReviewKeys?.includes(line.reviewKey))}
                  onReasonChange={value => setDeletedLineReasons(old => ({ ...old, [line.reviewKey]: value }))}
                  onReview={() => void reviewDeletedLine(line.reviewKey)}/>)}
              </section>}
              <SemanticDifferenceList comparison={group.semanticComparison}/>
              {selectedPart?.outOfScopeCodes?.length ? <small className="part-scope-advisory">Scope advisory: this assignment remains saveable for {selectedPart.outOfScopeCodes.map(code => code.code).join(", ")}.</small> : null}
              {group.explicitlyUnassigned && <small className="part-unassigned-note">The last saved action explicitly cleared the Part. Historical assignments remain in History.</small>}
              {blocker && <small className="part-save-blocker">{blocker}</small>}
              <div id={`part-group-content-${group.key}`} className="part-group-content" hidden={!groupIsExpanded}><SourceDetails group={group} expanded={Boolean(expanded[group.key])} onToggle={() => setExpanded(old => ({ ...old, [group.key]: !old[group.key] }))}
                selectionMode={Boolean(splitModes[group.key])} selectedLineKeys={selectedLineKeys} onToggleLine={row => setSelectedLineKeys(old => old.includes(String(row.lineKey || "")) ? old.filter(key => key !== row.lineKey) : [...old, String(row.lineKey || "")])}
                effectivePart={row => effectivePartForLine(group, row)} onViewPart={row => { const part = effectivePartForLine(group, row); if (part) openParts(group, part.id, "view", [row]); }}/></div>
            </article>}) : <div className="part-empty-state">No source groups match these filters.</div>}
        </div>
        <div className="part-sticky-save" role="region" aria-label="Save Part mapping drafts"><div><strong>{pendingGroups.length} group{pendingGroups.length === 1 ? "" : "s"} · {pendingLineCount} unsaved line{pendingLineCount === 1 ? "" : "s"}</strong><span>{requiredReasonGroups.length} group{requiredReasonGroups.length === 1 ? "" : "s"} have affected prior assignments</span></div>
          <small>Initial line assignments need no reason. Replacement and clear reasons apply only to previously assigned lines being changed.</small>
          <button type="button" className="button primary" disabled={busy || loading || (mappingAttempt ? false : (!detail.schemaAvailable || !detail.associationKey || !pendingGroups.length || requiredReasonGroups.some(group => !(groupReasons[group.key] || "").trim())))} onClick={() => mappingAttempt?.deletedLineReviewKeys?.length ? void reviewDeletedLine(mappingAttempt.deletedLineReviewKeys[0]) : mappingAttempt ? void saveGroups(mappingAttempt.groupKeys) : setConfirmBatch(true)}>{mappingAttempt?.deletedLineReviewKeys?.length ? "Retry deleted-line review" : mappingAttempt ? "Retry saving pending mappings" : "Save pending mappings"}</button>
        </div>
        {confirmBatch && <div className="part-modal-backdrop" role="presentation"><section className="part-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="part-confirm-title" aria-describedby="part-confirm-help">
          <h2 id="part-confirm-title">Save pending line assignments?</h2><p id="part-confirm-help">This includes every pending line, including lines in groups hidden by the current filters. Review which lines will be appended to mapping history.</p>
          <div className="part-confirm-list">{pendingGroups.map(group => { const needsReason = pendingReasonRequired(group); const wholeGroup = Object.prototype.hasOwnProperty.call(decisions, group.key); const pendingRows = wholeGroup
            ? group.rows : group.rows.filter(row => row.lineKey && Object.prototype.hasOwnProperty.call(lineDecisions, row.lineKey));
            return <article key={group.key}><strong>{group.sheet} · {groupTitle(group)}</strong><span>{wholeGroup ? `Whole group · ${pendingRows.length} lines` : `${pendingRows.length} selected lines`}</span><ul>{pendingRows.map(row => {
              const choice = wholeGroup ? decisions[group.key] : lineDecisions[row.lineKey || ""];
              const chosenPart = choice ? (wholeGroup ? draftParts[group.key] : lineDraftParts[row.lineKey || ""]) : null;
              return <li key={row.lineKey || row.row}>Source row {row.row} → {formatPart(chosenPart)}{!wholeGroup && lineReasonRequired(row, choice || null) ? " · replacement/clear reason required" : ""}</li>;
            })}</ul><span>{needsReason ? "Reason required for changed saved lines" : "No prior assignment reason needed"}</span>{needsReason && <label>Reason for changed saved lines<input name={`mapping-batch-reason-${group.key}`} autoComplete="off" spellCheck={false} autoCapitalize="sentences" aria-label={`Batch mapping reason · ${groupTitle(group)}`} value={groupReasons[group.key] || ""} onChange={event => setGroupReasons(old => ({ ...old, [group.key]: event.target.value }))} placeholder="Why are the existing line assignments changing?" disabled={locked}/></label>}</article>; })}</div>
          <div className="part-confirm-actions"><button type="button" className="button" onClick={() => setConfirmBatch(false)}>Cancel</button><button type="button" className="button primary" disabled={locked || requiredReasonGroups.some(group => !(groupReasons[group.key] || "").trim())} onClick={() => void saveGroups(pendingGroups.map(group => group.key))}>Confirm save {pendingLineCount} lines</button></div>
        </section></div>}
      </>}
    </section>
    {createOrigin && <div className="part-create-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget && !createAttemptPending) closeCreateDialog(); }}><section ref={createDialog} className="part-create-dialog" role="dialog" aria-modal="true" aria-labelledby="part-create-dialog-title" tabIndex={-1}>
      <header><div><span className="eyebrow">Part Mapping · {createOrigin.sheet}</span><h2 id="part-create-dialog-title">Create and assign a Part</h2><p>The canonical Part is published first. Its source mapping remains a draft until you save it.</p></div><button type="button" className="button quiet" aria-label="Close Part creation dialog" disabled={createAttemptPending} onClick={closeCreateDialog}>Close</button></header>
      <PartCreationForm key={`${createOrigin.workbookPath}:${createOrigin.groupKey}`} origin={createOrigin} originContextCurrent={currentOriginMatches(createOrigin)} onAttemptStateChange={setCreateAttemptPending}
        onCreated={(part, attempt) => finishContextualCreation(part, attempt)} onUseExisting={useExistingInContext} onCancel={closeCreateDialog} cancelLabel="Cancel before creation"/>
    </section></div>}
  </main>;}

function PartSearchCombobox({ group, selected, disabled, path, open, onOpen, onClose, onSelect, controlKey, label }: { group: Group; selected: Part | null | undefined; disabled: boolean; path: string; open: boolean; onOpen: () => void; onClose: () => void; onSelect: (part: Part | null) => void; controlKey?: string; label?: string }) {
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
  const idKey = controlKey || group.key;
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
    <label className="part-combobox-label" htmlFor={`part-search-${idKey}`}>{label || `Canonical Part for ${groupTitle(group)}`} · permanent number, name, description, variant or previous alias</label>
    <input ref={input} id={`part-search-${idKey}`} name="part-mapping-canonical-search" autoComplete="off" spellCheck={false} autoCapitalize="none" aria-label={label || `Part assignment for ${groupTitle(group)}`} role="combobox" aria-autocomplete="list" aria-expanded={open} aria-busy={open && loading} aria-controls={`part-options-${idKey}`} aria-activedescendant={open && currentResults && options[activeIndex] ? `part-option-${idKey}-${activeIndex}` : undefined}
      value={open ? query : formatPart(selected)} placeholder="Search canonical Parts…" disabled={disabled} onFocus={onOpen} onClick={() => { if (!open) { setQuery(""); onOpen(); } }} onChange={event => { setQuery(event.target.value); setResults([]); setResultsFor(null); setLoading(true); setError(""); setActiveIndex(0); if (!open) onOpen(); }} onKeyDown={onKeyDown} />
    {open && createPortal(<div ref={menu} id={`part-options-${idKey}`} className="part-combobox-options" style={menuStyle} role="listbox" aria-label="Matching canonical Parts">
      {loading || !currentResults ? <div role="status" className="part-search-loading">Searching canonical Parts…</div> : error ? <div role="alert" className="part-search-error">{error}</div> : options.length ? options.map((part, index) => <button id={`part-option-${idKey}-${index}`} type="button" role="option" aria-selected={selected?.id === part.id || activeIndex === index} key={part.id} onMouseEnter={() => setActiveIndex(index)} onMouseDown={event => event.preventDefault()} onClick={() => choose(part)}>
        <strong>{formatPart(part)}</strong><span>{part.description || part.variant || "Canonical Safari Manufacturing Part"}</span>
      </button>) : <div className="part-search-empty">No unambiguous active Part matches this search.</div>}
      {currentResults && !error && total > options.length && <small className="part-search-count">Showing {options.length} of {total} results. Type more to narrow the list.</small>}
      {currentResults && !error && cacheAge > 0 && <small className="part-search-count">Search index read from Grist {cacheAge.toFixed(1)} seconds ago; Save rechecks current Part data.</small>}
    </div>, document.body)}
  </div>;
}

function SourceDetails({ group, expanded, onToggle, selectionMode, selectedLineKeys, onToggleLine, effectivePart, onViewPart }: { group: Group; expanded: boolean; onToggle: () => void; selectionMode: boolean; selectedLineKeys: string[]; onToggleLine: (row: SourceRow) => void; effectivePart: (row: SourceRow) => Part | null; onViewPart: (row: SourceRow) => void }) {
  const columns = sourceFields[group.sheet] || [];
  const compactKeys: Record<string, string[]> = {
    "5. Material Cut List Price": columns.slice(0, 5).map(column => column.key),
    "Tool Shop Items": ["item_name", "material_to_cut", "dimension_to_cut_mm", "qty", "toolshop_remarks"],
    "CNC Cut List": columns.slice(0, 5).map(column => column.key),
  };
  const withoutLegacyRemarksDuplicate = columns.filter(column => !(group.sheet === "Tool Shop Items" && column.key === "toolshop_part_name"
    && group.rows.some(row => row.sourceHeaders?.toolshop_part_name === "Remarks")));
  const visibleColumns = (expanded ? withoutLegacyRemarksDuplicate : withoutLegacyRemarksDuplicate.filter(column => (compactKeys[group.sheet] || []).includes(column.key)));
  return <section className={`part-source-details ${expanded ? "expanded" : ""}`} aria-label={`${group.sheet} source details`}>
    <div className="part-source-details-heading"><strong>Source evidence</strong><button type="button" className="button quiet" aria-expanded={expanded} onClick={onToggle}>{expanded ? "Show key fields" : "Expand source details"}</button></div>
    {!columns.length ? <p>Source-specific fields are not configured for this sheet.</p> : <div className="part-detail-scroll"><table><thead><tr>{selectionMode && <th>Assign line</th>}{visibleColumns.map(column => {
      const header = group.rows.map(row => row.sourceHeaders?.[column.key]).find(Boolean);
      const available = group.rows.some(row => row.availableFields?.includes(column.key));
      return <th key={column.key} title={header || (available ? column.label : "Column not found in source")}>{header || `${column.label}${available ? "" : " · not found"}`}</th>;
    })}<th>Source row</th>{selectionMode && <th>Canonical Part</th>}</tr></thead><tbody>{group.rows.map(row => <tr key={`${row.sheet}:${row.row}`} className={selectionMode && selectedLineKeys.includes(String(row.lineKey || "")) ? "is-line-selected" : ""}>{selectionMode && <td className="part-line-select-cell"><input type="checkbox" aria-label={`Select ${group.sheet} row ${row.row} for Part assignment`} checked={selectedLineKeys.includes(String(row.lineKey || ""))} disabled={!row.lineKey} onChange={() => onToggleLine(row)}/></td>}{visibleColumns.map(column => {
      const value = row.fields?.[column.key];
      const cell = row.sourceCells?.[column.key]?.cell;
      const available = row.availableFields?.includes(column.key);
      return <td className="part-source-value" key={column.key} title={cell ? `Source cell ${cell}${row.sourceHeaders?.[column.key] ? ` · ${row.sourceHeaders[column.key]}` : ""}` : undefined}>
        {value === null || value === undefined || String(value).trim() === "" ? <span className={available ? "part-source-blank" : "part-source-missing"}>{available ? "— blank in source" : "Not in sheet"}</span> : <>{String(value)}{cell && <small className="part-cell-ref">{cell}</small>}</>}
      </td>;
    })}<td><strong className={`part-sheet-label ${sheetFamilyClass(row.sheet)}`}>{row.sheet}</strong><small>row {row.row}{row.headerRow ? ` · headers row ${row.headerRow}` : ""}</small></td>{selectionMode && <td className="part-line-assignment-cell">{effectivePart(row) ? <><strong>{formatPart(effectivePart(row))}</strong><button type="button" className="button quiet" disabled={!row.reviewed} onClick={() => onViewPart(row)}>View</button></> : row.previousAssignment?.name ? <span className="part-line-prior-proposal">Needs review · proposed {row.previousAssignment.partNumber ? `${row.previousAssignment.partNumber} · ` : ""}{row.previousAssignment.name}</span> : <span className="part-source-blank">Unassigned</span>}{effectivePart(row)?.outOfScopeCodes?.length ? <small className="part-scope-advisory">Scope advisory: {effectivePart(row)!.outOfScopeCodes!.map(code => code.code).join(", ")}</small> : null}{row.lineKey && Object.prototype.hasOwnProperty.call(row, "reviewed") && !row.reviewed && <small>{row.semanticComparison?.status === "ambiguous_correspondence" ? "Ambiguous match · confirm this line" : row.explicitlyUnassigned ? "Explicitly cleared" : "Needs assignment"}</small>}</td>}</tr>)}</tbody></table></div>}
    {expanded && <p className="part-source-note">Values and units are copied from the saved workbook. Blank or unavailable values remain blank; source text is displayed as text. CR Log is source evidence only.</p>}
  </section>;
}
