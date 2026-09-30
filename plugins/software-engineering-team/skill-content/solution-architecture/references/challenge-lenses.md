# Solution Review Plan

Run review panel `solution_design` as
`challenge-review/references/review-panel.md` defines it.
`challenge-review/data/review-panels.json` declares its four challenge lenses,
their focus and the default panel of one fresh, read-only `solution-reviewer`
per lens. Together the lenses also cover BA allocation, topology, naming,
sourcing and decision status. Every reader is independent of the Solution
Architect and of the other readers.

The panel replaces the former single primary reviewer. That primary existed
only so that loading both the entry skill and this reference could not start
two full panels; it saved reader invocations and was never a quality
measurement. The guard stays: run exactly one panel per review, never a
primary reviewer beside it and never a second panel because both sources were
loaded.

Give every reader the same exact engagement, landscape, component, decision
and cited analysis paths, their current evidence, the constitution and its one
lens assignment. Use canonical files, never conversation history or the
author's interpretation as a substitute for evidence. This plan is transient
invocation input, not a new project artifact or approval record.

## Targeted specialists

Add a separate fresh `solution-reviewer` invocation with the affected lens and
exact supporting evidence when the candidate changes any of these risks:

- authorization, privacy, trust boundaries or regulatory obligations;
- ownership, migration, retention or irreversible loss of stored data;
- external integration contracts or failure/reconciliation semantics;
- app/component topology or shared cross-component responsibilities;
- scale, availability or operating-cost commitments with unresolved or
  unquantified constraints.

The specialist is independent of the writer and of every panel reader. Give
it the full affected contracts and dependency context, not just the panel's
conclusions. One specialist may cover related risks explicitly; unrelated
risks need the appropriate focused perspectives. Unknown impact is an
unresolved question: obtain the missing evidence and broaden the review where
needed, never silently declare it outside scope. The panel keeps every lens
assignment even when a specialist is selected, and a specialist is never a
second panel.

## Severity

Rate each finding by what approving the package as written would cause.
Severity is evidence-based, never inflated to look thorough or deflated to
reach a verdict, and the Solution Architect never changes a returned severity.

| severity | blocks approval | the package as written would |
|---|---|---|
| `critical` | yes | commit to a topology, technology, data placement or trust boundary that contradicts an approved requirement, budget or accepted decision, or leave an authorization, privacy, regulatory, data-loss or irreversible-migration obligation unaddressed |
| `major` | yes | leave a decision untraceable or unverifiable, a BA process, app or component misallocated or unowned, a binding to a non-accepted decision, a dependency direction undeclared, or a failure, ownership, cost or exit path missing at the stated scale |
| `minor` | no | still yield the same decisions, verification and ownership |

Minor findings are wording, clarity, redundancy and formatting, and
imprecision that cannot mislead. Imprecision that could lead a careful reader
to a different decision is major, never minor. Findings that share one root
cause are one finding at the severity of that cause.

## Return and disposition

Each panel reader returns its lens, a verdict and findings with severity,
evidence, impact and required resolution. Each specialist returns its assigned
scope, lens coverage, verdict and findings with severity. A verdict requests
changes only while a critical or major finding is open. Reviewers never edit
project files and their replies are not durable audit records. Independent
readers may run in parallel against unchanged inputs; wait for every selected
reader before the Solution Architect writes, then merge findings that share
one root cause as the panel protocol defines.

The Solution Architect resolves each critical or major finding: it fixes the
finding in the final landscape, engagement or decision documents, rejects it
with a concrete reason shown at the approval gate, or records a genuine
product risk with a named revisit trigger. A minor finding never blocks and
never starts another review. Fix it only in a writer pass that already carries
a blocking fix, otherwise show it with its acceptance reason at the approval
gate. Run the mechanical compilers after serialized writes. Re-run only the
lens assignments and specialists affected by a blocking fix, meaning a fix for
a critical or major finding, with the panel's changed-text check and any newly
exposed dependency or risk. A first review with no blocking findings needs no
extra clean round. No fixed retry count, reviewer artifact, digest or lock is
part of the project contract.
