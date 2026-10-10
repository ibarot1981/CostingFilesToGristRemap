# Luna implementer instructions

Read `protocol.md` first. Use the EXISTING implementation chat with GPT 6 Luna, Extra High. Own application changes, tests and designated PR updates. Do not change approved requirements, criteria, reviewer reports or workflow state directly.

1. Run `status`. Only claim an awaiting_implementation task, at its exact current round, using a unique request ID and `--owner 'Luna existing implementation chat'`. Retain the ownership token and read requirement.md and the committed numbered prompt. A retry result does not authorize duplicate work.
2. Work only in the designated checkout and approved scope. Preserve all unrelated edits. No automatic merge, deployment or production Grist/schema writes. The coordination-only manual exercise explicitly needs NO application edits.
3. Finish and commit application work, run required checks, update the designated PR if necessary, and write the implementation report using the example schema. Include exact SHA and honest unverified checks/blockers. Publish through the helper with the ownership token.
4. After awaiting_review, stop application edits and keep the submitted HEAD fixed until review/corrections arrive. Never self-accept or write SUCCESS.
5. Claim corrections only when state is awaiting_implementation at a new round. Use only the existing requirement. Five rounds maximum. Block if human input or an external dependency is required. Never reclaim stale work automatically.

Manual recurring-check text: Read these instructions and protocol; run helper status. If exactly one task is awaiting_implementation, claim its exact round and execute its committed prompt, then publish a complete implementation report with your token. Otherwise do no duplicate work. Report stale claims or blockers to the owner. Keep submitted work fixed while awaiting review. No schedule is enabled by this text.
