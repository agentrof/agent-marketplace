# Architecture and contracts

## Boundaries and topology

Start from accepted components and their responsibilities. Assign each changed
business process to an existing owner or explain why no technical component is
needed. A built deployable has a source path. A managed, external or self-hosted
dependency can be a component without inventing an application source tree.
Component identity describes responsibility rather than a vendor or environment.

Choose module boundaries around cohesive rules and vocabulary. Cross-boundary
calls use an explicit interface. Respect established dependency direction and
avoid reaching into another module's internal state. Introduce a new service
only when an independent deployment, ownership or operating constraint earns
its cost. Read-model separation, event processing and additional adapters each
need a concrete requirement rather than a default architecture template.

For parallel work, give files or modules one active writer, identify shared
schemas/generated files, and define the order in which compatible increments
can integrate. Read access for investigation does not confer write ownership.

## Interface contract

Define the consumer-visible request and response, identity/authorization,
validation, errors, ordering, pagination/bounds, deadlines and retry semantics.
Keep storage representation behind the boundary. Match existing naming and
error conventions. Record compatibility and versioning for changed consumers.
For retryable state-changing operations, define the idempotency scope and
conflicting-request behavior. Refusals must neither leak protected existence
nor accidentally reveal internal errors.

Generated types or schemas should have one authoritative source. Test the real
response and client interpretation against it. Trace a significant contract
change to its business criterion and affected journey. A field documented but
never delivered is still a defect.

## Cross-cutting decisions

Keep a transaction within its authoritative store when that satisfies the
invariant. Cross-store work declares delivery guarantees, duplicate handling,
ordering and recovery. Use an outbox or compensation only for the consistency
problem it solves. A cache names its source, invalidation/refresh, staleness
tolerance, access boundary and behavior during source failure.

Bound request chains, queues, streams and retries. Propagate cancellation and
deadlines. Identify saturation behavior and safe degradation. Define readiness
from the service's own required capabilities. Include correlation, errors,
latency and capacity signals that make the changed behavior diagnosable without
logging sensitive payloads.

## Decision and document scope

Record material boundary, technology, ownership, consistency, security,
compatibility and rollout choices. Include the forcing requirement, meaningful
alternative, tradeoffs and exit/recovery path. Quantify the load or budget when
it is the reason for the choice. Keep uncertain estimates labeled and choose a
measurement when it changes the decision.

Document ordinary local changes following the accepted pattern in the task. Preserve existing document locations and links. Update
affected API, data, runtime and design records in the same delivery. Supersede
an accepted decision through the project's decision process rather than
quietly rewriting its rationale.
