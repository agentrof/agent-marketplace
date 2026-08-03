# Design Qualities

Shared vocabulary for two consumers: the architect applies these tests when drawing boundaries and judging deltas; the reviewer uses them to name structural findings. Every test states what a violation looks like, so a finding can cite it and a design can be checked against it.

## Coupling and Cohesion as Finding Vocabulary

| Finding label | What was observed | Why it blocks |
|---|---|---|
| Content coupling | Code reaches into another module's internals: private types, its tables | Bypasses the declared interface; the ownership map can no longer protect the seam |
| Common coupling | Modules share writable state: a shared table, a mutable global | A second writer under the ownership map; every reader inherits every writer's bugs |
| Control coupling | A flag parameter tells the callee which behavior to run | The caller steers the callee's internals; split into two named operations |
| Scattered cohesion | One business rule spread across several modules | A rule change becomes a multi-owner change; move the rule to its owning module |
| Accidental cohesion | A module named utils, common, or helpers accumulating unrelated code | No owner and no single reason to change; distribute members to their owning modules |

Data coupling, passing exactly the values needed through declared interfaces, is the goal state, not a finding.

## Structural Decision Tests

Each test is a DO rule, a self-check question, and the failure symptom a reviewer can catch.

### One reason to change

- DO give every module, class, and function a single owning concern, one reason to change.
- Self-check: list the deltas that would force an edit here; do they arrive from more than one domain (transport, business rule, storage, formatting)?
- Symptom: the same file shows up in the diffs of unrelated deltas, and every review of it drags in an owner who did not change anything.

### Extend by adding, not editing

- DO shape seams so the next variant ships as a new implementation behind an existing interface; DON'T route new cases through an ever-growing conditional in working code.
- Self-check: can the next likely variant land as a new file plus a registration, with no edit to the tested core?
- Symptom: every new variant edits the same switch or if-chain, and each edit re-risks all shipped variants.

### Point dependencies at the stable side

- DO point every dependency from the volatile toward the stable: the domain depends on nothing; transport, storage, and integrations depend on the domain through interfaces the domain owns.
- Self-check: could you replace either side using only the contract between them?
- Symptom: a change to one package's files forces edits in another owner's files with no contract change recorded.

### Compose, don't inherit

- DO build behavior by combining collaborators held behind interfaces; reserve inheritance for a true is-a relationship with a stable base.
- Self-check: does the subclass exist to borrow code rather than to be its parent? Would injecting a collaborator say it more directly?
- Symptom: deep hierarchies where a base change breaks distant descendants, and subclasses overriding methods to cancel behavior they inherited.

### Third occurrence before abstraction

- DO tolerate duplication until a third occurrence proves the shape, then abstract from evidence. Exception: copies that have already diverged and caused a bug are abstracted immediately, with a test pinning the shared behavior.
- Self-check: are the copies the same decision, or coincidentally similar code that will evolve apart?
- Symptom: a parameterized helper serving two callers through internal branching; the wrong abstraction now costs more than the duplication ever did.

## Using the Vocabulary

- A structural finding names three things: the label or test violated, the file or seam, and the artifact contradicted (ownership row, declared interface, decision entry). A finding that names no artifact is an opinion.
- Severity follows the architectural impact rating defined in the review skill: see [passes](../../code-review/references/passes.md). Contract and ownership violations rate high; local layering drift rates medium; naming drift rates low and never blocks.
- The architect runs the same tests forward: a proposed boundary or interface that fails a self-check above does not enter the ownership map; fixing it on paper is the cheapest it will ever be.
