# Findings and convergence

## Finding record

Use a stable ID, severity, affected path/line or artifact, concrete evidence,
impact, proposed correction, verification condition and status. Identify whether
the candidate introduced the defect or exposed a pre-existing issue. State a
missing behavior at the source location that should implement it. Unsupported
speculation can be a question, never a blocking finding.

Critical means a demonstrated path to serious security compromise, data loss or
corruption. Major means a material failure of required behavior, compatibility
or an accepted architectural constraint. Minor means advisory maintainability,
style or polish without that consequence. Grade by impact and reachability,
including assumptions needed to trigger it. A raw literal or missing abstraction
is not automatically critical.

## Review scope

The first pass checks the whole candidate against its task and relevant
consumers. The reviewer uses the owner's exact candidate and its evidence.
Report unavailable evidence without staging, changing files or launching a
duplicate proof. A requested diagnostic check must fit the project's review
policy and permissions.

Separate defects in implementation from a conflict in accepted requirements or
architecture. An unresolvable ownership/consistency decision needs its decision
authority. Offer the concrete contradiction and smallest viable resolution.

## Correction loop

Group repeated instances under their root cause. Keep existing IDs/statuses.
After a fix, inspect the delta, unresolved findings and affected consumers.
Require new evidence to reopen a resolved finding. Do not repeatedly mine the
same unchanged code for fresh stylistic requests.

Stop at the delivery flow's correction limit. Return the remaining blocking
evidence and a changed approach or owner decision. Passing a time budget never
turns a failed invariant into approval. Advisory and unrelated work remains
outside the current correction loop unless authorized.

The final verdict states candidate identity, counts of open critical/major/minor
findings and unchecked scope. Zero open critical/major findings yields
`approve` only when required verification evidence is available.
