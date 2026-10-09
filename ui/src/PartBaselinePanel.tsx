import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Check, ChevronDown, ChevronRight, LoaderCircle, Search } from "lucide-react";
import { api } from "./api";
import "./partBaseline.css";

type Part = { id: string; partNumber?: string | null; name: string; description?: string; variant?: string; engineeringRevision?: string | null; legacy?: boolean; status?: string; publishStatus?: string; selectable: boolean; duplicateName: boolean; outOfScopeCodes?: { id: string; code: string }[] };
type Group = { key: string; sheet: string; evidenceFingerprint: string; rows: unknown[]; description: string };
type Family = { family: string; sheet: string; sourceEvidenceStatus: string; mappedGroupCount: number; requirementCount: number; applicabilityStatus: string; completenessConfirmed: boolean };
type Review = { baselineStatus: string; source: { fileId: string; workbookPath: string; sourceHash: string; associationKey: string; associationVersion: number; mappingVersion: number; mappingPolicyVersion: string }; families: Record<string, Family>; existingContent: { processRequirements: number; components: number; purchaseSpecifications: number; requiresAppendConfirmation: boolean } };
type Comparison = { comparison: Record<string, any>; families: Record<string, any>[]; differences: Record<string, any>[]; decisions: Record<string, any>[]; proposals: Record<string, any>[]; evidenceWarnings?: string[] };
type Attempt = { operation: "establish" | "compare"; key: string; partId: string; payload: Record<string, unknown> };
type DecisionAttempt = { comparisonKey: string; partId: string; action: string; differenceKeys: string[]; payload: Record<string, unknown>; key: string; status: "saving" | "failed"; replacement?: Part };

const familyOrder = ["mcl", "toolshop", "cnc"];
const familyNames: Record<string, string> = { mcl: "Material Cut List", toolshop: "Toolshop", cnc: "CNC" };
const attemptStorageKey = (path: string, partId: string) => `part-baseline-attempt:${path}:${partId}`;
const decisionAttemptStorageKey = (path: string, partId: string) => `part-baseline-decision-attempt:${path}:${partId}`;
const errorCode = (cause: unknown) => cause && typeof cause === "object" && "code" in cause
  ? String((cause as { code: unknown }).code) : "";
const errorDisposition = (cause: unknown) => cause && typeof cause === "object" && "retryDisposition" in cause
  ? String((cause as { retryDisposition: unknown }).retryDisposition) : "retry_same_request";
const parsed = (value: unknown): any => {
  if (typeof value !== "string") return value || {};
  try { return JSON.parse(value); } catch { return {}; }
};
const pretty = (value: unknown) => {
  if (!value) return "No values";
  if (typeof value === "string") { try { return JSON.stringify(JSON.parse(value), null, 2); } catch { return value; } }
  return JSON.stringify(value, null, 2);
};

export function PartBaselinePanel({ path, parts, groups, onUseDifferentPart }: { path: string; parts: Part[]; groups: Group[]; onUseDifferentPart: (groupKey: string, part: Part) => void }) {
  const choices = useMemo(() => [...new Map(parts.filter(part => !part.legacy && part.status !== "retired").map(part => [part.id, part])).values()], [parts]);
  const [open, setOpen] = useState(false);
  const [partId, setPartId] = useState("");
  const [review, setReview] = useState<Review | null>(null);
  const [families, setFamilies] = useState<Record<string, { status: string; confirmedComplete: boolean }>>({});
  const [appendExisting, setAppendExisting] = useState(false);
  const [reason, setReason] = useState("");
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [decisionAttempt, setDecisionAttempt] = useState<DecisionAttempt | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [decisionReason, setDecisionReason] = useState("");
  const [oldData, setOldData] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!choices.some(part => part.id === partId)) setPartId(choices[0]?.id || "");
  }, [choices, partId]);
  useEffect(() => {
    setReview(null); setComparison(null); setError(""); setNotice("");
    setAttempt(null); setDecisionAttempt(null);
    if (!partId || !open) return;
    let restoredWrite: Attempt | null = null;
    let restoredDecision: DecisionAttempt | null = null;
    try { restoredWrite = JSON.parse(localStorage.getItem(attemptStorageKey(path, partId)) || "null"); } catch { /* Keep an invalid local record out of the write path. */ }
    try { restoredDecision = JSON.parse(localStorage.getItem(decisionAttemptStorageKey(path, partId)) || "null"); } catch { /* Keep an invalid local record out of the write path. */ }
    if (restoredDecision) {
      restoredDecision.status = "failed";
      localStorage.setItem(decisionAttemptStorageKey(path, partId), JSON.stringify(restoredDecision));
    }
    setAttempt(restoredWrite);
    setDecisionAttempt(restoredDecision);
    if (restoredDecision) {
      setDecisionReason(String(restoredDecision.payload.reason || ""));
      setOldData(Boolean(restoredDecision.payload.oldData));
    }
    if (restoredWrite || restoredDecision) {
      setBusy(true);
      void Promise.all([
        api.partBaselineReview(partId, path),
        restoredDecision ? api.partManufacturingComparison(restoredDecision.comparisonKey) : Promise.resolve(null),
      ]).then(([nextReview, savedComparison]) => {
        setReview(nextReview as Review);
        setFamilies(Object.fromEntries(Object.entries((nextReview as Review).families || {}).map(([key, family]) => [key, {
          status: family.applicabilityStatus === "unconfirmed" ? "" : family.applicabilityStatus,
          confirmedComplete: false,
        }])));
        if (savedComparison) setComparison(savedComparison as Comparison);
      }).catch(cause => setError(String(cause))).finally(() => setBusy(false));
    }
  }, [path, partId, open]);

  async function loadReview() {
    if (!partId) return;
    setBusy(true); setError(""); setNotice(""); setComparison(null);
    try {
      const result = await api.partBaselineReview(partId, path) as Review;
      setReview(result);
      setFamilies(Object.fromEntries(Object.entries(result.families || {}).map(([key, family]) => [key, {
        status: family.applicabilityStatus === "unconfirmed" ? "" : family.applicabilityStatus,
        confirmedComplete: false,
      }])));
      setAppendExisting(false);
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }

  function makePayload(operation: Attempt["operation"]) {
    if (!review) return null;
    return { path, expectedHash: review.source.sourceHash, expectedAssociationKey: review.source.associationKey,
      expectedAssociationVersion: review.source.associationVersion, expectedMappingVersion: review.source.mappingVersion,
      sourceFamilies: families, appendExisting, reason: reason.trim() };
  }

  const establishmentIssues = useMemo(() => {
    if (!review) return [];
    const issues: string[] = [];
    for (const key of familyOrder) {
      const family = review.families[key];
      const choice = families[key] || { status: "", confirmedComplete: false };
      const label = family?.sheet || familyNames[key];
      if (!family || family.sourceEvidenceStatus !== "ok") {
        issues.push(`${label}: source evidence is missing or unreadable.`);
        continue;
      }
      if (!["applicable", "not_applicable"].includes(choice.status)) {
        issues.push(`${label}: choose Applicable or Confirmed not applicable.`);
      } else if (choice.status === "applicable") {
        if (!family.mappedGroupCount) issues.push(`${label}: map at least one current source group to this Part.`);
        else if (!choice.confirmedComplete) issues.push(`${label}: confirm that all applicable rows are mapped and complete.`);
      } else {
        if (family.mappedGroupCount) issues.push(`${label}: this Part has mapped rows and cannot be marked not applicable.`);
        else if (!choice.confirmedComplete) issues.push(`${label}: confirm that this family does not apply.`);
      }
    }
    if (review.existingContent.requiresAppendConfirmation && !appendExisting) {
      issues.push("Review and confirm that existing Rev A content will be preserved before appending workbook lines.");
    }
    return issues;
  }, [review, families, appendExisting]);

  async function runWrite(operation: Attempt["operation"]) {
    if (!review || !partId || busy) return;
    let current = attempt;
    if (!current) {
      if (operation === "establish" && establishmentIssues.length) {
        setError("Complete the source-family and existing-content confirmations before establishing this baseline.");
        return;
      }
      const payload = makePayload(operation);
      if (!payload) return;
      current = { operation, key: crypto.randomUUID(), partId, payload };
      localStorage.setItem(attemptStorageKey(path, partId), JSON.stringify(current));
      setAttempt(current);
    }
    if (current.partId !== partId || current.operation !== operation) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = operation === "establish"
        ? await api.establishPartBaseline(partId, current.payload, current.key)
        : await api.comparePartBaseline(partId, current.payload, current.key);
      setAttempt(null); localStorage.removeItem(attemptStorageKey(path, partId));
      if (operation === "compare") setComparison(result as Comparison);
      const completedNotice = operation === "establish"
        ? "Initial manufacturing baseline is published in Grist. Review the accepted Rev A definition in Part details, then finalize it there when ready."
        : "Workbook evidence was saved and compared with the accepted Part baseline. The baseline and Rev A definition remain unchanged.";
      await loadReview();
      setNotice(completedNotice);
      if (operation === "compare") setComparison(result as Comparison);
    } catch (cause) {
      if (["safe_to_edit", "refresh_required"].includes(errorDisposition(cause))) {
        setAttempt(null); localStorage.removeItem(attemptStorageKey(path, partId));
        if (errorDisposition(cause) === "refresh_required") setReview(null);
      }
      setError(String(cause));
    }
    finally { setBusy(false); }
  }

  function clearDecisionAttempt() {
    setDecisionAttempt(null);
    localStorage.removeItem(decisionAttemptStorageKey(path, partId));
  }

  async function sendDecisionAttempt(current: DecisionAttempt) {
    if (!comparison || busy || current.partId !== partId) return;
    setDecisionAttempt(current);
    localStorage.setItem(decisionAttemptStorageKey(path, partId), JSON.stringify(current));
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await api.decidePartManufacturingComparison(current.comparisonKey, current.payload, current.key) as Comparison & { replacementMapping?: Record<string, any>; idempotent?: boolean };
      clearDecisionAttempt();
      setComparison(result);
      const recovered = Boolean(result.idempotent);
      setNotice(recovered ? "The saved decision was recovered from its original request." : current.action === "propose_part_change"
        ? "Change proposal is pending approved Change Request processing. The Rev A baseline was not changed."
        : current.action === "keep_existing_baseline"
          ? Boolean(current.payload.oldData) ? "The incoming workbook was recorded as old data; its observed differences remain preserved." : "The existing accepted baseline was retained and incoming differences remain preserved."
          : "The decision was saved. Any replacement mapping draft remains separate from the Part baseline.");
      if (current.action === "use_different_part" && current.replacement && result.replacementMapping?.groupKey) {
        onUseDifferentPart(String(result.replacementMapping.groupKey), current.replacement);
      }
  } catch (cause) {
      if (["safe_to_edit", "refresh_required"].includes(errorDisposition(cause))) {
        clearDecisionAttempt();
        if (errorDisposition(cause) === "refresh_required") { setComparison(null); setReview(null); }
        else if (["PART_DECISION_ALREADY_RESOLVED", "PART_MATCH_CANDIDATE_ALREADY_USED", "PART_MATCH_SOURCE_ALREADY_USED"].includes(errorCode(cause))) {
          try { setComparison(await api.partManufacturingComparison(current.comparisonKey) as Comparison); }
          catch { setComparison(null); }
        }
      } else {
        const failed = { ...current, status: "failed" as const };
        setDecisionAttempt(failed);
        localStorage.setItem(decisionAttemptStorageKey(path, partId), JSON.stringify(failed));
      }
      setError(String(cause));
    } finally { setBusy(false); }
  }

  async function decide(difference: Record<string, any>, action: string, replacement?: Part, baselineRequirementKey?: string) {
    if (!comparison || busy || decisionAttempt || !decisionReason.trim()) return;
    const groupKey = String(difference.MappingGroupKey || "");
    const group = groups.find(item => item.key === groupKey);
    const payload = { action, differenceKeys: [difference.DifferenceKey], reason: decisionReason.trim(), oldData,
      path, groupKey, evidenceFingerprint: group?.evidenceFingerprint || "", replacementPartId: replacement?.id || "",
      baselineRequirementKey: baselineRequirementKey || "" };
    const saved: DecisionAttempt = { comparisonKey: String(comparison.comparison.ComparisonKey), partId,
      action, differenceKeys: [String(difference.DifferenceKey)], payload, key: crypto.randomUUID(), status: "saving", replacement };
    await sendDecisionAttempt(saved);
  }

  if (!path) return null;
  const selected = choices.find(part => part.id === partId);
  return <section className="part-baseline-panel" aria-label="Part manufacturing baseline and workbook comparison">
    <button className="part-baseline-toggle" type="button" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      {open ? <ChevronDown size={16}/> : <ChevronRight size={16}/>}<span><strong>Manufacturing baseline &amp; workbook comparison</strong><small>Explicitly establish Rev A requirements or compare another mapped workbook.</small></span>
    </button>
    {open && <div className="part-baseline-content">
      <div className="part-baseline-toolbar"><label>Canonical Part<select value={partId} disabled={busy || Boolean(attempt) || Boolean(decisionAttempt) || !choices.length} onChange={event => { setPartId(event.target.value); setReview(null); setComparison(null); }}><option value="">Choose a saved mapping</option>{choices.map(part => <option key={part.id} value={part.id}>{part.partNumber} · {part.name}</option>)}</select></label>
        <button className="button" disabled={!partId || busy || Boolean(attempt) || Boolean(decisionAttempt)} onClick={() => void loadReview()}>{busy ? <LoaderCircle size={13}/> : <Search size={13}/>} Review this workbook</button></div>
      {!choices.length && <p className="part-baseline-muted">Save at least one source-group mapping to a canonical Part before baseline processing.</p>}
      {error && <div className="part-baseline-error" role="alert"><AlertTriangle size={14}/><span>{error}</span></div>}
      {notice && <p role="status" className="part-baseline-notice"><Check size={14}/>{notice}</p>}
      {review && selected && <>
        <div className="part-baseline-status"><span>Part · {selected.partNumber} · Rev A</span><strong>{review.baselineStatus.replaceAll("_", " ")}</strong>
          <small>Workbook · {review.source.workbookPath} · SHA-256 {review.source.sourceHash}</small></div>
        {review.baselineStatus === "recovery_required" && <p role="alert" className="part-baseline-warning">An earlier baseline publication stopped partway through. {attempt?.operation === "establish" ? "Retry the exact saved request to resume." : "This browser does not have the original request identity, so another baseline publication is blocked. Recover the original request from the browser that started it."}</p>}
        {review.existingContent.requiresAppendConfirmation && <div className="part-baseline-warning"><strong>Existing Rev A content will be preserved.</strong><span>{review.existingContent.processRequirements} process requirements · {review.existingContent.components} child Parts · {review.existingContent.purchaseSpecifications} purchase specifications</span><label><input type="checkbox" checked={appendExisting} disabled={busy || Boolean(attempt) || Boolean(decisionAttempt)} onChange={event => setAppendExisting(event.target.checked)}/> Append workbook-derived lines without replacing existing content.</label></div>}
        {review.baselineStatus !== "established" && !attempt && establishmentIssues.length > 0 && <div className="part-baseline-validation" role="status"><strong>Before establishing the baseline:</strong><ul>{establishmentIssues.map(issue => <li key={issue}>{issue}</li>)}</ul></div>}
        <div className="part-baseline-families"><h3>Confirm applicable source families</h3>{familyOrder.map(key => {
          const family = review.families[key]; const choice = families[key] || { status: "", confirmedComplete: false };
          const readable = family?.sourceEvidenceStatus === "ok";
          return <article className="part-baseline-family" key={key}><div><strong>{family?.sheet || familyNames[key]}</strong><span>{readable ? `${family.mappedGroupCount} mapped groups · ${family.requirementCount} mapped requirement rows` : `Evidence ${family?.sourceEvidenceStatus?.replaceAll("_", " ") || "missing"}`}</span></div>
            <label>Applicability<select value={choice.status} disabled={busy || Boolean(attempt) || Boolean(decisionAttempt) || !readable} onChange={event => setFamilies(old => ({ ...old, [key]: { ...choice, status: event.target.value, confirmedComplete: false } }))}>
              <option value="">Choose…</option><option value="applicable">Applicable</option><option value="not_applicable" disabled={(family?.mappedGroupCount || 0) > 0}>Confirmed not applicable</option></select></label>
            {choice.status === "applicable" && <label className="part-baseline-confirm"><input type="checkbox" checked={choice.confirmedComplete} disabled={busy || Boolean(attempt) || Boolean(decisionAttempt) || !readable || !family?.mappedGroupCount} onChange={event => setFamilies(old => ({ ...old, [key]: { ...choice, confirmedComplete: event.target.checked } }))}/>All applicable rows for this Part are mapped and complete</label>}
            {choice.status === "not_applicable" && <label className="part-baseline-confirm"><input type="checkbox" checked={choice.confirmedComplete} disabled={busy || Boolean(attempt) || Boolean(decisionAttempt)} onChange={event => setFamilies(old => ({ ...old, [key]: { ...choice, confirmedComplete: event.target.checked } }))}/>I confirm this source family does not apply</label>}
            {readable && choice.status === "applicable" && !family?.mappedGroupCount && <small className="part-baseline-warning-text">Applicable but incomplete: map this Part’s rows before establishing the baseline.</small>}
          </article>;
        })}</div>
        <label className="part-baseline-reason">Processing reason<input value={reason} disabled={busy || Boolean(attempt) || Boolean(decisionAttempt)} onChange={event => setReason(event.target.value)} placeholder="Optional audit context"/></label>
        <div className="part-baseline-actions">{attempt?.operation === "establish" || review.baselineStatus !== "established"
          ? <button className="button primary" disabled={busy || Boolean(decisionAttempt) || (attempt !== null && attempt.operation !== "establish") || (!attempt && (establishmentIssues.length > 0 || review.baselineStatus === "recovery_required"))} onClick={() => void runWrite("establish")}>{attempt?.operation === "establish" ? "Retry baseline publication" : "Establish Part baseline"}</button>
          : <button className="button primary" disabled={busy || Boolean(decisionAttempt) || (attempt !== null && attempt.operation !== "compare")} onClick={() => void runWrite("compare")}>{attempt?.operation === "compare" ? "Retry comparison" : "Compare incoming workbook"}</button>}
          {attempt && <small>Outcome is unresolved. Retry uses the original request identity and exact payload before another operation can start.</small>}
        </div>
      </>}
      {comparison && <ComparisonReview comparison={comparison} path={path} groups={groups} onDecision={decide} onRetryDecision={() => decisionAttempt && void sendDecisionAttempt(decisionAttempt)} decisionAttempt={decisionAttempt} reason={decisionReason} setReason={setDecisionReason} oldData={oldData} setOldData={setOldData} busy={busy}/>}
      {decisionAttempt && !comparison && <div className="part-baseline-warning" role="status">A saved comparison decision has an unresolved outcome. The comparison is being restored so its original request can be retried.</div>}
    </div>}
  </section>;
}

function ComparisonReview({ comparison, path, groups, onDecision, onRetryDecision, decisionAttempt, reason, setReason, oldData, setOldData, busy }: { comparison: Comparison; path: string; groups: Group[]; onDecision: (difference: Record<string, any>, action: string, replacement?: Part, baselineRequirementKey?: string) => Promise<void>; onRetryDecision: () => void; decisionAttempt: DecisionAttempt | null; reason: string; setReason: (value: string) => void; oldData: boolean; setOldData: (value: boolean) => void; busy: boolean }) {
  const summary = comparison.comparison;
  const [replacements, setReplacements] = useState<Record<string, Part>>({});
  return <section className="part-comparison-review"><h3>Workbook comparison · {String(summary.Status || "").replaceAll("_", " ")}</h3>
    <p>{summary.WorkbookName} · incoming {summary.SourceHash} · baseline {summary.BaselineSourceHash}</p>
    {decisionAttempt && <div className="part-decision-attempt" role="status"><strong>{decisionAttempt.status === "saving" ? "Saving decision…" : "Decision outcome is uncertain"}</strong><span>{decisionAttempt.action.replaceAll("_", " ")} · {decisionAttempt.differenceKeys.length} difference(s) · request {decisionAttempt.key}</span>
      {decisionAttempt.status === "failed" && <p>The original payload and request identity are saved on this device. Retry to recover or finish the same Grist decision before editing another one.</p>}
      <button className="button primary" disabled={busy} onClick={onRetryDecision}>{busy ? "Saving decision…" : "Retry saved decision"}</button>
    </div>}
    <div className="part-comparison-counts">{[["Matches", summary.MatchCount], ["Additions", summary.AdditionCount], ["Modifications", summary.ModificationCount], ["Deletions", summary.DeletionCount], ["Ambiguous / incomplete", summary.AmbiguousCount]].map(([label, value]) => <span key={String(label)}><strong>{String(value ?? 0)}</strong>{label}</span>)}</div>
    {!!comparison.evidenceWarnings?.length && <ul className="part-baseline-warning-list">{comparison.evidenceWarnings.map(item => <li key={item}>{item}</li>)}</ul>}
    {comparison.differences.filter(item => item.DifferenceType !== "match").map(difference => {
      const candidateKeys = parsed(difference.CandidateBaselineLines);
      const baselineCandidates = (Array.isArray(candidateKeys) ? candidateKeys : []).flatMap((key: string) => {
        const candidate = comparison.differences.find(item => item.BaselineRequirementKey === key && item.DifferenceType === "ambiguous_correspondence" && item.BaselineRevisionLine);
        if (!candidate) return [];
        const physical = parsed(candidate.BaselineValues)?.physical || {};
        const identity = [physical.material, physical.dimension, physical.item_code, physical.item_name].filter(Boolean).join(" · ");
        return [{key, label: `${identity || "Baseline requirement"} · Qty ${physical.quantity ?? "?"}`}];
      });
      return <DifferenceCard key={difference.DifferenceKey} difference={difference} path={path} groups={groups} baselineCandidates={baselineCandidates}
        selected={replacements[difference.DifferenceKey]} onSelect={part => setReplacements(old => ({ ...old, [difference.DifferenceKey]: part }))}
        onDecision={onDecision} reason={reason} oldData={oldData} busy={busy || Boolean(decisionAttempt)} complete={summary.Status === "complete"}/>;
    })}
    {comparison.differences.every(item => item.DifferenceType === "match") && <p>No manufacturing requirement differences were found. The comparison was saved as source evidence.</p>}
    {!!comparison.differences.some(item => item.DifferenceType !== "match" && item.DifferenceType !== "incomplete_evidence") && <div className="part-comparison-decision-tools"><label>Decision reason<input value={reason} onChange={event => setReason(event.target.value)} disabled={busy || Boolean(decisionAttempt)} placeholder="Required for every decision"/></label><label><input type="checkbox" checked={oldData} onChange={event => setOldData(event.target.checked)} disabled={busy || Boolean(decisionAttempt)}/>Classify this incoming workbook as old data</label></div>}
  </section>;
}

function DifferenceCard({ difference, path, groups, baselineCandidates, selected, onSelect, onDecision, reason, oldData, busy, complete }: { difference: Record<string, any>; path: string; groups: Group[]; baselineCandidates: {key: string; label: string}[]; selected?: Part; onSelect: (part: Part) => void; onDecision: (difference: Record<string, any>, action: string, replacement?: Part, baselineRequirementKey?: string) => Promise<void>; reason: string; oldData: boolean; busy: boolean; complete: boolean }) {
  const hasIncoming = Boolean(difference.IncomingObservation);
  const canPropose = complete && ["proposed_addition", "proposed_modification", "proposed_deletion"].includes(difference.DifferenceType);
  const group = groups.find(item => item.key === difference.MappingGroupKey);
  const [matchedBaselineKey, setMatchedBaselineKey] = useState("");
  return <article className="part-difference-card"><header><strong>{String(difference.DifferenceType || "difference").replaceAll("_", " ")} · {difference.Family}</strong><span>{difference.SourceSheet}{difference.SourceRow ? ` · row ${difference.SourceRow}` : ""}</span></header>
    <div className="part-difference-values"><div><strong>Accepted baseline</strong><pre>{pretty(difference.BaselineValues)}</pre></div><div><strong>Incoming workbook</strong><pre>{pretty(difference.IncomingValues)}</pre><small>{pretty(difference.SourceCellEvidence)}</small></div></div>
    {difference.CandidateBaselineLines && <p>Possible baseline matches · {pretty(difference.CandidateBaselineLines)}</p>}
    {difference.FieldDifferences && <details><summary>Compared engineering fields</summary><pre>{pretty(difference.FieldDifferences)}</pre></details>}
    {difference.Status !== "pending_review" && <p className="part-baseline-notice">Decision saved · {String(difference.Status).replaceAll("_", " ")}</p>}
    {difference.DifferenceType === "incomplete_evidence" && <p className="part-baseline-warning">The source family is incomplete. No addition or deletion is inferred until its evidence is complete.</p>}
    {difference.Status === "pending_review" && difference.DifferenceType === "ambiguous_correspondence" && hasIncoming && complete && baselineCandidates.length > 0 && <div className="part-ambiguous-match">
      <label>Match this incoming row to a baseline requirement<select value={matchedBaselineKey} onChange={event => setMatchedBaselineKey(event.target.value)} disabled={busy}>
        <option value="">Choose a possible baseline line…</option>{baselineCandidates.map(candidate => <option key={candidate.key} value={candidate.key}>{candidate.label}</option>)}
      </select></label><button className="button" disabled={busy || !reason.trim() || !matchedBaselineKey}
        onClick={() => void onDecision(difference, "match_correspondence", undefined, matchedBaselineKey)}>Match to baseline requirement</button>
    </div>}
    {difference.Status === "pending_review" && difference.DifferenceType !== "incomplete_evidence" && <div className="part-difference-actions">
      <button className="button" disabled={busy || !reason.trim()} onClick={() => void onDecision(difference, "keep_existing_baseline")}>Keep existing baseline</button>
      {canPropose && <button className="button" disabled={busy || !reason.trim()} onClick={() => void onDecision(difference, "propose_part_change")}>Propose Part change</button>}
      {hasIncoming && group && <><PartReplacementLookup path={path} disabled={busy} onSelect={onSelect}/><button className="button" disabled={busy || !reason.trim() || !selected} onClick={() => selected && void onDecision(difference, "use_different_part", selected)}>Use a different Part</button></>}
    </div>}
  </article>;
}

function PartReplacementLookup({ path, disabled, onSelect }: { path: string; disabled: boolean; onSelect: (part: Part) => void }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Part[]>([]);
  const [resultsFor, setResultsFor] = useState("");
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    const search = query.trim();
    if (!search) { setResults([]); setResultsFor(""); setLoading(false); return; }
    const controller = new AbortController(); setLoading(true); setResultsFor("");
    const timer = window.setTimeout(() => void api.searchParts(search, 0, 30, path, controller.signal).then(result => {
      setResults(result.items || []); setResultsFor(search);
    }).catch(() => { if (!controller.signal.aborted) { setResults([]); setResultsFor(search); } }).finally(() => { if (!controller.signal.aborted) setLoading(false); }), 180);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [query, path]);
  return <div className="part-replacement-lookup"><label>Replacement canonical Part<input value={query} disabled={disabled} onChange={event => setQuery(event.target.value)} placeholder="Search Part number, name or alias"/></label>
    {loading && <small role="status">Searching…</small>}{!loading && resultsFor === query.trim() && results.length === 0 && <small>No selectable Parts found.</small>}
    {resultsFor === query.trim() && results.length > 0 && <ul role="listbox">{results.filter(item => item.selectable && !item.duplicateName).map(part => <li key={part.id}><button type="button" role="option" disabled={disabled} onClick={() => { onSelect(part); setQuery(`${part.partNumber} · ${part.name}`); setResults([]); setResultsFor(""); }}>{part.partNumber} · {part.name}</button></li>)}</ul>}
  </div>;
}
