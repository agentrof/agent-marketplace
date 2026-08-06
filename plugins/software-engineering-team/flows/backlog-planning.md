# Backlog Planning Flow

One flow serves baseline, replan and feature modes. It produces an exact, approved PMO mutation and stops before delivery activation in baseline/replan mode.

Spawn template: paste `{{constitution}}`, then the frozen upstream registries,
PMO snapshot, plan path, role boundary and required SELF-CHECK.

## 0. Select mode and freeze inputs

- `baseline`: first greenfield program backlog.
- `replan`: approved program change outside active-lane frozen contracts.
- `feature`: bounded planning inside `deliver` for an existing project.

Freeze approved BA revisions and hashes, solution and budget decisions, exact experience registry revisions and current PMO state. Initialize `backlog-plan`; retain its plan revision and draft hash.

## 1. Product Owner draft

Spawn `product-owner` with `product-planning`. Produce one JSON plan under `workspace/planning/` containing program and releases, epics and stories, qualified criteria, delivery owner/support mappings, solution/budget/exact UX refs, dependency DAG, SHARES, structured DoR/DoD, deferral metadata and release allocation.

Feature mode may include only feature stories plus user-approved unfinished transitive prerequisites. Preserve active and completed story contracts.

## 2. Mechanical compile and challenge

Run `backlog_compile.py check --mode <mode>` and `diff --against-pmo`. Mechanical findings cannot be waived. Spawn `backlog-reviewer` fresh-context to challenge scope coverage, slicing, dependency direction, release ordering, operability and execution-set bounds. Record every finding in PMO. A semantic blocker gets at most three fix/review rounds; non-blocking accepted risk requires owner and revisit trigger.

## 3. Gates

1. Product Owner draft complete.
2. Mechanical compiler clean.
3. Backlog reviewer approved.
4. Domain owner gates approved.
5. Cross-domain and cross-release reconciliation approved.
6. Program final gate approved by the user.

Every decision is append-only and tied to the exact plan hash and gate revision. Approval does not activate a release.

## 4. Atomic PMO apply

Run `backlog-plan verify`, `backlog_compile.py verify-apply`, then `backlog-plan apply`. Apply all structural changes in one transaction. A hash, gate revision, active-lane contract or compiler mismatch rolls back the whole mutation.

In baseline/replan mode report the applied program/release matrix and stop. In feature mode return only the approved execution set to `deliver`, which explicitly activates the target release before development.
