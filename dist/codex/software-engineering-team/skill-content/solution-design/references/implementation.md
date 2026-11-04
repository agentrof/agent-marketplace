# Implementation checks

Read the section relevant to the selected stack and changed behavior. Use the
repository's actual versions, conventions and existing helpers. Consult the
technology's official documentation for unfamiliar or version-sensitive APIs.

## Backend and services

- Validate untrusted inputs at the boundary and preserve type/shape contracts
  through business and persistence code. Translate internal failures into the
  declared external error form without secrets or stack traces.
- Keep resource ownership explicit. Close sessions, connections, files and
  streams on success, error and cancellation. Avoid blocking calls on an event
  loop. Bound thread pools, connection pools, task queues and fan-out.
- Follow the project's transaction owner. Avoid independent commits in helpers
  that must succeed atomically. Prove idempotency and conflicts through real
  persistence when those guarantees matter.
- Detect query amplification and unbounded response size. Use measured query
  plans and representative data before adding indexes or caches.
- Validate configuration at the appropriate startup boundary. Inject required
  services through existing mechanisms and support graceful shutdown.

In Python/FastAPI, inspect async/sync boundaries, validation-model behavior,
dependency cleanup and supported lifespan APIs. In Java or other thread-based
servers, inspect transaction proxies, thread/context propagation and pool
exhaustion. In Node.js, inspect event-loop blocking, rejected promises,
backpressure and cancellation. These checks do not require a new framework or
a uniform router/service/repository class hierarchy.

## Frontend and clients

- Interpret the API contract at one data boundary and reuse the typed result.
  Keep server state and local interaction state consistent. Avoid parallel
  caches with conflicting ownership.
- Cover loading, empty, error, denied, stale and success states relevant to the
  journey. Keep pending actions visible and handle retries or cancellation
  without duplicate effects. Client route guards complement server enforcement.
- Clean up effects, subscriptions, timers and listeners. Ignore or cancel stale
  responses and prevent state updates from an obsolete request.
- In React/TypeScript, inspect effect dependencies, stable list identity, derived
  state and escape hatches in types. Add memoization or virtualization when the
  actual workload warrants it. Keep independent requests parallel and bounded.
- Follow semantic tokens and component conventions. Test important controls
  with a keyboard and real rendered states, including focus restoration.

## Conditional capabilities

For localization, use the project's message catalog, plural/date/number rules,
direction-aware layout and text-expansion checks. For realtime feeds, define
bounded reconnect, reauthentication, missed-event reconciliation and one state
owner. For offline mutations, define durable queue ownership, replay identity,
conflict handling and visible sync status. For telemetry, minimize sensitive
data, honor consent and prevent instrumentation failures from breaking the
product. Add these capabilities only when the task or accepted design requires
them.

## Delivery integrity

Implement a usable vertical slice before spreading placeholders across many
components. Keep executable schemas, tests and owning docs synchronized.
Use the selected dependency manifest and lockfile workflow. Evidence must cover
the feature through its normal entry point and retain behavior for existing
callers and data.
