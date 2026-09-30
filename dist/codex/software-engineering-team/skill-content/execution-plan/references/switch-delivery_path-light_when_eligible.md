# Topology-only pass

These are the instructions of process switch `delivery_path` at
`light_when_eligible`. A task binds this file only when the project's Process
Policy selects that value; at the default, `standard`, the Item topology is
planned in `/execution-plan DLV-###` after scope approval, as the flow
describes. Where this file and the flow differ for a Delivery on the light
path, this file governs.

`/delivery-plan` plans an eligible Delivery, one small Story, in one step with
one owner gate, as
`skill-content/delivery-plan/references/switch-delivery_path-light_when_eligible.md`
defines. Its Item topology comes from one bounded pass of the Software
Architect on the proposal, before the scope is approved.

## The pass

Author only the planning fields of the Delivery's one `item.md`, under the same
rules as execution planning:

- `execution_after`, empty for a single Story, and `waits_for`, holding every
  approved dependency of the Story.
- `path_claims` and `contract_claims`: at least one exact claim.
- `runtime_required`: `true` only when the Item needs a live service
  environment.
- `architecture_impact: not_applicable` with your own `architecture_reason`,
  naming why the Story fits the current Solution and System Architecture
  unchanged; the reason `init` writes is a placeholder and never counts.
- `role_sequence` as `init` derived it, and the schedules: keep
  `verification_schedule`, and at switch `implementation_schedule` value
  `parallel_lanes_v1` fill `lane_scopes` and `lane_seams`.

Write no execution-planning definitions document, Operation contract,
architecture record or any other file. The plan reuses the approved current
Operation contracts as they are.

When the Story needs architecture impact, an Operation contract revision or a
Governance change, record that in the Item as execution planning requires,
for example `architecture_impact: required` with its components and record
kinds, and return. `light-path-check` then ends the light path and
`/execution-plan DLV-###` plans the Delivery on the standard path. The
escalation clause of your role stays as it is.

## After the pass

`/execution-plan DLV-###` stays available on the light path: for a plan
revision of a light Delivery and for a Delivery that left the light path. It
then runs as the flow describes, with its own owner gate, and execution
approval records the Delivery's path as `standard` unless the plan still binds
the topology and the contract receipts that scope approval recorded.
