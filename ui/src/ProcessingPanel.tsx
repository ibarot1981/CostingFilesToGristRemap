import { useEffect, useState } from "react";
import { api } from "./api";

type Event = { key: string; version: number; from_state: string; state: string; actor: string; reason: string; occurred_at: string; source_hash: string };
type ProcessingState = { state: string; effectiveState: string; version: number; sourceHash: string; sourceChanged: boolean; schemaAvailable: boolean; stateBasis: string; associationKey:string|null; associationVersion:number|null; history: Event[] };
const labels: Record<string,string> = { new:"New", associated:"Associated", extracted:"Extracted", mapping_review:"Mapping review", reconciliation_review:"Reconciliation review", ready_to_store:"Ready to store", processed:"Processed", changes_pending:"Changes pending" };
const choices: Record<string,string[]> = {new:[],associated:["associated","extracted","changes_pending"],extracted:["mapping_review","changes_pending"],mapping_review:["reconciliation_review","changes_pending"],reconciliation_review:["mapping_review","changes_pending"],ready_to_store:["reconciliation_review","changes_pending"],processed:["changes_pending"],changes_pending:["changes_pending","associated","extracted"]};

export function ProcessingPanel({path}: {path:string}) {
  const [detail,setDetail]=useState<ProcessingState|null>(null);
  const [reason,setReason]=useState("");
  const [next,setNext]=useState("");
  const [error,setError]=useState("");
  const [busy,setBusy]=useState(false);
  const [retry,setRetry]=useState<{key:string; payload:Record<string,unknown>}|null>(null);
  useEffect(()=>{
    let active=true;
    setDetail(null);setReason("");setNext("");setError("");setRetry(null);
    if(path) void (async()=>{
      try { const value=await api.processingState(path); if(active) setDetail(value as ProcessingState); }
      catch(cause) { if(active) setError(String(cause)); }
    })();
    return ()=>{active=false;};
  },[path]);
  const save=async()=>{
    if(!detail) return;
    const request=retry || {key:crypto.randomUUID(),payload:{path,state:next,reason,expectedVersion:detail.version,expectedHash:detail.sourceHash,expectedAssociationKey:detail.associationKey,expectedAssociationVersion:detail.associationVersion}};
    setRetry(request);setBusy(true);setError("");
    try {
      await api.changeProcessingState(request.payload,request.key);
      setDetail(await api.processingState(path) as ProcessingState);setReason("");setNext("");setRetry(null);
    } catch(cause) {setError(String(cause));}
    finally {setBusy(false);}
  };
  if(!path) return null;
  return <section className="saved-association" aria-label="File processing">
    <strong>File processing</strong>
    {error && <p role="alert">{error}</p>}
    {!detail ? <p>Loading processing history…</p> : <>
      <p>State: {labels[detail.effectiveState] || detail.effectiveState} · version {detail.version}</p>
      {detail.sourceChanged && <p>The source or association changed since the last recorded review.</p>}
      <small style={{overflowWrap:"anywhere"}}>Source revision: {detail.sourceHash}</small>
      <p>Costing authority is managed separately. Structural and Model Code configuration review must be complete before marking processed.</p>
      {!detail.schemaAvailable && <p>Processing history storage needs setup before recording a state.</p>}
      <fieldset className="processing-form" disabled={busy || !detail.schemaAvailable}>
        <label>Next processing state<select value={next} onChange={e=>{setNext(e.target.value);setRetry(null);}}>
          <option value="">Select state</option>{(choices[detail.effectiveState]||[]).map(s=><option key={s} value={s}>{labels[s]}</option>)}
        </select></label>
        <label>Processing reason<textarea value={reason} onChange={e=>{setReason(e.target.value);setRetry(null);}}/></label>
        <button className="button" disabled={!next || !reason.trim()} onClick={()=>void save()}>{busy?"Saving…":"Record processing state"}</button>
      </fieldset>
      <details><summary>Processing history ({detail.history.length})</summary>{detail.history.map(e=><div className="history-entry" key={e.key}>
        <span>{labels[e.from_state]} → {labels[e.state]} · {e.actor} · {e.occurred_at}</span><small>{e.reason}</small><small style={{overflowWrap:"anywhere"}}>{e.source_hash}</small>
      </div>)}</details>
    </>}
  </section>;
}
