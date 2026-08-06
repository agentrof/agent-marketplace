# Program and Release Plan Contract

The transient JSON plan is compiler input. PMO is the only durable writer.

- `program_id`, ordered releases, epics and stories use stable IDs. Every epic
  has a goal. Every story has one epic and one release.
- Every release names its exact effective Experience Design registry path and
  hash. Backward cross-release dependencies are invalid.
- Criteria are qualified as `<space>:<criterion-id>`. Migrated unverified
  criteria alone use `legacy:<criterion-id>`.
- Every story carries one delivery owner, a supporting-role list, solution and
  budget refs, structured DoR and DoD lists, and exact UX refs such as
  `PRG-001:SCR-001@r2` when `ui` is true.
- A dependency is `{item, reason}` and points to consumed output. SHARES is
  `{left, right, subject}` for a shared contract that is not an ordering edge.
- Deferred stories carry reason, owner and revisit trigger. No criterion is
  silently dropped.
- Feature mode declares `execution_set`: requested feature stories plus only
  user-approved unfinished transitive prerequisites. Completed prerequisites
  remain outside it. Active and completed story contracts are immutable.
- The plan declares every expected domain gate. Reviewer, domain,
  reconciliation and program decisions are append-only PMO records tied to the
  current plan hash. Approval and atomic apply do not activate a release.
- Reviewer-driven draft changes are new revisions of the same active plan.
  Prior gate decisions stay in history and cannot approve the new hash.
