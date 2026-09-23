# Docker Compose QA Checklist

Assertions for verifying a story with environment impact. Run against the approved Environment Contract command only; never invent commands. Every failed assertion is a finding with severity per the verification skill's table.

## From-Scratch Cycle

- [ ] `down` from any prior state, then `up`, succeeds on the first attempt with a single command
- [ ] `up` waits on health: no service reports ready before its healthcheck passes; an induced unhealthy dependency fails the verb with nonzero exit
- [ ] The default scenario is seeded by `up` itself; no manual follow-up step exists
- [ ] `url <service>` resolves a reachable base URL for every published service
- [ ] A second `up` over a running environment converges (idempotent), it does not duplicate or wedge

## Scenario Determinism

- [ ] `seed <scenario>` for each reserved scenario loads without error
- [ ] Re-seeding the same scenario yields identical domain data (spot-check fixed ids and timestamps)
- [ ] The baseline graph is referentially intact: no orphaned rows, no dangling references
- [ ] An unknown scenario name is refused, not silently ignored

## Service-Log Audit

- [ ] `logs` after bring-up and after the surface walk: zero container restarts, zero error-level lines or stack traces from first-party services, zero error-level lines from third-party services after their own readiness
- [ ] Every boot-time third-party error line is dispositioned in the verification record with its bounded cause; warning-level lines are counted per service, not gated
- [ ] No credential, token or secret appears anywhere in the log output

## Verification Cost

- [ ] A full suite run on an already-warm host moves zero bytes from publishers, or exactly the one small pin the record names
- [ ] The shared cache or store the environment depends on survives the whole suite, and the run leaves no test-scoped fixture behind

## Teardown

- [ ] `down` leaves no containers, networks or volumes carrying the project name
- [ ] A fresh `up` after teardown reproduces the exact same healthy, seeded state

## Isolation (parallel lanes in flight only)

- [ ] Two sibling working trees can hold running environments simultaneously with disjoint container, network and volume names and disjoint host ports
