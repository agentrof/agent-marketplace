# One verifier per Item pass

These are the instructions of process switch `item_verification` at
`single_verifier`. A task binds this file only when the project's Process
Policy selects that value; at the default, `separate`, a code reviewer and a
QA reader verify each frozen candidate independently. Inside a Delivery read
the value with
`process_policy.py value --switch item_verification --delivery DLV-###`.

It needs `code_review_panel` `single_reader` and a `review_loop` other than
`blocking_delta`: one verifier is neither a panel nor a calibration, and
`single-verifier-result` refuses otherwise.

## Coordinator

1. Freeze as usual, with `start-pass` or `freeze`, and derive both manifests,
   `--role code_reviewer` and `--role qa_engineer --mode qa_final`.
2. Spawn one fresh read-only `qa-engineer` verifier with both manifests and
   `--skill code-review`. The implementation writer never verifies its own
   candidate.
3. When it returns, register its result with `delivery_verification.py
   --worktree <item-root> single-verifier-result --file <result.json>`. Both
   readers settle together, each result passing every check a separate
   reader's result passes, or neither does.
4. Follow-ups: every finding below critical is one, as
   `switch-finding_disposition-follow_up_issue.md` defines; file them with
   `follow-up-issues` after `validate`, then approve evidence and integrate.

## Verifier

Read the candidate diff against the story, its Test Plan and the approved
contracts through the code review lenses: correctness, conformance and
security. Run the approved test command, or the change-level tier
`item_qa_tier` selects, through the QA manifest's `run` interface, and the
mutation and dependency checks it requires. Return one JSON object with
`candidate_hash`, `session_id`, your native identity in `verifier`,
`verdict`, `report`, `review_checks` as the code review manifest's checks,
`qa_checks` as the QA manifest's checks with their raw evidence hashes, and
`findings`, each with `role` `code_reviewer` or `qa_engineer`. Only a
critical finding or a failing check makes the verdict `failed`; give every
other finding its `file`, `description`, `owner_role` and `revisit_trigger`.
