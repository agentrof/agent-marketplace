# State changes and evolution

## Transactions, retries and concurrency

Name the invariant and the operations that must commit together. Choose the
store's transaction/isolation or conditional-write mechanism accordingly.
Read-then-write checks alone do not prevent a concurrent conflict. Include
unique constraints, version predicates or locks where they enforce the rule.

Define retry identity, key scope, request-payload matching, result retention and
the response to a duplicate still in progress. A timed-out caller may have
committed. Reconcile that uncertainty before repeating an external effect.
Single-use grants are consumed atomically with their effect where possible.

For cross-store work, describe delivery guarantees, duplicate processing,
ordering, replay and compensating action. A database commit and external send
need an explicit consistency strategy. Audit required for correctness must
succeed with the effect. Record denials without allowing an audit failure to
grant the denied action.

## Migrations and compatibility

Inspect existing migration history and representative old records. Identify
all readers and writers, including jobs, exports and older deployed versions.
Determine lock, rewrite and storage requirements for the selected engine and
version before running DDL. Set operational bounds for backfills and index work.

Where mixed versions or rollback require it, expand the representation, migrate
in bounded resumable batches, switch consumers, verify, and remove obsolete
structure only after its dependency window closes. Define defaults and invalid
legacy-value handling explicitly. Preserve data needed for recovery.

The migration runner must serialize conflicting changes, preserve applied
history and reject altered applied migrations according to the project's
contract. Coordinate migration identifiers across branches. Re-running the
runner must safely recognize completed work. Interrupted backfills and pending
steps need restart semantics, even when individual DDL statements are not
independently idempotent.

## Evidence

Choose representative old, null/absent, boundary and inconsistent records.
Verify the final values and constraints after the forward migration. Exercise
a second runner invocation and interruption/restart for resumable work. Test
concurrent/duplicate requests against the actual persistence boundary when
mocked tests cannot establish its guarantee.

Prove restore or compensating recovery for consequential changes. Distinguish
an application rollback from a data rollback and state when an inverse migration
would lose information. Check startup applies or detects required schema changes.
Record the candidate, migration/fixture versions and observed results. Empty
database creation remains a separate compatibility case.
