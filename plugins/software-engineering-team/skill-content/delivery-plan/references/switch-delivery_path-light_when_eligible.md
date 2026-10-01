# Light Delivery path

These are the instructions of process switch `delivery_path` at
`light_when_eligible`. A task binds this file only when the project's Process
Policy selects that value; at the default, `standard`, every Delivery plans its
scope in `/delivery-plan` and its execution in `/execution-plan DLV-###`, each
with its own owner gate, as the flows describe. Where this file and a flow
differ on how an eligible Delivery is planned, this file governs.

The light path merges planning steps and owner gates, never checks. Every
compiler approval, coordinator verb, refusal, hash and pin, the publication and
Fence protocol, code review and QA run as the standard path runs them for the
same content. Reservation stays the point after which the Delivery ID, slug and
scope hash are immutable, and execution approval still needs the reserved
Integration.

## Eligibility

The compiler decides eligibility from the records; nothing is assumed.
`delivery_compile.py init` reports it for the selection under `delivery_path`,
and `delivery_compile.py light-path-check --delivery DLV-###` repeats it on the
Delivery's records together with the `plan_findings` that execution approval
would refuse, the findings `delivery_compile.py check-plan` reports. A Delivery
is eligible only while every condition holds:

- `single_story`: the selection holds exactly one Story.
- `no_architect_role`: `software_architect` is neither the Story's owner nor
  one of its supporting roles.
- `architecture_not_applicable`: the Item declares
  `architecture_impact: not_applicable` with the Software Architect's own
  reason; the reason `init` writes is a placeholder. `init` lists this
  condition as `pending`, because the topology pass decides it.
- `no_operation_impact`: the approved Story does not classify
  `operation_impact: required`, which expects an Operation contract revision,
  so `init` decides it before any Operation draft exists. A Story without the
  classification is unknown here, and `operation_contracts_unchanged` still
  decides.
- `operation_contracts_unchanged`: no Operation contract revision is open, and
  the Verification Contract, with the Environment Contract for a
  `runtime_required` Item, is approved and current. After scope approval the
  plan must still bind the revisions that scope approval recorded.
- `dependencies_met`: every Story outside the selection that the Story depends
  on or the Item waits for is recorded `integrated` by a Delivery whose merge
  the target branch holds.
- `within_story_size_budget`: switch `story_size_budget` is at `propose_split`
  and the Story is over none of the owner's limits. With no limit set no Story
  is eligible, so small is always the owner's definition. A Size Exception
  keeps a Story over its limit.
- `topology_unchanged`: after scope approval, the Item topology is still the
  one scope approval recorded.

## One step, one gate

1. Run `init`. When it reports `eligible: false`, name each failed condition
   and continue on the standard path, as the flow describes.
2. When it reports `eligible: true`, derive the Software Architect's task with
   `task_inputs.py --entry execution-plan --role software-architect --mode
   create --delivery DLV-###`, the Item record and the selected Story and Test
   Plan as inputs. The architect runs the topology-only pass of
   `skill-content/execution-plan/references/switch-delivery_path-light_when_eligible.md`.
3. Run `light-path-check`. A failed condition ends the light path, and the
   check records it: see Fallback. Return any `plan_findings` to the topology
   pass and check again.
4. Present one owner gate as one choice gate: the scope proposal, the Item
   topology with its claims, role sequence and schedules, and the reused
   contract receipts and topology hash the check lists. It replaces both the
   scope gate and the execution gate.
5. On approval, run in this order with no further gate, each after
   `light-path-check` passes: `delivery_compile.py approve-scope`,
   `delivery_git.py reserve-delivery`, `delivery_compile.py approve-execution`,
   `delivery_git.py publish-execution-plan` and `delivery_git.py claim-items`.
   Then hand over to `/deliver DLV-###`.
6. A rejection writes nothing the gate would have authorized. Revise the scope
   or the topology, check again and present the gate again.

To resume, run `light-path-check`, read the Delivery's status and its remote
refs, and continue with the first step not yet done.

## Fallback

The light path ends at the first failed `light-path-check` or refused step,
whatever the refused step's remedy. The Delivery records the fallback: a failed
check rewrites its `Delivery path:` line to `standard` itself, naming each
failed condition, and a refused step is recorded with `delivery_compile.py
light-path-check --delivery DLV-### --refused <step>`, which names it. The
recorded fallback stands when the failure clears again. A Delivery never
returns to the light path.

Name each failed condition or refused step to the owner, keep every approval
already made and continue on the standard path from where the sequence
stopped. Before the gate, that is the scope gate. After it, the gate's
approval of the scope stands: run `approve-scope` and `reserve-delivery` when
they have not run, then `/execution-plan DLV-###` with its own owner gate for
the plan when execution approval has not run, or the refused verb's own remedy
after it. Until the fallback is recorded, `approve-execution` refuses a plan
whose Item topology or bound contract receipts differ from the light line,
since the owner never saw it, so a plan changed after the gate always takes
the owner gate of `/execution-plan DLV-###`.

A `DELIVERY_TRANSACTION_UNCERTAIN` from `reserve-delivery` or
`publish-execution-plan` is no fallback: the refetched refs it names hold the
step's candidate, so the step took effect. Read the refs again as the finding
asks, run `light-path-check` and continue with the next step. The gate's
approval still covers the sequence, so the owner is not asked again.

## Record

`approve-scope` writes one line that starts `Delivery path:` first in the
Delivery's `User Decisions` section before it hashes the scope: `light`, with
the Item topology hash and the reused contract receipts it approved, or
`standard`, with each failed condition. `light-path-check` rewrites a light
line to `standard` at the first failed check or recorded refusal.
`approve-execution` refuses a plan that no longer binds that topology and
those receipts, keeps a light line only while every other condition holds, and
otherwise rewrites it to `standard`, naming the conditions. A line names
`delivery_path` in place of a condition when the policy, the scope record or a
refused step decides it: a policy revised to `standard` or no longer readable,
a scope approved without a light line, or a step named with `--refused`. The
pinned Process Policy names the switch value the Delivery ran
under and the line the path it took. The line is compiler-owned and no owner
ruling: whoever writes `User Decisions` records rulings beside it and leaves it
as the compiler writes it.

## With owner_gates

Switch `owner_gates` at `two_fixed_gates` already holds the scope and the plan
in gate A, so the two switches compose into one gate, never two. For an
eligible Delivery, gate A is the light path's one gate: it presents what this
file's step 4 lists together with the decision log so far and every queued
question, grouped as
`skill-content/deliver/references/switch-owner_gates-two_fixed_gates.md`
groups them, and there is no Operation revision to present. Gate A's approval
then authorizes the sequence of step 5 and Item start, and gate B stays as that
file defines it. The `Delivery path:` line stands above the decision log table,
which keeps every row. An ineligible Delivery takes gate A with the standard
execution plan, and a fallback after gate A presents gate A again only for what
its approval no longer covers.
