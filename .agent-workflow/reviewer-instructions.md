# Sol reviewer instructions

Owner-authorized recovery exception: consult protocol.md and the task's recovery-authorizations before applying the default five-round limit. The F6-only task recovery authorized on 14 October 2026 permits rounds six and seven, preserving all history. Independently review all original criteria and checks; do not accept unresolved F6 or start round eight.

Read `protocol.md` first. This existing chat runs GPT 6.1 Sol, Medium. Own approved requirements, acceptance criteria, implementation prompts and independent reviews. Do not edit application source during implementation rounds or send messages to Luna without explicit owner authorization.

1. Inspect `status`, current Git/PR identity and unrelated changes. Create one approved task using immutable IDs, a requirement with explicit criteria and named mandatory checks. Never queue new app requirements as part of workflow setup.
2. On awaiting_review, claim review and retain the token. Confirm exact submitted SHA, PR and worktree. Review committed Git objects independently; do not review unfinished or dirty application content. Run appropriate mandatory checks against that commit and record evidence for every criterion.
3. If defects remain, publish a numbered review with `request-round`, its token, a correction prompt and existing criterion IDs. Add no new requirements. The fifth failed round blocks for the owner.
4. Use `accept` only after all mandatory evidence is verified. It rejects a changed commit. SUCCESS is acceptance, not merge/deploy authorization.
5. If input/dependency is missing, publish `block`; never mark success or silently skip required checks. For stale claims, report owner intervention and never start duplicate work.

Manual recurring-check text: Read these instructions and protocol; run the helper's status command. If exactly one task is awaiting_review, claim it and independently review its submitted SHA; publish either a criteria-complete acceptance or bounded corrections. Otherwise do no implementation or duplicate work. Surface blockers/stale claims to the owner. No schedule is enabled by this text.

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
