# Part identity, automatic naming and sharing scope

Recorded from the owner's instructions on 6 October 2026. Requirement PART-002; decision D066. Status: accepted requirement, implementation pending.

This is a prerequisite to advancing the Part mapping screens or applying reviewed Parts to imports. The 5 October screen is a preserved prototype; it does not yet satisfy this requirement.

## Permanent identity and numbering

- Every managed Part, including assemblies and subassemblies, has an immutable internal identity and a globally unique, permanent Part number.
- Adopt the discussed numbering convention `SM-P-000001`: fixed Safari Manufacturing/Part prefix and a centrally allocated sequence. Product, Model, Model Code, scope, name, material and revision do not enter the number.
- Allocate on saved creation through a dedicated durable allocator with duplicate protection. Do not use spreadsheet row positions, Grist record IDs, record counts or an unchecked maximum-plus-one calculation as the allocator.
- A retry of the same creation request returns the same Part and number. Retired numbers are never reused; gaps are acceptable.
- Renaming, broadening scope and adding Model Code uses preserve the Part identity and number. A name-derived key must not be the identity of new Parts.
- Process rows describing manufacture of one Part reference that Part; they do not automatically create separate numbered Parts. Independently managed physical components may have their own numbers.

## Automatic naming

The user selects a sharing scope and its target, enters a Part description (for example, Chassis), and supplies a meaningful design variant where needed. The application derives the complete name and previews it before Save:

`<scope shortcode> — <Part description> [— <design variant>]`

| Scope | Shortcode source | Illustrative name |
|---|---|---|
| Global | Maintained global shortcode, working example GBL | GBL — Bearing Housing — Standard |
| Product | Selected Product shortcode | S1K — Drum Assembly — Standard |
| Product Model | Selected Model shortcode | S1KHF — Chassis — Standard |
| Model Code | Selected Model Code shortcode | S1KHFELP — Chassis — Standard |

Shortcodes come from maintained master records. The examples are not assertions that those shortcode fields already exist. Missing or ambiguous shortcodes require resolution before a generated name can be saved; users should not have to type the entire prefixed name.

Names remain globally unique under the existing Unicode/whitespace/case normalization rules. On a collision, offer the existing Part or request a meaningful design distinction; never silently append a sequence to an ambiguous name. A temporary reviewed designation such as Design 01 is allowed when a meaningful variant designation is not yet available. Old names remain searchable aliases and must not ambiguously resolve to another Part.

## Sharing scope is advisory, not a configuration restriction

- Sharing scope derives the name. It does not prohibit configuring any Product Model Code with the Part.
- Configuration outside the declared scope shows a warning and allows saving without requiring a scope change. The warning may offer a separate review of broader scope.
- The explicit Part/revision references in Model Code configurations record actual usage. Applicability describes those reviewed uses; it must not become a whitelist that defeats the advisory-scope rule.
- Global or Product scope does not automatically add the Part to every descendant Model Code. Adding use outside scope does not automatically broaden scope or rename the Part.
- Scope warnings are distinct from physical compatibility and engineering revision review.

## Multiple shared designs within one Model

Several different Parts may use the same Product Model scope and base description. Distinguish them by design variant and give each its own number; their code usage lists may differ or overlap.

| Illustrative Part name | Part number | Illustrative usage |
|---|---|---|
| S1KHF — Chassis — Standard | SM-P-000241 | 10 HF Model Codes |
| S1KHF — Chassis — Reinforced | SM-P-000242 | 6 HF Model Codes |

The counts and variants illustrate the owner's scenario, not verified current configurations. Do not concatenate code lists into names. Adding/removing a Model Code use does not itself change the Part number or name.

A Drum shared by approximately 90% of Safari 1000 codes can use Product scope, with explicit configuration references for the actual users. Other codes may use a different numbered Drum. Product scope does not imply 100% use.

## Scope/name changes and revisions

- Provide a reviewed scope/name change with proposed before/after name, affected uses, actor, reason and time. It may move from Model Code to Model, Product or Global scope.
- Store immutable metadata versions for scope/name changes and preserve previous names as aliases.
- Keep metadata versions separate from engineering revisions. A scope/name change alone does not revise the physical design.
- Existing mappings, BOMs, configurations and drawings reference stable identity and the appropriate engineering revision. Completed/historical records retain the name and revision used at the time.
- A reviewed physically unchanged reuse keeps the Part number and engineering revision. An interchangeable controlled design update may use a new engineering revision. A distinct noninterchangeable design gets a new Part number and a recorded relationship to its predecessor.

## Engineering revision lock until CR implementation

Owner clarification, 6 October 2026: a Part engineering revision is evidence that the Part has followed the Change Request (CR) process. All managed Parts are Rev A until that process is implemented. New Parts start at Rev A. Do not expose or permit Rev B or later through the UI, API, import, direct service method or automatic source reconciliation.

After the CR flow is implemented, a later engineering revision requires the appropriate approved CR, linked to its before/after design evidence, actor, reason and approval history. Source refresh, Part mapping, wider reuse or renaming cannot themselves create or approve an engineering revision.

Name/scope metadata versions and source/line observation versions remain separate audit concepts. They must not be presented as Part engineering revisions or imply CR approval. Name/scope changes preserve Rev A when physical design is unchanged. Before CR implementation, do not overwrite an existing Part's engineering definition as an informal substitute for revision; preserve the evidence and defer the controlled change. Independently distinct new Parts can be created with their own numbers at Rev A.

The current schema's numeric PartRevision field and temporary unallocated records require an explicit compatibility plan; do not infer CR approval from their numeric value or rewrite historical records silently.

## Implementation order and exit checks

1. Implement the Part master foundation: stable identity, number allocator, maintained shortcode fields, scope/target, description and design variant.
2. Implement generated-name uniqueness, immutable metadata history, aliases and separate engineering revisions, including a safe migration/compatibility plan for existing name-derived keys.
3. Implement explicit Model Code usage/revision references and advisory out-of-scope warnings; scope changes remain separate reviewed actions.
4. Verify the cases below before adapting and advancing the Part mapping screens and typed import work.

Required acceptance cases:

- Create Parts at all four scopes; generated prefixes use the selected masters and names preview before Save.
- Two chassis designs within one Model receive distinct meaningful names and permanent numbers, with different subsets of Model Codes.
- Configure a code outside scope: warning is visible, Save remains possible, and no automatic rename/scope change occurs.
- Broaden a code-specific Part to Model/Product scope: identity, number, engineering revision and existing references remain unchanged; metadata history and old-name lookup remain available.
- Create/retry/concurrent allocation cannot duplicate a number. Renames/collisions cannot make old names ambiguous. Retirement never recycles a number.
- Historical names/design revisions remain reproducible, and configurations are never auto-populated by selecting a broad scope.
- Every new managed Part starts at Rev A; client/service/import attempts to create a later Part engineering revision fail until the CR flow and its required approval are available. Metadata/source history remains separate.

Recording this requirement does not apply a live migration or change business records. Existing source ODS and Costing-New remain read-only; the normal verified-target and audit rules apply to later implementation.
