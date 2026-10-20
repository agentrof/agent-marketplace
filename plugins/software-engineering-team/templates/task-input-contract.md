# Derived task inputs

Before delegating a project role, the entry derives its input manifest with
the packaged `scripts/task_inputs.py`. Use `--entry <entry> --role <agent>
--mode create|revise|consume|review|repair --project-root <root>`, repeated
`--input <project-relative-file>` for the complete selected source set, and
repeated `--skill <method-skill>` from accepted Solution bindings.
Technology skills are selected from those bindings, never from a role name;
include the committed, accepted Solution decision in `--input` when selecting
a technology method for a project.
`--findings <record>` binds the existing finding set. `--base <commit>` binds
the complete committed and working change inventory; it does not infer a baseline.
Initial Setup may bind an initialized repository before its first commit;
its initial files are still hashed, and the first commit invalidates that input.

A skill may carry `references/switch-<switch>-<value>.md`: the instructions of
one value of a process switch declared in
`skill-content/configure/data/process-switches.json`. The manifest binds it as
a required read only when the project's approved Process Policy,
`workspace/docs/operation/process-policy.md`, sets that switch to that value
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
`process_policy.py value --switch <id>`.

Named project input files remain the authority. A manifest never decides applicability or creates an approval.

Every role reads the sources its task file lists and greps `workspace/docs/`
for anything missing; manual discovery stays allowed whenever context is
insufficient or appears wrong. Record extra sources and reasons and rebind
added evidence before relying on it. Every owning-flow review obligation
remains. Skill selection is unchanged. Return `context_findings` to the parent for observed failures and
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
artifact interiors remain opaque and are excluded from that inventory.

`write_scope.allowed_write_area` is the bounded authoring area, never a writer
grant. Read-only roles and modes have an empty area.

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

The manifest is stdout-only and disposable. It creates no project document or
durable truth. Issue reporting remains external and stateless;
it uses conversation evidence and never creates a project task manifest.
