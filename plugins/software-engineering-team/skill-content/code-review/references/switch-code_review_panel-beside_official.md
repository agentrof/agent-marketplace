# Code Review Panel

These are the instructions of process switch `code_review_panel` at
`beside_official` for Delivery code review. A task binds this file only when
the project's Process Policy selects that value; at the default,
`single_reader`, code review runs as its flow, skill and role files describe.
Where this file and those files differ on who reads a code review pass and
how its results register, this file governs, and a lens reader's assignment
narrows the three passes its role file runs to its lens. Inside a Delivery
read the value with `process_policy.py value --switch code_review_panel
--delivery DLV-###`; the Delivery pins the policy it runs under.

The panel never replaces the official code reviewer. The official
`code-reviewer` reviews exactly as at `single_reader`, and the panel can only
add blocking findings to its result. `data/code-review-panel.json` declares
the panel's reader role, its lenses with their focus and its lens
assignments; each assignment is one reader. This file decides who reads and
how their results merge into the one code review result. Which findings start
a repair cycle, where a minor finding goes and whether the official
reviewer's own claims are calibrated stay process switch `review_loop`'s.

## Dispatch

1. Freeze the candidate and derive the code review manifest once, as the flow
   says. At this value it carries `code_review_panel`: the pass number, each
   lens assignment with its lens ids, focus and `id_prefix`, and the lens
   result interface.
2. Give the official `code-reviewer` and one fresh `code-reviewer-lens` per
   assignment the same inputs: that manifest, the constitution, this skill
   and the stack checklists it names, `SELF-CHECK` and every file the
   coordinator supplies to the review, such as the lanes' result files. A
   lens reader also gets exactly its assignment. No reader of the step gets
   another reader's reply, QA's output or evidence that appears after
   dispatch, so every reader of the pass works from the same inputs.
3. Start the official reviewer and every lens reader together, beside QA,
   through the host's parallel agent invocation. The implementation writer
   stays idle until `merge-panel` settles the code review.

## Results

Each reader registers its own result with `delivery_verification.py
panel-result --file <result.json>`; at this value `result` refuses a code
review result that is not a confirmed cancellation.

- The official reviewer registers its result as at `single_reader`, with its
  `review_initial` or `review_repair` mode, its checks and its findings, and
  `panel-result` checks it as `result` would before it records when the
  result returned.
- A lens reader returns `role` `code_reviewer`, `mode` `panel_lens`, `lens`
  set to its assignment's lens ids, the session's `candidate_hash` and
  `session_id`, `verdict` `passed` or `failed`, its `report` and its
  `findings`. Each finding is new and open, has an id that starts with the
  assignment's `id_prefix` and carries the skill's finding fields, among them
  `file` and `description`. It reports only defects inside its assignment
  and goes deep rather than wide; a defect outside it is one line of its
  report that names the lens that owns it. It reads the open findings of
  earlier cycles in the manifest and rules on those of its lens only in its
  report: the official reviewer dispositions them.

`panel-result` refuses an assignment or the official result registered
twice, a lens or mode the panel does not declare, an id outside the
assignment's prefix or one that another result of the pass or an earlier
cycle already holds, and any result once the code review settled.

## Calibration

Once the official result and every lens result are registered, one fresh,
read-only calibration reader rules the claims: every open critical or major
finding of a lens result and, when `review_loop` is `blocking_delta`, every
open critical or major claim of the official result that no earlier
calibration ruled. With no such claim, calibration is skipped.

1. Spawn a fresh `code-reviewer` on its own tier, never a `code-reviewer-lens`,
   neither an implementation writer nor a reader of this pass. Give it every
   claim as returned, the official result's findings, the Severity
   Definitions and `SELF-CHECK`, and let it read the frozen candidate through
   `inspect` and `diff`.
2. It returns one row per claim as
   `skill-content/code-review/references/switch-review_loop-blocking_delta.md`
   defines a calibration row, also at `review_loop` `current`: `finding`,
   `claimed_severity`, `calibrated_severity` and a `reason` that cites a line
   the frozen candidate holds as `path:line`, with the follow-up fields on a
   `minor` row. A lens claim may also be ruled `duplicate` with
   `duplicate_of`, the id of the open critical or major official finding, or
   of another lens claim not itself ruled duplicate, that reports the same
   defect and is at least as severe as the claim: no ruling lowers a critical
   claim to major, so a critical claim duplicates a critical finding only. A
   duplicate never gates on its own: the finding it names carries the defect.
3. The calibration reader registers its rows with `delivery_verification.py
   calibrate --file <calibration.json>`, `mode` `calibration`, with the
   claims it ruled exactly as returned. `calibrate` refuses claims other than
   these, a row those rules refuse, a `duplicate_of` outside those findings
   or less severe than its claim and a calibration before every result of
   the pass is registered.

## Merge

`delivery_verification.py merge-panel` registers the one code review result
the machine interface accepts and settles the code review. Its findings are:

- every official finding, with `source` `official`, except a finding of an
  earlier pass with `source` `panel` that the official result re-lists, which
  keeps that source and its `lens`;
- every lens claim calibration confirmed, with `source` `panel`, its `lens`
  and its claimed and calibrated severity;
- every lens claim calibrated `minor`, as a minor finding with the row's
  follow-up fields.

A claim ruled `invalid` or `duplicate`, and a lens finding that is not
critical or major, never enter the result; the calibration rows on the result
keep every ruling. The result keeps the official report, mode and checks, and
it fails when the official result fails or a lens claim is confirmed. Every
check `result` applies holds for it. Its `panel` record keeps the pass
number, each member's result hash, the official reviewer's and the slowest
lens reader's seconds from the freeze, the combined seconds to the merge and
their ratio to the official reviewer's, and the claims by ruling. It lists
the official reviewer's own open critical or major findings in
`official_blocking` and those carried from an earlier pass with `source`
`panel` in `carried_panel_blocking`, and it keeps every lens claim with its
lens, severity, ruling, `file`, `description`, the calibration `reason` and a
`minor` ruling's follow-up fields. `merge-panel` refuses before every result
is registered and while a claim lacks its calibration.

A repair cycle runs the whole panel again beside the official reviewer on the
new candidate: the official reviewer dispositions every unresolved finding,
those with `source` `panel` included, and each lens reader reviews the new
delta through its lens. `approve-item-evidence` records the `panel` record of
every pass of the Item in the Implementation Evidence of its code review
record, with every lens claim of each pass, at either `review_loop` value.
