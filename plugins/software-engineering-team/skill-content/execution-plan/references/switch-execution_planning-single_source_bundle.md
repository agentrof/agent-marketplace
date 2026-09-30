# Single-source execution planning

These are the instructions of process switch `execution_planning` at
`single_source_bundle`. A task binds this file only when the project's Process
Policy selects that value; at the default, `per_document`, every Operation
contract and the Item topology are written and reviewed as their flows
describe. Where this file and a flow differ on execution planning, this file
governs.

## Fact ownership

`data/fact-ownership.json` names, for each fact class of an execution plan,
the one document, section and writer role that own it. Write each fact once,
in its owning section. Every other document links that section, or cites an
owner ruling by its id, and never restates it: a copy is a second truth that
some review then has to reconcile.

Four deliberate rules stay exactly as they are:

- `flows/operation.md` names the only writer of each Operation contract: the
  QA Engineer writes the Verification Contract and the DevOps Engineer the
  Environment Contract.
- An architecture record exists only inside an active Item: the Software
  Architect writes it after the Item is claimed, with `architecture_compile.py`.
- Publication carries only the contracts an Item pins.
- A non-runtime Item never binds the Environment Contract.

## Steps

1. Architect handoff. The Software Architect's execution-planning task
   writes only the Item topology it owns, with a one-paragraph
   `architecture_reason` that summarizes the design, and creates no planning
   document. Its reply carries every other definition, grouped by fact class.
   Write that reply unchanged to
   `.agentrof/agent-marketplace/.runtime/<dlv-id>/execution-handoff.md`, which
   is disposable runtime scratch, never vault truth.
2. Contract drafts. Each contract the plan revises is drafted by its
   writer through `flows/operation.md` steps 1 and 2. Derive the writer's task
   with `task_inputs.py --entry configure --role <writer> --mode revise
   --skill challenge-review --input <handoff>`, so it binds the handoff, the
   fact ownership data and this switch's writer instructions. The two writers
   may draft at the same time: each writes only its own contract.
3. Bundle manifest. Once the drafts and the Item topology are ready, run
   `delivery_compile.py bundle-manifest --delivery DLV-###`. It lists every
   contract the plan revises, or the current revision an Item pins, every Item
   record with its Story and Test Plan, and the fact ownership data, each with
   the hash of its bytes. `readers` names the counterpart of every revised
   contract: the DevOps Engineer for the Verification Contract and the QA
   Engineer for the Environment Contract. When it names none, the plan revises
   no contract, no bundle review runs and approval follows as the flow
   describes.
4. Bundle review. One review layer reads the bundle. Start every reader
   together on the same manifest, as the host contract says, and derive each
   reader's task with `task_inputs.py --entry configure --role <counterpart>
   --mode review --skill challenge-review` and one `--input` per path in the
   manifest's `inputs`. Each prompt carries the manifest, the contract the
   reader counterparts and `SELF-CHECK`. No separate counterpart review runs
   for a contract inside the bundle. At `review_panels` `lens_panel`, review
   panel `execution_bundle` replaces these readers. Recompute the manifest
   with `--expected-hash <source_hash>` before accepting any finding; a
   changed input needs a fresh read by every affected reader.
5. Findings and verdict. A restatement of a fact outside its owning
   section is a finding that names the owning section; one that contradicts
   the owner is critical. The owning writer fixes its own document: a contract
   writer its contract, the Software Architect the Item topology. The bundle
   gets one verdict. It is approved only when no reader has an open critical
   or major finding and `operation_compile.py check --kind <kind> --json` for
   every revised contract and `delivery_compile.py check --delivery DLV-###`
   are green.
6. Approval and publication. Approve each revised contract with
   `operation_compile.py approve --kind <kind>`, then approve the plan. For a
   path in the manifest's `unpinned_revisions`, a revised contract that no
   Item pins, commit the approved contract to the target branch and run
   `delivery_git.py refresh-target` before `publish-execution-plan`, in the
   same step: publication refuses with `DELIVERY_OPERATION_UNCARRIED` a
   revision the Integration does not hold.

## Review loop

The bundle loop follows switch `review_loop` as an Operation contract review
does. At `blocking_delta` it follows
`challenge-review/references/switch-review_loop-blocking_delta.md`: a minor
finding on a contract goes to that contract's `Accepted Minor Findings`, one on
an Item record is shown at the plan approval gate with owner role
`software_architect` and its revisit trigger, and a calibration reader is a
fresh counterpart of the contract a claim concerns, or of the Verification
Contract for a claim on an Item record.

## Owner rulings

Record each owner ruling once, in the Delivery's `User Decisions` section, as
a line that starts with its stable id: `D-` and at least two digits, numbered
in the order the rulings are made. Every other document, a contract, an Item
record or an architecture record, cites the id and never restates the ruling.
