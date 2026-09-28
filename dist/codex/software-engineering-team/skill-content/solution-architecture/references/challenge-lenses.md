# Solution Review Plan

Use one fresh, read-only `solution-reviewer` as the primary reviewer for the
candidate engagement and package. The primary is independent of the Solution
Architect and covers all four lenses below together with the role's allocation,
topology, naming, sourcing and decision-status checks. Lenses are coverage
obligations within this review, not four additional reviewer assignments.

Give each reviewer exact engagement, landscape, component, decision and cited
analysis paths, their current evidence, the constitution and its assigned
coverage. Use canonical files, never conversation history or the author's
interpretation as a substitute for evidence. This plan is transient invocation
input, not a new project artifact or approval record.

## Required primary lenses

- **technology-fit-and-traceability:** Does every verdict trace to an approved
  requirement or budget? Are assumptions and unverified cells named?
- **sustainability-and-operability:** Who owns each component, failure mode,
  observability concern and future change cost?
- **cost-and-lock-in:** Is cost judged at the stated scale and is the exit path
  concrete rather than a slogan?
- **security-and-compliance:** Which trust boundaries, data obligations and
  deferred security decisions are present?

## Targeted specialists

Add a separate fresh `solution-reviewer` invocation with the affected lens and
exact supporting evidence when the candidate changes any of these risks:

- authorization, privacy, trust boundaries or regulatory obligations;
- ownership, migration, retention or irreversible loss of stored data;
- external integration contracts or failure/reconciliation semantics;
- app/component topology or shared cross-component responsibilities;
- scale, availability or operating-cost commitments with unresolved or
  unquantified constraints.

The specialist is independent of both the writer and the primary reviewer.
Give it the full affected contracts and dependency context, not just the
primary's conclusions. One specialist may cover related risks explicitly;
unrelated risks need the appropriate focused perspectives. Unknown impact is
an unresolved question: obtain the missing evidence and broaden the review
where needed, never silently declare it outside scope. The primary retains
all four lenses even when a specialist is selected. Do not add a second full
panel merely because both the entry skill and this reference were loaded.

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

The primary returns a verdict, findings with severity, evidence, impact and
required resolution, and a coverage statement for every required lens. Each
specialist returns its assigned scope, lens coverage, verdict and findings
with severity. A verdict requests changes only while a critical or major
finding is open. Reviewers never edit project files and their replies are not
durable audit records. Independent readers may run in parallel against
unchanged inputs; wait for every selected reader before the Solution Architect
writes.

The Solution Architect resolves each critical or major finding: it fixes the
finding in the final landscape, engagement or decision documents, rejects it
with a concrete reason shown at the approval gate, or records a genuine
product risk with a named revisit trigger. A minor finding never blocks and
never starts another review. Fix it only in a writer pass that already carries
a blocking fix, otherwise show it with its acceptance reason at the approval
gate. Run the mechanical compilers after serialized writes. Re-run only
readers affected by a blocking fix, meaning a fix for a critical or major
finding, and any newly exposed dependency or risk. A first review with no
blocking findings needs no extra clean round. No fixed retry count, reviewer
artifact, digest or lock is part of the project contract.
