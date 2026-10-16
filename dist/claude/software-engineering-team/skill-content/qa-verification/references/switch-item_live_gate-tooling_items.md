# Run a tooling Item's affected live groups once before merge

These are the instructions of process switch `item_live_gate` at
`tooling_items`, selected in the project's Process Policy. It applies while
`live_verification` is `delivery_integration`. At `off`, no Item runs live tests before the
integration live run.

An Item changes environment tooling when one of its changed paths is covered
by a rule of class `tooling` in the project's live impact map. Such an Item
runs only its affected live groups, once, after its QA pass has passed and
before its merge, outside the pass loop; a failure blocks the merge and is
repaired as a scoped repair. Every other Item runs no live test.
