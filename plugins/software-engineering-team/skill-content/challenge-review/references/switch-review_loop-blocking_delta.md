# Review Loop

These are the instructions of process switch `review_loop` at `blocking_delta`.
A task binds this file only when the project's Process Policy selects that
value; at the default, `current`, every review loop runs as its flow and role
files describe. The value applies with either value of `review_panels`: to
the step's single reviewer at `single_reader` and to every lens reader of the
step's panel at `lens_panel`. Where this file and a flow or role file differ on
a review loop, this file governs.

It covers the backlog epic and root reviews, the Solution Design review, the
Design System review and the Operation contract reviews. Every reader and the
writer of those steps bind it: a role that does not bind this skill, the
Operation counterparts and every step's writer, adds
`--skill challenge-review` to its task. Business Analysis challenges keep
their own loop, the Experience attestation stays advisory, and Delivery code
review follows `code-review/references/switch-review_loop-blocking_delta.md`.

## Severity

Only a critical or major finding blocks, once calibration confirms it. A step
rates findings by its own table where it has one: the backlog Review findings
section of `product-planning/references/structured-records.md` and the
Solution Severity table of `solution-architecture/references/challenge-lenses.md`.
Design System and Operation contract reviews use this table:

| severity | blocks | the reviewed document as written would |
|---|---|---|
| `critical` | yes | contradict an approved source or accepted decision, or drop or weaken a security, privacy, accessibility, data or irreversible-action obligation |
| `major` | yes | be unverifiable, unexecutable or unowned, or let careful readers build, test or operate different behavior |
| `minor` | no | still yield the same behavior, verification and ownership |

Imprecision that could mislead a careful reader is major, never minor.
Findings that share one root cause are one finding at the severity of that
cause. The writer never lowers a returned severity, and a verdict requests
changes only while a confirmed critical or major finding is open.

## Minor findings

A minor finding never blocks and never starts a round. Fix it only in a writer
pass that already carries a blocking fix, whose re-review reads all changed
text. Otherwise leave the reviewed text unchanged and record the finding as a
follow-up with an owner role, the reason the text is safe to accept and a
revisit trigger:

- Backlog: the review note's `Accepted Minor Findings` table, as the Review
  findings section of `product-planning/references/structured-records.md`
  defines.
- Solution Design: shown with its acceptance reason at the approval gate, as
  the Solution review plan defines.
- Design System: shown at the approval gate with its reason, the owner role
  `ux_designer` and its revisit trigger.
- Operation contracts: the contract's optional `Accepted Minor Findings`
  section, placed before `Navigation` and before a generated relations block:

```markdown
## Accepted Minor Findings

| finding | owner_role | reason | revisit_trigger |
|---|---|---|---|
| [[operation/verification-contract\|Verification Contract]] The Contract section states the test workdir twice in different words. | qa_engineer | Both sentences name one directory, so the test command runs the same way. | Revisit at the next revision of the Verification Contract. |
```

`finding` states the minor finding and cites the affected vault note with an
escaped-table wikilink. `owner_role` is `qa_engineer` or `devops_engineer`,
the Operation contract writer that follows it up. `reason` says why the text is
safe to accept as written, and `revisit_trigger` names the event that reopens
it. `operation_compile.py` validates every row whenever the section is present;
a contract without accepted minor findings omits it. Recording the section is
not a change to the reviewed text and starts no re-review.

A critical or major finding never enters an `Accepted Minor Findings` section
or an approval-gate list of minor findings. It closes only through a fix or a
disproof that the re-review confirms.

## Re-review

After a writer pass fixes or disproves a critical or major finding, the
re-review reads only:

- the open critical and major findings, as returned, with their evidence;
- the changed text: the diff between the reviewed revision and the fixed one;
- its dependency context: every note, contract or decision the changed text
  cites or that cites it, and anything else a fix could have touched.

For the Solution Design, Design System and Operation contract reviews, commit
the candidate before its first review, so the reviewed revision has a commit.
Write the open critical and major findings to a record under the project-local
runtime directory, `.agentrof/agent-marketplace/.runtime/`, and derive each
rerun reader's task with `--findings <record>`, `--base <reviewed commit>` and
one `--input` per changed path and dependency-context note, and no other
document of the step. A backlog re-review keeps its regenerated manifest:
the changed paths, the files whose `sha256` changed, are the changed text, as
the Review findings section of `structured-records.md` defines.

Each rerun reader confirms that every finding it was given is closed and
reviews the changed text; it never re-audits unchanged text. The step's own
rule selects the rerun readers: at `single_reader` one fresh reviewer of the
step's reader role, for Solution Design the primary reviewer with the affected
specialists; at `lens_panel` the assignments that returned a blocking finding,
and no other. The first of those assignments in `default_panel` order also
reads every changed path with its dependency context through all of the
step's lenses and names the owning lens of any new finding, and a newly
exposed risk in a lens that did not rerun adds that lens's assignment.
A new critical or major finding continues the loop; a new minor finding
follows the minor rule. A writer's claim that a fix is complete never replaces
the re-review, and no clean extra round runs once no critical or major finding
is open.

## Calibration

Before a verdict with an open critical or major finding becomes a gate, one
fresh, read-only calibration reader checks every critical and major claim of
the review. Calibration is skipped when no critical or major finding is open.
It rules each claim once: a new critical or major finding of a re-review gets
its own calibration before it gates.

- The calibration reader is neither the writer nor a reader that returned a
  finding of the review. Spawn a fresh instance of the claiming reviewer's
  role on that role's own tier: `backlog-reviewer`, `solution-reviewer` or
  `design-system-reviewer`, and for an Operation contract the non-writing
  counterpart, `devops-engineer` for the Verification Contract and
  `qa-engineer` for the Environment Contract. Never spawn a `-lens` variant
  for calibration, also when a lens panel returned the claims: a lower
  calibration tier needs its own data first. Derive its task as the step
  derives its reader's, in mode `review`, adding
  `--findings <record of the claims>`.
- Give it each claim as returned, with its id, severity, evidence, impact and
  cited paths, the step's severity table, the constitution and `SELF-CHECK`.
  Never pass the writer's triage or interpretation, another reply or the
  conversation.
- It returns one row per claim: `finding`, `claimed_severity`,
  `calibrated_severity` and `reason`. `calibrated_severity` is the claimed
  severity when the claim holds, `minor` when the text as written still yields
  the same behavior, verification and ownership, and `invalid` when the cited
  text disproves the claim. `reason` cites the text that decides it: a
  wikilink or path and the passage. Imprecision that could mislead a careful
  reader stays major, and calibration never raises a severity.
- A row that lowers or invalidates a claim without citing the text is refused:
  that claim keeps its claimed severity.
- Only confirmed critical and major findings keep the verdict at
  `changes_requested` and start a writer pass. A claim calibrated `minor`
  follows the minor rule, and one calibrated `invalid` closes with its cited
  evidence. The writer never changes a returned or calibrated severity.

Record every row. A backlog review note keeps them in an optional
`Severity Calibration` section placed before `Verdict`:

```markdown
## Severity Calibration

| finding | claimed_severity | calibrated_severity | reason |
|---|---|---|---|
| F-3 | major | minor | [[backlog/epics/identity/stories/sign-in/story\|ST-002]] Scope states the lockout rule twice, and both sentences name five attempts, so behavior and tests stay the same. |
```

Solution Design, Design System and Operation contract reviews keep no review
record, so they show the rows with the verdict at the approval gate.
