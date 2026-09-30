# Code Review Loop

These are the instructions of process switch `review_loop` at `blocking_delta`
for Delivery code review, including the System Architecture records that an
Architecture Item's code review checks. A task binds this file only when the
project's Process Policy selects that value; at the default, `current`, code
review runs as its flow, skill and role files describe. Where this file and
those files differ on the review loop, this file governs. Inside a Delivery
read the value with `process_policy.py value --switch review_loop --delivery
DLV-###`; the Delivery pins the policy it runs under.

## Blocking findings

CRITICAL and MAJOR findings block, as the Severity Definitions of this skill
define: the verdict is `fix_required` only while one is open. QA keeps its own
blocking severities.

## Minor findings as follow-ups

A MINOR finding never blocks and never starts a review cycle. Fix it only in a
repair pass that already carries a blocking fix, whose repair review reads
that change; otherwise it stays open as a tracked follow-up. Every open finding
of a non-blocking severity in the code review result carries, besides its `id`,
`severity`, `status` and `verification`:

- `file`: its `path:line` anchor;
- `description`: what is wrong, stated from the code itself;
- `owner_role`: the implementation role of this Item that follows it up, one
  of the Item's `role_sequence` roles other than `code_reviewer` and
  `qa_engineer`; a finding on a System Architecture record names
  `software_architect`;
- `revisit_trigger`: the event that reopens it, such as the next change to the
  anchored file.

`delivery_verification.py result` refuses a code review result whose open
non-blocking finding lacks one of these fields or names another role. A repair
review carries every open minor finding forward on its id with its follow-up
fields and never re-litigates it.

`approve-item-evidence` copies the open non-blocking findings into the
`Deviations and Follow-ups` section of the Item's code review record, and
`approve-review` lists the follow-ups of every integrated Item in the Delivery
Review's `Lessons and Follow-up`. The Item record itself keeps the bytes its
plan approved: evidence approval changes only the two report files.

## Repair review

A repair review keeps its scope: the unresolved blocking findings, the new
delta and the consumers a fix could have touched, with the correctness,
conformance and security passes all mandatory. Once no CRITICAL or MAJOR
finding is open, the Item needs no further review cycle.
