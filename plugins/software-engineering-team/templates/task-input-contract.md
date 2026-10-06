# Derived task inputs

Before delegating a project role, the entry derives its input manifest with
the packaged `scripts/task_inputs.py`. Use `--entry <entry> --role <agent>
--mode create|revise|consume|review|repair --project-root <root>`, repeated
`--input <project-relative-file>` for the complete selected source set, and
repeated `--skill <method-skill>` from accepted Solution or Item bindings.
Technology skills are selected from those bindings, never from a role name;
include the committed, accepted Solution decision in `--input` when selecting
a technology method for a project. Delivery's specialized manifest retains
the Item's historical approved bindings.
`--findings <record>` binds the existing finding set. `--base <commit>` binds
the complete committed and working change inventory; it does not infer a baseline.
Initial Setup may bind an initialized repository before its first commit;
its initial files are still hashed, and the first commit invalidates that input.

A skill may carry `references/switch-<switch>-<value>.md`: the instructions of
one value of a process switch declared in
`skill-content/configure/data/process-switches.json`. The manifest binds it as
a required read only when the project's approved Process Policy,
`workspace/docs/delivery/process-policy.md`, sets that switch to that value
and the task's entry runs one of the switch's owning flows, for a task that
selects the skill, or for every task of those flows when the registry gives
the switch `reference_scope: owning_flows`;
otherwise the file is neither read nor hashed. A switch at its default binds
nothing, so the default path keeps its instructions. Package data that only one
value reads, which the registry lists as that value's `value_data`, is bound as
a required read together with that value's switch references and is never read
or hashed on another path; data that several values read is listed under each
and bound with the references of any of them. When the policy exists it is a
project input, and a draft or invalid policy fails the derivation. Before the
flow step that names switch `<id>`, the entry reads its value with
`process_policy.py value --switch <id>`, adding `--delivery DLV-###` inside a
Delivery. Derive a Delivery's tasks with `--delivery DLV-###` too: a task that
names a Delivery, or reads a file of its package, binds the switch values that
Delivery pinned. Until the Delivery Review, an approved policy that changed
the value of a switch the task's flows own refuses the derivation, as the
switch read does, until a new execution approval pins it or a later revision
sets the value back; a `/delivery-plan`, `/execution-plan` or `/configure` task
binds the approved policy instead before the first execution approval and
while the Delivery's plan-revision barrier is held, since that approval pins
it. From the Review on, the task binds the values of the revision the
Delivery pinned, read from Git history when the policy moved on, and a pin
that cannot be read back refuses the derivation.

Use the owning compiler's resolved source set. For Backlog review,
`--epic <exact-epic>` derives the existing scoped dependency and source
closure; bare `--epic` retains the full root package. Other stage compilers'
approved receipts, selected scope and named project input files remain the
authority. A manifest never decides applicability or creates an approval.

Every project task automatically receives `project_reading` from the resolver,
using the selected source files and the entry, role and task mode. No opt-in flag
is needed. An explicit `--context-plan <project-relative JSON>` may supply a
validated plan instead. `project_context.py read --plan <task manifest>` accepts
the complete manifest or the nested plan; `units` and `expand` supply smaller
units and continuation. A `needs_split` or `needs_resolution` result is incomplete.
A `needs_scope` result requires source selection by the owning flow. An
`unavailable` resolver never licenses omitted reading: recover through targeted
manual search and reads, report the failure, and preserve the original scope.

Agent-initiated and parent-directed manual discovery remain allowed whenever
context is insufficient or appears wrong. Record extra sources and reasons and
rebind added evidence before relying on it. The plan guides reading order and
granularity; every owning-flow review obligation remains. Skill selection is
unchanged. Return `context_findings` to the parent for observed failures and
recurring friction, including impact, recovery, and a fix and verification case
when known. The parent uses `issue-report` to offer an anonymous report and
obtains explicit user approval of its exact payload before any GitHub write.
Project-only authoring gaps remain with their owner. Declining a report never
blocks ordinary work. Never copy source content or transcripts into an issue.

The manifest lists full required reads, conditional references with their
original read conditions, source identities, write boundaries and the output
contract. Read every required file completely. Read each conditional reference
when its condition applies. The manifest is an index, never a substitute for
those reads. Give the same manifest and its source hash to the delegated role.
Immediately before persisting a result, rerun the same invocation with
`--expected-hash <source_hash>`; stale inputs require refreshed work.
The identity also includes the canonical Markdown/JSON source inventory, so
new or removed sources invalidate an older input. Prototype and exploratory
artifact interiors remain opaque and are excluded from that inventory. A task
scoped with `--epic <exact-epic>` binds its closure instead, as that epic's
review manifest does. The closure is derived again on every run, so a new or
removed source that reaches it still invalidates the task, and so does an
incoming dependency edge to a story whose own links the closure follows; an
edge to a story it reads only through a link, without following that story's
links, leaves it fresh. Canonical sources outside it are left out of its
inventory, working inputs and changed paths, and the stubs it lists from notes
outside its paths are information, not identity, so another epic's writer
leaves the task fresh.

`write_scope.allowed_write_area` is the bounded authoring area, never a writer
grant. Read-only roles and modes have an empty area. BA derives exact selected
owned documents in one analysis space. The Product Owner derives exact selected
backlog source and review paths from the existing `--epic` closure (bare `--epic`
selects the root package); an epic closure excludes dependency context and
the global backlog record. A writer task's closure lists the untouched
`stub-epic` and `stub-story` placeholders in `check.scaffold_findings` instead
of failing on them. An epic task, read-only or writer, fails on any other
source finding only in a path it reads or when the finding concerns the
backlog as a whole, and lists the findings of other backlog notes in
`check.scaffold_findings`; a read-only epic task also fails on a placeholder in
a path it reads. A root task still needs complete sources beyond its writer's
placeholders. Story implementation ownership and QA
contribution ownership do not transfer canonical backlog writing authority.
Delivery implementation derives literal path-and-descendant claims from one
selected Item and its role sequence; vault, Git, and runtime authority paths
remain excluded and the active writer receipt is still mandatory.

Missing, ambiguous, new-document, or unsupported write scopes are explicitly
`unresolved`, with an empty area. Resolve them through the existing owning
compiler before writing; do not infer permission from the read set or expand a
selected file into its containing directory. Compiler-owned fields, lifecycle
transitions, and generated projections retain their existing commands.
`next_transition_conditions` records required entry gates and reader/source
barriers. A condition marked `required` is not a claim that the gate passed.

On repair, preserve finding IDs and pass the new delta, unresolved verification
conditions and affected consumers. Correctness, conformance and security still
run. Architectural findings go to the architecture owner. A shared or unclear
impact widens the read set instead of assuming a local fix.

Independent readers return separate results. Wait for every reader to finish
or confirm cancellation before the owning role writes canonical sources.
Delivery additionally uses `scripts/delivery_verification.py` to enforce its
frozen candidate and terminal evidence barriers. Generic task inputs alone
never authorize Delivery evidence or replace a compiler gate.

The manifest is stdout-only and disposable. Its resolver reuses the project-local
disposable index, with an in-memory fallback on read-only filesystems. It creates
no project document or durable truth. Issue reporting remains external and stateless;
it uses conversation evidence and never creates a project task manifest.
