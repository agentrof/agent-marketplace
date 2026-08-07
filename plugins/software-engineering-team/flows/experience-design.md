# Experience Design Flow

This flow is interactive and program-scoped. It ends at an approved experience program; it never starts implementation.

Spawn template: paste `{{constitution}}`, then the frozen input paths, owning
node claim, output paths and required SELF-CHECK into each role prompt.

## 0. Preconditions and state

1. Resolve PMO and the marketplace dispatcher through the team state contract.
2. Run `preparation_check.py status --project-root <root> --json`. The legal predecessor state must be `experience-design`.
3. Run the BA compiler approval gate for every referenced scope, the solution landscape checker and the design-system vault check. Any nonzero result routes to its owning entry and stops this run.
4. Initialize the PMO run with `experience-run init`. A competing run or node claim blocks mutation.
5. Reconcile the lazy `experience-design` vault payload with
   `vault_check.py reconcile-payload-fragment`. A named property or graph-color
   collision blocks tree birth; user properties remain untouched. On first
   birth, present output-language designations for experience, program,
   release, journey, flow-set and screen, then write the owner-approved values
   only through `reconcile-designations`. Never invent translated designations
   during upgrade.

## 1. Program and release framing

Use `experience_compile.py init-program` and `init-release`. Agree on the program outcome, release boundaries, actors, BA scopes, inherited release and non-UI criteria. Setup does not create these folders.

## 2. Domain-first modeling

For each leaf domain, spawn the UX designer with the constitution, exact BA notes, solution decisions, design master and `experience-modeling`. Create journey, flow-set and screen stubs only through the compiler. Keep exploratory HTML in `workspace/experience-design-work/<run-key>/`.

Run `experience_compile.py check` after each bounded node. Then spawn `experience-reviewer` fresh-context. Record findings and dispositions under `reviews/`; blocking findings must be fixed or rejected with a recorded rationale within three rounds. Mechanical findings cannot be rejected.

## 3. Reconciliation gates

Close in this order:

1. Leaf domain gate.
2. Parent-domain reconciliation.
3. Space gate.
4. Multi-space reconciliation.
5. Release gate.
6. Program gate.

At every gate render the registry, scope map, coverage, structural navigation,
typed-relation inverse projections and status; run the experience compiler,
artifact checker and scoped vault checker; then record the user decision
through PMO. Earlier releases cannot cite a later revision.

## 4. Artifact promotion

Promote an approved bounded preview only with `experience_compile.py
promote-artifact`. The verb creates a sibling Markdown artifact manifest,
links the owning note to that manifest, and records its SHA-256, registry hash,
release/revision and declared IDs. HTML is not a knowledge node and is reached
only through the manifest. Remote assets, undeclared IDs or invalid navigation
block promotion.

## 5. Close

Stamp approved releases and the approved program with the compiler, record the program gate, release PMO claims and commit the tracked docs. Name `backlog-plan` as the next entry and stop.
