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
changes. Select reruns from changed inputs, invalid evidence and the diagnostic
question. Required proof still runs fresh when reuse is not configured.

Optional project-owned `proof_reuse` uses a direct argv `identity_command` and
an optional `max_age_seconds` from 1 to 86400, defaulting to 86400. The probe
must cheaply identify actual installed dependencies, untracked fixtures,
toolchains and relevant service generations with stable opaque stdout. It is
limited to five seconds and 64 KiB. Raw output and raw environment values are
not persisted. The runtime additionally binds tracked code and tests, the
candidate, proof root, selected commands, runtime and environment identity.

Reuse requires matching inputs and complete successful evidence within its
age limit. Changes, missing or unreadable records, expiry, probe failure and
failed or interrupted runs require fresh proof. Legacy records run fresh once.
Cached evidence retains its original execution timestamps. The project owns
external-state identity and isolation, including any mutable fixtures or
services beyond tracked files. A matching identity establishes eligibility
under that contract, while coverage and semantic correctness still need review.

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
