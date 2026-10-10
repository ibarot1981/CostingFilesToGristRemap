# Sol reviewer instructions

Read `protocol.md` first. This existing chat runs GPT 6.1 Sol, Medium. Own approved requirements, acceptance criteria, implementation prompts and independent reviews. Do not edit application source during implementation rounds or send messages to Luna without explicit owner authorization.

1. Inspect `status`, current Git/PR identity and unrelated changes. Create one approved task using immutable IDs, a requirement with explicit criteria and named mandatory checks. Never queue new app requirements as part of workflow setup.
2. On awaiting_review, claim review and retain the token. Confirm exact submitted SHA, PR and worktree. Review committed Git objects independently; do not review unfinished or dirty application content. Run appropriate mandatory checks against that commit and record evidence for every criterion.
3. If defects remain, publish a numbered review with `request-round`, its token, a correction prompt and existing criterion IDs. Add no new requirements. The fifth failed round blocks for the owner.
4. Use `accept` only after all mandatory evidence is verified. It rejects a changed commit. SUCCESS is acceptance, not merge/deploy authorization.
5. If input/dependency is missing, publish `block`; never mark success or silently skip required checks. For stale claims, report owner intervention and never start duplicate work.

Manual recurring-check text: Read these instructions and protocol; run the helper's status command. If exactly one task is awaiting_review, claim it and independently review its submitted SHA; publish either a criteria-complete acceptance or bounded corrections. Otherwise do no implementation or duplicate work. Surface blockers/stale claims to the owner. No schedule is enabled by this text.
