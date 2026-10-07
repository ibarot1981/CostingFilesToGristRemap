import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import "./partMapping.css";

type Part = { id: string; name: string; selectable: boolean; duplicateName: boolean; partNumber?: string | null; engineeringRevision?: string | null; outOfScopeCodes?: { id: string; code: string }[] };
type Group = { key: string; description: string; blankDescription: boolean; rows: { sheet: string; row: number }[]; part: Part | null; reviewed: boolean };
type Detail = { sourceHash: string; associationKey: string; associationVersion: number; version: number; schemaAvailable: boolean; legacyHistoryAvailable?: boolean; parts: Part[]; groups: Group[]; unresolvedGroups: number; history: { ReviewKey: string; Version: number; SourceDescription: string; SheetName: string; SourceRow: number; Actor: string; Reason: string; OccurredAt: string }[] };
type Attempt = { key: string; payload: unknown };
type ReturnSelection = { path: string; groupKey: string; partId: string | null };

export function PartMappingView({ path, onOpenParts, returnSelection, onReturnSelectionConsumed }: { path: string; onOpenParts?: (groupKey: string, partId: string | null) => void; returnSelection?: ReturnSelection | null; onReturnSelectionConsumed?: () => void }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [decisions, setDecisions] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [mappingAttempt, setMappingAttempt] = useState<Attempt | null>(null);
  const generation = useRef(0);
  async function load(revision: number, clear = true) {
    setLoading(true); setError("");
    if (clear) { setDetail(null); setDecisions({}); setMappingAttempt(null); }
    try {
      const result = await api.partMappings(path);
      if (revision === generation.current) {
        setDetail(result);
        const saved = sessionStorage.getItem(`part-mapping:${path}`);
        if (saved) {
          try {
            const draft = JSON.parse(saved);
            const matches = draft.sourceHash === result.sourceHash && draft.version === result.version
              && draft.associationKey === result.associationKey && draft.associationVersion === result.associationVersion;
            if (matches) { setDecisions(draft.decisions || {}); setReason(draft.reason || ""); setQuery(draft.query || ""); setMappingAttempt(draft.mappingAttempt || null); }
            else sessionStorage.removeItem(`part-mapping:${path}`);
          } catch { sessionStorage.removeItem(`part-mapping:${path}`); }
        }
        if (returnSelection?.path === path && result.parts.some((part: Part) => part.id === returnSelection.partId && part.selectable)) {
          setDecisions(old => ({ ...old, [returnSelection.groupKey]: returnSelection.partId! }));
          onReturnSelectionConsumed?.();
        }
      }
    }
    catch (cause) { if (revision === generation.current) { setDetail(null); setError(String(cause)); } }
    finally { if (revision === generation.current) setLoading(false); }
  }
  useEffect(() => {
    const revision = ++generation.current;
    setDetail(null); setDecisions({}); setReason(""); setNotice(""); setError(""); setBusy(false);
    setMappingAttempt(null);
    if (path) void load(revision);
    return () => { ++generation.current; };
  }, [path]);
  useEffect(() => {
    if (!detail || !path) return;
    sessionStorage.setItem(`part-mapping:${path}`, JSON.stringify({ path, sourceHash: detail.sourceHash, version: detail.version,
      associationKey: detail.associationKey, associationVersion: detail.associationVersion, decisions, reason, query, mappingAttempt }));
  }, [path, detail, decisions, reason, query, mappingAttempt]);
  function openParts(groupKey: string, partId: string | null) {
    if (detail) sessionStorage.setItem(`part-mapping:${path}`, JSON.stringify({ path, sourceHash: detail.sourceHash, version: detail.version,
      associationKey: detail.associationKey, associationVersion: detail.associationVersion, decisions, reason, query, mappingAttempt }));
    onOpenParts?.(groupKey, partId);
  }
  async function save() {
    if (!detail) return;
    const revision = generation.current;
    const attempt = mappingAttempt || { key: crypto.randomUUID(), payload: { path, decisions, reason,
      expectedHash: detail.sourceHash, expectedVersion: detail.version,
      expectedAssociationKey: detail.associationKey, expectedAssociationVersion: detail.associationVersion } };
    sessionStorage.setItem(`part-mapping:${path}`, JSON.stringify({ path, sourceHash: detail.sourceHash, version: detail.version,
      associationKey: detail.associationKey, associationVersion: detail.associationVersion, decisions, reason, query, mappingAttempt: attempt }));
    setMappingAttempt(attempt); setBusy(true); setError("");
    try {
      const result = await api.savePartMappings(attempt.payload, attempt.key);
      if (revision !== generation.current) return;
      sessionStorage.removeItem(`part-mapping:${path}`);
      setMappingAttempt(null); setDecisions({}); setReason(""); setNotice(`Saved ${result.savedRows} source-row assignments in review version ${result.version}.`);
      await load(revision, false);
    } catch (cause) { if (revision === generation.current) setError(String(cause)); }
    finally { if (revision === generation.current) setBusy(false); }
  }
  if (!path) return <main className="part-mapping"><h1>Part Mapping</h1><p>Select a costing workbook in Files to review its Parts.</p></main>;
  const parts = detail?.parts.filter(part => part.selectable && !part.duplicateName) || [];
  const groups = detail?.groups.filter(group => `${group.description} ${group.rows.map(row => row.sheet).join(" ")}`.toLowerCase().includes(query.toLowerCase())) || [];
  const locked = busy || loading || Boolean(mappingAttempt);
  return <main className="part-mapping">
    <div className="part-heading"><div><h1>Part Mapping</h1><p>{path}</p></div><div className="workbench-actions"><button className="button" disabled={locked} onClick={() => openParts("", null)}>Open Parts</button><button className="button" disabled={busy || loading} onClick={() => void load(++generation.current)}>Reload review</button></div></div>
    <p>Descriptions come from the saved workbook. Each assignment records the workbook version, association, reviewer and reason. New Part names must be unique across Safari Manufacturing.</p>
    {error && <div role="alert" className="error-banner">{error}{mappingAttempt && <><p>Retry sends the same saved request. Reload the review to check whether it was saved.</p><button className="button" disabled={busy || loading} onClick={() => { sessionStorage.removeItem(`part-mapping:${path}`); setMappingAttempt(null); void load(++generation.current); }}>Discard retry and reload</button></>}</div>}
    {notice && <p role="status">{notice}</p>}
    {loading && <p role="status">Loading Part review…</p>}
    {detail && <>
      <p><strong>{detail.unresolvedGroups} of {detail.groups.length} description groups need review</strong> · Review version {detail.version} · {detail.groups.reduce((total, group) => total + group.rows.length, 0)} active source rows</p>
      <details><summary>Workbook version</summary><p className="part-hash">{detail.sourceHash}</p></details>
      {!detail.schemaAvailable && <p role="alert">The Part review storage is unavailable.</p>}
      {detail.legacyHistoryAvailable === false && <p role="alert">Legacy Grist Part assignment history is unavailable. New Part reviews are stored in the durable local Part registry; legacy history will need review before migration.</p>}
      {!detail.associationKey && <p role="alert">Save a file association before saving Part assignments.</p>}
      <p>Part creation is handled in the Parts register so identity, scope, shortcode and Rev A are reviewed before assignment.</p>
      <label>Find a description<input value={query} onChange={event => setQuery(event.target.value)}/></label>
      <div className="part-table"><table><thead><tr><th>Machine Piece Description</th><th>Source rows</th><th>Canonical Part</th></tr></thead><tbody>{groups.map(group => <tr key={group.key}>
        <td><strong>{group.description || "Blank description — individual row"}</strong><small>{group.reviewed ? `Saved: ${group.part?.name}` : "Needs review"}</small></td>
        <td>{group.rows.map(row => <div key={`${row.sheet}:${row.row}`}>{row.sheet} · row {row.row}</div>)}</td>
        <td><select aria-label={`Part for ${group.description || `${group.rows[0].sheet} row ${group.rows[0].row}`}`} disabled={locked || !detail.schemaAvailable} value={decisions[group.key] || ""} onChange={event => setDecisions(old => { const next = { ...old }; if (event.target.value) next[group.key] = event.target.value; else delete next[group.key]; return next; })}>
          <option value="">{group.part ? `Keep ${group.part.partNumber ? `${group.part.partNumber} · ` : ""}${group.part.name}` : "Select a Part"}</option>{parts.map(part => <option key={part.id} value={part.id}>{part.partNumber ? `${part.partNumber} · ` : ""}{part.name}{part.engineeringRevision ? ` · Rev ${part.engineeringRevision}` : ""}</option>)}</select><button className="button" disabled={locked} onClick={() => openParts(group.key, decisions[group.key] || group.part?.id || null)}>Open in Parts</button>{(() => { const selectedPart = parts.find(part => part.id === (decisions[group.key] || group.part?.id)); return selectedPart?.outOfScopeCodes?.length ? <small role="status" className="scope-advisory">Scope advisory: this assignment remains saveable for {selectedPart.outOfScopeCodes.map(code => code.code).join(", ")}.</small> : null; })()}</td>
      </tr>)}</tbody></table></div>
      <label>Reason for these assignments<textarea disabled={locked} value={reason} onChange={event => setReason(event.target.value)}/></label>
      <button className="button primary" disabled={busy || loading || !detail.schemaAvailable || !detail.associationKey || (!mappingAttempt && (!Object.keys(decisions).length || !reason.trim()))} onClick={() => void save()}>{mappingAttempt ? "Retry saving assignments" : `Save ${Object.keys(decisions).length} selected groups`}</button>
      <details className="part-history"><summary>Assignment history ({detail.history.length} source rows)</summary>{detail.history.map(row => <p key={row.ReviewKey}>Version {row.Version} · {row.SheetName} row {row.SourceRow} · {row.SourceDescription || "Blank description"} · {row.Actor} · {row.OccurredAt}<br/>{row.Reason}</p>)}</details>
    </>}
  </main>;
}
