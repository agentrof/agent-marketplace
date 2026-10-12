# Scratch clone per fix lane

These are the instructions of process switch `lane_isolation` at
`scratch_clone`. A task binds this file only when the project's Process
Policy selects that value; at the default, `shared_checkout`, a delegated fix
lane may work in the main checkout. Inside a Delivery read the value with
`process_policy.py value --switch lane_isolation --delivery DLV-###`.

A fix lane is a delegated lane that fixes a defect on a branch of its own, in
this repository or another one, beside other lanes. An implementation lane of
switch `implementation_schedule` keeps the Item's one worktree and is no fix
lane.

## Rules

1. Each parallel fix lane clones the repository into its own directory under
   the session scratch and works on its own branch there.
2. No delegated lane checks out, switches, rebases or resets a branch in the
   main checkout. Only the coordinator changes the main checkout's branch.
3. Record each lane with `lane_table.py start --isolation scratch_clone
   --main <main checkout> --workdir <clone>`, which refuses a working directory
   that is the main checkout or inside it, and records the main checkout's
   branch on the run's first lane.
4. Before the coordinator uses the main checkout again, `lane_table.py check
   --run <run> --main <main checkout>` refuses when its branch differs from the
   one recorded.
5. A lane pushes its branch before its clone is removed.

## Repair beside an active reader

When a registered open finding is ready to fix while QA still reads the frozen
candidate, the coordinator may call `delivery_verification.py --worktree
<item-root> prepare-repair`. It returns a private repair checkout outside the
Item, its frozen base commit and the registered findings. Repeating the command
returns that same checkout without resetting the writer's work. No unregistered
finding authorizes an overlapping repair.

The writer changes only the repair checkout and does not run commands against
the shared QA environment. The frozen Item remains protected by its reader
barrier. Keep the repair commits there until both original readers settle; then
reconcile the final findings and bring the reviewed repair commits to the Item
through its normal repair flow before freezing the next candidate. Never replace
QA's current candidate or reuse its evidence for the repair tree.
