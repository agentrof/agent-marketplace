# Solution Design Flow

Spawn template: paste `{{constitution}}`, the exact BA receipt, tree paths,
the reader's lenses or specialist risk and `SELF-CHECK` into every reviewer
prompt.

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
4. Follow the single review plan in
   `skill-content/solution-architecture/references/challenge-lenses.md` in
   the mode that `review_mode` in
   `skill-content/challenge-review/data/review-panels.json` selects. In
   `single` mode, the default, spawn one independent primary
   `solution-reviewer` for all four challenge lenses plus BA allocation,
   topology, naming, sourcing and decision status. In `panel` mode run review
   panel `solution_design` instead: one fresh `solution-reviewer` per lens
   assignment, in parallel, whose lenses together cover the same checks. The
   panel replaces the single primary reviewer and never runs beside one. In
   either mode add only the plan's risk-triggered specialist invocations;
   they do not form a second panel. Wait for every selected reader before the
   writer resolves blockers in canonical landscape/components/decisions.
   Replies are transient; repeat only affected review when blocking evidence
   changes.
5. After the owner confirms the exact topology and naming set, run
   `landscape_check.py confirm-topology`, then `check`, render
   capability/component/topology catalogs and package `approve`. Approval
   requires `topology_selected: true`, a version-3 confirmation receipt and a
   complete BA allocation universe. An approved package changes only through
   `begin-revision`.
6. Requirement mode binds the result. Manual mode returns the exact solution
   package receipt and suggests `/design-system`. It never creates an app,
   System Architecture record or Delivery Item.
