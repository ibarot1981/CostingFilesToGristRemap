import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, ChevronRight, LoaderCircle, Search } from "lucide-react";
import { api } from "./api";
import { PartBaselinePanel } from "./PartBaselinePanel";
import type { Group, Part } from "./PartMappingView";
import "./partBaselineWorkspace.css";

type MappingDetail = { parts: Part[]; groups: Group[]; sourceHash: string; associationKey: string; associationVersion: number; version: number };
type WorkspaceRequest = { groupKey: string; part: Part; lineKey?: string; lineFingerprint?: string; context: Record<string, unknown> };

function baselineLabel(status?: string) {
  const value = String(status || "").replaceAll("_", " ").trim();
  return value || "Not reviewed";
}

export function PartBaselineWorkspace({ path, initialPartId = "", initialPart = null, returnLabel, onBack, onUseDifferentPart }: {
  path: string;
  initialPartId?: string;
  initialPart?: Part | null;
  returnLabel: string;
  onBack: () => void;
  onUseDifferentPart: (request: WorkspaceRequest) => void;
}) {
  const [detail, setDetail] = useState<MappingDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [selectedPartId, setSelectedPartId] = useState(initialPartId);
  const [statuses, setStatuses] = useState<Record<string, string>>({});

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    api.partMappings(path).then(result => {
      if (!active) return;
      const next = result as MappingDetail;
      setDetail(next);
      setStatuses(Object.fromEntries((next.parts || []).map(part => [part.id, part.manufacturingBaselineStatus || "not_established"])));
      setSelectedPartId(current => {
        if (initialPartId) return initialPartId;
        return (next.parts || []).some(part => part.id === current) ? current : next.parts?.[0]?.id || "";
      });
    }).catch(cause => { if (active) setError(String(cause)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [path, initialPartId]);

  const parts = useMemo(() => {
    const available = [...(detail?.parts || [])];
    if (initialPart && !available.some(part => part.id === initialPart.id)) available.unshift(initialPart);
    return [...new Map(available.filter(part => !part.legacy && part.status !== "retired" && part.selectable !== false)
      .map(part => [part.id, part])).values()];
  }, [detail, initialPart]);
  const visibleParts = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return parts.filter(part => !needle || [part.partNumber, part.name, part.description, part.variant]
      .some(value => String(value || "").toLocaleLowerCase().includes(needle)));
  }, [parts, query]);
  const selected = parts.find(part => part.id === selectedPartId) || null;

  function useDifferentPart(groupKey: string, part: Part, lineKey?: string, lineFingerprint?: string) {
    if (!detail || !lineKey || !lineFingerprint) {
      setError("This comparison does not identify one current source line. Return to Part Mapping and select the current line manually.");
      return;
    }
    const group = detail.groups.find(item => item.key === groupKey);
    const matches = group?.rows.filter(row => String(row.lineKey || "") === lineKey
      && String(row.semanticEvidence?.fingerprint || "") === lineFingerprint) || [];
    if (!group || matches.length !== 1) {
      setError("The source line changed or is ambiguous. Return to Part Mapping and review the current line before changing its draft assignment.");
      return;
    }
    const selectedLineEvidenceFingerprint = JSON.stringify([{ lineKey, fingerprint: lineFingerprint }]);
    onUseDifferentPart({ groupKey, part, lineKey, lineFingerprint, context: {
      sourceHash: detail.sourceHash,
      associationKey: detail.associationKey,
      associationVersion: detail.associationVersion,
      evidenceFingerprint: group.evidenceFingerprint,
      lineKeys: [lineKey],
      selectedLineEvidenceFingerprint,
    } });
  }

  function selectNextPart() {
    if (!parts.length) return;
    const index = parts.findIndex(part => part.id === selectedPartId);
    setSelectedPartId(parts[(index + 1) % parts.length].id);
  }

  return <main className="part-baseline-workspace" aria-label="Manufacturing baseline review workspace">
    <header className="baseline-workspace-heading">
      <div><span className="eyebrow">Safari Manufacturing · Part review</span><h1>Review manufacturing baselines</h1>
        <p title={path}>Workbook · {path || "No workbook selected"}</p></div>
      <button type="button" className="button" onClick={onBack}><ArrowLeft size={14}/>{returnLabel}</button>
    </header>
    <div className="baseline-workspace-context" role="status">Each Part is reviewed and published separately. Publication records an initial manufacturing baseline; it does not finalize Rev A. Your Part Mapping drafts remain available when you return.</div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {loading && <p className="baseline-workspace-loading" role="status"><LoaderCircle className="spin" size={15}/> Loading saved mapped Parts and workbook evidence…</p>}
    {!loading && <div className="baseline-workspace-layout">
      <aside className="baseline-workspace-nav" aria-label="Mapped Part navigation">
        <div className="baseline-workspace-nav-heading"><strong>Mapped Parts</strong><span>{parts.length}</span></div>
        <label className="baseline-workspace-search"><Search size={14}/><input aria-label="Search mapped Parts" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search number or name"/></label>
        {!parts.length && <p className="baseline-workspace-empty">No saved mapped Parts are available for this workbook. Open this review from a Part’s empty baseline entry or save a Part mapping first.</p>}
        {!!parts.length && !visibleParts.length && <p className="baseline-workspace-empty">No Parts match this search.</p>}
        <div className="baseline-workspace-part-list">{visibleParts.map(part => {
          const status = statuses[part.id] || part.manufacturingBaselineStatus || "not_established";
          return <button type="button" key={part.id} className={`baseline-workspace-part ${part.id === selectedPartId ? "is-selected" : ""}`} aria-current={part.id === selectedPartId ? "true" : undefined} onClick={() => { setError(""); setSelectedPartId(part.id); }}>
            <span><strong>{part.partNumber || "Number unavailable"} · {part.name}</strong><small>{part.description || part.variant || "No additional description"}</small><small className="baseline-workspace-status">Baseline · {baselineLabel(status)}</small></span><ChevronRight size={15}/>
          </button>;
        })}</div>
        {parts.length > 1 && <button type="button" className="button baseline-workspace-next" onClick={selectNextPart}><ChevronRight size={14}/>Next Part</button>}
      </aside>
      <section className="baseline-workspace-main" aria-label={selected ? `Baseline review for ${selected.partNumber || selected.name}` : "Baseline review details"}>
        {selected && detail ? <>
          <div className="baseline-workspace-part-heading"><div><span className="eyebrow">{selected.partNumber || "Part number unavailable"} · Rev {selected.engineeringRevision || "A"}</span><h2>{selected.name}</h2><p>{selected.description || "No Part description"}</p></div><span className="baseline-workspace-status-pill">{baselineLabel(statuses[selected.id] || selected.manufacturingBaselineStatus)}</span></div>
          <PartBaselinePanel key={`${path}:${selected.id}`} path={path} parts={[...detail.parts, ...(initialPart ? [initialPart] : [])]} groups={detail.groups}
            initialPartId={selected.id} autoOpen autoLoadReview showPartSelector={false}
            onStatusChange={(partId, status) => setStatuses(old => ({ ...old, [partId]: status }))}
            onUseDifferentPart={useDifferentPart}/>
        </> : <div className="baseline-workspace-empty-state"><strong>Select a mapped Part</strong><span>Search or choose a Part on the left to review its baseline evidence.</span></div>}
      </section>
    </div>}
  </main>;
}
