# Local coordination protocol

This is a manual, file-based handoff between the existing Sol reviewer chat (GPT 6.1 Sol, Medium) and existing Luna implementation chat (GPT 6 Luna, Extra High). No scheduler, messaging service, merge, deployment, or production Grist/schema operation is enabled or authorized by this protocol.

## Authority and roles

Sol records owner-approved requirements, explicit acceptance criteria and required verification checks, issues implementation prompts, and independently reviews submitted commits. Luna alone edits application source during an implementation round, runs checks and updates the designated PR. Workflow setup code is separate from application implementation. Neither role invents requirements during correction rounds. Ask the owner about scope changes.

One task may be active at a time, including blocked or interrupted creation. Five implementation rounds maximum; a failed fifth round becomes blocked and needs owner intervention. The helper never starts round six. Owner intervention must explicitly settle the task; do not reset counters or delete history to bypass the limit.

Use `scripts/agent_workflow.py` with the repository virtual-environment Python. It uses only the standard library. `.agent-workflow/tasks/` and the transition lock are ignored by Git. These instructions, helper and tests are reusable tracked files.

## Task layout

```
tasks/<immutable-task-id>/
  requirement.md                 # approved requirement AND acceptance criteria
  state.json                     # sole authority: state, round, claims, hashes, events, request ledger
  prompts/001.md, 002.md ...
  implementation-reports/001.json, 002.json ...
  review-reports/001.json, 002.json ...
  correction-prompts/001.md ...   # preserves corrections, including failed fifth round
  blockers/001.json ...
  BLOCKED.json                   # historical index; current blocking state is in state.json
  SUCCESS.json                   # verified acceptance; actionable only when state is complete
  inputs/                        # optional draft inputs; never actionable
```

Example input/report templates are in `examples/`. An example is not a live task. IDs use lowercase letters, digits, hyphens or underscores and cannot change. Every criterion has an immutable unique ID. `requirement.md` must repeat those criteria in prose; `state.json` stores the structured criteria. Sol must check their agreement before creating a task.

## Transitions and commands

`create` → awaiting_implementation; `claim-implementation` → implementing; `publish-implementation` → awaiting_review; `claim-review` → reviewing; `request-round` → awaiting_implementation at the next round; `accept` → complete. `block` records a human/external dependency and releases the claim. `resume` requires an explicit human authorization note and returns to the appropriate awaiting state, never past the round limit.

All mutations require `--task-id`, `--round`, `--expected-state`, `--request-id`. Creation uses expected state `absent` and round 1. Claims additionally take `--owner`; the JSON result contains an ownership token. Result publication, correction and acceptance require `--token` from the matching claim. An exact retry with the SAME request ID and arguments returns the previous result without executing work again. Reusing an ID with different arguments is refused. A replayed claim result is not permission to repeat implementation: inspect the current status first.

Run `python scripts/agent_workflow.py --help` and the command's `--help` for arguments. Representative commands (replace placeholders; PowerShell):

```powershell
$py = '.\.venv\Scripts\python.exe'
& $py scripts/agent_workflow.py status
& $py scripts/agent_workflow.py create --task-id example-001 --round 1 --expected-state absent --request-id example-create --requirement requirement.md --criteria criteria.json --prompt prompt.md --required-check unit-tests --worktree 'D:\path\to\checkout' --pr-url https://github.com/owner/repo/pull/3 --pr-number 3
& $py scripts/agent_workflow.py claim-implementation --task-id example-001 --round 1 --expected-state awaiting_implementation --request-id example-impl-claim-1 --owner 'Luna existing chat'
& $py scripts/agent_workflow.py publish-implementation --task-id example-001 --round 1 --expected-state implementing --request-id example-impl-report-1 --token TOKEN --report implementation.json
& $py scripts/agent_workflow.py claim-review --task-id example-001 --round 1 --expected-state awaiting_review --request-id example-review-claim-1 --owner 'Sol existing chat'
& $py scripts/agent_workflow.py request-round --task-id example-001 --round 1 --expected-state reviewing --request-id example-correction-1 --token TOKEN --report review.json --prompt correction.md --criterion-id c1
& $py scripts/agent_workflow.py accept --task-id example-001 --round 1 --expected-state reviewing --request-id example-accept-1 --token TOKEN --report review.json
& $py scripts/agent_workflow.py block --task-id example-001 --round 1 --expected-state implementing --request-id example-block-1 --token TOKEN --reason 'Dependency unavailable'
& $py scripts/agent_workflow.py resume --task-id example-001 --round 1 --expected-state blocked --request-id example-resume-1 --owner-note 'Owner explicitly authorized resumption after dependency recovered'
```

Acceptance and correction above are alternatives, not successive commands for the same round. An unclaimed task can be blocked only with `--owner-note`; never fabricate human authorization.

## Publication, interruption and stale claims

Every transition uses one exclusive directory lock and publishes complete bytes with flush/fsync and atomic same-directory replacement. Immutable artifact hashes are checked on every load. Files published before an interrupted state transition are orphaned, not actionable. Only files referenced by a committed state are consumed. Do not treat the appearance of a prompt, report, SUCCESS or BLOCKED marker alone as an event. Temporary files and tasks without committed state are never actionable. Retrying the original request can reconcile identical orphan artifacts; conflicting immutable content requires owner intervention.

Status flags claim age and `stale_claim` (default one hour). Old age never permits another worker to claim. If the worker is alive, wait or ask the owner. If a process died holding `.transition.lock`, the owner must verify it is stopped, inspect `owner.json` and any inert temporary files, and explicitly authorize recovery. Remove only the verified lock directory after preserving its owner metadata; do not recursively delete task data. No automatic stale-lock/claim recovery exists. To recover a stale task claim, after owner authorization, block it using its existing token, then resume with that authorization note. Preserve the previous token/event history.

## Commit and verification gates

Implementation reports include task ID, round, PR URL/number, branch, worktree path, exact 40-character SHA, implemented requirement IDs, validation results, unverified checks and blockers. Each validation result has `name`, `status`, `verified`, `evidence`. Publish only a finished committed round. After submission Luna must not move HEAD or edit application files until Sol returns corrections. Sol reviews Git objects at the submitted SHA, not mutable worktree content; use `git show <sha>:<path>` or a separate clean detached checkout. Any preexisting application edits are excluded from review and must remain untouched.

The helper verifies designated worktree/PR, unchanged branch and exact HEAD, absence of new uncommitted application changes under `app/`, `ui/src/`, `config/`, and hashes of all preexisting workspace edits. This is a cooperative protocol, not a security boundary: the reviewer must inspect other source paths and evidence too. Do not stash, reset, commit, or overwrite unrelated edits.

Review reports identify `reviewed_commit`, assess EVERY criterion with status (pass/fail/unverified), evidence and `verified`, and list findings and unverified checks. Acceptance requires every criterion verified/pass, every named required check verified/pass with evidence, no unresolved findings, unverified checks or blockers, and a fresh exact-commit check. Never mark a claimed or unverified test as verified. Missing browser/live verification remains unverified and prevents SUCCESS if required. Acceptance grants no permission to merge/deploy/write production Grist.

Correction prompts cite existing criterion IDs and describe failures within approved scope. Semantic scope and evidence truth require reviewer judgment; the helper validates IDs and gate fields, not the truth of prose. Preserve all rounds and reports permanently. Do not edit published JSON or markdown directly.

## Manual exercise and later checks

Run `python -m unittest discover -s tests -p test_agent_workflow.py -v`. Complete a coordination-only task through both existing chats before enabling any schedule. A simulated local lifecycle proves file mechanics but does not prove the other chat has read and followed the protocol. Manual checks use `status`: Luna claims only awaiting_implementation; Sol claims only awaiting_review. Non-actionable states produce no duplicate work. Scheduling needs a later explicit owner instruction after the two-chat exercise passes.
