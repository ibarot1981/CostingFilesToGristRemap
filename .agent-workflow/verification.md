# Setup verification — 2026-10-10

Command: `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_agent_workflow.py -v`

Result: 16 tests passed (30.363 seconds). Coverage includes separate-process simultaneous claims; interrupted replacement; report published before interrupted state publication and exact retry recovery; real process exit during publication leaving only an inert temporary file; repeated claims/publications/acceptance; conflicting request IDs; wrong task/round/state/token; changed commit; incomplete review; missing or unverified required checks; immutable requirement tampering; one active task; new scope rejection; fifth-round blocking with history preserved; stale claim visibility; owner-authorized blocker resume; unfinished application changes and preexisting-edit preservation.

The synthetic `local-handoff-exercise-20261010` traversed create → claim implementation → publish → claim review → accept using a local fixture Git repository under its ignored task directory. Its SUCCESS confirms only this synthetic fixture exercise; it is not acceptance of any application change or PR. Both roles were simulated locally by Sol.

The real existing-chat exercise is `manual-handoff-20261010`, initially awaiting_implementation. It requires Luna to read the protocol, claim, run the workflow tests and submit the current committed setup SHA. Sol must independently review and reproduce verification. This exercise makes no application source changes or PR updates. Its PR identity is recorded for traceability; remote PR content is outside this coordination-only task's scope.

No scheduled checks, external messages, automatic merge, deployment or production Grist/schema writes were created. Unrelated workspace edits remain untouched. Runtime task files are excluded from Git.
