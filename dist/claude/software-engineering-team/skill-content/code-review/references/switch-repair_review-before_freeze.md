# Review repairs before freeze

Process switch `repair_review` at `before_freeze` adds a pre-freeze correctness
review after the first verification candidate. It does not replace the full
code review, lens panel, calibration, QA or live verification of the next pass.
The default `after_freeze` preserves the existing workflow. Select this value
through the project's Process Policy.

1. Commit the repaired product and tests. The coordinator runs
   `delivery_verification.py --worktree <item-root> repair-review-manifest
   --delivery DLV-### --story <story>` and saves the JSON in verification scratch.
   A first or unchanged candidate reports that no review is required.
2. When required, invoke a fresh read-only `code-reviewer` with the manifest and
   its correctness-contract lens. The repair writer never reviews its own fix.
   Read the exact `base_commit` to `head_commit` diff in `repair_delta`, its source
   inputs and `findings_to_check`. Check whether the repair preserves the original
   invariant, replaces a narrow exception with an unchecked path, uses limits
   consistent with the contract, or adds tests that endorse the defect. Read both
   versions of a deleted file. Do not edit the candidate or run a test suite.
   This review may run beside the single-case live repair runs; the ordinary
   freeze still waits for earlier readers and runtime cleanup.
3. Return JSON with the manifest's `manifest_hash`, `candidate_hash` and
   `source_session_id`, `role: code_reviewer`, `mode: repair_review`, the native
   reader identity in `reviewer`, `verdict: passed|failed`, a substantive `report`
   and `findings`. Every finding has a new ID beginning with `finding_prefix`,
   declared `severity`, `status: open`, `file`, `description` and `verification`.
   Critical and major findings require `failed`; minor findings may accompany
   `passed`. Do not copy an earlier finding as a new one.
4. The coordinator records the result with `repair-review-result --delivery
   DLV-### --story <story> --file <result.json>`. `freeze`, including a freeze
   called by `start-pass`, refuses a missing, changed, stale or failed result.
   `start-pass` checks this requirement before `regression-selection` or
   `regression-run`, so an unreviewed repair starts no expensive verification.
   A product, contract, instruction or previous-pass findings change invalidates
   the review; obtain a new manifest and fresh review of the changed repair.
5. Repair a blocking result and repeat this review before another freeze.
   Passing minor findings enter the next pass's unresolved findings, with their
   original IDs, for the full reviewer to disposition. The new session retains
   the repair review receipt and its duration timestamp as audit evidence.

A passed result approves only starting verification of the bound repair. It is
not approval of the Item, a merge, a release, or an exception to another gate.
