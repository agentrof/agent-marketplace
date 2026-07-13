# API Contract Rulebook

Stack-agnostic rules for the interface contract. Every rule here stays true when the backend stack changes; realization (routing, serialization, middleware) lives in the bound stack skill. Each rule lands in a contract field: path, method, request or response shape, error cases, or the conventions preamble.

## Resource Modeling

- Resources are plural nouns: `/users`, `/orders`, `/order-items`. One naming convention across the whole contract.
- No verbs in paths. A non-CRUD action is modeled as a state transition (update the status field) or as a subordinate resource recording the act: `POST /orders/{id}/cancellations`, never `POST /orders/{id}/cancel`.
- Nesting expresses containment, one parent deep: `/users/{id}/orders` is fine. Deeper chains get promoted to a top-level resource with a filter: `/order-items?order_id=...`, not `/users/{uid}/orders/{oid}/items/{iid}`.
- Path identifiers are opaque to clients; queries are expressed by declared filters, never by inventing path segments.
- The contract does not mirror the storage model. Response shapes are designed for consumers; a contract that exposes storage column layout couples every client to the schema.

## Verb Semantics

| Verb | Meaning | Idempotent | Success | Contract note |
|---|---|---|---|---|
| GET | Read, no side effects | Yes | 200 | Safe to cache and retry; never mutates |
| POST | Create or process | No | 201 plus Location on create; 200 on process | Declare an idempotency key when clients may retry |
| PUT | Full replacement | Yes | 200 | Requires the complete representation; missing fields are removals |
| PATCH | Partial update | Not by default | 200 | Declare merge semantics for nested fields |
| DELETE | Remove | Yes | 204 | Repeated DELETE returns 204 or 404: pick one stance contract-wide and record it |

## Idempotency

- PUT and DELETE are idempotent by construction: repeating the call converges on the same state and the contract must not break that (no counters bumped on replay).
- Unsafe retries: any POST a client may retry after a timeout (payment, submission, dispatch) declares an idempotency key. The client sends a unique key header; the server stores key and response for a declared window and replays the stored response on duplicates. The header name and the window are contract facts, listed with the endpoint.
- Decision test: IF a timeout would make a client retry and the retry could double an effect, THEN the endpoint declares the key. No key on such an endpoint is a missing error case, and the contract is incomplete.

## Filtering, Sorting, Field Selection

- Filters are declared query parameters, one per filterable field: `?status=active&role=admin`. The contract lists which fields are filterable; "filter on anything" cannot be declared, tested, or indexed.
- Sorting: `?sort=created_at` ascending, `?sort=-created_at` descending, comma-separated tie-breakers. Every collection declares its default sort; an undeclared default changes silently when storage changes.
- Field selection (`?fields=id,name`) only when a consumer's payload budget demands it; record which endpoints support it and why.
- Cross-field search is a declared parameter (`?q=`) with declared matching semantics, never an accident of implementation.

## Pagination: Offset vs Cursor

Every collection endpoint declares its pagination at contract time; retrofitting is a breaking change.

| Choose | When | Response declares |
|---|---|---|
| Offset (`page`, `page_size`) | Bounded collections, jump-to-page needs, admin lists | `items`, `page`, `page_size`, `total` |
| Cursor (`cursor`, `limit`) | Unbounded growth, feeds, collections mutating under readers | `items`, `next_cursor` (opaque), `has_more` |

- Cursor tokens are opaque: clients never parse or construct them, so the server may change their encoding freely.
- Offset over a fast-mutating collection skips or repeats rows as pages shift; that observed symptom forces cursor.
- IF expected growth is unquantified, THEN the choice is blocked on a budget: escalate per the nfr-budgets script rather than defaulting silently.

## Status-Code Taxonomy

One meaning per code, uniform across the contract. DON'T invent per-endpoint meanings.

| Code | Meaning | Use |
|---|---|---|
| 200 | Success with body | GET, PUT, PATCH, processing POST |
| 201 | Created | Creating POST; include Location |
| 204 | Success, no body | DELETE |
| 400 | Malformed request | Unparseable body, unknown parameters |
| 401 | Not authenticated | Missing or invalid credentials |
| 403 | Authenticated, not allowed | Authorization failure |
| 404 | Not found | Absent resource; also cross-tenant probes when existence itself is sensitive |
| 409 | State conflict | Duplicate unique value, illegal state transition |
| 422 | Validation failure | Parseable body, invalid content |
| 429 | Rate limited | Include a retry-after signal |
| 500 | Server fault | Never for an expected condition |
| 503 | Temporarily unavailable | Declared degraded mode, maintenance |

- 400 vs 422: 400 is unparseable or unknown shape; 422 is parseable but invalid values.
- 409 vs 422: 409 conflicts with existing state; 422 is invalid in isolation.
- 401 vs 403: 401 asks "who are you"; 403 answers "not you". A 403 where existence is sensitive becomes 404 by recorded stance.

## Error Envelope

One envelope for every error of every endpoint:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed.",
    "details": [
      {"field": "email", "message": "Invalid format."}
    ]
  }
}
```

- `code` is machine-readable, stable, snake_case. Consumers branch on it, so renaming a code is a breaking change. The complete code set per endpoint belongs to the contract's error cases; an endpoint without them is incomplete.
- `message` is human-readable and safe: no stack traces, no internal identifiers, no storage details.
- `details` is optional and structured: per-field entries for validation, per-item entries for bulk.

## Versioning

- Recommended default: a path version segment (`/api/v1/...`) declared in the first contract, with an additive-first policy: add optional fields and new endpoints freely inside a version; never remove, rename, retype, or tighten within one.
- Breaking changes (removal, rename, type change, semantics change, validation tightening, pagination or envelope change) require: a new version, both versions served through a declared deprecation window, and the delta summary's breaking-change flag set with a migration note.
- Header or query-parameter versioning are acceptable recorded alternatives when path versioning is unavailable at the edge. The stance, whichever it is, is written in the contract; the only wrong stance is an implicit one.

## Bulk Operations

- A bulk endpoint takes an `items` array and returns per-item results in input order; each entry carries its own status and, on failure, its own error envelope. One failed item never silently voids the report of the others.
- Declare the atomicity stance per bulk endpoint: all-or-nothing (a single transaction, legal only inside one store) or per-item partial success. Per-item is the default for anything crossing entities or modules.
- Declare a maximum batch size; an unbounded batch is an unquantified load budget wearing a request body.

```json
{
  "results": [
    {"index": 0, "status": "created", "id": "u_123"},
    {"index": 1, "status": "failed", "error": {"code": "duplicate_email", "message": "Email already registered."}}
  ]
}
```
