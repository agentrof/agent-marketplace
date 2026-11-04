# Test selection and evidence

## Cheapest sufficient proof

Map criteria and failure modes to evidence. Prefer an in-process test for pure
rules, an integration test for real storage/transport guarantees, a contract
check for consumer compatibility, and a live check for runtime wiring and
interaction. Combine them when no single layer establishes the criterion.
Reuse relevant existing tests and add a focused regression for a missed defect.

Check that an assertion would fail for the intended defect. Use a known failing
case or safe negative control when necessary. Refusal tests must verify that
the protected effect did not happen. Persistence tests inspect actual stored
values. Migration tests include representative old data. Time-dependent tests
should control the clock where that preserves the property under test.

Select by changed dependencies and risk. Unknown impact expands coverage through
the project's existing test policy. Required CI still runs its designated
coverage. Keep application tests with their application and shared tests at the
project's configured test root.

## Shared validation

Compatible tasks can share one integrated validation run. Preserve the task to
criterion to check mapping and identify every included candidate. A result on
one branch does not automatically cover a different integration tree. Reuse a
result only when the relevant source, generated output, dependencies,
configuration, fixtures and runtime inputs match the proof contract.

Freeze the tested snapshot. Keep implementation workers off that snapshot while
they continue in owned workspaces. Start with cheap checks, run heavy groups
within measured capacity and investigate a failing batch by narrowing its
changes. Full reruns require a changed relevant input, invalid evidence or a
diagnostic reason.

## Failure handling

Preserve the first failure, command, exit status and relevant logs. Classify
product behavior, test design, infrastructure, resource contention and missing
observation separately. A changed load level makes a timing comparison a
different experiment. Do not hide failures by extending timeouts or rerunning
until green. A bounded diagnostic retry is acceptable with its reason and both
results retained. Skips and expected failures do not establish the skipped
property.

## Evidence record

Capture the task and criterion, candidate identity, actual command, environment
and fixture identity, exit/result, start/end or duration and artifact location.
State whether evidence was executed now or validly reused. Keep summaries short
and full logs in artifacts. Do not retain secrets or unnecessary personal data.

Report observed outcomes rather than test counts alone. Include unverified
criteria, failures and the next authorized recovery step. A self-reported result
file needs corroboration from command/tool evidence and the resulting state.
