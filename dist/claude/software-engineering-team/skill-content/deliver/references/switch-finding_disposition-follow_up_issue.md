# Findings below critical become follow-up issues

These are the instructions of process switch `finding_disposition` at
`follow_up_issue`. A task binds this file only when the project's Process
Policy selects that value; at the default, `next_pass`, every blocking
finding of code review or QA starts a repair and a new verification pass.
Inside a Delivery read the value with
`process_policy.py value --switch finding_disposition --delivery DLV-###`.

## Readers

Only a finding of a `critical_severities` severity of the verification
policy, `critical` or `P0`, blocks the Item. A reader that finds nothing
critical returns `passed`, also with open major or minor findings, and gives
each such finding its `file`, `description`, `verification`, an `owner_role`
from the Item's implementation roles and a concrete `revisit_trigger`.
`result` refuses a passing result that leaves one incomplete, and a passing
result that keeps a critical finding open. Required checks still pass: a
failing test, mutation or runtime check is never a follow-up.

## Coordinator

1. After `validate`, run `delivery_verification.py --worktree <item-root>
   follow-up-issues --delivery DLV-### --story <story>`. It lists one issue
   per follow-up, its title and body, from the two final results.
2. File each as an issue in the project's own repository through the
   project's provider CLI, never in the package's upstream repository, and
   report the issue links.
3. Approve the evidence and integrate the Item at once: no repair pass runs
   for a follow-up. `approve-item-evidence` copies the follow-ups into the
   code review record and `approve-review` lists them in the Delivery Review.

## Measurement

Per Item: passes saved, follow-up issues filed, and follow-ups later found
to have been critical. A follow-up that turns out critical is reported to the
owner at once with the Item it escaped.
