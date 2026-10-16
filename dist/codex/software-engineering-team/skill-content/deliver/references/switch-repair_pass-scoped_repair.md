# Scoped repair without a new full pass

These are the instructions of process switch `repair_pass` at
`scoped_repair`. A task binds this file only when the project's Process
Policy selects that value; at the default, `full_pass`, every repaired
candidate runs the full code review and QA pass again. Inside a Delivery
read the value with
`process_policy.py value --switch repair_pass --delivery DLV-###`.

## When it applies

A repaired candidate is settled without a new reader pass only when all of
these hold; otherwise `scoped-repair` refuses with
`DELIVERY_SCOPED_REPAIR_REFUSED`, names the reason, freezes nothing, and the
coordinator runs the ordinary full pass:

- `repair_review` is `before_freeze` and `pre_handoff_regression` is
  `touched_suites` for the Delivery;
- both readers of the earlier pass settled a final result, at least one
  failed, and no open finding of it is critical;
- the Item is not `runtime_required`;
- every test file the repair changed is in the touched-suites selection, or
  the selection runs the full approved test command.

## Steps

1. Commit the repair. Run the repair review of
   `code-review/references/switch-repair_review-before_freeze.md`: a fresh
   correctness reader reviews only the repair diff and the earlier findings.
2. Run `regression-run --delivery DLV-### --story <story>` on the repaired
   candidate; it must pass.
3. Run `delivery_verification.py --worktree <item-root> scoped-repair
   --delivery DLV-### --story <story>`. It freezes the candidate, which
   requires both records above, and settles both readers with
   `scoped_repair` results that bind the repair review and the run; the
   earlier blocking findings are resolved by the passing repair review, and
   its minor findings stay open.
4. Approve the evidence and integrate as after a full pass; `validate`
   refuses a scoped result whose records changed.

The full suite, mutation and reader judgement of unchanged scope are not
rerun; the next full pass of the Delivery, or its follow-ups, carries any
doubt about them.
