---
name: review
description: Independently checks a candidate against its outcome, contracts and risks, producing evidence-backed findings within a bounded correction loop.
exposure: internal
---

# Review

## Outcome

A candidate-specific verdict and a compact set of actionable defects, with
review scope and limitations explicit.

## When to use

Load for the delivery review or a requested independent assessment. Select
security, data and architecture depth from risk independently of change size.

## Inputs and authority

Use the request/task, exact candidate and base, full change inventory, relevant
accepted contracts, supplied procedure packet, prior findings and verification
evidence. The packet contains the applicable source excerpts and Graphify result.
Apply those excerpts directly and open linked depth only when needed. Review is
read-only. Native worker permissions provide any technical restriction. This
instruction alone cannot enforce read-only access. Reviewers return findings
without editing, staging, committing or approving their own fixes.

## Method

1. Read the supplied procedures, task and full diff, then the affected callers, dependencies and
   source contracts. Verify the candidate matches the supplied evidence.
   Inspect behavior, error/refusal paths, data integrity and runtime wiring.
   Apply the supplied security-audit guidance for security-sensitive changes.
   For every required procedure, return a concrete assessment and current
   source-file line citations, including a reason when no defect is found.
   Identify missing evidence explicitly.
2. Check compatibility and the smallest sufficient design. Trace changed
   documentation to implemented behavior. Identify missing cases that can
   cause a concrete failure, rather than mechanically demanding every pattern.
3. For each defect, supply evidence, impact, a specific correction and a
   verification condition. Grade severity by actual consequence. Style and
   optional cleanup remain advisory. Zero findings is a valid result.
4. Keep finding identities stable. Re-review unresolved findings, the repair
   delta and affected consumers. Do not reopen a resolved point without new
   evidence. Group instances sharing one root cause.
5. Respect the flow's correction budget. At the limit, return the unresolved
   evidence and recommend a changed approach or the required owner decision.
   A disagreement about accepted architecture goes to its decision authority.

## Evidence and completion

Return `approve` when no critical or major finding remains and required evidence
is available, otherwise `fix_required`, with candidate identity, severity counts
and unchecked scope. Follow the launcher's result schema. Its checks bind source
delivery, coverage and citations to this candidate. They cannot establish the
truth of a natural-language assessment. Missing coverage remains unresolved.
Merge authority remains with the delivery flow and host controls, separate
from this review verdict.

## Conditional references

- For finding severity, scope or re-review, read [findings](references/findings.md).
- For authentication, secrets, untrusted input or consequential actions, read [security](references/security.md).
- For stored-state changes, load [data-design](../data-design/SKILL.md).
- For architecture changes, read [architecture](../solution-design/references/architecture.md).
