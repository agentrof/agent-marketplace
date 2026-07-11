---
name: code-review
description: Review framework for the software-team code reviewer. Defines the finding contract, severity taxonomy, verdict rollup, the single evolving finding record, and the three review passes. Loaded by the reviewer agent during review loops, never invoked directly.
user-invocable: false
---

# Code Review Framework

The contract every review cycle must satisfy. The reviewer emits findings and
a verdict; it never mutates code. Depth lives in the references listed at the
end of this file.

## When to Use

Loaded by the software-team code-reviewer agent at the start of every review
cycle, first review and re-reviews alike. Not a user-facing skill. During
review, also load the review checklist file shipped by each stack skill bound
to the change (its `references/review-checklist.md`); those checklists supply
the stack-specific items, this skill supplies the framework they plug into.

## Finding Format

Every finding MUST carry all of the following fields. A finding missing any
field does not exist.

- **Id:** `F-###`, stable across the whole review loop (see record rule below)
- **Severity:** CRITICAL | MAJOR | MINOR
- **Category:** the pass and checklist item that produced it
- **File:** `path/to/file:line` (repo-relative; one anchor per finding)
- **Description:** what is wrong, stated from the code itself, not from expectation
- **Impact:** why it matters: the security, correctness, or maintainability risk
- **Fix:** the specific, actionable change; never vague advice
- **Verification:** how to confirm the fix works (test to run, behavior to observe)

## Severity Definitions

- **CRITICAL:** security holes, data loss or corruption, auth bypass,
  injection vectors, secret exposure. Blocks the verdict.
- **MAJOR:** architecture or contract violations, missing error handling,
  type-safety gaps, broken error propagation across layer boundaries.
  Blocks the verdict.
- **MINOR:** style, naming, micro-optimizations, missing docstrings.
  Recorded as notes; minors NEVER block.

Severity is evidence-based. DO rate by what the defect can actually cause;
DON'T inflate to look thorough or deflate to pass the gate.

## Verdict Rollup

- Verdict enum: `approve` | `fix_required`.
- `fix_required` if and only if at least one CRITICAL or MAJOR finding is
  open. Otherwise `approve`, regardless of the minor count.
- Close every cycle with the verdict plus counts by severity and the top
  priority fixes.

## Single Evolving Record

One finding set per review loop, evolving across re-reviews. The
canonical copy lives in the orchestrator's PMO database; the spawn
prompt hands you the currently open findings, and your reply returns the
updated set for the orchestrator to record.

- DO keep `F-###` ids stable: a re-review updates each existing finding's
  status (open, fixed, wont-fix) on the same id.
- DO assign new ids only to genuinely new issues; the record is an audit
  trail across cycles, not a fresh dump per pass.
- DON'T renumber, delete, or re-open findings without new evidence.
- On re-review, re-check what changed plus anything a fix could have
  touched; do not re-litigate untouched, already-passed code.

## The Three Passes

Fixed order, all three on every cycle. Security is never optional. Full
checklists per pass live in the passes reference.

1. **Correctness:** logic, edge cases, error propagation, type safety.
2. **Conformance:** the change against the declared interface contract, data
   model, decisions, and ownership map; every conformance finding carries an
   architectural impact rating (high, medium, low).
3. **Security:** authentication and authorization, input validation, data
   protection, common vulnerability classes.

## Architecture-Implicating Findings

Some findings do not belong in the fix loop. When a finding implicates the
approved architecture itself, escalate it to the architecture owner with its
impact rating instead of proposing a local patch; a local patch would
entrench the violation.

Canonical example: an undeclared denormalized copy of a mutable field,
written from more than one owner with no declared sync path. The defect is
in the design, not the diff; routing it to the developer produces a patch
that hides the drift instead of removing it.

Route to the owner when the finding reveals:

- an undeclared copy of mutable data, or two writers to one field
- a contract the implementation cannot satisfy as designed
- a module writing across a declared ownership boundary
- a decision the code contradicts that no local change can reconcile

Everything else enters the normal fix loop via `fix_required`.

## References

- [passes](references/passes.md): the full per-pass checklists (correctness, conformance, security) and the architectural impact rating. Read when executing the three passes on any cycle.
- [pitfalls](references/pitfalls.md): reviewer anti-patterns plus feedback craft for human-facing notes. Read when a verdict feels off (too harsh, too lenient) or when writing PR-body notes for a human.
- [smells](references/smells.md): smell-to-named-move table for maintainability findings. Read when a finding concerns code structure and its Fix field needs a named refactoring move.
