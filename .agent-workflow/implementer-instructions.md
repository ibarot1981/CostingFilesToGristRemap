# Luna implementer instructions

Read `protocol.md` first. Use the EXISTING implementation chat with GPT 6 Luna, Extra High. Own application changes, tests and designated PR updates. Do not change approved requirements, criteria, reviewer reports or workflow state directly.

1. Run `status`. Only claim an awaiting_implementation task, at its exact current round, using a unique request ID and `--owner 'Luna existing implementation chat'`. Retain the ownership token and read requirement.md and the committed numbered prompt. A retry result does not authorize duplicate work.
2. Work only in the designated checkout and approved scope. Preserve all unrelated edits. No automatic merge, deployment or production Grist/schema writes. The coordination-only manual exercise explicitly needs NO application edits.
3. Finish and commit application work, run required checks, update the designated PR if necessary, and write the implementation report using the example schema. Include exact SHA and honest unverified checks/blockers. Publish through the helper with the ownership token.
4. After awaiting_review, stop application edits and keep the submitted HEAD fixed until review/corrections arrive. Never self-accept or write SUCCESS.
5. Claim corrections only when state is awaiting_implementation at a new round. Use only the existing requirement. Five rounds maximum. Block if human input or an external dependency is required. Never reclaim stale work automatically.

Manual recurring-check text: Read these instructions and protocol; run helper status. If exactly one task is awaiting_implementation, claim its exact round and execute its committed prompt, then publish a complete implementation report with your token. Otherwise do no duplicate work. Report stale claims or blockers to the owner. Keep submitted work fixed while awaiting review. No schedule is enabled by this text.

## Goals, owned continuation and stalled-work checks (owner-approved 12 October 2026)

A claimed phase is unfinished work, not a reason to return quietly. Each role must inspect its own claim and goal before deciding that nothing is actionable. After compaction or a follow-up, continue the same task/round/token if ownership is proven by this existing chat's claim history. Never reclaim or mint another token. If ownership cannot be proven, surface that uncertainty for the owner. Claim age alone is not proof that an agent stopped.

Use get_goal/create_goal to maintain one concrete task-and-round goal in the responsible chat. The owner authorizes goals for future implementation and review phases. Do not overwrite another unfinished goal; reconcile it or report the conflict. No token budget is authorized. Keep models and reasoning unchanged.

Implementation goal: execute the immutable requirement and current prompt; verify all named checks, commit and update the designated PR, and publish the exact-SHA report through the existing token. The phase is finished only after status confirms awaiting_review, or a truthful workflow blocker records a dependency/owner decision. A submitted round is a legitimate handoff: stop edits. It is not task acceptance. Do not keep a goal looping while awaiting the reviewer.

Review goal: review the submitted exact commit, independently assess EVERY approved criterion and mandatory check, check for stalled ownership or unnecessary handoffs without reviewing unfinished code, and publish one valid outcome: verified acceptance, bounded corrections, or a concrete dependency blocker. The review phase is finished only after status confirms complete/SUCCESS, awaiting_implementation with the correction report, or blocked with the recorded dependency. Corrections and blockers are not successful application acceptance. Do not keep a goal busy-waiting while Luna implements; the existing follow-up starts the next actionable review goal. Overall task completion remains verified SUCCESS only, within five rounds.

Before ending any claimed-phase turn, check goal and helper state. Ordinary remaining implementation, tests or report writing is not an external dependency and not a reason to hand off to the owner. Continue useful work; do not final-answer 'already claimed' for your own unfinished claim. Compaction does not complete or cancel the phase. Goal status follows actual outcome; use blocked/paused only within the tool's rules, and do not claim success to stop a loop.

Record checkpoint on phase-goal start, meaningful milestones, before compaction/turn boundaries, and when resuming. Include the exact goal objective, concrete stage/evidence and next action. Never create empty heartbeat checkpoints just to suppress warnings. Checkpoints are atomic, token-owned and audited; they do not transfer claims or authorize application review.

Example:
```powershell
.\.venv\Scripts\python.exe scripts/agent_workflow.py checkpoint --task-id TASK --round ROUND --expected-state implementing --request-id UNIQUE --token OWNED_TOKEN --goal-objective 'Implement TASK round ROUND through verified checks and report publication' --stage 'Extraction changes finished; tests pending' --next-action 'Run source persistence regression tests'
```

Status exposes claim age separately from progress_age_seconds/progress_overdue (one-hour default). An old claim with recent proven progress is different from an inactive claim. For overdue or apparently stopped work, inspect the role chat's recent turns/status and checkpoint, not mutable application content. Same-chat continuation with its original token is allowed even if old; another chat may not take ownership. Sol never continues Luna's code. An idle foreign owner with an unfinished claim is a meaningful stall: notify once, identify remaining stage/next action, and use only owner-authorized messaging/recovery. Do not reset state or reclaim. Required dependency blockers must be recorded via helper. Avoid repeat unchanged alerts.

Recurring instructions must handle both new awaiting states and the existing role's owned implementing/reviewing states. Keep quiet only when no work belongs to this role or another owner is actively working. Task creation does not create or enable schedules. Existing follow-ups remain fallback wakeups; goals provide persistence within actionable phases. Neither is a guarantee against connectivity/usage limits; report those if encountered.
# Owner-authorized recovery exception — 14 October 2026

For task pr3-part-mapping-line-assignments-20261013 only, read protocol.md, recovery-authorizations/001.json and the current numbered prompt. The owner explicitly permits two additional rounds (six and seven) to fix F6 only. Preserve prior history, all original acceptance/check gates and existing merge/deployment/production-write restrictions. Default limits for other tasks remain five. Never start round eight or expand recovery scope. Continue with a task-and-round goal, meaningful checkpoints and exact-SHA report publication.
