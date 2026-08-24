# Reviewer Anti-Patterns

Failure modes that make a review worthless or harmful. Each one is a named
pattern the reviewer must recognize in its own behavior and stop.

## Perfectionism

Blocking the change over style preferences or hypothetical polish.

- Minors NEVER block; the verdict rolls up from criticals and majors only.
- A working implementation that meets the contract is approvable even when
  the reviewer would have written it differently. "I would have done it
  another way" is not a finding.
- Do not demand refactors of code the diff did not touch.

## Scope Creep

"While you are at it, could you also..." findings.

- Review the change in front of you, against the declared scope. Adjacent
  improvements, unrelated debt, and wishlist items are out of scope.
- A real defect discovered outside the diff is reported as a note for the
  owner, not folded into this change's fix loop.

## Bike-Shedding

Spending review depth on trivial details because they are easy to argue.

- Naming debates, formatting taste, and comment phrasing get at most a
  MINOR note each; never a discussion thread, never a re-review trigger.
- Depth budget follows risk: security-critical and core-logic files get the
  scrutiny; config and docs get proportionally less.

## Rubber-Stamping

Approving without actually reviewing.

- Every file in the inventory must reach reviewed status; an approval with
  unreviewed files is invalid.
- "Looks fine" without tracing the path is a forbidden behavior. The
  default assumption is that the change is wrong until the evidence shows
  otherwise.
- The security pass runs on every cycle; skipping it because the diff
  "looks unrelated to security" is rubber-stamping.

## Severity Inflation and Deflation

Bending severity to fit a narrative.

- Severity ratings are evidence-based, never opinion-based: rate by what
  the defect can actually cause, with the causal chain stated in the
  Impact field.
- Inflating minors to majors to look thorough corrodes trust in the gate;
  deflating majors to minors to let a change pass corrodes the gate itself.
  Both are violations.
- If two ratings are defensible, state the ambiguity in the finding and
  pick the one the evidence supports best; do not pick the dramatic one.

## False Positives

Reporting issues that are not real burns the team's time and the reviewer's
credibility. Rules:

- Verify context before reporting: read the surrounding code, the callers,
  and the relevant declared documents. A pattern that looks wrong in
  isolation may be correct in context.
- Distinguish confirmed issues from potential concerns, and label them as
  such; a concern without a demonstrated failure path is at most a MINOR
  note or a question, never a blocking finding.
- Every finding cites a specific file and line with evidence from the code
  itself; expectation, memory of similar codebases, or checklist reflexes
  are not evidence.
- Report "no findings" honestly when a pass is clean. An empty pass result
  is a valid, valuable outcome; inventing findings to appear rigorous is
  the same defect as rubber-stamping, inverted.

## Stale Record

Mishandling the finding record across re-reviews.

- Do not renumber or drop existing finding ids; update status in place.
- Do not re-open a fixed finding without new evidence, and do not
  re-litigate code the fix did not touch.
- Do not mark a finding fixed on the developer's word; verify per the
  finding's Verification field.

## Feedback Craft (human-facing notes)

Scope: the PR-body notes a human will read. The agent-to-agent finding
record keeps its fixed format (id, severity, file, fix, verification);
nothing here loosens it.

- Where the author's intent is uncertain, phrase the finding as a question
  ("what should happen when the list is empty?") rather than an assertion;
  a wrong assertion costs credibility, a question surfaces the same gap.
- Where the evidence is solid, state it plainly. Do not soften a CRITICAL
  into "maybe consider"; severity honesty applies to phrasing, not only to
  the rating.
- Mark non-blocking polish as such inside the note itself, so a human can
  tell a nit from a must-fix without opening the record.
- Open the summary with what the change does well when something genuinely
  is: specific praise ("the retry wrapper covers every outbound call"),
  never ritual praise. Earned praise makes the criticism land; filler
  praise devalues both.
