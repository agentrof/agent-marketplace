# Operation contracts in an execution-plan bundle

These are the instructions of process switch `execution_planning` at
`single_source_bundle` for an Operation contract that a Delivery's execution
plan revises. A task binds this file only when the project's Process Policy
selects that value; at the default, `per_document`, and for a contract revised
outside a plan, `flows/operation.md` applies unchanged. Where this file and
that flow differ on such a contract, this file governs.
`execution-plan/references/switch-execution_planning-single_source_bundle.md`
orders the plan's steps.

## Writer

- Writer ownership is unchanged: the QA Engineer alone writes the Verification
  Contract and the DevOps Engineer alone the Environment Contract, through
  `flows/operation.md` steps 1, 2 and 4.
- `skill-content/execution-plan/data/fact-ownership.json` names the fact
  classes your contract owns and the section that holds them. Write each fact
  of those classes once, in that section.
- The architect's handoff is input, not text to copy. For a fact another
  document owns, link its owning section, or cite an owner ruling by its
  `User Decisions` id; never restate it.
- Leave the contract a draft until the bundle verdict is approved. Its
  counterpart reads it in the bundle review, which replaces the separate
  counterpart review of step 3.

## Bundle reader

You read the whole bundle as the counterpart of each revised contract you are
given: the DevOps Engineer for the Verification Contract and the QA Engineer
for the Environment Contract. Read every file the bundle manifest names, and
judge the bundle through every lens of review panel `execution_bundle` in
`skill-content/challenge-review/data/review-panels.json`. These contracts are
project-global, so judge a change's fit for every Delivery that pins them, not
only this one.

A restatement of a fact outside its owning section is a finding that names the
owning section. A restatement that contradicts the owner is `critical`. Rate
every other finding as an Operation contract review does. Return findings with
a stable id, severity, the cited file and section, impact, repair and
verification condition, then `SELF-CHECK` over every manifest path.
