# Conditional Capability Disciplines

[conditional] Read when the brief names one of these capabilities: internationalization, client telemetry, real-time data, or offline support. Implement only the sections the brief names; adding an unnamed capability is scope creep. When the brief does name one, that section's rules are review-blocking, not optional polish.

## Internationalization

- Every user-facing string comes from a message catalog keyed by stable ids; a hardcoded string in a component is the token violation of text.
- Plurals go through the locale plural rules, never `count === 1 ? singular : plural`; languages disagree about plural categories.
- Dates, numbers, and currency format through the locale formatting APIs with the active locale, never hand-built.
- Interpolated values are named parameters inside the message, not string concatenation; word order changes across languages.
- RTL: use logical properties and direction-aware layout (start/end, not left/right), set the document direction per locale, and mirror direction-carrying icons (arrows, chevrons) while leaving neutral ones alone.
- Text expands across languages; layouts, buttons, and truncation rules must survive the longest catalog entry, not the English one.

## Client Telemetry

- Error reporting: one global handler for uncaught errors and unhandled rejections, plus error-boundary hooks; report the component stack and app release id, and deduplicate repeats.
- Events: batch in memory and flush on an interval, on batch size, and on page-hide; a network call per click is a defect.
- No PII in any payload: no names, emails, tokens, or free-text field values; identify with opaque ids and scrub URLs of query parameters that carry user data.
- Telemetry must never break the app: reporting calls are fire-and-forget, wrapped so their failures are swallowed and never bubble into UI state.
- Respect consent where the brief declares it: no events before consent, and a kill switch that disables collection entirely.

## Real-Time Data

- Socket lifecycle lives in one module (connect, authenticate, subscribe, teardown); components consume through a hook and never own connections.
- Reconnect with exponential backoff plus jitter, capped; after reconnect, re-authenticate, resubscribe, and reconcile missed state (refetch or replay) instead of assuming continuity.
- Pushed data invalidates or updates the server-state cache; components keep reading the cache. DON'T build a parallel socket-fed store beside the query layer; two owners for server state is the state-separation violation.
- Surface connection state (live, reconnecting, offline) where the brief requires user awareness; silent staleness is a defect on a real-time surface.
- Clean up subscriptions on unmount; a leaked subscription is a leaked timer with a network bill.

## Offline Support

- Declare the cache strategy per resource class: static assets precached, reference data cache-first with background refresh, user data network-first with cached fallback. An undeclared strategy is not offline support; it is undefined behavior.
- Queue mutations made offline with their timestamps in durable storage, not memory, and replay them in order on reconnect.
- Conflict surface: define what happens when a replayed write collides with a newer server write. Last-write-wins must be a declared decision, and the losing write is surfaced to the user, never silently dropped.
- Show sync status: a pending-changes indicator and per-item failure states; the user must never wonder whether an edit is saved.
- The QA matrix gets rows for the offline path: go offline, mutate, reconnect, verify replay and the conflict surface.
