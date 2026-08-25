# Solution Design Flow

Spawn template: paste `{{constitution}}`, the exact BA receipt, tree paths,
decision-status lens and `SELF-CHECK` into every reviewer prompt.

Read this complete flow before `/solution-design` changes durable state.
Exact `REQ-###` selects Requirement mode; its strict-current BA receipt is the only
input. No Requirement argument is manual mode and requires an explicit exact
strict-current BA package selection. A legacy-readonly BA package may only be
bound as an explicit Requirement reuse, never used to author a new Solution revision.

1. `solution-architect` is the only writer. Before accepting topology, it
   allocates each active BA process to one explicit component or records a
   rationale-bearing `not_technical` disposition in the landscape. It compares
   meaningful monolith, modular-monolith, distributed or hybrid alternatives.
2. The user explicitly confirms the selected topology and the complete naming
   set: every project-built deployable app, its lower-kebab ID, responsibility,
   app kind and canonical future `workspace/apps/<app-id>` path. Build apps
   are components; self-hosted, managed and third-party dependencies are
   components but never project app directories.
3. Author `components/<component-id>/component.md` plus accepted technology,
   data-store, environment and integration decisions. A component may use a
   different accepted stack than another component. Proposed, in-review,
   rejected and superseded decisions never constrain an approved topology.
4. Spawn `solution-reviewer` read-only with BA allocation, topology, naming,
   sourcing, decision-status and `SELF-CHECK` lenses. Resolve blockers in the
   canonical landscape/components/decisions; reviewer responses are transient.
5. After the owner confirms the exact topology and naming set, run
   `landscape_check.py confirm-topology`, then `check`, render
   capability/component/topology catalogs and package `approve`. Approval
   requires `topology_selected: true`, a version-3 confirmation receipt and a
   complete BA allocation universe. An approved package changes only through
   `begin-revision`.
6. Requirement mode binds the result. Manual mode returns the exact solution
   package receipt and suggests `/design-system`. It never creates an app,
   System Architecture record or Delivery Item.
