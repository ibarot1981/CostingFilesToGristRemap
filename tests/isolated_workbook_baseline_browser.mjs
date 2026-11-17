import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";
const playwrightModule = process.env.PLAYWRIGHT_MODULE || "file:///C:/Users/ibaro/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs";
const { chromium } = await import(playwrightModule);
const base="http://127.0.0.1:44571";
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_EXECUTABLE || "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",args:["--no-sandbox"]});
const evidence={browser:"Google Chrome via Playwright",baseUrl:base,jsErrors:[],nonGetRequests:[],reconciliationReads:[],blockedPreview:{},stalePreview:{},bulkRequests:[],longOperation:{},recovery:{},comparison:{},pendingReturn:{},partsReturn:{},workbookSwitch:{},finalState:{}};
try {
  const blockedPage=await browser.newPage({viewport:{width:1280,height:900}});
  await blockedPage.route("**/api/parts/manufacturing-baselines/workbook-preview**",async route=>{
    const response=await route.fetch(); const payload=await response.json();
    const readyParts=payload.parts.filter(item=>item.status==="ready"); assert.ok(readyParts.length>=3,"fixture includes ready Parts for mixed-outcome confirmation");
    const blocked=readyParts[0], retained=readyParts[1];
    blocked.status="blocked"; blocked.blockers=["This Part's saved source assignment needs review."];
    retained.status="already_established";
    payload.pendingRows=[]; payload.counts={mappedParts:payload.parts.length,readyParts:1,alreadyEstablishedParts:1,blockedParts:1,pendingRows:0};
    payload.workbookComplete=false; payload.canConfirm=true;
    await route.fulfill({status:response.status(),contentType:"application/json",body:JSON.stringify(payload)});
  });
  await blockedPage.route("**/api/parts/manufacturing-baselines/workbook-confirm",async route=>{
    const body=JSON.parse(route.request().postData()||"{}"); assert.equal(body.parts.length,1,"only the ready Part is submitted");
    const response={status:"partial",totalSuccess:false,results:[
      {partId:body.parts[0].partId,partNumber:"SM-P-000001",status:"established"},
      {partId:"browser-retained",partNumber:"SM-P-000002",status:"already_established",retryable:false,message:"An existing baseline was retained and not overwritten."},
      {partId:"browser-blocked",partNumber:"SM-P-000003",status:"blocked",retryable:false,message:"This Part remains blocked and was not included in workbook confirmation."},
    ],pendingRows:[],workbookComplete:false};
    await route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(response)});
  });
  await blockedPage.goto(base,{waitUntil:"networkidle"});
  await blockedPage.locator(".tree-row").filter({hasText:"Browser-QA-Incoming.ods"}).click();
  await blockedPage.getByRole("button",{name:"Part Mapping",exact:true}).click();
  await blockedPage.getByRole("button",{name:"Confirm manufacturing baselines",exact:true}).click();
  await blockedPage.locator(".baseline-bulk-blocked").waitFor();
  const blockedButton=blockedPage.getByRole("button",{name:"Confirm 1 ready baseline",exact:true});
  evidence.blockedPreview={cardText:await blockedPage.locator(".baseline-bulk-blocked").innerText(),readyConfirmationEnabled:await blockedButton.isEnabled(),counts:await blockedPage.locator('[aria-label="Workbook baseline preview counts"]').innerText()};
  assert.match(evidence.blockedPreview.cardText,/needs review/); assert.equal(evidence.blockedPreview.readyConfirmationEnabled,true);
  assert.match(evidence.blockedPreview.counts,/1\s+already established/); assert.match(evidence.blockedPreview.counts,/1\s+blocked/);
  await blockedButton.click(); await blockedPage.locator(".baseline-bulk-results").waitFor();
  evidence.blockedPreview.resultText=await blockedPage.locator(".baseline-bulk-results").innerText();
  assert.match(evidence.blockedPreview.resultText,/confirmation needs review/);
  assert.match(evidence.blockedPreview.resultText,/1 Part baseline\(s\) established; 1 already established \(retained\); 1 blocked; 0 failed; 0 source row\(s\) remain for review\./);
  assert.match(evidence.blockedPreview.resultText,/not overwritten/);
  await blockedPage.close();

  const stalePage=await browser.newPage({viewport:{width:1280,height:900}}); let staleWrites=0;
  await stalePage.route("**/api/parts/manufacturing-baselines/workbook-confirm",async route=>{staleWrites++; await route.fulfill({status:409,contentType:"application/json",body:JSON.stringify({detail:{code:"PART_BASELINE_PREVIEW_STALE",message:"The workbook or saved mapping changed since this preview.",retryDisposition:"refresh_required"}})});});
  await stalePage.goto(base,{waitUntil:"networkidle"}); await stalePage.locator(".tree-row").filter({hasText:"Browser-QA-Incoming.ods"}).click();
  await stalePage.getByRole("button",{name:"Part Mapping",exact:true}).click();
  await stalePage.getByRole("button",{name:"Confirm manufacturing baselines",exact:true}).click();
  await stalePage.getByRole("button",{name:"Confirm 3 ready baselines",exact:true}).click();
  const staleAlert=stalePage.getByRole("alert"); await staleAlert.getByText(/PART_BASELINE_PREVIEW_STALE/).waitFor();
  await stalePage.getByRole("button",{name:"Confirm 3 ready baselines",exact:true}).waitFor();
  evidence.stalePreview={alert:await staleAlert.innerText(),retryButtons:await stalePage.getByRole("button",{name:"Retry same confirmation",exact:true}).count(),freshConfirmEnabled:await stalePage.getByRole("button",{name:"Confirm 3 ready baselines",exact:true}).isEnabled(),storedAttempt:await stalePage.evaluate(()=>localStorage.getItem("workbook-baseline-confirm-attempt:Browser-QA-Incoming.ods")),interceptedWrites:staleWrites};
  assert.equal(evidence.stalePreview.retryButtons,0); assert.equal(evidence.stalePreview.storedAttempt,null); assert.equal(evidence.stalePreview.interceptedWrites,1);
  await stalePage.close();

  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on("pageerror",error=>evidence.jsErrors.push(error.message));
  await page.route("**/api/parts/manufacturing-baselines/workbook-confirm",async route=>{
    const request=route.request(); const startedAt=Date.now(); const response=await route.fetch({timeout:60000}); const elapsedMs=Date.now()-startedAt; const result=await response.json();
    const entry={key:request.headers()["idempotency-key"],body:request.postData(),status:response.status(),result,clientRoundTripMs:elapsedMs,responseAborted:evidence.bulkRequests.length===0}; evidence.bulkRequests.push(entry);
    if(evidence.bulkRequests.length===1) { await route.abort("failed"); return; }
    await route.fulfill({status:response.status(),contentType:"application/json",body:JSON.stringify(result)});
  });
  page.on("request",request=>{if(request.url().startsWith(base)&&request.url().includes("workbook-confirm-status")) evidence.reconciliationReads.push({method:request.method(),url:request.url(),key:request.headers()["idempotency-key"],body:request.postData()}); else if(request.method()!=="GET"&&request.url().startsWith(base)) evidence.nonGetRequests.push({method:request.method(),url:request.url()});});
  await page.goto(base,{waitUntil:"networkidle"});
  const beforeState=await page.evaluate(async()=>await (await fetch("/api/qa/workbook-baseline-state")).json());
  assert.equal(beforeState.baselineCount,1); assert.equal(beforeState.mappingRecordCount,5);
  await page.locator(".tree-row").filter({hasText:"Browser-QA-Incoming.ods"}).click();
  await page.getByRole("button",{name:"Part Mapping",exact:true}).click();
  const draft=page.getByRole("combobox",{name:"Part assignment for Draft MCL pending"});
  await draft.click(); await draft.fill("SM-P-000001"); await page.getByRole("option",{name:/SM-P-000001/}).click();
  evidence.pendingReturn.draftBeforeBulk={value:await draft.inputValue(),stickyText:await page.locator(".part-sticky-save").innerText()};
  assert.match(evidence.pendingReturn.draftBeforeBulk.value,/SM-P-000001/); assert.match(evidence.pendingReturn.draftBeforeBulk.stickyText,/1 group · 1 unsaved line/);
  await page.getByRole("button",{name:"Confirm manufacturing baselines",exact:true}).click();
  const counts=page.locator('[aria-label="Workbook baseline preview counts"]');
  await page.getByRole("button",{name:"Confirm 3 ready baselines",exact:true}).waitFor();
  evidence.preview={counts:await counts.innerText(),pendingText:await page.locator(".baseline-bulk-pending").innerText(),readyButtonEnabled:await page.getByRole("button",{name:"Confirm 3 ready baselines",exact:true}).isEnabled()};
  assert.match(evidence.preview.pendingText,/row 3/); assert.match(evidence.preview.pendingText,/row 4/); assert.equal(evidence.preview.readyButtonEnabled,true);
  await page.screenshot({path:"C:/Users/ibaro/AppData/Local/Temp/workbook-baseline-preview-round1.png",fullPage:true});
  await page.setViewportSize({width:390,height:844});
  const narrowButton=page.getByRole("button",{name:"Confirm 3 ready baselines",exact:true}); await narrowButton.scrollIntoViewIfNeeded();
  const box=await narrowButton.boundingBox(); const narrowWidth=await page.evaluate(()=>({innerWidth,scrollWidth:document.documentElement.scrollWidth}));
  evidence.narrow={buttonBox:box,viewport:narrowWidth,enabled:await narrowButton.isEnabled()};
  assert.ok(box&&box.x>=0&&box.x+box.width<=390+1,"confirmation control fits narrow viewport"); assert.equal(evidence.narrow.enabled,true);
  await page.screenshot({path:"C:/Users/ibaro/AppData/Local/Temp/workbook-baseline-preview-narrow-round1.png",fullPage:true});
  await narrowButton.click();
  await page.getByText(/Server evidence confirms 0 of 3 requested Part baselines\. 3 in progress/).waitFor({timeout:15000});
  const liveProgressText=await page.locator(".baseline-bulk-progress").innerText();
  const storedDuringRun=await page.evaluate(()=>JSON.parse(localStorage.getItem("workbook-baseline-confirm-attempt:Browser-QA-Incoming.ods")||"null"));
  assert.ok(storedDuringRun?.key); assert.equal(storedDuringRun.payload.parts.length,3);
  await page.locator(".baseline-bulk-results").waitFor({timeout:60000});
  await page.waitForFunction(()=>localStorage.getItem("workbook-baseline-confirm-attempt:Browser-QA-Incoming.ods")===null,{timeout:15000});
  const firstKey=evidence.bulkRequests[0].key, firstBody=evidence.bulkRequests[0].body;
  assert.equal(storedDuringRun.key,firstKey); assert.equal(JSON.stringify(storedDuringRun.payload),JSON.stringify(JSON.parse(firstBody)));
  assert.ok(evidence.bulkRequests[0].clientRoundTripMs>30000,"the isolated server confirmation must exceed the former 30-second cutoff");
  assert.equal(evidence.bulkRequests[0].responseAborted,true,"the browser must recover after the original response is lost");
  evidence.longOperation={serverDelaySeconds:Number(process.env.WORKBOOK_BASELINE_QA_CONFIRM_DELAY_SECONDS||0),requestRoundTripMs:evidence.bulkRequests[0].clientRoundTripMs,liveProgressText,responseLost:true,attemptClearedAfterVerifiedCompletion:true};
  const submitted=JSON.parse(firstBody); assert.equal(submitted.parts.length,3); assert.ok(!("sourceFamilies" in submitted.parts[0]));
  assert.equal(await page.getByRole("button",{name:"Retry same confirmation",exact:true}).count(),0);
  assert.match(await page.locator(".baseline-bulk-results").innerText(),/3 Part baseline\(s\) established/);
  await page.evaluate(({key,payload})=>localStorage.setItem("workbook-baseline-confirm-attempt:Browser-QA-Incoming.ods",JSON.stringify({key,payload})),{key:firstKey,payload:submitted});
  const reconciliationReadsBeforeReload=evidence.reconciliationReads.length;
  await page.reload({waitUntil:"networkidle"});
  await page.setViewportSize({width:1440,height:1000});
  await page.locator(".tree-row").filter({hasText:"Browser-QA-Incoming.ods"}).click();
  await page.getByRole("button",{name:"Part Mapping",exact:true}).click();
  await page.locator("#part-mapping-context").getByRole("button",{name:"Hide supporting information",exact:true}).click();
  await page.getByRole("button",{name:"Confirm manufacturing baselines",exact:true}).click();
  await page.locator(".baseline-bulk-results").waitFor();
  await page.waitForFunction(()=>localStorage.getItem("workbook-baseline-confirm-attempt:Browser-QA-Incoming.ods")===null,{timeout:10000});
  await page.waitForFunction(()=>document.querySelector('[aria-label="Workbook baseline preview counts"] span:first-child strong')?.innerText==="0");
  assert.equal(evidence.bulkRequests.length,1,"reopening must reconcile without a second confirmation write");
  const storedAfterReload=await page.evaluate(()=>localStorage.getItem("workbook-baseline-confirm-attempt:Browser-QA-Incoming.ods"));
  assert.equal(storedAfterReload,null);
  const reloadedStatusReads=evidence.reconciliationReads.slice(reconciliationReadsBeforeReload);
  assert.ok(reloadedStatusReads.length>0); assert.ok(reloadedStatusReads.some(item=>item.key===firstKey&&JSON.stringify(JSON.parse(item.body))===JSON.stringify(submitted)));
  evidence.recovery={legacyPr6PayloadFormat:true,key:firstKey,body:submitted,attemptClearedAfterReload:true,confirmationRequestsAfterReload:evidence.bulkRequests.length,statusReadRequests:reloadedStatusReads};
  evidence.bulk={requestCount:evidence.bulkRequests.length,key:firstKey,payload:submitted,firstAttemptHttp:evidence.bulkRequests[0].status,firstAttemptResults:evidence.bulkRequests[0].result.results.map(item=>({partId:item.partId,status:item.status})),resultText:await page.locator(".baseline-bulk-results").innerText(),refreshedCounts:await counts.innerText(),longOperation:evidence.longOperation,recovery:evidence.recovery};
  assert.match(evidence.bulk.resultText,/3 Part baseline\(s\) established/);
  evidence.afterConfirm=await page.evaluate(async()=>await (await fetch("/api/qa/workbook-baseline-state")).json());
  assert.equal(evidence.afterConfirm.baselineCount,4); assert.equal(evidence.afterConfirm.revisionLineCount,4); assert.equal(evidence.afterConfirm.baselineRequestKeys.length,4); assert.equal(evidence.afterConfirm.mappingRecordCount,5);

  const existing=page.locator(".baseline-workspace-part").filter({hasText:"SM-P-000004"}); await existing.click();
  const compareButton=page.getByRole("button",{name:"Compare incoming workbook",exact:true}); await compareButton.waitFor({timeout:10000});
  const comparisonResponse=page.waitForResponse(response=>response.url().includes("/manufacturing-baseline/compare")&&response.request().method()==="POST");
  await compareButton.click(); const compare=await comparisonResponse; const comparePayload=await compare.json(); await page.getByText(/Workbook comparison ·/).waitFor();
  const incomingCnc=comparePayload.differences.find(item=>item.Family==="cnc"&&item.IncomingObservation);
  const baselineCnc=comparePayload.differences.find(item=>item.Family==="cnc"&&item.BaselineRevisionLine);
  const incomingCncValues=incomingCnc?JSON.parse(incomingCnc.IncomingValues).physical:null;
  const baselineCncValues=baselineCnc?JSON.parse(baselineCnc.BaselineValues).physical:null;
  evidence.comparison={httpStatus:compare.status(),status:comparePayload.comparison.Status,sourceHash:comparePayload.comparison.SourceHash,baselineSourceHash:comparePayload.comparison.BaselineSourceHash,incomingCnc:{differenceType:incomingCnc?.DifferenceType,row:incomingCnc?.SourceRow,length:incomingCncValues?.length},baselineCnc:{differenceType:baselineCnc?.DifferenceType,length:baselineCncValues?.length},warnings:comparePayload.evidenceWarnings};
  assert.equal(compare.status(),200); assert.equal(comparePayload.comparison.BaselineSourceHash,beforeState.previousHash); assert.equal(comparePayload.comparison.SourceHash,beforeState.incomingHash); assert.equal(incomingCncValues?.length,"140"); assert.equal(baselineCncValues?.length,"120");
  evidence.finalState=await page.evaluate(async()=>await (await fetch("/api/qa/workbook-baseline-state")).json());
  assert.equal(evidence.finalState.baselineCount,4); assert.equal(evidence.finalState.revisionLineCount,4); assert.deepEqual(evidence.finalState.baselineRequestKeys,evidence.afterConfirm.baselineRequestKeys); assert.equal(evidence.finalState.mappingRecordCount,5);

  const initialPreview=await page.evaluate(async()=>await (await fetch("/api/parts/manufacturing-baselines/workbook-preview?path=Browser-QA-Incoming.ods")).json());
  const pending=initialPreview.pendingRows.find(row=>row.row===3); assert.ok(pending);
  await page.locator(".baseline-bulk-pending").getByRole("button",{name:"Review in Part Mapping"}).first().click();
  await page.getByRole("heading",{name:"Part Mapping",exact:true}).waitFor();
  const draftAfterReturn=page.getByRole("combobox",{name:"Part assignment for Draft MCL pending"});
  await page.getByText(/Review 5\. Material Cut List Price row 3 and save any Part assignment explicitly/).waitFor();
  const pendingLine=page.locator(`[id="part-source-line-${encodeURIComponent(pending.lineKey)}"]`); await pendingLine.waitFor();
  evidence.pendingReturn={...evidence.pendingReturn,notice:(await page.locator("body").innerText()).match(/Review 5\. Material Cut List Price row 3[^\n]*/)?.[0],pendingLineVisible:await pendingLine.isVisible(),workbookPath:await page.locator(".part-workbook").innerText(),draftValue:await draftAfterReturn.inputValue(),stickyText:await page.locator(".part-sticky-save").innerText()};
  assert.equal(evidence.pendingReturn.pendingLineVisible,true); assert.match(evidence.pendingReturn.workbookPath,/Browser-QA-Incoming\.ods/); assert.match(evidence.pendingReturn.draftValue,/SM-P-000001/); assert.match(evidence.pendingReturn.stickyText,/1 group · 1 unsaved line/);

  const group=page.locator(".part-source-group").filter({hasText:"Bulk MCL ready"}); await group.getByRole("button",{name:"View Part",exact:true}).click();
  await page.getByRole("heading",{name:"Parts",exact:true}).waitFor(); await page.getByText("Accepted Manufacturing baseline",{exact:true}).waitFor({timeout:10000}); await page.getByLabel("Part detail sections").getByRole("button",{name:"Manufacturing baseline",exact:true}).click(); await page.locator("#manufacturing-baseline[open]").waitFor();
  const partText=await page.locator("body").innerText(); assert.match(partText,/SM-P-000001/); assert.match(partText,/established/); assert.match(partText,/Originating workbook:[^\n]*Browser-QA-Incoming\.ods/);
  evidence.partsReturn={hasAcceptedBaselineSection:partText.includes("Accepted Manufacturing baseline"),showsIncomingWorkbook:partText.includes("Browser-QA-Incoming.ods"),baselineText:partText.match(/Manufacturing baseline[^\n]*/)?.[0]};
  await page.getByRole("button",{name:"Return to Part Mapping",exact:true}).click(); await page.getByRole("heading",{name:"Part Mapping",exact:true}).waitFor();
  const draftAfterParts=page.getByRole("combobox",{name:"Part assignment for Draft MCL pending"}); assert.match(await draftAfterParts.inputValue(),/SM-P-000001/);

  await page.getByLabel("Review status",{exact:true}).selectOption("unsaved");
  await page.getByRole("button",{name:"Files",exact:true}).click(); await page.locator(".tree-row").filter({hasText:"Browser-QA-Baseline.ods"}).click();
  await page.getByRole("button",{name:"Part Mapping",exact:true}).click(); await page.locator(".part-workbook").filter({hasText:"Browser-QA-Baseline.ods"}).waitFor();
  const baselineMappingPath=await page.locator(".part-workbook").innerText();
  await page.getByRole("button",{name:"Files",exact:true}).click(); await page.locator(".tree-row").filter({hasText:"Browser-QA-Incoming.ods"}).click();
  await page.getByRole("button",{name:"Part Mapping",exact:true}).click(); await page.locator(".part-workbook").filter({hasText:"Browser-QA-Incoming.ods"}).waitFor();
  const restoredDraft=page.getByRole("combobox",{name:"Part assignment for Draft MCL pending"});
  evidence.workbookSwitch={baselineMappingPath,restoredPath:await page.locator(".part-workbook").innerText(),restoredFilter:await page.getByLabel("Review status",{exact:true}).inputValue(),restoredDraftValue:await restoredDraft.inputValue(),restoredStickyText:await page.locator(".part-sticky-save").innerText()};
  assert.match(evidence.workbookSwitch.baselineMappingPath,/Browser-QA-Baseline\.ods/); assert.match(evidence.workbookSwitch.restoredPath,/Browser-QA-Incoming\.ods/); assert.equal(evidence.workbookSwitch.restoredFilter,"unsaved"); assert.match(evidence.workbookSwitch.restoredDraftValue,/SM-P-000001/);
  assert.equal(evidence.finalState.mappingRecordCount,5);
  const report=JSON.stringify(evidence,null,2);
  if(process.env.BROWSER_EVIDENCE_PATH) await writeFile(process.env.BROWSER_EVIDENCE_PATH,`${report}\n`,"utf8");
  console.log(report);
} catch(error) { console.error("PARTIAL_EVIDENCE",JSON.stringify(evidence,null,2)); throw error; } finally { await browser.close(); }
