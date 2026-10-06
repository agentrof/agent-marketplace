# Test Levels

These are the instructions of process switch `test_levels` at `declared`.
Every task of Backlog Planning binds this file when the project's Process
Policy selects that value; at the default, `off`, no scenario declares a
level, nothing is listed and every step runs as its flow and role files
describe.

A Test Plan that proves every acceptance criterion through its most expensive
path pays twice: once while the implementing lane writes and debugs its live
scenarios, and again in every later gate and regression run. Most of that cost
buys nothing when the assertion is a decision: inputs to an outcome, a refusal
code, a phase list, a label. At this value Backlog Planning asks at what level
each scenario has to run before its epic review.

## The two scenario fields

A Test Plan scenario may state the level its automation target runs at:

```markdown
- automation: required
- automation_target: tests/rules/test_refusal_codes.py::test_every_input
- level: unit
```

A `fixture` or `live` scenario also states what has to be real for its
assertion to mean something:

```markdown
- automation: required
- automation_target: tests/runtime/test_teardown.py::test_no_process_left
- level: live
- level_reason: a real process group is the only proof that a stop leaves none behind
```

- `level`: `unit`, the target calls the rule in-process with its inputs and
  starts nothing; `fixture`, the target runs the real entry point over a
  prepared fixture, with no engine or cluster brought up; or `live`, the
  target runs against the real engine, operating system or environment.
- `level_reason`: what has to be real for the assertion to mean something,
  stated on one line.

Both are optional. `backlog_compile.py check` refuses a `level` outside the
three values whenever a scenario states it, at every value of this switch. At
this value every automation-required scenario states its `level`, and a
`fixture` or `live` one also states its `level_reason`.

## The authoring rule

QA applies it when contributing scenarios, and the Business Analyst's
proposals follow it. The Product Owner persists each level and reason with its
scenario:

- A decision rule is proven at `unit` level, over every combination of its
  inputs, as the rows of one table.
- One `fixture` scenario per entry point and decision family proves that the
  real entry point consults the rule. Further rows of that family belong at
  `unit` level.
- `live` only when the subject is engine or operating-system behaviour:
  process liveness, signals and process groups, network and port isolation,
  container and cluster lifecycle, teardown residue, real concurrency.
- Coverage never drops: the Test Plan still maps every acceptance criterion to
  at least one proving scenario. A cheaper level changes how a scenario is
  arranged and where it runs, never what it asserts, so its Then and its
  source refs stay as they are.

## The list

Under this value `backlog_compile.py check --json` adds `test_levels`, which
lists under `without_level` each automation-required scenario that states no
level, with its story, scenario and automation target, and under
`without_level_reason` each automation-required `fixture` or `live` scenario
whose `level_reason` is absent or blank, with the same and its level. A manual
scenario is never listed. The epic and root review manifests carry the same
lists for the stories they read.

While `test_cost_budget` is at `flag_serial_rows`, each scenario in its
`serial_row_scenarios` list also names its `level`, none when it states none,
in `check --json`, the review manifests and `delivery_compile.py init`. A long
`live` or `fixture` table is then flagged together with its cheaper option:
before a split of the table is proposed, ask whether its rows prove a decision
and belong at `unit` level, where they cost next to nothing.

The list is advisory: it never fails a check, never blocks a review or an
approval and never rewrites a scenario.

## Before the epic review

After the stories and Test Plans are authored and `check --json` reports no
source finding, and before the first epic review manifest, QA proposes the
level of each scenario under `without_level`, and the reason of each under
`without_level_reason`, by the authoring rule. A scenario whose assertion is a
decision moves to `unit` level, and its entry point and decision family keep
one `fixture` scenario. The Product Owner persists the result and runs
`check --json` again.

## The reviewer

The review manifest's `check.test_levels` carries the lists as given facts; the
reviewer never recounts them. Where it reads test design, the epic review's
`Test Design` and the root review's `Global Test Coverage`, the reviewer asks of
every `live` scenario it reads whether its assertion is a decision: inputs to
an outcome, a refusal code, a phase list or a label. A decision proven only
live is a minor finding that names the `unit` scenario over every combination
of its inputs and the `fixture` scenario of its entry point and decision family
that would prove it. A scenario the lists name is a minor finding too. The
level question only ever yields a minor finding, and never one for the count of
`live` scenarios alone; a criterion left without a proving scenario is a
finding at its own severity, as always.

## Measurement

The project owner measures outside every task; no role acts on it. For each
backlog revision, record the scenarios listed without a level or a reason, the
`live` scenarios moved to a cheaper level and the ones kept `live` with a
reason, and for each Delivery the live cases and the wall clock of QA's final
gate. The registry's promotion rule judges them over at least 3 backlog
revisions and 3 Deliveries.
