# Cross-Cutting Decisions

Defaults and obligations for concerns that span modules. Every rule lands in a named artifact: an endpoint's authorization field, an interface obligation at a seam, or a decision log entry.

## Authorization Model

- Name the model in every delta that adds or changes an endpoint: role-based by default; attribute-based only when an attribute condition (ownership, tenancy, time window, record state) is itself a business rule in the brief. The choice and its trigger are recorded per delta.
- Where the check lives: every endpoint's authorization field declares the required role AND the object-level condition ("owner of the record, or workspace admin"). A role check alone is incomplete whenever the resource is per-user or per-tenant; the object-level condition is what stops identifier guessing across tenants.
- The check is enforced in the service layer against data the owning module reads itself. DON'T derive any privilege decision from client-supplied identity or role claims.
- Scope reminder: changing the security or authorization model is an architect halt-and-escalate, never a silent decision. This file designs within the approved model; the trust-boundary walk below prepares the escalation when the model itself moves.

## Transactions Across Stores

- Inside one store, use the store's transaction. Hard DON'T: never deploy outbox relays or saga machinery for writes inside a single store; that is a transaction wearing a costume, and it trades away atomicity for nothing.
- Outbox, for reliably propagating facts: the state change and the event record are written in the same local transaction; a relay publishes afterwards. Delivery becomes at-least-once, so every consumer is idempotent. Choose when the workflow is "commit, then inform".
- Saga, for multi-step workflows with rollback semantics: a sequence of local transactions, each with a declared compensation, run by one owning orchestrator or an explicit event chain. A step without a compensation is a design gap, not a detail for later. Choose when steps across modules or stores must all complete or all be undone.
- Decision test: IF the second write can lag the first by seconds without breaking a business rule, THEN outbox suffices; IF a later failure must undo earlier writes, THEN saga. Either choice is a decision record, and any data these paths copy carries the constitution's snapshot declaration.

## Caching

- A cache exists only with a declared invalidation path: event-driven invalidation, TTL with an accepted staleness tolerance, or write-through. "We flush it manually when it looks stale" is an undeclared cache and fails the design gate.
- The record names four things: what is cached, where it lives, the invalidation trigger, and the staleness tolerance. A cached copy of mutable data is a snapshot under the constitution rule; undeclared, it is a violation, not a speedup.
- Decision test: IF the invalidation trigger cannot be named in one sentence, THEN the cache is not designed yet; do not bank its latency win against a budget.

## Resilience Obligations at Seams

Every cross-module or external call declares these as interface obligations in the contract, not as implementation details. The stack skill implements them; the review checks the seam against the contract.

- Timeout: a number per call, derived from the latency budget of the calling flow. A missing timeout is an unbounded worst case, an infinite budget nobody granted.
- Retry: only on idempotent operations or under an idempotency key; retry count and backoff-with-jitter parameters declared. Retrying a non-idempotent call without a key is how one payment becomes two.
- Circuit breaking: after the declared failure threshold the caller stops calling and serves the declared degraded behavior; recovery is probed with trial calls, never assumed on a clock alone.
- Degraded behavior: what the caller returns while the dependency is broken: stale cache with its declared staleness, a partial response with an explicit marker, or a clean 503. "Whatever the exception bubbles up as" is not a declared behavior.

## Observability Design

- Four golden signals per boundary: latency, traffic, errors, saturation. The delta declares which endpoints and seams emit them; a new seam with no declared signals is invisible in production by design, not by accident.
- Correlation id propagation is an interface obligation: accept the inbound id or mint one at ingress, return it in every response, forward it on every outbound call and event. A contract omitting the correlation header at a seam is incomplete, the same class as a missing error case.
- Semantic log levels are a design stance: debug for diagnostics, info for lifecycle facts, warning for handled anomalies (retry fired, fallback served), error for failures needing attention. An expected user error (wrong password, validation failure) is never an error-level event; alarms trained on noise get ignored, which is the real cost.
- Bounded cardinality: metric labels carry only bounded value sets (status code, endpoint name). Unbounded identifiers (user id, order id) belong in logs and traces, never in labels.

## Trust-Boundary Walk

[conditional] Read this section only when the delta touches the security model: a new principal, a new data classification, a new external surface, or a changed authorization rule. It does not gate ordinary deltas. Security-model changes are escalations; the walk prepares the escalation summary, it does not authorize the change.

1. Enumerate the entry points the delta adds or alters; mark each protected or public.
2. Name the principals (user roles, service identities, anonymous) that can reach each entry point.
3. Trace each data classification crossing a boundary: what leaves, to whom, where it is logged.
4. At each crossing check: authentication demanded, authorization declared (role plus object condition), input validated at the boundary, secrets absent from payloads and logs.
5. Output an attack-surface delta: entry points touched, protection status and new data exposures. Attach it to the active Delivery Item and persist it in the owning component or module security facet under `workspace/docs/system-architecture/components/<component>/.../security/<scope>/threat-model.md`, so the security picture accumulates instead of dying with the escalation.
