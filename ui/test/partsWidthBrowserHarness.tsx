import {useEffect, useState} from "react";
import {createRoot} from "react-dom/client";
import {api} from "../src/api";
import {PartMappingView} from "../src/PartMappingView";
import {PartsView} from "../src/PartsView";
import "../src/styles.css";

type Scenario = "empty" | "create" | "detail" | "mapping" | "mapping-preserved" | "mapping-changed" | "mapping-stale-selection";
const part = {id:"fixture-part", partNumber:"SM-P-000101", name:"S1KHF — Chassis — Standard", description:"Chassis", variant:"Standard",
  scope:"product_model", scopeTargetId:"model-1", scopeTarget:"Safari 1000 HF", shortcode:"S1KHF", engineeringRevision:"A", status:"active",
  metadataVersion:1, aliases:[], legacy:false, actor:"Isolated fixture", reason:"Browser width fixture"};
const scopes = {scopes:[
  {id:"global",label:"Global",target:{id:"global",label:"Safari Manufacturing",shortcode:"SM"}},
  {id:"product",label:"Product",targets:[{id:"product-1",label:"Safari 1000",shortcode:"S1K"}]},
  {id:"product_model",label:"Product Model",targets:[{id:"model-1",parentId:"product-1",label:"Safari 1000 HF",shortcode:"S1KHF"}]},
  {id:"model_code",label:"Model Code",targets:[{id:"code-1",parentId:"model-1",label:"S1KHFELP"}]},
]};
const partDetails = {part:{...part,compositionStatus:"draft",metadataHistory:[],lifecycleHistory:[]},mappingHistory:[],
  manufacturingBaseline:{status:"not_established",families:[],requirements:[]},
  intendedSharing:{status:"available",version:0,items:[],history:[]},
  processLines:{status:"available",items:[]},components:{status:"available",items:[]},
  purchases:{specifications:[],vendorCatalog:[]},drawings:{status:"empty",items:[]},usedIn:{status:"available",items:[]}};
const mappingDetail = {sourceHash:"isolated-source-hash",associationKey:"fixture-association",associationVersion:1,version:0,schemaAvailable:true,
  legacyHistoryAvailable:true,mappingPolicyVersion:"part-source-labels-v2",semanticPolicyVersion:"part-mapping-semantic-v1",
  sourceSheets:{"5. Material Cut List Price":{present:true,status:"ok",labelField:"product_part_name",labelHeader:"Part",labelHeaderCell:"A8",ambiguousLabelHeaders:[],headerRow:8}},
  parts:[],unresolvedGroups:1,history:[],removedGroups:[],groups:[{key:"fixture-group",mappingPolicyVersion:"part-source-labels-v2",
    evidenceFingerprint:"fixture-evidence",semanticFingerprint:"fixture-semantic",sheet:"5. Material Cut List Price",labelField:"product_part_name",
    description:"Chassis",blankDescription:false,rows:[{sheet:"5. Material Cut List Price",row:10,fields:{product_part_name:"Plate",material_to_cut:"MS Plate",
      dimension_to_cut_mm:"3 x 25",qty:"2",optional_item_group_1:"HF",in_use:"Yes"},sourceHeaders:{},sourceHeaderCells:{},sourceCells:{},availableFields:[
      "product_part_name","material_to_cut","dimension_to_cut_mm","qty","optional_item_group_1","in_use"]}],part:null,reviewed:false,version:0,
      semanticComparison:{status:"no_prior_evidence",differences:[],requiresReview:false}}]};

const mappedPart = {...part,selectable:true,duplicateName:false};
function getMappingFixture(scenario:Scenario):any {
  const detail:any = structuredClone(mappingDetail);
  if (scenario === "mapping-preserved") {
    detail.sourceHash = "current-source-hash";
    detail.parts = [mappedPart];
    detail.groups[0].part = mappedPart;
    detail.groups[0].reviewed = true;
    detail.groups[0].semanticComparison = {status:"unchanged",previousFingerprint:"prior-semantic",currentFingerprint:"fixture-semantic",
      sourceHashChanged:true,associationChanged:false,groupingPolicyChanged:false,differences:[],requiresReview:false};
    detail.unresolvedGroups = 0;
  } else if (scenario === "mapping-changed" || scenario === "mapping-stale-selection") {
    detail.sourceHash = "current-source-hash";
    detail.parts = [mappedPart];
    detail.groups[0].previousAssignment = {partId:part.id,partNumber:part.partNumber,name:part.name,requiresReview:true,sourceChanged:true,
      previousRowsAgree:true,previousRowsReviewed:1,currentRows:1,hasPriorAssignment:true,evidenceStatus:"complete"};
    detail.groups[0].semanticComparison = {status:"changed",previousFingerprint:"prior-semantic",currentFingerprint:"fixture-semantic",
      sourceHashChanged:true,associationChanged:false,groupingPolicyChanged:false,requiresReview:true,differences:[{type:"modified",
        previous:{fingerprint:"prior-line",fields:[{field:"qty",state:"value",value:"2",display:"2"}]},
        current:{fingerprint:"current-line",fields:[{field:"qty",state:"value",value:"3",display:"3"}]},
        fieldDifferences:[{field:"qty",previous:{field:"qty",state:"value",value:"2",display:"2"},current:{field:"qty",state:"value",value:"3",display:"3"}}]}]};
    detail.unresolvedGroups = 1;
  }
  return detail;
}

const fixtureApi = api as any;
fixtureApi.parts = async () => ({items:window.__scenario === "empty" || window.__scenario === "create" ? [] : [part],legacyItems:[],storage:"Isolated fixture"});
fixtureApi.partScopeTargets = async () => scopes;
fixtureApi.partDetails = async () => partDetails;
fixtureApi.partNamePreview = async (_scope:string, _target:string, description:string, variant:string) => ({
  name:`S1KHF — ${description}${variant ? ` — ${variant}` : ""}`,available:true,collision:[],usageFingerprint:"fixture-usage"});
fixtureApi.partMappings = async () => getMappingFixture(window.__scenario);
fixtureApi.searchParts = async () => ({items:[],total:0,offset:0,limit:30,hasMore:false});
fixtureApi.fileAssociation = async () => ({current:{product_id:"product-1",model_id:"model-1"},product:{id:"product-1"},model:{id:"model-1"},codes:[{id:"code-1"}]});
window.__saveCount = 0;
fixtureApi.savePartMappings = async () => { window.__saveCount += 1; return {ok:true}; };

declare global { interface Window { __scenario: Scenario; __saveCount:number; __showFixture: (scenario:Scenario)=>void } }

function Fixture({scenario}: {scenario:Scenario}) {
  useEffect(() => {
    if (scenario !== "create") return;
    const timer = window.setTimeout(() => document.querySelector<HTMLButtonElement>(".parts-new-button")?.click(), 350);
    return () => window.clearTimeout(timer);
  }, [scenario]);
  return <div className="app-shell">
    <header className="topbar"><strong>Isolated Parts width and mapping safety check</strong><nav aria-label="Fixture scenarios">
      {(["empty","create","detail","mapping","mapping-preserved","mapping-changed","mapping-stale-selection"] as Scenario[]).map(item => <button key={item} type="button" onClick={() => window.__showFixture(item)}>{item}</button>)}
    </nav></header>
    <div className="app-cached-view" data-scenario={scenario}>
      {scenario.startsWith("mapping") ? <PartMappingView key={scenario} path="isolated-fixture.ods" returnSelection={scenario === "mapping-stale-selection" ? {
        path:"isolated-fixture.ods",groupKey:"fixture-group",partId:part.id,mode:"select",sourceHash:"stale-source-hash",
        associationKey:"fixture-association",associationVersion:1,evidenceFingerprint:"stale-evidence"} : null}/> : <PartsView key={scenario} active={true}/>}
    </div>
    <output id="width-report" aria-live="polite" aria-label="Measured browser layout" />
  </div>;
}

const root = createRoot(document.getElementById("fixture-root")!);
function showFixture(scenario:Scenario) {
  window.__scenario = scenario;
  window.__saveCount = 0;
  sessionStorage.removeItem("part-mapping:isolated-fixture.ods");
  if (scenario === "mapping-preserved" || scenario === "mapping-changed") {
    const changed = scenario === "mapping-changed";
    sessionStorage.setItem("part-mapping:isolated-fixture.ods", JSON.stringify({path:"isolated-fixture.ods",sourceHash:"prior-source-hash",version:0,
      associationKey:"fixture-association",associationVersion:1,decisions:changed?{"fixture-group":part.id}:{},draftParts:changed?{"fixture-group":mappedPart}:{},
      draftGroups:changed?[{groupKey:"fixture-group",sheet:"5. Material Cut List Price",description:"Chassis",rowCount:1,evidenceFingerprint:"prior-evidence"}]:[],
      staleDrafts:[],reason:"",query:"",sheetFilter:"all",reviewFilter:"all",scrollTop:0,mappingAttempt:null}));
  }
  history.replaceState(null,"", scenario === "detail" ? "#parts/fixture-part" : "#parts");
  root.render(<Fixture scenario={scenario}/>);
}
window.__showFixture = showFixture;
showFixture((new URLSearchParams(location.search).get("scenario") as Scenario) || "empty");

window.setInterval(() => {
  const page = document.querySelector<HTMLElement>(".parts-page");
  const explorer = document.querySelector<HTMLElement>(".parts-explorer");
  const work = document.querySelector<HTMLElement>(".parts-work-panel");
  const dialog = document.querySelector<HTMLElement>(".part-create-dialog");
  const report = document.getElementById("width-report");
  if (!report) return;
  if (dialog) {
    const box = dialog.getBoundingClientRect();
    report.textContent = `scenario=${window.__scenario}; viewport=${window.innerWidth}x${window.innerHeight}; popup=${Math.round(box.width)}; popupLeft=${Math.round(box.left)}; popupRightGap=${Math.round(window.innerWidth-box.right)}`;
  } else if (page && explorer && work) {
    const pageBox = page.getBoundingClientRect();
    const explorerBox = explorer.getBoundingClientRect();
    const workBox = work.getBoundingClientRect();
    report.textContent = `scenario=${window.__scenario}; viewport=${window.innerWidth}x${window.innerHeight}; page=${Math.round(pageBox.width)}; explorer=${Math.round(explorerBox.width)}; panel=${Math.round(workBox.width)}; panelRightGap=${Math.round(pageBox.right-workBox.right)}; form=${Math.round(document.querySelector<HTMLElement>(".parts-form-card")?.getBoundingClientRect().width || 0)}; selected=${Math.round(document.querySelector<HTMLElement>(".parts-selected-card")?.getBoundingClientRect().width || 0)}`;
  }
}, 150);
