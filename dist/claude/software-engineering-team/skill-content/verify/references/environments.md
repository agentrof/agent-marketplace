# Environment and resource discipline

## Warm development and isolated tests

Use the project's approved Environment Contract. Prefer one convergent warm
development environment and isolated mutable test resources when the runtime
supports that safely. A namespace must isolate its data, credentials, queues,
object keys, ports and cleanup authority. Shared compute or databases still need
capacity controls. Use a separate environment when the isolation requirement
cannot be met within the shared one.

Identify the resource owner before creating or deleting anything. Make
reservations atomic when multiple workers compete. Account for CPU, memory,
connections, storage and active jobs. Admit heavy work using measured capacity and its expected resource demand. A failed capacity probe leaves capacity unknown. Diagnose or
serialize safely instead of assuming the machine is empty.

## Build and bring-up

Invalidate builds from the component's relevant source, dependencies and build
configuration. A repository-wide metadata change should not rebuild unrelated
images unless it changes their output. Keep shared caches and expensive
artifacts intact. Downloads and image pulls remain subject to existing grants.
Use committed manifests/lockfiles and record the actual image identity.

Run image entrypoints on the real image. Check runtime configuration, mounted
secrets, storage, migration, readiness and seed wiring. A started process is not
evidence that its required capability works. Bound readiness waits, surface a
repeating error early and retain redacted workload diagnostics.

For cluster changes, check config/secret rollout behavior, single-writer volume
strategy, credential persistence, dependency restart after store replacement and
scratch paths under read-only filesystems. Derive these from the selected
runtime rather than imposing a container tool on every project.

## Fixtures and lifecycle

Use named, minimal, realistic fixtures with deterministic identity where tests
depend on it. Keep domain data in its owning application loader and orchestration
in the environment runner. Re-seeding an owned fixture should converge without
damaging other workers' state. Refuse unknown fixture names.

Prove cold start, restore, reset and destructive cleanup on disposable owned
fixtures when those behaviors changed. Ordinary application edits can use the
warm environment. Teardown removes only the task's resources and preserves
user/dev state. Keep volume and credential retention explicit in the contract.
Age alone does not establish that another worker's resource is safe to delete.

## Live quality

Observe relevant requests/interactions, persisted effects, logs, resource health
and restarts. Triage first-party and dependency errors with their context. Do
not demand silence from a log when an expected refusal is being tested. Record
actual degradation or unexplained warnings and verify the recovery path.
