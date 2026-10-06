# Change-level QA gate per Item

These are the instructions of process switch `item_qa_tier` at
`change_tier_per_item`. A task binds this file only when the project's Process
Policy selects that value; at the default, `full_per_item`, every Item's QA
gate runs the full approved acceptance command. Inside a Delivery read the
value with `process_policy.py value --switch item_qa_tier --delivery DLV-###`;
the Delivery pins the policy it runs under.

## When it applies

Only when the approved Verification Contract the Item binds declares both a
change-level tier and an integration tier, each with its approved command. A
contract that declares one or neither keeps the full acceptance command as the
Item's gate, exactly as at the default. Never derive, write or edit a tier
command in a task; a missing tier is a finding for the Verification Contract.

## The Item's gate

1. QA's final gate for the Item runs the change-level tier for the Item's
   change through the runner, as the contract defines it. Every other part of
   QA's work keeps its rules: the Test Plan coverage, the coverage audit, the
   right-reason checks, mutation, dependency audit and live runtime protocol
   where the Item needs them.
2. The result names the tier it ran. A tier that skips a Test Plan scenario
   the Item claims is a coverage finding, never a pass.

## The Delivery's integration run

After the last Item is integrated and before the Delivery's pull request, the
coordinator runs the integration tier once over the integrated result, under
the Delivery's environment lock. The Delivery opens no pull request until that
run passed on the exact integrated commit. A failure reopens the Item that
owns the failing path through the existing reopen verbs, and the run repeats
after its repair. The full acceptance command keeps its place, once per
Delivery instead of once per Item.

## Measurement

The project owner measures outside every task; no role acts on it. Record each
Item's QA gate wall clock and the cases it ran, the integration run's wall
clock, and each failure the integration run found that no Item run found.
