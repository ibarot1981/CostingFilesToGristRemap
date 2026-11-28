# Later verification of pending observations

The owner25November backend-first verification method allows a submission to retain truthful pending UI observations until reviewer/owner checks finish. Normal `accept` now accepts an optional `--verification-addendum FILE` for later verified observations without rewriting the immutable implementation report.

The review must list exactly every original pending string under `resolved_submission_checks`, assess every criterion as independently verified/pass, and independently pass every required check. All ORIGINAL implementation validation checks must still be verified/pass; failed or unverified implementation checks cannot be overridden. Open findings, remaining unverified checks, blockers, wrong ownership/round, changed submitted SHA/branch or protected workspace files still prevent acceptance.

The helper saves the addendum, exact tested SHA, reviewer identity and resolved pending strings as an immutable `verification-addenda/NNN.json` alongside the original report and review. Original request retry remains idempotent. This does not authorize merge/deployment/production writes.

Package C round3 used this to record Sol-observed minimal in-app-browser checks after the implementation report was submitted. It is reviewer evidence, not owner signoff. Application acceptance remains at d8788c3e520a65ed77e022e17b41dbc8f8b925dd. The bookkeeping change has25 passing coordination tests, including missing/incomplete evidence rejection, failed implementation check rejection, preserved original bytes and retry.
