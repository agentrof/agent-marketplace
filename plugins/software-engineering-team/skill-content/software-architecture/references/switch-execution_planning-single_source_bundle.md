# Single-source architecture

These are the Software Architect's instructions under process switch
`execution_planning` at `single_source_bundle`. A task binds this file only
when the project's Process Policy selects that value; at the default,
`per_document`, the architect works as its role file and flows describe. The
role file is unchanged: this file changes where a definition is written, never
what the architect decides or when it escalates.

`skill-content/execution-plan/data/fact-ownership.json` names the one document,
section and writer of every execution-planning fact class. You own the Item
topology and the architecture decision records; the contracts and the owner
rulings belong to their writers.

## Execution planning

- Your execution-planning task has no vault output for its definitions and
  creates no planning document. Write only the Item topology, with a
  one-paragraph `architecture_reason` that summarizes the design.
- Return every other definition in your reply, grouped by fact class, each
  with its owning document and section. The entry hands the reply to the
  contract writers unchanged; they write each definition once.
- Cite an owner ruling by its `User Decisions` id; never restate it.

## Architecture records in the Item

- Architecture records still exist only inside the active Item, written with
  `architecture_compile.py` as today.
- A decision record carries the decision, the alternatives weighed and the
  rationale. For rule text a contract owns, such as a suite declaration, a
  cache location or a teardown rule, link the owning contract section instead
  of restating it, and cite owner rulings by id.
- Record only the delta the Item makes; the planning handoff is not a draft to
  copy into records.
