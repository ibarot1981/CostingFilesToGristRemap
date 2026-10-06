import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import "./partMapping.css";

type Part = { id: string; name: string; selectable: boolean; duplicateName: boolean };
type Group = { key: string; description: string; blankDescription: boolean; rows: { sheet: string; row: number }[]; part: Part | null; reviewed: boolean };
type Detail = { sourceHash: string; associationKey: string; associationVersion: number; version: number; schemaAvailable: boolean; parts: Part[]; groups: Group[]; unresolvedGroups: number; history: { ReviewKey: string; Version: number; SourceDescription: string; SheetName: string; SourceRow: number; Actor: string; Reason: string; OccurredAt: string }[] };
type Attempt = { key: string; payload: unknown };

export function PartMappingView({ path }: { path: string }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [decisions, setDecisions] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [name, setName] = useState("");
  const [createReason, setCreateReason] = useState("");
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [mappingAttempt, setMappingAttempt] = useState<Attempt | null>(null);
  const [createAttempt, setCreateAttempt] = useState<Attempt | null>(null);
  const generation = useRef(0);
  async function load(revision: number, clear = true, preserveChoices = false) {
    setLoading(true); setError("");
    if (clear) { setDetail(null); setDecisions({}); setMappingAttempt(null); setCreateAttempt(null); }
    try {
      const result = await api.partMappings(path);
      if (revision === generation.current) {
        if (preserveChoices && detail && (result.sourceHash !== detail.sourceHash || result.version !== detail.version
            || result.associationKey !== detail.associationKey || result.associationVersion !== detail.associationVersion)) {
          setDecisions({}); setReason("");
          setNotice("The workbook, association or mapping review changed. Review the rows again before saving assignments.");
        }
        setDetail(result);
      }
    }
    catch (cause) { if (revision === generation.current) { setDetail(null); setError(String(cause)); } }
    finally { if (revision === generation.current) setLoading(false); }
  }
  useEffect(() => {
    const revision = ++generation.current;
    setDetail(null); setDecisions({}); setReason(""); setName(""); setCreateReason(""); setNotice(""); setError(""); setBusy(false);
    setMappingAttempt(null); setCreateAttempt(null);
    if (path) void load(revision);
    return () => { ++generation.current; };
  }, [path]);
  async function create() {
    const revision = generation.current;
    const attempt = createAttempt || { key: crypto.randomUUID(), payload: { name, reason: createReason } };
    setCreateAttempt(attempt); setBusy(true); setError("");
    try {
      const result = await api.createPart(attempt.payload, attempt.key);
      if (revision !== generation.current) return;
      setCreateAttempt(null); setName(""); setCreateReason(""); setNotice(`Part created: ${result.part.name}. Select it below and save the assignments.`);
      await load(revision, false, true);
    } catch (cause) { if (revision === generation.current) setError(String(cause)); }
    finally { if (revision === generation.current) setBusy(false); }
  }
  async function save() {
    if (!detail) return;
    const revision = generation.current;
    const attempt = mappingAttempt || { key: crypto.randomUUID(), payload: { path, decisions, reason,
      expectedHash: detail.sourceHash, expectedVersion: detail.version,
      expectedAssociationKey: detail.associationKey, expectedAssociationVersion: detail.associationVersion } };
    setMappingAttempt(attempt); setBusy(true); setError("");
    try {
      const result = await api.savePartMappings(attempt.payload, attempt.key);
      if (revision !== generation.current) return;
      setMappingAttempt(null); setDecisions({}); setReason(""); setNotice(`Saved ${result.savedRows} source-row assignments in review version ${result.version}.`);
      await load(revision, false);
    } catch (cause) { if (revision === generation.current) setError(String(cause)); }
    finally { if (revision === generation.current) setBusy(false); }
  }
  if (!path) return <main className="part-mapping"><h1>Part Mapping</h1><p>Select a costing workbook in Files to review its Parts.</p></main>;
  const parts = detail?.parts.filter(part => part.selectable && !part.duplicateName) || [];
  const groups = detail?.groups.filter(group => `${group.description} ${group.rows.map(row => row.sheet).join(" ")}`.toLowerCase().includes(query.toLowerCase())) || [];
  const locked = busy || loading || Boolean(mappingAttempt || createAttempt);
  return <main className="part-mapping">
    <div className="part-heading"><div><h1>Part Mapping</h1><p>{path}</p></div><button className="button" disabled={busy || loading} onClick={() => void load(++generation.current)}>Reload review</button></div>
    <p>Descriptions come from the saved workbook. Each assignment records the workbook version, association, reviewer and reason. New Part names must be unique across Safari Manufacturing.</p>
    {error && <div role="alert" className="error-banner">{error}{(mappingAttempt || createAttempt) && <p>Retry sends the same saved request. Reload review to check the result before changing your choices.</p>}</div>}
    {notice && <p role="status">{notice}</p>}
    {loading && <p role="status">Loading Part review…</p>}
    {detail && <>
      <p><strong>{detail.unresolvedGroups} of {detail.groups.length} description groups need review</strong> · Review version {detail.version} · {detail.groups.reduce((total, group) => total + group.rows.length, 0)} active source rows</p>
      <details><summary>Workbook version</summary><p className="part-hash">{detail.sourceHash}</p></details>
      {!detail.schemaAvailable && <p role="alert">The Part review storage is unavailable.</p>}
      {!detail.associationKey && <p role="alert">Save a file association before saving Part assignments.</p>}
      <details className="part-create"><summary>Create a new Part</summary>
        <fieldset disabled={locked || !detail.schemaAvailable}><label>New Part name<input value={name} onChange={event => setName(event.target.value)} maxLength={200}/></label>
          <label>Reason for creating this Part<input value={createReason} onChange={event => setCreateReason(event.target.value)}/></label></fieldset>
        <button className="button" disabled={busy || loading || Boolean(mappingAttempt) || !detail.schemaAvailable || (!createAttempt && (!name.trim() || !createReason.trim()))} onClick={() => void create()}>{createAttempt ? "Retry Part creation" : "Create Part"}</button>
      </details>
      <label>Find a description<input value={query} onChange={event => setQuery(event.target.value)}/></label>
      <div className="part-table"><table><thead><tr><th>Machine Piece Description</th><th>Source rows</th><th>Canonical Part</th></tr></thead><tbody>{groups.map(group => <tr key={group.key}>
        <td><strong>{group.description || "Blank description — individual row"}</strong><small>{group.reviewed ? `Saved: ${group.part?.name}` : "Needs review"}</small></td>
        <td>{group.rows.map(row => <div key={`${row.sheet}:${row.row}`}>{row.sheet} · row {row.row}</div>)}</td>
        <td><select aria-label={`Part for ${group.description || `${group.rows[0].sheet} row ${group.rows[0].row}`}`} disabled={locked || !detail.schemaAvailable} value={decisions[group.key] || ""} onChange={event => setDecisions(old => { const next = { ...old }; if (event.target.value) next[group.key] = event.target.value; else delete next[group.key]; return next; })}>
          <option value="">{group.part ? `Keep ${group.part.name}` : "Select a Part"}</option>{parts.map(part => <option key={part.id} value={part.id}>{part.name}</option>)}</select></td>
      </tr>)}</tbody></table></div>
      <label>Reason for these assignments<textarea disabled={locked} value={reason} onChange={event => setReason(event.target.value)}/></label>
      <button className="button primary" disabled={busy || loading || Boolean(createAttempt) || !detail.schemaAvailable || !detail.associationKey || (!mappingAttempt && (!Object.keys(decisions).length || !reason.trim()))} onClick={() => void save()}>{mappingAttempt ? "Retry saving assignments" : `Save ${Object.keys(decisions).length} selected groups`}</button>
      <details className="part-history"><summary>Assignment history ({detail.history.length} source rows)</summary>{detail.history.map(row => <p key={row.ReviewKey}>Version {row.Version} · {row.SheetName} row {row.SourceRow} · {row.SourceDescription || "Blank description"} · {row.Actor} · {row.OccurredAt}<br/>{row.Reason}</p>)}</details>
    </>}
  </main>;
}
