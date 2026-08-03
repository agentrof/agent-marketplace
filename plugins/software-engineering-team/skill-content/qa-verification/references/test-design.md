# Test Design Audit

How the QA role derives the minimal expected test set for each requirement and diffs it against the delivered suite. Verification, not authorship: these methods produce findings about missing tests, never new test code. The [coverage-audit](coverage-audit.md) answers "does each id have a test"; this file answers "does each id have ENOUGH tests", one level finer.

## The Derivation Loop

For each BR-### and AC-### in the brief:

1. Derive the expected checks with the cheapest method that fits the rule's shape (table below).
2. Name each derived check `<id>/<partition-name>` (`BR-007/empty-input`, `AC-002/over-max`).
3. Diff the derived set against the tests actually mapped to that id in the suite.
4. Every derived check with no matching test is a NO-TEST finding carrying the partition name, routed to the developer who owns the suite. QA never writes the missing test.

| Rule shape | Method |
|---|---|
| One input, classed valid or invalid | Equivalence partitioning |
| Numeric, size, or length limit | Boundary values |
| Several conditions combine into one outcome | Decision table |
| Many independent configuration parameters | Pairwise reduction |

## Equivalence Partitioning

Split each input's domain into classes the rule treats identically; one test per class is the minimum expected set.

- DO derive at least one valid class and every distinct invalid class the rule names: wrong type, out of range, wrong format, not permitted in the current state.
- DO treat the rejecting classes as first-class partitions; a suite that only tests the accepting class covers half the rule.
- DON'T count two tests from the same class as extra coverage; they verify the same claim twice and count once in the diff.
- Self-check per class: can you name the input that represents it and the distinct outcome the rule demands? Two classes demanding the same outcome through the same path merge into one.
- Failure symptom in audits: a BR row with one mapped happy-path test and nothing in any rejecting class. That is one PASS-looking row hiding several findings.

## Boundary Values

Where a partition has a numeric or size edge, the minimum expected set is min-1, min, min+1 (and max-1, max, max+1 where a maximum exists).

- DO derive boundaries from the limits the brief states, not from constants in the implementation; the code's constant may itself be the defect under audit.
- DO include the empty case and the just-past-max case for lengths, and zero plus negative for constrained quantities.
- DON'T accept a single mid-range value as covering a boundary partition.
- Finding names carry the edge: `<id>/min-1`, `<id>/max+1`. The owning developer then knows exactly which edge lacks a test.

## Decision Tables

When a rule combines conditions ("premium AND verified AND under limit"), enumerate the combinations as columns with the expected outcome per column.

- DO expect one test per column whose outcome differs from a neighbor's; collapse columns the rule provably treats identically.
- DO check the suite for the failing combinations, not only the all-conditions-true column.
- DON'T let an implicit "otherwise" go unaudited; the default branch is a column too.
- Failure symptom: a compound rule mapped to exactly one test. A rule combining three conditions almost never derives to fewer than four expected checks.

## Pairwise Reduction

For parameter matrices (roles crossed with resource states crossed with request variants), the full cross-product is not the expectation; all-pairs is.

- DO expect every pair of parameter values to co-occur in at least one test; defects overwhelmingly involve one or two parameters interacting, rarely all at once.
- DO keep any exact combination the brief names explicitly, even when a pairwise set would drop it.
- DON'T demand the full cross-product in findings; an inflated expected set is an audit the team learns to ignore.

## Emitting the Findings

- One finding per missing partition, named `<id>/<partition>`. Severity per the ladder in SKILL.md: High when the missing partition is the criterion's demanded outcome, Medium for edge partitions; never Low, because a missing test is not cosmetic.
- Route each finding to the owning developer with the representative input, the expected outcome, and a suggested test name in the suite's naming convention.
- Record the derived-vs-mapped diff per id in the verification record's coverage section, so a re-audit diffs against the same derivation instead of re-deriving from judgment.
