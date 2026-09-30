# Parallel implementation lanes: planning

These instructions apply while the project's Process Policy sets switch
`implementation_schedule` to `parallel_lanes_v1`. They change how an Item's
implementation roles are planned; the rest of the execution plan is unchanged.

## Which Items run lanes

`delivery_compile.py init` writes `implementation_schedule: parallel_lanes_v1`
with empty `lane_scopes` and `lane_seams` on each new Item that has two or more
implementation roles besides the Software Architect. Every other Item carries
no field and runs its roles one after another. Declare
`implementation_schedule: sequential_v1` on an Item whose roles cannot own
separate files, such as one that claims contracts but no product paths.

## Lane scopes

Derive the scopes from the architect's ownership map: one owner per module or
file group, and every shared test, build or configuration file owned by exactly
one lane.

- `lane_scopes` lists `<role>:<path>` entries, for example
  `backend_developer:workspace/apps/api`.
- Every implementation role except the Software Architect owns at least one
  entry. The Software Architect runs first and alone and owns none.
- The entries together equal `path_claims` exactly, so each claimed path
  belongs to one lane.
- Scopes of different roles are disjoint in both directions: `api` and
  `api/tests` overlap, `api` and `api-tools` do not.
- No scope reaches `workspace/docs`, `.git` or `.agentrof`. Vault writes, Git
  state and runtime scratch stay serial.

## Seams

- `lane_seams` lists `<producer> -> <consumer> via <interface>` entries, for
  example `devops_engineer -> backend_developer via IFC-004`. A seam means the
  consumer needs the producer's finished work, so the consumer's lane starts
  only after the producer's lane has finished. Lanes without a seam between
  them start together.
- `<interface>` is one of the Item's `contract_claims`, or the id of an
  architecture record whose kind the Item claims in `architecture_record_kinds`,
  such as `IFC-004` with `interface-contract`.
- Seams join two different lanes and form no cycle.

Lanes that share an interface need a code-level seam specification before they
start: exact signatures, data shapes, error codes and the landing order of the
lanes' changes. Plan such an Item with architecture impact `required` and the
record kind that will hold the specification, unless an approved contract the
Item claims already fixes the interface completely.

## Approval

`approve-execution` rejects an unknown schedule, lane scopes or seams that break
these rules, lane fields on a sequential Item, and an Item that still declares
`parallel_lanes_v1` once the Process Policy no longer selects it. The Execution
Plan's Role Sequences render the phases: the Software Architect alone, the
lanes grouped by seam order, then Code Review and QA as the verification
schedule sets. `item_plan_hash` and the plan hash cover the schedule, the
scopes and the seams, so changing any of them follows the normal plan
revision, approval and publication.
