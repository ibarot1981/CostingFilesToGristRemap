import {useMemo, useState} from "react";
import {createRoot} from "react-dom/client";
import {api} from "../src/api";
import {PartMappingView} from "../src/PartMappingView";
import {PartsView} from "../src/PartsView";
import "../src/styles.css";

const STORAGE = "isolated-grist-part-line-records";
const workbookPath = "isolated-line-assignments.ods";
type FixturePart = {id:string; partNumber:string; name:string; description:string; variant:string; selectable:boolean; duplicateName:boolean;
  engineeringRevision:string; legacy:boolean; status:string; scope:string; scopeTargetId:string; scopeTarget:string; shortcode:string;
  aliases:string[]; outOfScopeCodes:any[]};
const chassis:FixturePart = {id:"fixture-chassis",partNumber:"SM-P-900001",name:"S1KHF — Chassis",description:"Chassis",variant:"Standard",
  selectable:true,duplicateName:false,engineeringRevision:"A",legacy:false,status:"active",scope:"product_model",scopeTargetId:"model-1",
  scopeTarget:"Safari 1000 HF",shortcode:"S1KHF",aliases:[],outOfScopeCodes:[]};
const bracket:FixturePart = {...chassis,id:"fixture-bracket",partNumber:"SM-P-900002",name:"S1KHF — Bracket",description:"Bracket",variant:"Folded"};
type SavedLine = {partId:string|null; reviewed:boolean; actionType:string; reason:string; version:number};
type FixtureServer = {version:number; writes:number; lineAssignments:Record<string,SavedLine>; history:any[]; parts:FixturePart[]};
function readServer():FixtureServer {
  try { const value = JSON.parse(sessionStorage.getItem(STORAGE) || "null"); if (value) return value as FixtureServer; } catch { /* start with an empty isolated Grist fixture */ }
  return {version:0,writes:0,lineAssignments:{},history:[],parts:[chassis,bracket]};
}
let server = readServer();
let mappingReads = 0;
let changedLineEvidence = false;
let pendingCreate: (() => void) | null = null;
let notify: (() => void) | null = null;
function persistServer() { sessionStorage.setItem(STORAGE,JSON.stringify(server)); notify?.(); }
function lineKey(groupKey:string,row:number) { return `${groupKey}:line:${row}`; }
function sourceRow(groupKey:string,sheet:string,row:number,description:string,quantity:string) {
  const key = lineKey(groupKey,row);
  return {sheet,row,lineKey:key,semanticEvidence:{fingerprint:`semantic-${key}${changedLineEvidence && row === 13 ? "-changed" : ""}`},
    fields:{product_part_name:description,part_category:description,material_to_cut:"MS Plate",dimension_to_cut_mm:"3 x 25",qty:quantity,
      optional_item_group_1:"HF",in_use:"Yes",item_name:description,toolshop_remarks:"Isolated browser line"},
    sourceHeaders:{product_part_name:"Plate Part to Cut",part_category:"Part Category",material_to_cut:"Material to Cut",
      dimension_to_cut_mm:"Dimension to Cut (mm)",qty:"Quantity",item_name:"Item Name / Material",toolshop_remarks:"Remarks"},
    sourceHeaderCells:{product_part_name:"A8",part_category:"B8",material_to_cut:"C8",dimension_to_cut_mm:"D8",qty:"E8",item_name:"A7",toolshop_remarks:"F7"},
    sourceCells:{qty:{cell:`E${row}`},material_to_cut:{cell:`C${row}`}},headerRow:sheet === "Tool Shop Items" ? 7 : 8,
    availableFields:["product_part_name","part_category","material_to_cut","dimension_to_cut_mm","qty","item_name","toolshop_remarks"]};
}
function group(key:string,sheet:string,description:string,rows:any[]) {
  const assigned = rows.map(row => ({row,saved:server.lineAssignments[row.lineKey]}));
  const mapped = assigned.filter(item => item.saved?.partId);
  const partIds = [...new Set(mapped.map(item => item.saved!.partId!))];
  const allReviewed = assigned.length > 0 && assigned.every(item => item.saved?.reviewed);
  return {key,mappingPolicyVersion:"part-source-labels-v2",evidenceFingerprint:`browser-group-evidence-${key}`,
    semanticFingerprint:`browser-semantic-${key}`,sheet,labelField:sheet === "CNC Cut List" ? "part_category" : "product_part_name",
    description,blankDescription:false,rows:assigned.map(({row,saved}) => ({...row,part:saved?.partId ? server.parts.find(part => part.id === saved.partId) || null : null,
      reviewed:Boolean(saved?.reviewed),explicitlyUnassigned:Boolean(saved?.reviewed && !saved?.partId)})),
    part:allReviewed && mapped.length === rows.length && partIds.length === 1 ? server.parts.find(part => part.id === partIds[0]) || null : null,
    reviewed:allReviewed,explicitlyUnassigned:allReviewed && mapped.length === 0,version:server.version,
    assignmentSummary:{partCount:partIds.length,mappedLineCount:mapped.length,unassignedLineCount:rows.length-mapped.length},
    semanticComparison:{status:"no_prior_evidence",differences:[],requiresReview:false}};
}
function mappingDetail() {
  mappingReads += 1;
  notify?.();
  const mclRows = [10,11,12,13].map((row,index) => sourceRow("mcl-chassis","5. Material Cut List Price",row,`Plate ${index+1}`,["2","1","4","3"][index]));
  const toolRows = [21,22].map((row,index) => sourceRow("tool-jig","Tool Shop Items",row,`Jig ${index+1}`,["1","2"][index]));
  const groups = [group("mcl-chassis","5. Material Cut List Price","Chassis",mclRows),group("tool-jig","Tool Shop Items","Inspection jig",toolRows)];
  return {sourceHash:"isolated-source-hash",associationKey:"fixture-association",associationVersion:1,version:server.version,schemaAvailable:true,
    legacyHistoryAvailable:true,mappingPolicyVersion:"part-source-labels-v2",semanticPolicyVersion:"part-mapping-semantic-v1",
    sourceSheets:{"5. Material Cut List Price":{present:true,status:"ok",labelField:"product_part_name",labelHeader:"Machine Piece Description",labelHeaderCell:"A8",ambiguousLabelHeaders:[],headerRow:8},
      "Tool Shop Items":{present:true,status:"ok",labelField:"product_part_name",labelHeader:"Machine Piece Description",labelHeaderCell:"A7",ambiguousLabelHeaders:[],headerRow:7}},
    parts:server.parts,groups,unresolvedGroups:groups.filter(item => !item.reviewed).length,
    unresolvedLines:groups.reduce((sum,item) => sum + item.rows.filter((row:any) => !row.reviewed).length,0),history:server.history,removedGroups:[]};
}
const scopes = {scopes:[
  {id:"global",label:"Global",target:{id:"global",label:"Safari Manufacturing",shortcode:"SM"}},
  {id:"product",label:"Product",targets:[{id:"product-1",label:"Safari 1000",shortcode:"S1K"}]},
  {id:"product_model",label:"Product Model",targets:[{id:"model-1",parentId:"product-1",label:"Safari 1000 HF",shortcode:"S1KHF"}]},
  {id:"model_code",label:"Model Code",targets:[{id:"code-1",parentId:"model-1",label:"S1KHFELP"}]},
]};
const fixtureApi = api as any;
fixtureApi.parts = async (query:string = "") => ({items:server.parts.filter(part => !query || `${part.partNumber} ${part.name}`.toLowerCase().includes(query.toLowerCase())),legacyItems:[],storage:"Isolated Grist fixture"});
fixtureApi.partDetails = async (partId:string) => { const part = server.parts.find(item => item.id === partId) || chassis; return {part:{...part,compositionStatus:"draft",metadataHistory:[],lifecycleHistory:[]},
  mappingHistory:[],manufacturingBaseline:{status:"not_established",families:[],requirements:[]},intendedSharing:{status:"available",version:0,items:[],history:[]},
  processLines:{status:"available",items:[]},components:{status:"available",items:[]},purchases:{specifications:[],vendorCatalog:[]},drawings:{status:"empty",items:[]},usedIn:{status:"available",items:[]}}; };
fixtureApi.partScopeTargets = async () => scopes;
fixtureApi.fileAssociation = async () => ({current:{product_id:"product-1",model_id:"model-1"},product:{id:"product-1"},model:{id:"model-1"},codes:[{id:"code-1"}]});
fixtureApi.partNamePreview = async (_scope:string,_target:string,description:string,variant:string) => ({name:`S1KHF — ${description}${variant ? ` — ${variant}` : ""}`,available:true,collision:[],usageFingerprint:"isolated-origin"});
fixtureApi.searchParts = async (query:string = "") => {
  const items = server.parts.filter(part => !query || `${part.id} ${part.partNumber} ${part.name}`.toLowerCase().includes(query.toLowerCase()));
  return {items,total:items.length,offset:0,limit:30,hasMore:false};
};
fixtureApi.partMappings = async () => mappingDetail();
fixtureApi.savePartMappings = async (payload:any) => {
  if (payload.expectedVersion !== server.version || payload.expectedHash !== "isolated-source-hash"
    || payload.expectedAssociationKey !== "fixture-association" || payload.expectedAssociationVersion !== 1) {
    throw Object.assign(new Error("PART_REVIEW_STALE: isolated fixture mapping changed"),{code:"PART_REVIEW_STALE",retryDisposition:"safe_to_refresh"});
  }
  const targetChoices:Record<string,string> = {...(payload.lineDecisions || {})};
  for (const [key,partId] of Object.entries(payload.decisions || {})) {
    for (const row of [...[10,11,12,13].map(number => lineKey("mcl-chassis",number)),...[21,22].map(number => lineKey("tool-jig",number))]) {
      if (row.startsWith(`${key}:line:`)) targetChoices[row] = String(partId || "");
    }
  }
  const lineAssignments:Record<string,any> = {};
  for (const [key,value] of Object.entries(targetChoices)) {
    const old = server.lineAssignments[key];
    if (old?.partId && old.partId !== value && !(payload.lineReasons?.[key] || payload.reasons?.[key.split(":line:")[0]] || "").trim()) {
      throw Object.assign(new Error("A reason is required for replacing an existing line assignment"),{code:"PART_MAPPING_REASON_REQUIRED"});
    }
    const record = {partId:value || null,reviewed:true,actionType:value ? (old?.partId ? "replacement_assignment" : "initial_assignment") : "clear_assignment",
      reason:payload.lineReasons?.[key] || payload.reasons?.[key.split(":line:")[0]] || "Initial assignment",version:server.version + 1};
    lineAssignments[key] = record;
  }
  server.version += 1; server.writes += 1;
  server.lineAssignments = {...server.lineAssignments,...lineAssignments};
  for (const [key,record] of Object.entries(lineAssignments)) server.history.push({ReviewKey:`isolated-review-${server.history.length+1}`,
    Version:server.version,StablePartId:record.partId,SourceRow:Number(key.split(":line:")[1]),SheetName:key.startsWith("mcl-") ? "5. Material Cut List Price" : "Tool Shop Items",
    Reason:record.reason,Actor:"Isolated browser user",FileKey:workbookPath,SourceHash:"isolated-source-hash",LineKey:key});
  persistServer();
  return {savedRows:Object.keys(lineAssignments).length,version:server.version,idempotent:false,sourceHash:"isolated-source-hash",
    associationKey:"fixture-association",associationVersion:1,lineAssignments,assignments:{},confirmedRows:server.history};
};
fixtureApi.partBaselineReview = async () => ({baselineStatus:"not_established",source:{fileId:workbookPath,workbookPath,sourceHash:"isolated-source-hash",
  associationKey:"fixture-association",associationVersion:1,mappingVersion:server.version,mappingPolicyVersion:"part-source-labels-v2"},families:{},existingContent:{}});
fixtureApi.createManagedPart = async (payload:any) => new Promise(resolve => {
  pendingCreate = () => {
    const created:FixturePart = {...chassis,id:`fixture-created-${server.parts.length}`,partNumber:`SM-P-90000${server.parts.length+1}`,
      description:String(payload.description),variant:String(payload.variant || ""),name:String(payload.expectedName),scope:String(payload.scope),
      scopeTargetId:String(payload.targetId),shortcode:"S1KHF"};
    server.parts = [...server.parts.filter(part => part.id !== created.id),created]; persistServer();
    (window as any).__lastCreatedPart = created; resolve({part:created}); pendingCreate = null;
  };
});

declare global { interface Window { __completeFixtureCreate:()=>void; __changeLineEvidenceAndReload:()=>void; __syncFixture:()=>void; __lastCreatedPart?:FixturePart } }

function Harness() {
  const [page,setPage] = useState<"mapping"|"parts">("mapping");
  const [fixtureVersion,setFixtureVersion] = useState(0);
  const [mappingReturn,setMappingReturn] = useState<any>(null);
  const [returnSelection,setReturnSelection] = useState<any>(null);
  notify = () => { setFixtureVersion(value => value + 1); };
  window.__completeFixtureCreate = () => pendingCreate?.();
  window.__changeLineEvidenceAndReload = () => {
    changedLineEvidence = true;
    Array.from(document.querySelectorAll<HTMLButtonElement>("button")).find(button => button.textContent?.trim() === "Reload review")?.click();
  };
  window.__syncFixture = () => setFixtureVersion(value => value + 1);
  const evidence = useMemo(() => JSON.stringify({page,gristVersion:server.version,gristWrites:server.writes,mappingReads,
    savedLines:Object.entries(server.lineAssignments).map(([key,value]) => ({lineKey:key,partId:value.partId,reviewed:value.reviewed})),
    quantityEdits:0,serverHistoryRows:server.history.length,fixtureVersion}),[page,fixtureVersion]);
  return <div className="app-shell">
    <header className="topbar" style={{position:"relative",zIndex:10000,display:"block",minHeight:"auto",padding:"10px 16px"}}>
      <strong>Isolated Grist fixture · Part line assignment browser verification</strong>
      <div style={{display:"flex",gap:8,flexWrap:"wrap",marginTop:8}}>
        <button type="button" onClick={() => { if (pendingCreate) window.__completeFixtureCreate(); }}>Complete isolated Part creation</button>
        <button type="button" onClick={() => window.__changeLineEvidenceAndReload()}>Change row 13 evidence and reload review</button>
        <button type="button" onClick={() => { sessionStorage.removeItem(STORAGE); server={version:0,writes:0,lineAssignments:{},history:[],parts:[chassis,bracket]}; changedLineEvidence=false; location.reload(); }}>Reset isolated records</button>
      </div>
      <pre id="fixture-evidence" aria-label="Isolated persistence evidence" style={{whiteSpace:"pre-wrap",fontSize:11,margin:"8px 0 0"}}>{evidence}</pre>
    </header>
    {page === "mapping" ? <PartMappingView key="mapping" path={workbookPath} returnSelection={returnSelection}
      onReturnSelectionConsumed={() => setReturnSelection(null)} onOpenParts={(groupKey,partId,mode,context) => {
        setMappingReturn({path:workbookPath,groupKey,partId,mode,...context}); setPage("parts");
      }}/> : <PartsView key="parts" mappingReturn={mappingReturn} onReturnToMapping={partId => {
        setReturnSelection({...mappingReturn,partId}); setMappingReturn(null); setPage("mapping");
      }}/>}
  </div>;
}

const root = createRoot(document.getElementById("fixture-root")!);
root.render(<Harness/>);
