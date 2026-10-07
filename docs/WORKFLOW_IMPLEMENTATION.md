# Ongoing Safari costing workflow implementation

Started 3 October 2026 on codex/milestone-3-normalized-s1khf, HEAD 476ad3a.
The implementation remains ongoing across all twelve stages.

## Preserved baseline

Before edits, 31 modified/untracked Milestone 3 files were copied into
.git/workflow-baselines/2026-10-03-start/milestone3-worktree.zip with a SHA-256
manifest and binary tracked diff. No reset, stash, branch change or commit.
The baseline tests passed: 119 Python tests (one platform skip), two Node
view-model tests and 11 Vitest tests using the installed bundled Node runtime.
The default Node cannot start Vitest 4; no dependency upgrades were made.

## Stages 1-2

- Root-confined selected-file association retrieval returns saved Product,
  Model, Codes, version, status, queue batch, history and audit.
- Grist refreshes persisted records on that read; no mutation occurs. A fresh
  adapter restart test verifies recovery, plus external-state refresh.
- The UI restores selections and protects against late responses after changing
  files. Validate remains separate from audited Save. Save retries retain their
  idempotency key and reload saved history after success.
- Full-width Workbook Preview refreshes local external sheets automatically on
  tab selection. Indeterminate progress, original/copy hashes, dependency count
  and timestamp are shown. Failures clear stale cells; retry is explicit by
  selecting the tab again. No-link sources are reported without LibreOffice.
- Fixed an existing missing time import in LibreOffice profile cleanup retries.

Verification so far: 123 Python tests (one skip), 14 Vitest tests, TypeScript
checking and a production build pass. The live read-only browser walkthrough
restored Safari 1000 / Safari 1000 HF, all eight saved Codes, version 1, mapped /
queued status, actor, reason and history for the Local V 4.2 pilot. Source ODS
and all four linked-source hashes were captured before refresh. Final preview
and source-hash checks are recorded below when complete.

No live schema or business-data changes were made for these slices. The
walkthrough server blocks business mutations. Earlier Milestone 3 live writes
remain historical evidence, not writes performed by this implementation.

## Next gates and unresolved inputs

Stage 3: persisted/auditable processing state separate from costing authority.
Later gates: all required process sheets and Summary configuration, reviewed
Part assignments (including every blank-description row), later-source immutable
revision import, and model-code-specific structural/configuration reconciliation.
Price mismatches remain visible and do not independently block processing.

The pilot still has ten ambiguous identities and a temporary unallocated Part.
The current one-active-owner rule is retained pending the cross-family question.
Ask mandatory-sheet/tolerance, Summary examples, rates, Part naming/reuse, CR,
write-back, spares and Google Sheet questions when their relevant stage begins.
Credentials remain local. No approved costing-authority transfer is implied.

## Live first-slice evidence

The full-width tab completed a real LibreOffice refresh on 3 October 2026 at
13:33 IST. The source revision was f2517f1105321d3b6494ab33448876a1a1bce13b75500503677590c7b6f8affb;
this is newer than the earlier Milestone 3 report and is not implicitly accepted.
The preview displayed all 17 sheet choices, the first 200 bounded MCL rows,
2,626 source rows / 21 columns, refresh time and original/copy hashes.
All five pre/post hashes matched (selected source plus four linked workbooks).
The temporary copy was discarded. No Safari schema/data write or Costing-New
write occurred. The narrow-browser layout displayed the full-width preview;
a duplicate refresh banner was removed after visual review to leave more grid space.

Regression evidence: 123 Python tests, 122 passed and one platform skip;
14 Vitest component tests passed, two Node view-model tests passed; TypeScript
and production build passed using the bundled compatible Node runtime.
The working tree remains uncommitted. Stage 3 is the next active implementation gate.

## 5 October continuation: processing lifecycle foundation

Preserved a second 40-file working-tree snapshot under
.git/workflow-baselines/2026-10-05-stage3 before edits.

Implemented immutable FileProcessingEvent history through memory/Grist stores.
Each event pins file/association keys, association version, source SHA-256,
from/to state, processing version, request key/fingerprint, actor, reason and time.
Source/association drift projects Changes pending without a GET mutation; a
reasoned action records it. Fresh adapters reload events. A lost save response
retries the original event; changed request payloads and conflicting versions
fail explicitly. API actor attribution comes from the trusted proxy headers.

The selected-file processing panel displays states/history and reasoned actions.
Ready to store and Processed remain unavailable until server structural and
Model Code configuration gates are connected. An Extracted transition requires
an accepted extraction matching the current source revision. Pricing equality
is not a processing gate. Initial New/Associated state is derived from source
registration/saved association until the first processing event is recorded.

Live change: after exact document/workspace validation and a reviewed one-table
plan, applied Safari schema v6, adding FileProcessingEvent only. Existing
columns/types and costing records were untouched. Follow-up diff is empty;
the event table remains empty. The read-only Local V4.2 pilot returns Associated,
version 0, schema available, authority unchanged. A browser fixture demonstrated
Associated -> Changes pending, reason/audit history and source hash; its writes
were confined to memory. Screenshot is preserved in the continuation archive.

Verification: 130 Python tests (one platform skip), 16 Vitest tests, TypeScript
and production build passed; HTTP actor regression was added afterward.
Normal local API/UI were started using the configured Safari adapter at ports
4321/4320. The isolated browser fixture runs at 4332 and is disposable.
No source ODS or Costing-New writes were performed. Work remains uncommitted.

Stage 3 lifecycle storage/UI is present; extraction/configuration/completion
gates remain later-stage work. Next is stage 4: Files versus Product Models ->
Model Codes navigation, stored-record reads and on-demand source reconciliation.
Ten pilot identities and temporary Part ownership remain unresolved.

## 5 October stage 4 navigation slice

Files and Product Models -> Model Codes tabs provide both entry paths. Stored code inspection reads accepted Safari child rows without opening/hashing ODS, offers filters/pagination, snapshot/source revision/time and revision/audit details. Source-file opening and explicit source reconciliation are available; Refresh and calculate starts the disposable-copy review. Workbook process lines also link to source reconciliation.

Accepted-snapshot revisions are pinned to their own snapshot; duplicate snapshot keys and inconsistent/duplicate owners fail explicitly. Rows are labelled shared file-baseline evidence, not per-code Summary configuration or approved authority. Missing association/baseline/storage/schema states are explicit.

Live read-only evidence: main S1KHF code 58 returned 313 rows in 1.934 seconds with sourceRead=false and authorityChanged=false. The HF-MS Drum variant reports no accepted snapshot. No schema or business-data write occurred in stage 4. Source refresh remains on demand; stage 9 caching and comparative timings remain open. An isolated fixture walkthrough selected Product/Model/Code and expanded stored revision details; screenshot model-code-ui.png is preserved in the continuation archive.

Verification: 134 Python tests (133 passed, one platform skip), 18 Vitest tests, TypeScript and production build passed. The new checks cover unavailable-source reads, duplicate/stale owners, snapshot revision isolation and late code responses. Existing preview-tab regression was updated for the Files label.

Stage 5 inputs requested: required sheets/tolerances, two representative Summary/code examples, and Costing-New rate selection/unavailable-rate display. Independent extraction work can continue; completion cannot be asserted until the structural and configuration rules are reviewed. Ten ambiguous pilot identities and temporary Part ownership remain unresolved. All work is uncommitted and preserved.

## 5 October stage 5 evidence foundation and agreed rules

The user supplied all eight mandatory sheets, exact quantities, weight matching to 2 decimals in kg, and nonblocking final cost-total differences. Decimal half-up rounding is explicit. New Part names must be globally unique. Interim prices use Costing-New MaterialLatestRate when its MaterialRateLog has entries; otherwise Default_MaterialRate. Reviewed material_mapping.yaml aliases are required; duplicates/unmapped/missing values remain explicit and never silently become zero. The exact Costing-New document name and actual master/log field names were inspected read-only. Its MaterialLatestRate calculation is preserved, including its configured adjustment.

The refreshed costing-review response/UI now includes required-sheet coverage, physical quantity/weight comparisons (including fields omitted by legacy semantic comparison), structural-versus-pricing differences, individual blank-description Part rows, Summary evidence and nonblocking Costing-New/ODS rates. No completion action is enabled. Part mappings, per-code Summary configuration, spares ingestion and later-source import remain open. Source rows are evidence until mappings are reviewed.

Live disposable-copy pilot: all eight sheets present; process rows 58 MCL, 23 Tool Shop, 168 Stores, 56 CNC, 8 Paint/Packing. 100 active Part rows require mapping review, none blank in this source. 414 physical comparisons produced 100 findings: all are missing older-baseline weight evidence (58 total-weight and 42 part-weight fields), not demonstrated quantity or weight value changes. Summary extraction reports 526 items and 10 headings; this broad extraction includes auxiliary rows and must be narrowed into reviewed per-code configuration. Rates: 35 available, 2 unmapped, 2 reviewed aliases whose canonical master is unavailable. Missing materials: Alang Cut Piece York Patta, CI Casting; unavailable mapped masters MS-Channels 125 x 65 and MS-Channels 75 x 40. Source hash unchanged after refresh; linked sources also verified by refresh evidence.

Both representative Summary workbooks were inspected without writes. HF codes are in column D, H codes in column C, and both cost formulas are in E. HF uses a common-base subtraction of optional groups followed by selected additions and tyre deductions. H includes a 10HP self-start option and separate labour calculations. Evidence retained in summary-examples.json.

Stored inspection was further corrected to follow accepted predecessor lineage: unchanged rows can reuse older immutable revisions; later/unrelated revisions and audit do not leak into a baseline. Missing/ambiguous/cyclic lineage fails explicitly.

Verification after these changes: 139 Python tests (138 passed, one platform skip), 19 Vitest tests, TypeScript and build. Live browser verified Safari 1000 HF -> S1KHFELP -> 313 rows, then explicitly opened source reconciliation and started disposable-copy refresh. Screenshot live-model-code-ui.png records actual stored inspection. Source ODS and Costing-New remain read-only. No additional live schema/business changes in stages 4-5. Stage 6 Part mapping is the next major implementation slice; stage 5 configuration extraction remains partial.

## 5 October stage 6 Part review slice

Part Mapping groups exact nonblank Machine Piece Descriptions across active MCL, Tool Shop and CNC rows; blank descriptions remain individual rows. Users choose an existing canonical Part or create a globally unique name with a reason. Name checks include all Safari ProductPart statuses and normalize Unicode, whitespace and case. Temporary unallocated records and duplicate-name candidates cannot be selected. Creating a Part does not assign source rows. Store Issue rows do not require Parts. Paint/Packing and per-code Summary association remain later configuration work.

Creation audit is stored in the canonical Part row. Assignment batches append typed PartMappingReview rows, one per source row, with a ProductPart reference, file/hash/association version, description, sheet/row, reviewer/reason/time and durable retry keys. Reads never create records. Saved-workbook hashes and association/mapping versions are checked; a final source/association recheck precedes writes. Lost responses reuse the identical request. Incomplete or conflicting batches fail closed. Processed files must explicitly reopen Changes Pending before revised assignments. Refreshed reconciliation counts only current valid assignments. Completion and costing authority remain separate and unavailable until the remaining gates are implemented.

Live schema v7 was applied after exact Safari document/workspace validation: one empty PartMappingReview table and six ProductPart creation-audit fields. The post-apply plan is empty. No live Part or assignment business records were created. Read-only pilot inspection reports 55 unresolved description groups / 100 active source rows; its only existing Part is temporary_unallocated. Source hash remains f2517f1105321d3b6494ab33448876a1a1bce13b75500503677590c7b6f8affb. The live browser displays those counts and excludes the temporary Part.

Application-wide locking serializes writes through this single local service. Grist has no unique constraint through this schema API; duplicate names or simultaneous external writes are detected and blocked for review, rather than silently accepted. External direct-edit constraints are a future deployment requirement. Stage 7 must carry reviewed assignments into typed imports; this slice does not alter existing line masters. Ten ambiguous pilot identities remain open. All source ODS files and Costing-New remain read-only.

Stage 6 verification: 147 Python tests (146 passed, one platform skip), 23 Vitest tests, two Node view-model checks, TypeScript and production build passed. Isolated browser created a canonical test Part, then saved two selected groups spanning three rows; the other blank row remained unresolved. Actor/reason/version history was visible. Fixture writes were memory-only and its server/tab were closed. The normal API/UI remain at 4321/4320. Final live check: ProductPart still has only the temporary unallocated record, PartMappingReview is empty, and pilot source SHA-256 is unchanged. UI clears retained choices if source/association/review evidence changes during Part creation. All work remains uncommitted.

## 6 October owner steering: Part identity before Part mapping progression

Recorded PART-002 and D066. The next implementation gate is now stable Part identity/number allocation, scope-based automatic naming with meaningful variants, metadata revisions/aliases and advisory scope warnings. Scope cannot restrict Model Code configuration; selecting a broad scope does not auto-assign any codes. Multiple chassis designs can share Model scope with different usage subsets. Number/name/engineering revision are separate concepts.

The owner requires this foundation before advancing Part mapping screens. Preserve the 5 October prototype; adapt it after the prerequisite passes. No application code, live schema, Part records, source ODS or Costing-New changed while recording this steering. Requirements, roadmap, architecture, API design notes and interactive model/status were updated. Detailed acceptance cases are in PART_IDENTITY_REQUIREMENTS.md.

## 6 October revision policy and implementation handoff

Recorded PART-003 / D067: all managed Parts remain Rev A until the CR process is implemented. Later Part engineering revisions require the governed CR flow, not mapping, refresh or wider reuse. Metadata name/scope audit versions remain separate. Part creation is the first implementation slice; mapping follows after its identity/number/name/revision guards pass. Detailed handoff: PART_CREATION_MAPPING_IMPLEMENTATION_PROMPT.md. Existing prototype and source history are preserved; these are requirements, not claims of implemented guards.

## 7 October Part creation and mapping foundation

PART-002/D066 and PART-003/D067 are implemented on `codex/part-identity-foundation` as a first application slice. `app/part_identity.py` uses SQLite unique constraints and `BEGIN IMMEDIATE` for same-host multi-process number allocation, request recovery, metadata/alias history, retirement, and mapping serialization. Managed numbers are permanent `SM-P-000001` style identifiers; every managed Part starts and remains Rev A. Physical design variant edits are rejected and a distinct design uses a new identity. Scope target IDs are validated against active repository Product/Model/Code relationships; maintained shortcodes are separately audited.

The Parts tab supports search by number/current name/alias, guided creation, live server-validated names, existing Part details, metadata preview/change, retirement, stable `#parts/<uuid>` detail navigation and history. Mapping opens the same register with its draft/group context preserved; returning may select a Part but does not save the assignment. New source mappings store the stable Part UUID and source/review evidence in the same SQLite file. Out-of-scope codes show a warning and remain saveable. Process lines, drawings and per-code `Used in` evidence are explicitly partial or unavailable until those domains are linked to the stable identity.

The deployment contract is one host with a shared local disk database; a network share or multi-host setup is unsupported. `scripts/backup_part_registry.py` makes a consistent online backup and checks it. The ignored local DB is not committed. Legacy Grist records are read-only inputs and no live Grist schema/data write or Part migration has been done. A read-only Safari recheck validated the configured document/workspace and returned 24 Products, 44 Models, 173 Codes, one temporary unallocated Part, one unverified numeric revision and no Part mappings. The normal Parts screen displayed the legacy Part as unallocated and unverified.

Verification: 159 Python tests ran (158 passed, one platform skip); all 23 Vitest tests passed; TypeScript production build, Python compilation and `git diff --check` passed. In an isolated browser fixture, a scoped Part was created as `SM-P-000001`, Rev A, and assigned to a workbook associated with a different Model Code. The UI displayed the scope advisory and saved one source-row assignment; fixture writes were isolated from Safari. No live Grist schema or business-data write occurred. PR review is the remaining delivery check. Later typed imports, Summary configuration, Paint/Packing, drawings, CR approval and processing completion remain out of scope.
