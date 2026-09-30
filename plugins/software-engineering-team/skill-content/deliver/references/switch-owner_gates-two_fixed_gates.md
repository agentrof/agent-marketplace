# Two fixed owner gates

These are the instructions of process switch `owner_gates` at
`two_fixed_gates`. A task binds this file only when the project's Process
Policy selects that value; at the default, `per_step`, the owner answers each
question when it comes up, as the flows describe. Where this file and a flow
differ on when the owner is asked, this file governs.

The switch changes when a question is asked, never who decides it. Nothing is
decided by default: a recommendation is never an answer, and the run never
proceeds on a guess. Setup, `/configure` outside a Delivery's plan, backlog
approval and Requirement-flow gates keep their own gates.

## Gate A: scope and plan

1. `/delivery-plan` renders the proposal with `delivery_compile.py init` and
   continues into execution planning on it without asking the scope decision.
   The Item topology, the Operation contract and Governance revisions the plan
   needs and their reviews run as their flows describe, up to their approval.
2. When the plan and every revision it needs pass their checks
   (`delivery_compile.py check` and `check-plan`, `operation_compile.py
   check`, `delivery_governance.py check`), present gate A as one choice gate:
   the Delivery scope, the execution plan with its topology, claims, role
   sequence and schedules, every Operation revision and Governance change the
   plan needs, the decision log so far and every queued question.
   `check-plan` reports every refusal execution approval would raise on the
   plan; an open Operation revision that its approval would take, checked as
   that approval renders it, is listed under `pending_operation_revisions`
   instead, with the `source_hash` the approval stamps, since gate A approves
   it first. Gate A names that receipt, and an open revision its approval
   would refuse is refused with what the approval finds.
3. Group the questions in calls of at most four, with the recommended option
   first and the tradeoffs in the option descriptions.
4. Record every answer, then carry out what gate A approved without asking
   again: approve each Operation revision, approve the Governance change and
   apply it with `delivery_git.py apply-governance` before any Item starts,
   run `approve-scope` and reserve the Delivery, run `approve-execution`,
   publish the plan, claim the Items and start them. Gate A's approval is the
   go for Item start.
5. A rejection writes nothing that gate A would have authorized. Revise, then
   present gate A again.

## With delivery_path

Switch `delivery_path` at `light_when_eligible` plans an eligible Delivery, one
small Story, in one step with one owner gate. Gate A already holds the scope
and the plan, so the two switches compose into one gate, never two: for an
eligible Delivery, gate A is that one gate. The Software Architect's
topology-only pass replaces the execution planning before it, gate A presents
the reused contract receipts in place of Operation revisions, and its approval
authorizes the writes above in the same order, as
`skill-content/delivery-plan/references/switch-delivery_path-light_when_eligible.md`
defines. The decision log and the queued questions are presented and recorded
as this file defines; the Delivery's `Delivery path:` line stands above the
log's table.

## Between the gates

- A question outside the at-once classes is queued: add a `pending` row to the
  Delivery's `User Decisions` table and continue. Work that does not depend on
  the question goes on; a task that depends on it waits, and only that task
  waits.
- When every remaining task depends on pending questions, ask the queued
  questions at once as an early gate, grouped as gate A groups them.
- Only the owner's answer closes a question. Record it verbatim and set the
  row `answered` before the task that depends on it starts, and record in
  `wait_minutes` how long its dependent tasks waited.
- No approved document changes between the gates unless an `answered` row
  names it in its answer.

## At-once classes

Ask a decision of these classes at once and name its class in the question:

- the Software Architect's escalation clause in `agents/software-architect.md`,
  class `architect_escalation`, which stays exactly as that file states it;
- every class in `data/owner-decision-classes.json`: an exception to a project
  invariant or approved rule, a scope or grant change, and credentials,
  spending or an irreversible external action.

Log it as a row with that class. The work that depends on it halts until the
answer, as it does at `per_step`.

## Gate B: review and merge

When every Item is integrated and the Delivery Review draft is ready, present
gate B as one choice gate: the Delivery Review, its follow-ups, the decision
log since gate A and the merge. Gate B's approval authorizes `approve-review`,
the Review and PR publication and the merge once the provider checks pass. A
failed check or a change after gate B returns to the Delivery flow, and the
merge waits for a new gate B.

## Decision log

The `User Decisions` section of `delivery.md` holds one table.
`delivery_compile.py init` writes its header; `delivery_compile.py check`
validates it while the Delivery runs under this value.

```markdown
| id | class | question | options | recommendation | status | answer | blocks | wait_minutes |
|---|---|---|---|---|---|---|---|---|
| D-01 | queued | Which cache root do parallel verification runs share? | One fixed root per checkout; one root per Item | One fixed root per checkout | answered | One fixed root per checkout. | Verification Contract revision 6 | 12 |
```

- `id` is `D-` with at least two digits, unique and stable; other documents
  cite the ruling by it.
- `class` is `queued` or an at-once class id.
- `options` lists at least two options separated by semicolons, the
  recommended one first, and `recommendation` is one of them.
- `status` is `pending` or `answered`. An `answered` row records the owner's
  answer; a `pending` row records none.
- `blocks` names the tasks that wait for the answer, and `wait_minutes` is the
  whole number of minutes they waited.

## Roles

- Software Architect: the escalation clause is unchanged and is asked at
  once. Return any other question with its options, the recommended one
  first, and the work that depends on it, then continue with the parts that
  do not.
- Delivery Coordinator: owns the table. Add each queued question as a
  `pending` row, record the owner's answers verbatim and the wait minutes,
  never mark a row `answered` without the owner's answer, and run
  `delivery_compile.py check --delivery DLV-###` after each change.
- Every other role returns a question the way the Software Architect does.
